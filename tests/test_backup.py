import gzip
import sys

import pytest

from judgments_core.backup import dump_database

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
