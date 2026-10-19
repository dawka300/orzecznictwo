"""NSA: JuDDGES rows -> raw records -> project tables (fixture: 5 real rows, texts cut)."""

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from conftest import TEST_DSN
from test_etpc_search import get

from judgments_core import db
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
    result = PROJECT.deriver(store).run()
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
    PROJECT.deriver(store, from_start=True).run()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM judgment_legal_bases b JOIN judgments j ON j.id = b.judgment_id WHERE j.cbosa_id = 'B51A16D504'"
        )
        assert cur.fetchone()[0] == bases
    conn.commit()


# --- chunker, wektory, wyszukiwanie, strony --------------------------------

from test_etpc_embed import FakeEmbedder, FakeVectors

from judgments_core.semantic import Passage, SemanticUnavailable
from judgments_core.settings import Settings
from nsa import search
from nsa.chunker import NsaChunker
from nsa.embed import NsaEmbedJob
from nsa.parser import judgment_body
from nsa.web import NsaWebApp


def test_body_and_chunks_follow_the_parts():
    j = parse_judgment(row("B51A16D504"))
    body = judgment_body(j.thesis, j.sentence, j.reasons, j.dissenting_opinion)
    assert body.startswith("TEZA\n") and "\n\nSENTENCJA\n" in body and "\n\nUZASADNIENIE\n" in body
    chunks = NsaChunker().chunk(1, body, "I OSK 1769/12 - Wyrok NSA", {"kind": "wyrok"})
    assert chunks[0].part == "teza" and "uzasadnienie" in {c.part for c in chunks}
    for c in chunks:
        assert body[c.start :].startswith(c.text.split("\n")[0])
        assert c.meta["context"].startswith("I OSK 1769/12 - Wyrok NSA | ")


def test_query_with_inflection():
    terms = search.parse_query('skarga kasacyjna "podatek od nieruchomości" -vat oraz')
    assert terms.boolean == '+skar* +kasacyj* +"podatek od nieruchomości" -vat'
    assert search.parse_query("skarga", inflect=False).boolean == "+skarga"
    pattern = search.highlight_pattern(terms.highlight)
    assert pattern.search("skargi kasacyjnej").group(0) == "skargi"
    with pytest.raises(search.QueryError):
        search.parse_query("a i w")


@pytest.fixture
def nsa_db(conn):
    db.migrate(conn, ("core", db.CORE_MIGRATIONS), ("nsa", PROJECT.migrations))
    store = db.MariaDbStore(conn)
    records = [to_record(r) for r in ROWS]
    for record in records:
        record.fetched_at = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
    store.save_page("juddges", records, "juddges:cursor", None)
    deriver = PROJECT.deriver(store)
    deriver.run()
    with conn.cursor() as cur:
        deriver.after(cur)
        cur.execute("SELECT id FROM judgments WHERE cbosa_id = 'B51A16D504'")
        conn.target = cur.fetchone()[0]
    conn.commit()
    return conn


def test_embed_job_order_and_payload(nsa_db):
    vectors = FakeVectors()
    result = NsaEmbedJob(nsa_db, FakeEmbedder(), vectors, batch_size=8).run()
    assert result.judgments == len(ROWS) - 1 and result.finished
    first = vectors.points[next(iter(vectors.points))][0]
    assert first.meta["kind"] == "uchwala"  # uchwały najpierw
    target = vectors.points[str(nsa_db.target)]
    assert target[0].meta["is_nsa"] is True and target[0].meta["symbols"]
    again = NsaEmbedJob(nsa_db, FakeEmbedder(), vectors).run()
    assert again.judgments == 0


def run_search(conn, semantic=None, **kwargs):
    with conn.cursor() as cur:
        return search.search(cur, search.SearchQuery(**kwargs), semantic)


class Down:
    def judgments(self, text, q, k=200):
        raise SemanticUnavailable("niedostępne")

    def similar(self, judgment_id, k=10):
        raise SemanticUnavailable("niedostępne")


class Up:
    def __init__(self, judgment_id):
        self.judgment_id = judgment_id

    def judgments(self, text, q, k=200):
        return [Passage(self.judgment_id, 0.9, "B51A16D504", "", 0, 200, "T | TEZA", "teza")]

    def similar(self, judgment_id, k=10):
        return [Passage(self.judgment_id, 0.9, "B51A16D504", "", 0, 10, "", "teza")]


def test_search_fulltext_filters_and_hybrid(nsa_db):
    reasons = parse_judgment(row("B51A16D504")).reasons
    word = next(w for w in reasons.split() if w.isalpha() and len(w) >= 9)
    assert run_search(nsa_db, text=word, mode="pelnotekstowe").total >= 1
    assert run_search(nsa_db, instance="nsa").total == sum(
        1 for r in ROWS if r["court_name"].startswith("Naczelny") and r != row("25F1838607")
    )
    assert run_search(nsa_db, kind="uchwala").total == 1
    hybrid = run_search(nsa_db, Up(nsa_db.target), text="zzzzz")
    assert (hybrid.mode, hybrid.total) == ("hybrydowe", 1)
    assert hybrid.hits[0].snippet.startswith("TEZA") and hybrid.hits[0].passage == "TEZA"
    assert (
        run_search(nsa_db, Up(nsa_db.target), text="zzzzz", instance="wsa").total == 0
    )  # filtr SQL
    down = run_search(nsa_db, Down(), text=word)
    assert down.notice == "niedostępne" and down.total >= 1


def test_pages(nsa_db):
    app = NsaWebApp(Settings(db_dsn=TEST_DSN), semantic=Up(nsa_db.target))
    status, html = get(app, "/", "q=zzzzz")
    assert status.startswith("200") and "I OSK 1769/12" in html
    status, html = get(app, f"/orzeczenie/{nsa_db.target}", "q=zzzzz&fragment=0-50")
    assert status.startswith("200") and "id=fragment" in html and "Podobne orzeczenia" in html
    assert "orzeczenia.nsa.gov.pl/doc/B51A16D504" in html
    assert get(app, "/statystyki")[0].startswith("200")
    assert get(app, "/orzeczenie/999999")[0].startswith("404")


def test_mcp_tools(nsa_db):
    from nsa.mcp import NsaMcp

    server = NsaMcp(Settings(db_dsn=TEST_DSN), semantic=Up(nsa_db.target))
    found = server.szukaj("zzzzz")
    assert found.startswith("Znaleziono 1 (tryb hybrydowe)")
    assert f"[id {nsa_db.target}] I OSK 1769/12" in found and "teza: TEZA" in found
    text = server.orzeczenie(nsa_db.target)
    assert (
        "Sygnatura: I OSK 1769/12" in text
        and "CBOSA: https://orzeczenia.nsa.gov.pl/doc/B51A16D504" in text
    )
    assert "\nTEZA\n" in text
    assert server.orzeczenie(nsa_db.target, od=5).startswith("I OSK 1769/12, tekst od znaku 5")
    assert server.orzeczenie(999999) == "Nie ma orzeczenia o id 999999."
    assert "I OSK 1769/12" in server.podobne(nsa_db.target)
    assert NsaMcp(Settings(db_dsn=TEST_DSN), semantic=Down()).podobne(1) == "niedostępne"


def test_mcp_text_in_parts():
    from judgments_core.mcp import cut, lines

    assert cut("abcdef", 0, 4) == "abcd\n\n[... ucięto: znaki 4-6; dalej: od=4]"
    assert cut("abcdef", 4, 4) == "ef"
    assert lines(("A", "x"), ("B", ""), ("C", None), ("D", 0)) == "A: x\nD: 0"
