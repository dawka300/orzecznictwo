import gzip
import os
import sys
from datetime import datetime, timezone

import pytest

from judgments_core.backup import dated_path, dump_database, prune

DSN = "mysql://jan:tajne%21@db.example:3307/etpc"
# Udaje mysqldump: wypisuje argumenty i hasło z MYSQL_PWD.
FAKE_DUMP = [
    sys.executable,
    "-c",
    "import os, sys; sys.stdout.write(' '.join(sys.argv[1:]) + ' pwd=' + os.environ['MYSQL_PWD'])",
]
FAILING_DUMP = [sys.executable, "-c", "import sys; sys.stderr.write('Access denied'); sys.exit(2)"]


def test_dump_writes_gzip_with_password_outside_arguments(tmp_path):
    path = tmp_path / "kopie" / "etpc.sql.gz"
    size = dump_database(DSN, path, FAKE_DUMP)
    text = gzip.decompress(path.read_bytes()).decode()
    assert size == path.stat().st_size
    assert "--host=db.example --port=3307 --user=jan etpc" in text
    assert "--single-transaction" in text
    assert text.endswith("pwd=tajne!")
    assert "tajne" not in text.split(" pwd=")[0]
    assert [p.name for p in path.parent.iterdir()] == ["etpc.sql.gz"]


def test_failed_dump_keeps_previous_copy(tmp_path):
    path = tmp_path / "etpc.sql.gz"
    path.write_bytes(b"poprzednia")
    with pytest.raises(RuntimeError, match="Access denied"):
        dump_database(DSN, path, FAILING_DUMP)
    assert path.read_bytes() == b"poprzednia"
    assert [p.name for p in tmp_path.iterdir()] == ["etpc.sql.gz"]


def test_missing_program_is_a_runtime_error(tmp_path):
    with pytest.raises(RuntimeError, match="nie można uruchomić"):
        dump_database(DSN, tmp_path / "etpc.sql.gz", ["nie-ma-takiego-programu-xyz"])


def test_dated_path_uses_utc_time(tmp_path):
    now = datetime(2026, 10, 29, 8, 5, 3, tzinfo=timezone.utc)
    assert dated_path(tmp_path, "nsa", now) == tmp_path / "nsa-20261029-080503.sql.gz"


def test_prune_keeps_newest_three_of_the_project_only(tmp_path):
    names = [
        "etpc.sql.gz",
        "etpc-20261021-100000.sql.gz",
        "etpc-20261022-100000.sql.gz",
        "etpc-20261023-100000.sql.gz",
        "etpc-20261024-100000.sql.gz",
    ]
    for age, name in enumerate(reversed(names)):
        path = tmp_path / name
        path.write_bytes(b"x")
        os.utime(path, (1_000_000 - age, 1_000_000 - age))
    (tmp_path / "etpc2-20261020-100000.sql.gz").write_bytes(b"inny projekt")
    (tmp_path / "etpc-20261025-100000.sql.gz.abc.tmp").write_bytes(b"w toku")
    removed = prune(tmp_path, "etpc")
    assert sorted(p.name for p in removed) == ["etpc-20261021-100000.sql.gz", "etpc.sql.gz"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "etpc-20261022-100000.sql.gz",
        "etpc-20261023-100000.sql.gz",
        "etpc-20261024-100000.sql.gz",
        "etpc-20261025-100000.sql.gz.abc.tmp",
        "etpc2-20261020-100000.sql.gz",
    ]
