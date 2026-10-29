"""Database dump to <backup_dir>/<project>-<UTC date>.sql.gz with mysqldump/mariadb-dump.

Only the newest KEEP dumps of a project stay (prune); the older ones are removed.

The dump has no CREATE DATABASE, so it loads into a database of any name
(on the hosting database names get the account's prefix):
    gunzip -c etpc.sql.gz | mysql -u USER -p DATABASE
"""

from __future__ import annotations

import gzip
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from judgments_core.db import parse_dsn

log = logging.getLogger(__name__)

DUMP_OPTIONS = [
    "--single-transaction",  # spójny obraz InnoDB bez blokowania tabel
    "--quick",
    "--default-character-set=utf8mb4",
    "--hex-blob",
    "--no-tablespaces",  # bez uprawnienia PROCESS, którego hosting nie daje
]
KEEP = 3


def dated_path(directory, name, now=None):
    """<directory>/<name>-YYYYMMDD-HHMMSS.sql.gz with the UTC time."""
    now = now or datetime.now(timezone.utc)
    return Path(directory) / f"{name}-{now:%Y%m%d-%H%M%S}.sql.gz"


def prune(directory, name, keep=KEEP):
    """Remove all but the newest keep dumps of name (also the old undated <name>.sql.gz).

    Returns the removed paths."""
    pattern = re.compile(rf"{re.escape(name)}(-\d{{8}}-\d{{6}})?\.sql\.gz")
    dumps = sorted(
        (p for p in Path(directory).iterdir() if pattern.fullmatch(p.name)),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in dumps[keep:]:
        old.unlink()
        log.info("usunięto starą kopię %s", old)
    return dumps[keep:]


def dump_database(dsn, path, command, popen=subprocess.Popen):
    """Dump the DSN's database gzipped into path; returns its size in bytes.

    command is the dump program with its arguments, e.g. ["mysqldump"] or
    ["docker", "exec", "-i", "-e", "MYSQL_PWD", "<kontener>", "mariadb-dump"].
    The password goes in MYSQL_PWD, not on the command line. The previous dump
    is replaced only when the new one has finished.
    """
    conn = parse_dsn(dsn)
    if not conn["database"]:
        raise ValueError("DSN bez nazwy bazy - nie wiem, co zrzucić")
    args = [
        *command,
        *DUMP_OPTIONS,
        f"--host={conn['host']}",
        f"--port={conn['port']}",
        f"--user={conn['user']}",
        conn["database"],
    ]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "MYSQL_PWD": conn["password"]}
    started = time.monotonic()
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        with tempfile.TemporaryFile() as stderr:
            try:
                process = popen(args, stdout=subprocess.PIPE, stderr=stderr, env=env)
            except OSError as e:
                raise RuntimeError(f"nie można uruchomić {command[0]}: {e}") from e
            with process.stdout, gzip.open(tmp, "wb", compresslevel=6) as out:
                shutil.copyfileobj(process.stdout, out, 1024 * 1024)
            code = process.wait()
            stderr.seek(0)
            errors = stderr.read().decode("utf-8", errors="replace").strip()
        if code != 0:
            raise RuntimeError(f"kopia bazy nie powiodła się (kod {code}): {errors}")
        if errors:
            log.warning("kopia bazy: %s", errors)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    size = path.stat().st_size
    log.info(
        "kopia bazy %s: %s (%.0f MB, %.0fs)",
        conn["database"],
        path,
        size / 1e6,
        time.monotonic() - started,
    )
    return size
