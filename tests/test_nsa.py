"""NSA: JuDDGES rows -> raw records -> project tables (fixture: 5 real rows, texts cut)."""

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from judgments_core import db
from judgments_core.deriver import Deriver
from nsa.__main__ import PROJECT
from nsa.juddges import JuddgesSource, to_record
from nsa.parser import kind_of, parse_judgment

ROWS = json.loads(
    (Path(__file__).parent / "fixtures" / "nsa" / "juddges_rows.json").read_text(encoding="utf-8")
)


def row(cbosa_id):
    return next(r for r in ROWS if r["judgment_id"].endswith(cbosa_id))


def test_parse_nsa_judgment():
    j = parse_judgment(row("B51A16D504"))
    assert (j.cbosa_id, j.docket_number, j.kind, j.court) == (
        "B51A16D504",
        "I OSK 1769/12",
        "wyrok",
        "Naczelny Sąd Administracyjny",
    )
    assert isinstance(j.judgment_date, date) and j.is_final is True
    assert j.thesis and j.sentence and j.reasons
    assert j.text.startswith(j.thesis)
    assert any(p for _, p, _ in j.judges) and any(r for _, _, r in j.judges)
    assert j.symbols and all(code.isdigit() for code, _ in j.symbols)
    assert j.legal_bases[0].journal and j.related[0].cbosa_id


def test_parse_edge_cases():
    assert parse_judgment(row("21CBD4642A")).is_final is None
    assert parse_judgment(row("59F4C7289B")).kind == "uchwala"
    assert parse_judgment(row("428C626246")).docket_number == ""
    assert kind_of("Postanowienie NSA") == "postanowienie"
    assert kind_of("Orzeczenie WSA") == "inne"
    with pytest.raises(ValueError):
        parse_judgment({**row("B51A16D504"), "judgment_date": None})


def test_record_without_full_text():
    record = to_record({**row("B51A16D504"), "full_text": None})
    assert record.source_id == "B51A16D504"
    assert record.url == "https://orzeczenia.nsa.gov.pl/doc/B51A16D504"
    assert json.loads(record.body)["docket_number"] == "I OSK 1769/12"


def test_parquet_pages_resume(tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    columns = {k: [r.get(k) for r in ROWS] for k in ("judgment_id", "docket_number", "full_text")}
    pq.write_table(pa.table(columns), tmp_path / "data_0.parquet")
    source = JuddgesSource(data_dir=tmp_path, files=["data_0.parquet"])
    pages = list(source.iter_pages(None))
    assert [len(p.records) for p in pages] == [len(ROWS), 0]
    assert json.loads(pages[-1].cursor) == {"file": 1, "row": 0}
    assert "full_text" not in json.loads(pages[0].records[0].body)
    again = list(source.iter_pages(json.dumps({"file": 0, "row": 3})))
    assert [r.source_id for r in again[0].records] == [r.source_id for r in pages[0].records[3:]]


def test_derive_tables(conn):
    db.migrate(conn, ("core", db.CORE_MIGRATIONS), ("nsa", PROJECT.migrations))
    store = db.MariaDbStore(conn)
    records = [to_record(r) for r in ROWS]
    for record in records:  # przetworz bierze tylko rekordy sprzed bieżącej sekundy
        record.fetched_at = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
    store.save_page("juddges", records, "juddges:cursor", None)
    result = Deriver(store, "juddges", PROJECT.derive).run()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM judgments")
        saved = cur.fetchone()[0]
        cur.execute(
            "SELECT problem, COUNT(*) FROM record_problems WHERE resolved_at IS NULL GROUP BY problem"
        )
        problems = dict(cur.fetchall())
        cur.execute(
            "SELECT j.docket_number, COUNT(DISTINCT b.position), COUNT(DISTINCT r.position)"
            " FROM judgments j JOIN judgment_legal_bases b ON b.judgment_id = j.id"
            " JOIN judgment_related r ON r.judgment_id = j.id WHERE j.cbosa_id = 'B51A16D504'"
        )
        docket, bases, related = cur.fetchone()
    conn.commit()
    assert result.records == len(ROWS)
    assert saved == len(ROWS) - 1  # bez tekstu - pominięte
    assert problems == {"brak_sygnatury": 1, "pusty_tekst": 1}
    assert docket == "I OSK 1769/12" and bases >= 1 and related >= 1
    # drugi przebieg od początku nie dubluje tabel pobocznych
    Deriver(store, "juddges", PROJECT.derive, from_start=True).run()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM judgment_legal_bases b JOIN judgments j ON j.id = b.judgment_id WHERE j.cbosa_id = 'B51A16D504'"
        )
        assert cur.fetchone()[0] == bases
    conn.commit()
