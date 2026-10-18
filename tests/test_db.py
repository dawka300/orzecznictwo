"""db.py: pure helpers always; MariaDB parts on a scratch database, skipped without it.

The scratch database comes from TEST_DB_DSN (default: the docker-compose
MariaDB's szkielet_test, created by docker/mariadb-init.sql). Never point it
at a real database: the tests drop all its tables.
"""

import os
from datetime import datetime, timezone

import pymysql
import pytest

from judgments_core import db
from judgments_core.source import RawRecord

TEST_DSN = os.environ.get("TEST_DB_DSN", "mysql://szkielet:szkielet@127.0.0.1:3306/szkielet_test")


def test_parse_dsn():
    assert db.parse_dsn("mysql://u%40x:p%3Aw@db.host:3307/nsa?connect_timeout=5") == {
        "host": "db.host",
        "port": 3307,
        "user": "u@x",
        "password": "p:w",
        "database": "nsa",
        "charset": "utf8mb4",
        "connect_timeout": "5",
    }
    with pytest.raises(ValueError):
        db.parse_dsn("postgresql://u@h/db")


def test_split_statements():
    sql = "-- opis\nCREATE TABLE a (\n x INT\n);\n\n-- drugi\nINSERT INTO a VALUES (1);\n"
    assert db.split_statements(sql) == [
        "CREATE TABLE a (\n x INT\n)",
        "-- drugi\nINSERT INTO a VALUES (1)",
    ]


def test_migration_files_order(tmp_path):
    (tmp_path / "002_b.sql").write_text("")
    (tmp_path / "001_a.sql").write_text("")
    (tmp_path / "README.md").write_text("")
    names = [n for n, _ in db.migration_files(("core", db.CORE_MIGRATIONS), ("p", tmp_path))]
    assert names == ["core/001_core.sql", "p/001_a.sql", "p/002_b.sql"]


@pytest.fixture
def conn():
    try:
        c = db.connect(TEST_DSN, connect_timeout=2)
    except pymysql.err.OperationalError as e:
        pytest.skip(f"brak testowej MariaDB ({e})")
    with c.cursor() as cur:
        cur.execute("SHOW TABLES")
        for (table,) in cur.fetchall():
            cur.execute(f"DROP TABLE `{table}`")
    c.commit()
    yield c
    c.close()


def test_migrate_once(conn, tmp_path):
    (tmp_path / "001_x.sql").write_text("CREATE TABLE x (id INT);\nINSERT INTO x VALUES (1);")
    assert db.migrate(conn, ("core", db.CORE_MIGRATIONS), ("p", tmp_path)) == [
        "core/001_core.sql",
        "p/001_x.sql",
    ]
    assert db.migrate(conn, ("core", db.CORE_MIGRATIONS), ("p", tmp_path)) == []


def test_save_page_upserts_and_tracks_changes(conn):
    db.migrate(conn, ("core", db.CORE_MIGRATIONS))
    store = db.MariaDbStore(conn)
    t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 2, 1, tzinfo=timezone.utc)
    t3 = datetime(2026, 3, 1, tzinfo=timezone.utc)

    store.save_page("src", [RawRecord("1", "ąę treść", fetched_at=t1)], "src:cursor", "p1")
    store.save_page("src", [RawRecord("1", "ąę treść", fetched_at=t2)], "src:cursor", "p2")
    with conn.cursor() as cur:
        cur.execute("SELECT body, fetched_at, changed_at FROM raw_records")
        assert cur.fetchall() == (("ąę treść", datetime(2026, 2, 1), datetime(2026, 1, 1)),)

    store.save_page("src", [RawRecord("1", "nowa", fetched_at=t3)], "src:cursor", "p3")
    with conn.cursor() as cur:
        cur.execute("SELECT body, changed_at FROM raw_records")
        assert cur.fetchall() == (("nowa", datetime(2026, 3, 1)),)
    assert store.get_state("src:cursor") == "p3"


def test_state(conn):
    db.migrate(conn, ("core", db.CORE_MIGRATIONS))
    store = db.MariaDbStore(conn)
    assert store.get_state("k") is None
    store.set_state("k", "1")
    store.set_state("k", "2")
    assert store.get_state("k") == "2"
    store.delete_state("k")
    assert store.get_state("k") is None


def test_lock_blocks_second_connection(conn):
    store = db.MariaDbStore(conn)
    other = db.MariaDbStore(db.connect(TEST_DSN))
    try:
        with store.lock("test:pobierz"), pytest.raises(db.LockedError):
            with other.lock("test:pobierz"):
                pass
        with other.lock("test:pobierz"):
            pass
    finally:
        other.conn.close()
