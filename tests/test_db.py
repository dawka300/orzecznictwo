"""db.py: pure helpers always; MariaDB parts on a scratch database (conftest.conn)."""

from datetime import datetime, timezone

import pytest
from conftest import TEST_DSN

from judgments_core import db
from judgments_core.source import RawRecord


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
    assert names == ["core/001_core.sql", "core/002_logs.sql", "p/001_a.sql", "p/002_b.sql"]


def test_migrate_once(conn, tmp_path):
    (tmp_path / "001_x.sql").write_text("CREATE TABLE x (id INT);\nINSERT INTO x VALUES (1);")
    assert db.migrate(conn, ("core", db.CORE_MIGRATIONS), ("p", tmp_path)) == [
        "core/001_core.sql",
        "core/002_logs.sql",
        "p/001_x.sql",
    ]
    assert db.migrate(conn, ("core", db.CORE_MIGRATIONS), ("p", tmp_path)) == []


def test_save_page_upserts_and_tracks_changes(conn):
    db.migrate(conn, ("core", db.CORE_MIGRATIONS))
    store = db.MariaDbStore(conn)
    t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 2, 1, tzinfo=timezone.utc)
    t3 = datetime(2026, 3, 1, tzinfo=timezone.utc)

    stats = store.save_page("src", [RawRecord("1", "ąę treść", fetched_at=t1)], "src:cursor", "p1")
    assert (stats.new, stats.changed) == (1, 0)
    stats = store.save_page("src", [RawRecord("1", "ąę treść", fetched_at=t2)], "src:cursor", "p2")
    assert (stats.new, stats.changed) == (0, 0)
    with conn.cursor() as cur:
        cur.execute("SELECT body, fetched_at, changed_at FROM raw_records")
        assert cur.fetchall() == (("ąę treść", datetime(2026, 2, 1), datetime(2026, 1, 1)),)

    stats = store.save_page("src", [RawRecord("1", "nowa", fetched_at=t3)], "src:cursor", "p3")
    assert (stats.new, stats.changed) == (0, 1)
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
        lock, other_lock = store.lock("test:pobierz"), other.lock("test:pobierz")
        with lock, pytest.raises(db.LockedError), other_lock:
            pass
        with other.lock("test:pobierz"):
            pass
    finally:
        other.conn.close()


def test_run_log_events_and_problems(conn):
    db.migrate(conn, ("core", db.CORE_MIGRATIONS))
    store = db.MariaDbStore(conn)
    run_id = store.start_run("pobierz", "k0")
    store.add_event(run_id, "cloudflare", "Cloudflare challenge", "https://x.test/q", 30)
    store.add_problems(
        "src", "pobierz", [("1", "brak_w_bazie", "rok 1998"), ("2", "brak_w_bazie", "")]
    )
    store.add_problems("src", "pobierz", [("1", "brak_w_bazie", "rok 1998")])
    assert store.open_problems("src", "pobierz", "brak_w_bazie") == ["1", "2"]

    # zapisany rekord zamyka swój problem "pobierz"
    store.save_page("src", [RawRecord("1", "treść")], "src:cursor", None)
    assert store.open_problems("src", "pobierz", "brak_w_bazie") == ["2"]
    store.finish_run(run_id, "ok", pages=1, records=1, records_new=1, cursor_end="k1")

    with conn.cursor() as cur:
        cur.execute("SELECT command, status, records_new, events, cursor_end FROM fetch_runs")
        assert cur.fetchall() == (("pobierz", "ok", 1, 1, "k1"),)
        cur.execute("SELECT source_id, times, resolved_at IS NULL FROM record_problems ORDER BY 1")
        assert cur.fetchall() == (("1", 2, 0), ("2", 1, 1))
    conn.commit()

    # problem wraca po rozwiązaniu: licznik od nowa
    store.add_problems("src", "pobierz", [("1", "brak_w_bazie", "znowu")])
    with conn.cursor() as cur:
        cur.execute("SELECT times, details FROM record_problems WHERE source_id = '1'")
        assert cur.fetchall() == ((1, "znowu"),)
    conn.commit()
