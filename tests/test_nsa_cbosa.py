"""NSA: CBOSA judgment pages (/doc/<ID>) -> raw records -> the same tables as JuDDGES.

Fixtures: 3 real pages fetched 2026-10-22 and the JuDDGES rows of the same
judgments (dissenting opinion + related, uchwała with theses and glosa,
wyrok without reasons).
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from test_nsa import ROWS

from judgments_core import db
from judgments_core.http import SourceError
from nsa.__main__ import PROJECT
from nsa.cbosa import ids_in, is_judgment_page, page_to_row
from nsa.importer import NsaImporter
from nsa.juddges import to_record
from nsa.parser import parse_judgment
from nsa.source import CbosaSource

FIXTURES = Path(__file__).parent / "fixtures" / "nsa" / "cbosa"
JUDDGES = json.loads((FIXTURES / "juddges.json").read_text(encoding="utf-8"))


def page(cbosa_id):
    return (FIXTURES / f"{cbosa_id}.html").read_text(encoding="utf-8")


def test_page_gives_the_same_judgment_as_juddges():
    for cbosa_id, row in JUDDGES.items():
        assert parse_judgment(page_to_row(page(cbosa_id), cbosa_id)) == parse_judgment(row)


def test_page_fields():
    row = page_to_row(page("2FEE977824"), "2FEE977824")
    assert (row["docket_number"], row["judgment_type"]) == ("I OSK 1228/15", "Wyrok NSA")
    assert row["presiding_judge"] == row["judge_rapporteur"] == "Irena Kamińska"
    assert row["judges"][1] == "Joanna Runge - Lissowska"  # bez "/zdanie odrebne/"
    assert row["related_docket_numbers"][0]["judgment_id"] == "/doc/4E9F0B9829"
    assert row["dissenting_opinion"].startswith("Zdanie odrębne s. NSA")
    uchwala = page_to_row(page("59F4C7289B"), "59F4C7289B")
    assert uchwala["glosa_information"] == ["Radwański Zbigniew Nowe Prawo 1984 nr 6 str. 132"]
    assert len(uchwala["extracted_legal_bases"]) == 2 and uchwala["thesis"]
    assert page_to_row(page("74BB75927B"), "74BB75927B").get("reasons_for_judgment") is None


def test_ids_and_empty_pages():
    assert ids_in('<a href="/doc/846b7c2709">x</a> <a href="/doc/846B7C2709">') == ["846B7C2709"]
    assert ids_in("846B7C2709\n2FEE977824, 2FEE977824\nnie-id\n") == ["846B7C2709", "2FEE977824"]
    assert not is_judgment_page("") and is_judgment_page(page("74BB75927B"))


class FakeClient:
    """Answers from a dict url-id -> list of bodies (one per call) or an exception."""

    pause_s = 1.0

    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def get_text(self, url):
        cbosa_id = url.rsplit("/", 1)[-1]
        self.asked.append(cbosa_id)
        answer = self.answers[cbosa_id]
        if isinstance(answer, Exception):
            raise answer
        return answer.pop(0) if len(answer) > 1 else answer[0]


def test_source_reads_id_files_skips_fetched_and_retries_empty(tmp_path):
    (tmp_path / "2025-03.txt").write_text("2FEE977824\n59F4C7289B\n", encoding="utf-8")
    (tmp_path / "postman.html").write_text(
        '<a href="/doc/74BB75927B">x</a><a href="/doc/2FEE977824">', encoding="utf-8"
    )
    client = FakeClient(
        {
            "59F4C7289B": ["", page("59F4C7289B")],  # pusta odpowiedź, potem strona
            "74BB75927B": SourceError("HTTP 404"),
        }
    )
    source = CbosaSource(client=client, id_dir=tmp_path)
    assert client.pause_s == 3.0  # najkrótsza przerwa dla CBOSA
    source.fetched = lambda ids: {"2FEE977824"}
    pages = list(source.iter_pages(None))
    assert client.asked == ["59F4C7289B", "59F4C7289B", "74BB75927B"]
    assert [[r.source_id for r in p.records] for p in pages] == [["59F4C7289B"], []]
    record = pages[0].records[0]
    assert record.content_type == "text/html"
    assert record.url == "https://orzeczenia.nsa.gov.pl/doc/59F4C7289B"
    assert all(p.cursor is None for p in pages)


def test_fetch_and_derive_cbosa_over_juddges(conn):
    """pobierz (CBOSA) then przetworz: the CBOSA page replaces the JuDDGES judgment."""
    db.migrate(conn, ("core", db.CORE_MIGRATIONS), ("nsa", PROJECT.migrations))
    store = db.MariaDbStore(conn)
    old = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
    records = [to_record(r) for r in ROWS]
    for record in records:
        record.fetched_at = old
    store.save_page("juddges", records, "juddges:cursor", None)

    class Source(CbosaSource):
        def queued_ids(self):
            return ["59F4C7289B", "74BB75927B"]

        def fetch(self, cbosa_id):
            record = super().fetch(cbosa_id)
            record.fetched_at = old
            return record

    client = FakeClient({i: [page(i)] for i in ("59F4C7289B", "74BB75927B")})
    first = NsaImporter(Source(client=client), store).run()
    again = NsaImporter(Source(client=client), store).run()
    assert (first.saved, again.saved) == (2, 0)  # drugi raz: już pobrane

    result = PROJECT.deriver(store).run()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT cbosa_id, source FROM judgments WHERE cbosa_id IN %s",
            (("59F4C7289B", "74BB75927B"),),
        )
        sources = dict(cur.fetchall())
        cur.execute(
            "SELECT COUNT(*) FROM judgment_legal_bases b JOIN judgments j ON j.id = b.judgment_id"
            " WHERE j.cbosa_id = '59F4C7289B'"
        )
        bases = cur.fetchone()[0]
    conn.commit()
    assert result.records == len(ROWS) + 2 and result.finished
    assert sources == {"59F4C7289B": "cbosa", "74BB75927B": "cbosa"}
    assert bases == 2


# --- wektory poziomu 1: jeden skrót na orzeczenie ------------------------------

from nsa.chunker import SUMMARY_CHARS, NsaChunker
from nsa.embed import full_chunks
from nsa.parser import judgment_body


def test_summary_chunk_points_to_the_reasoning():
    j = parse_judgment(page_to_row(page("2FEE977824"), "2FEE977824"))
    parts = (j.thesis, j.sentence, j.reasons, j.dissenting_opinion)
    body = judgment_body(*parts)
    [chunk] = NsaChunker().summary(7, body, parts, "I OSK 1228/15 - Wyrok NSA", {"kind": "wyrok"})
    shown = body[chunk.start : chunk.start + chunk.text_length]
    assert chunk.part == "uzasadnienie" and shown.startswith("Wskazać należy na wstępie")
    assert chunk.text.endswith(shown) and len(chunk.text) <= SUMMARY_CHARS + 2
    assert "oddala skargę kasacyjną" in chunk.text  # koniec sentencji = rozstrzygnięcie
    assert chunk.meta["context"].endswith("| skrót") and chunk.meta["kind"] == "wyrok"
    # bez uzasadnienia: sentencja
    parts = (None, j.sentence, None, None)
    [only] = NsaChunker().summary(7, judgment_body(*parts), parts, "T", {})
    assert only.part == "sentencja" and only.start > 0
    assert only.text.endswith(
        "oddala skargę kasacyjną; 2. zasądza od P. na rzecz Polskiego Związku"
        " Łowieckiego kwotę 180 (sto osiemdziesiąt) złotych tytułem zwrotu"
        " kosztów postępowania kasacyjnego."
    )  # koniec = rozstrzygnięcie


def test_full_chunks_only_for_key_judgments():
    nsa = "Naczelny Sąd Administracyjny"
    assert full_chunks("uchwala", "Sąd Najwyższy", None, False)
    assert full_chunks("wyrok", nsa, None, False)
    assert full_chunks("postanowienie", "WSA", "teza", False)
    assert full_chunks("postanowienie", "WSA", None, True)
    assert not full_chunks("postanowienie", nsa, None, False)
    assert not full_chunks("wyrok", "Wojewódzki Sąd Administracyjny w Gliwicach", None, False)
