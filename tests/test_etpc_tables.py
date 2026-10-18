"""etpc/tables.py on the scratch MariaDB (conftest.conn): raw_records -> project tables."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from etpc.__main__ import PROJECT
from etpc.source import HudocSource, to_record
from judgments_core import db
from judgments_core.deriver import Deriver

FIXTURES = Path(__file__).parent / "fixtures" / "etpc"
ECLI = "ECLI:CE:ECHR:2026:1008JUD002271612"


def english():
    data = json.loads((FIXTURES / "results_pol_judgments_2.json").read_text(encoding="utf-8"))
    return data["results"][0]["columns"]


def polish():
    """The same ruling's Polish translation (made up from the English one)."""
    return {
        **english(),
        "itemid": "001-999999",
        "languageisocode": "POL",
        "doctype": "HJUDPOL",
        "docname": "CASE OF ANDRZEJ RUCIŃSKI v. POLAND - [Polish Translation]",
        "contentbody": "SPRAWA ANDRZEJ RUCIŃSKI przeciwko POLSCE",
        "appno": "22716/12;1/99",
        "updateddate": "2026-10-09T10:00:00.000Z",
    }


T0 = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)


def save(store, *columns, second=0):
    """Save as fetched at T0 + `second` - always before the derive pass."""
    records = [to_record(c) for c in columns]
    for record in records:
        record.fetched_at = T0 + timedelta(seconds=second)
    store.save_page(HudocSource.name, records, "hudoc:cursor", None)


def derive(store, **kwargs):
    return Deriver(store, HudocSource.name, PROJECT.derive, **kwargs).run()


def rows(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        result = cur.fetchall()
    conn.commit()
    return result


def setup(conn):
    db.migrate(conn, ("core", db.CORE_MIGRATIONS), ("etpc", PROJECT.migrations))
    return db.MariaDbStore(conn)


def test_language_versions_form_one_ruling_described_by_english(conn):
    store = setup(conn)
    save(store, polish())  # tłumaczenie przychodzi pierwsze
    derive(store)
    assert rows(conn, "SELECT title FROM rulings")[0][0].endswith("[Polish Translation]")

    save(store, english(), second=1)
    result = derive(store)
    assert (result.records, result.finished) == (1, True)  # tylko nowy rekord
    assert rows(conn, "SELECT group_key, kind, title, importance FROM rulings") == (
        (ECLI, "judgment", "CASE OF ANDRZEJ RUCIŃSKI v. POLAND", 4),
    )
    assert rows(
        conn, "SELECT itemid, language, is_translation FROM ruling_documents ORDER BY 1"
    ) == (
        ("001-252650", "ENG", 0),
        ("001-999999", "POL", 1),
    )
    # metadane z wersji angielskiej: bez dopisanej w tłumaczeniu skargi 1/99
    assert rows(conn, "SELECT appno FROM ruling_applications") == (("22716/12",),)
    assert rows(conn, "SELECT state FROM ruling_respondents") == (("POL",),)
    hits = rows(
        conn,
        "SELECT itemid FROM ruling_documents WHERE MATCH(body) AGAINST ('pecuniary')",
    )
    assert hits == (("001-252650",),)


def test_from_start_is_repeatable(conn):
    store = setup(conn)
    save(store, english(), polish())
    derive(store)
    before = rows(conn, "SELECT COUNT(*) FROM ruling_citations")
    assert derive(store).records == 0
    assert derive(store, from_start=True).records == 2
    assert rows(conn, "SELECT COUNT(*) FROM rulings") == ((1,),)
    assert rows(conn, "SELECT COUNT(*) FROM ruling_citations") == before


def test_document_moved_to_another_ecli_leaves_no_empty_ruling(conn):
    store = setup(conn)
    save(store, english())
    derive(store)
    save(store, {**english(), "ecli": "ECLI:CE:ECHR:2026:1008JUD000000001"}, second=1)
    derive(store, from_start=True)
    assert rows(conn, "SELECT group_key FROM rulings") == (("ECLI:CE:ECHR:2026:1008JUD000000001",),)


def test_records_of_the_current_second_wait_for_the_next_pass(conn):
    store = setup(conn)
    save(store, english())
    assert Deriver(store, HudocSource.name, PROJECT.derive, now=lambda: T0).run().records == 0
    assert derive(store).records == 1


def test_bad_and_empty_documents_become_problems(conn):
    store = setup(conn)
    broken = {**english(), "itemid": "001-1", "kpdate": "", "judgementdate": ""}
    empty = {**english(), "itemid": "001-2", "ecli": "", "contentbody": "  "}
    save(store, broken, empty, english())
    result = derive(store)
    assert (result.records, result.problems) == (3, 2)
    assert rows(conn, "SELECT COUNT(*) FROM ruling_documents") == ((2,),)
    assert rows(
        conn,
        "SELECT source_id, stage, problem FROM record_problems WHERE resolved_at IS NULL"
        " ORDER BY 1",
    ) == (("001-1", "przetworz", "blad_parsera"), ("001-2", "przetworz", "pusty_tekst"))

    # naprawiony dokument: problem zamknięty
    save(store, {**broken, "kpdate": "2026-10-08T00:00:00"}, second=1)
    derive(store)
    assert rows(conn, "SELECT source_id FROM record_problems WHERE resolved_at IS NULL") == (
        ("001-2",),
    )
