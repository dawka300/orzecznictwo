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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import NamedTuple
from urllib.parse import parse_qs, unquote, urlparse

import pymysql

from judgments_core.source import utc_now

CORE_MIGRATIONS = Path(__file__).parent / "migrations"
MIGRATION_RE = re.compile(r"^\d{3}_\w+\.sql$")

log = logging.getLogger(__name__)


class LockedError(RuntimeError):
    """Another pass of the same project is still running."""


RUN_COLUMNS = (
    "pages",
    "records",
    "records_new",
    "records_changed",
    "problems",
    "cursor_end",
    "error",
)


@dataclass
class SaveStats:
    new: int = 0
    changed: int = 0


class StoredRecord(NamedTuple):
    """A raw record as read back from raw_records for a project's parser."""

    source_id: str
    body: str
    changed_at: datetime


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
        """Upsert records and move the cursor in one transaction; returns SaveStats.

        Saved records also resolve their open "pobierz" problems (e.g. missing).
        """
        stats = SaveStats()
        try:
            with self.conn.cursor() as cur:
                if records:
                    known = self._known_hashes(cur, source, [r.source_id for r in records])
                    for r in records:
                        if r.source_id not in known:
                            stats.new += 1
                        elif known[r.source_id] != r.sha256:
                            stats.changed += 1
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
                    self._resolve_problems(
                        cur, source, [r.source_id for r in records], "pobierz", utc_now()
                    )
                if cursor is not None:
                    self._set_state(cur, state_key, cursor)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return stats

    def raw_records_after(self, source, after, before, limit):
        """Up to `limit` raw records changed after `after` and before `before`, oldest first.

        `after` is (changed_at, source_id) of the last record processed, or
        None for all; source_id breaks ties within the same second.
        """
        sql = "SELECT source_id, body, changed_at FROM raw_records WHERE source = %s"
        sql += " AND changed_at < %s"
        params = [source, _naive_utc(before)]
        if after is not None:
            sql += " AND (changed_at > %s OR (changed_at = %s AND source_id > %s))"
            params += [after[0], after[0], after[1]]
        sql += " ORDER BY changed_at, source_id LIMIT %s"
        with self.conn.cursor() as cur:
            cur.execute(sql, params + [limit])
            rows = cur.fetchall()
        self.conn.commit()
        return [StoredRecord(*row) for row in rows]

    def derive_batch(self, source, derive, records, state_key, state):
        """Run a project's derive(cursor, records) and save its state in one transaction.

        derive returns problems [(source_id, problem, details)]; they are
        recorded (stage "przetworz"), the other records' problems resolved.
        Returns the problems.
        """
        now = utc_now()
        try:
            with self.conn.cursor() as cur:
                problems = list(derive(cur, records) or [])
                failed = {p[0] for p in problems}
                self._resolve_problems(
                    cur,
                    source,
                    [r.source_id for r in records if r.source_id not in failed],
                    "przetworz",
                    now,
                )
                self._add_problems(cur, source, "przetworz", problems, now)
                self._set_state(cur, state_key, state)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return problems

    # --- dziennik: przebiegi, zdarzenia, problemy (migracja core/002) ---

    def start_run(self, command, cursor_start=None):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO fetch_runs (command, started_at, cursor_start) VALUES (%s, %s, %s)",
                (command, _naive_utc(utc_now()), cursor_start),
            )
            run_id = cur.lastrowid
        self.conn.commit()
        return run_id

    def finish_run(self, run_id, status, **counts):
        """counts: pages, records, records_new, records_changed, problems, cursor_end, error."""
        columns = ["finished_at = %s", "status = %s"]
        values = [_naive_utc(utc_now()), status]
        for name, value in counts.items():
            if name not in RUN_COLUMNS:
                raise ValueError(f"nieznana kolumna fetch_runs: {name}")
            columns.append(f"{name} = %s")
            values.append(value)
        with self.conn.cursor() as cur:
            cur.execute(
                f"UPDATE fetch_runs SET {', '.join(columns)},"
                " events = (SELECT COUNT(*) FROM fetch_events WHERE run_id = %s)"
                " WHERE id = %s",
                values + [run_id, run_id],
            )
        self.conn.commit()

    def add_event(self, run_id, kind, message=None, url=None, wait_s=None):
        """Saved and committed at once, also when the pass fails afterwards."""
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO fetch_events (run_id, at, kind, message, url, wait_s)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (run_id, _naive_utc(utc_now()), kind, message, url, wait_s),
            )
        self.conn.commit()

    def add_problems(self, source, stage, problems):
        """problems: [(source_id, problem, details)]."""
        with self.conn.cursor() as cur:
            self._add_problems(cur, source, stage, problems, utc_now())
        self.conn.commit()

    def open_problems(self, source, stage, problem, limit=None):
        sql = (
            "SELECT source_id FROM record_problems WHERE source = %s AND stage = %s"
            " AND problem = %s AND resolved_at IS NULL ORDER BY source_id"
        )
        params = [source, stage, problem]
        if limit:
            sql += " LIMIT %s"
            params.append(limit)
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            ids = [row[0] for row in cur.fetchall()]
        self.conn.commit()
        return ids

    # --- wektory: tabela projektu "co już policzone" (judgments_core/embed.py) ---
    # Kolumny: <key>, <doc>, body_sha256, model, chunks, embedded_at; nazwy
    # tabel i kolumn podaje klasa potomna EmbedJob (stałe w kodzie, nie od użytkownika).

    def vectors_done(self, table, model):
        """(judgments, chunks) already embedded with this model."""
        with self.conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*), COALESCE(SUM(chunks), 0) FROM {table} WHERE model = %s", (model,)
            )
            count, chunks = cur.fetchone()
        self.conn.commit()
        return int(count), int(chunks)

    def vectors_orphans(self, table, key, parent):
        """Ids in the tracking table whose judgment is gone from the parent table."""
        with self.conn.cursor() as cur:
            cur.execute(
                f"SELECT v.{key} FROM {table} v LEFT JOIN {parent} p ON p.id = v.{key} WHERE p.id IS NULL"
            )
            ids = [row[0] for row in cur.fetchall()]
        self.conn.commit()
        return ids

    def vectors_forget(self, table, key, ids=None):
        """Remove tracking rows (all of them for ids=None: everything is embedded again)."""
        with self.conn.cursor() as cur:
            if ids is None:
                cur.execute(f"DELETE FROM {table}")
            elif ids:
                cur.execute(f"DELETE FROM {table} WHERE {key} IN %s", (list(ids),))
        self.conn.commit()

    def vectors_record(self, table, key, doc, rows):
        """rows: [(judgment id, document id, sha256, model, chunks)] embedded now."""
        now = _naive_utc(utc_now())
        with self.conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO {table} ({key}, {doc}, body_sha256, model, chunks, embedded_at)"
                " VALUES (%s, %s, %s, %s, %s, %s)"
                f" ON DUPLICATE KEY UPDATE {doc} = VALUES({doc}), body_sha256 = VALUES(body_sha256),"
                " model = VALUES(model), chunks = VALUES(chunks), embedded_at = VALUES(embedded_at)",
                [(*row, now) for row in rows],
            )
        self.conn.commit()

    @staticmethod
    def _add_problems(cur, source, stage, problems, now):
        if problems:
            cur.executemany(
                "INSERT INTO record_problems"
                " (source, source_id, stage, problem, details, first_seen, last_seen)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)"
                # times/first_seen przed resolved_at: MariaDB liczy SET po kolei.
                " ON DUPLICATE KEY UPDATE"
                "  times = IF(resolved_at IS NULL, times + 1, 1),"
                "  first_seen = IF(resolved_at IS NULL, first_seen, VALUES(first_seen)),"
                "  problem = VALUES(problem), details = VALUES(details),"
                "  last_seen = VALUES(last_seen), resolved_at = NULL",
                [
                    (source, sid, stage, problem, details, _naive_utc(now), _naive_utc(now))
                    for sid, problem, details in problems
                ],
            )

    @staticmethod
    def _resolve_problems(cur, source, source_ids, stage, now):
        for start in range(0, len(source_ids), 500):
            chunk = source_ids[start : start + 500]
            cur.execute(
                "UPDATE record_problems SET resolved_at = %s"
                " WHERE source = %s AND stage = %s AND resolved_at IS NULL"
                f" AND source_id IN ({', '.join(['%s'] * len(chunk))})",
                [_naive_utc(now), source, stage, *chunk],
            )

    @staticmethod
    def _known_hashes(cur, source, source_ids):
        cur.execute(
            "SELECT source_id, sha256 FROM raw_records WHERE source = %s"
            f" AND source_id IN ({', '.join(['%s'] * len(source_ids))})",
            [source, *source_ids],
        )
        return dict(cur.fetchall())

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


def read_state(cur, key):
    """One sync_state value through an open cursor (pages read it this way), or None."""
    cur.execute("SELECT value FROM sync_state WHERE `key` = %s", (key,))
    row = cur.fetchone()
    return row[0] if row else None
