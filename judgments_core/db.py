"""MariaDB: connection, migrations, sync state, raw records, run lock.

All MariaDB-specific SQL of the core lives here. Unlike Postgres, MariaDB
commits every DDL statement at once: a migration that fails halfway leaves
the statements before the failure applied. Keep migrations small, one change
each, and make them safe to re-run where possible (IF NOT EXISTS).
"""

from __future__ import annotations

import contextlib
import logging
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pymysql

CORE_MIGRATIONS = Path(__file__).parent / "migrations"
MIGRATION_RE = re.compile(r"^\d{3}_\w+\.sql$")

log = logging.getLogger(__name__)


class LockedError(RuntimeError):
    """Another pass of the same project is still running."""


def parse_dsn(dsn):
    """mysql://user:password@host:3306/database -> pymysql.connect() arguments."""
    u = urlparse(dsn)
    if u.scheme not in ("mysql", "mariadb"):
        raise ValueError(f"DSN ma zaczynać się od mysql:// albo mariadb://, jest: {u.scheme}://")
    kwargs = {
        "host": u.hostname or "127.0.0.1",
        "port": u.port or 3306,
        "user": unquote(u.username or ""),
        "password": unquote(u.password or ""),
        "database": u.path.lstrip("/") or None,
        "charset": "utf8mb4",
    }
    for key, values in parse_qs(u.query).items():
        kwargs[key] = values[-1]
    return kwargs


def connect(dsn, **kwargs):
    return pymysql.connect(**{**parse_dsn(dsn), "autocommit": False, **kwargs})


def split_statements(sql):
    """Split a migration file on ';' at line ends. No procedures or triggers."""
    statements, current = [], []
    for line in sql.splitlines():
        if line.strip().startswith("--") and not current:
            continue
        current.append(line)
        if line.rstrip().endswith(";"):
            statement = "\n".join(current).strip().rstrip(";").strip()
            if statement:
                statements.append(statement)
            current = []
    rest = "\n".join(current).strip()
    if rest and not all(line.strip().startswith("--") for line in rest.splitlines()):
        statements.append(rest)
    return statements


def migration_files(*directories):
    """(name, path) in order: each directory's NNN_*.sql, directories in the given order.

    Names carry the directory ("core/001_raw_records.sql", "nsa/001_...")
    so core and project migrations can share numbers.
    """
    files = []
    for prefix, directory in directories:
        for path in sorted(Path(directory).glob("*.sql")):
            if MIGRATION_RE.match(path.name):
                files.append((f"{prefix}/{path.name}", path))
    return files


def migrate(conn, *directories):
    """Apply migrations not applied yet; returns their names."""
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name VARCHAR(191) PRIMARY KEY,"
            " applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
        )
        cur.execute("SELECT name FROM schema_migrations")
        done = {row[0] for row in cur.fetchall()}
    conn.commit()
    applied = []
    for name, path in migration_files(*directories):
        if name in done:
            continue
        log.info("migracja %s", name)
        with conn.cursor() as cur:
            for statement in split_statements(path.read_text(encoding="utf-8")):
                cur.execute(statement)
            cur.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (name,))
        conn.commit()
        applied.append(name)
    return applied


class MariaDbStore:
    """Sync state and raw records; what the Importer needs from the database."""

    def __init__(self, conn):
        self.conn = conn

    def get_state(self, key):
        with self.conn.cursor() as cur:
            cur.execute("SELECT value FROM sync_state WHERE `key` = %s", (key,))
            row = cur.fetchone()
        self.conn.commit()  # bez otwartej transakcji między przebiegami
        return row[0] if row else None

    def set_state(self, key, value):
        with self.conn.cursor() as cur:
            self._set_state(cur, key, value)
        self.conn.commit()

    def delete_state(self, key):
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM sync_state WHERE `key` = %s", (key,))
        self.conn.commit()

    def save_page(self, source, records, state_key, cursor):
        """Upsert records and move the cursor in one transaction."""
        try:
            with self.conn.cursor() as cur:
                if records:
                    cur.executemany(
                        "INSERT INTO raw_records"
                        " (source, source_id, url, content_type, body, sha256,"
                        "  fetched_at, changed_at)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                        # MariaDB nie zna "AS new" z MySQL 8 - stąd VALUES(kolumna).
                        # changed_at pierwsze: porównuje ze starym sha256.
                        " ON DUPLICATE KEY UPDATE"
                        "  changed_at = IF(sha256 = VALUES(sha256),"
                        "                  changed_at, VALUES(fetched_at)),"
                        "  url = VALUES(url), content_type = VALUES(content_type),"
                        "  body = VALUES(body), sha256 = VALUES(sha256),"
                        "  fetched_at = VALUES(fetched_at)",
                        [
                            (
                                source,
                                r.source_id,
                                r.url,
                                r.content_type,
                                r.body,
                                r.sha256,
                                _naive_utc(r.fetched_at),
                                _naive_utc(r.fetched_at),
                            )
                            for r in records
                        ],
                    )
                if cursor is not None:
                    self._set_state(cur, state_key, cursor)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    @contextlib.contextmanager
    def lock(self, name):
        """Named server lock: a second cron pass exits instead of running twice.

        Released by the server also when the process dies (the connection ends).
        """
        with self.conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s, 0)", (name,))
            got = cur.fetchone()[0] == 1
        if not got:
            raise LockedError(f"Poprzedni przebieg ({name}) jeszcze trwa.")
        try:
            yield
        finally:
            with self.conn.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK(%s)", (name,))

    @staticmethod
    def _set_state(cur, key, value):
        cur.execute(
            "INSERT INTO sync_state (`key`, value) VALUES (%s, %s)"
            " ON DUPLICATE KEY UPDATE value = VALUES(value)",
            (key, value),
        )


def _naive_utc(moment):
    # DATETIME nie ma strefy: zapisujemy zawsze UTC.
    return moment.replace(tzinfo=None) if moment.tzinfo else moment
