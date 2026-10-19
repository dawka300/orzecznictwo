"""etpc/search.py, etpc/stats.py and etpc/web.py on the scratch MariaDB."""

from wsgiref.util import setup_testing_defaults

import pytest
from conftest import TEST_DSN
from test_etpc_tables import derive, english, polish, save, setup

from etpc import glossary, search
from etpc.ranks import refresh_ranks
from etpc.stats import read_stats, refresh_stats
from etpc.web import make_app
from judgments_core.settings import Settings


def test_query_words_are_all_required():
    terms = search.parse_query('pre-trial detention "right to life" -turkey detent* of')
    assert terms.boolean == '+"pre trial" +detention +"right to life" -turkey +detent*'
    assert terms.highlight == [
        ("pre trial", False),
        ("detention", False),
        ("right to life", False),
        ("detent", True),
    ]
    assert terms.locate[:2] == ["pre trial", "right to life"]  # najpierw całe frazy
    assert "detention" in terms.locate
    assert terms.translations == []


def test_polish_phrases_become_groups_of_english_and_french():
    terms = search.parse_query("naruszenie prawa do sądu w Polsce postępowania")
    assert terms.boolean == (
        '+(violation) +("right to a court" "access to a court" "droit à un tribunal"'
        ' "accès à un tribunal") +(Poland Pologne) postępowania'
    )  # "w" pominięte, nieznane polskie słowo nieobowiązkowe
    assert [polish for polish, _ in terms.translations] == ["naruszenie", "prawa do sądu", "Polsce"]
    assert search.parse_query("tymczasowego aresztowania").translations[0][1][0] == (
        "pre-trial detention"
    )


def test_translation_can_be_switched_off():
    assert search.parse_query("prawo do sądu", translate=False).boolean == "+prawo +sądu"


def test_glossary_matches_inflected_forms_only():
    assert glossary.translate(["Przewlekłości", "postępowania"])[0][1][0] == "length of proceedings"
    assert glossary.translate(["sadzonki"])[0][1] is None  # sad* + za długa końcówka


def test_query_without_searchable_words_is_an_error():
    for text in ("the of to", "-turkey", "ab", '"of the"'):
        with pytest.raises(search.QueryError):
            search.parse_query(text)


def test_highlight_matches_words_phrases_and_prefixes():
    pattern = search.highlight_pattern([("pre trial", False), ("detent", True), ("law", False)])
    text = "Pre-trial detentions and lawful law"
    assert [m.group(0) for m in pattern.finditer(text)] == ["Pre-trial", "detentions", "law"]


@pytest.fixture
def ruling_db(conn):
    store = setup(conn)
    save(store, english(), polish())
    derive(store)
    with conn.cursor() as cur:
        refresh_stats(cur)
        refresh_ranks(cur)
    conn.commit()
    return conn


def run(conn, **kwargs):
    with conn.cursor() as cur:
        return search.search(cur, search.SearchQuery(**kwargs))


def test_fulltext_search_with_filters(ruling_db):
    result = run(ruling_db, text="satisfaction")
    assert result.total == 1
    hit = result.hits[0]
    assert hit.states == ["POL"] and hit.kind == "judgment"
    assert hit.snippet_language == "ENG" and "satisfaction" in hit.snippet.lower()

    assert run(ruling_db, text="przeciwko", language="POL").hits[0].snippet_language == "POL"
    assert run(ruling_db, text="przeciwko", language="ENG").total == 0
    assert run(ruling_db, text="satisfaction", respondent="TUR").total == 0
    assert run(ruling_db, text="satisfaction -poland").total == 0
    assert run(ruling_db, text="rucinski", field="tytul").total == 1
    assert run(ruling_db, text="słuszne zadośćuczynienie").total == 1  # just satisfaction
    assert run(ruling_db, text="satisfaction", min_cited=1).total == 0
    assert run(ruling_db, text="satisfaction", sort="cytowane").hits[0].cited_by == 0


def test_listing_without_text_filters_rulings(ruling_db):
    assert run(ruling_db).total == 1
    assert run(ruling_db, appno="22716/12").total == 1
    assert run(ruling_db, appno="1/99").total == 0  # tylko w tłumaczeniu, nie w orzeczeniu
    assert run(ruling_db, year_from=2027).total == 0
    assert run(ruling_db, kind="decision").total == 0


def test_stats_count_rulings_and_documents(ruling_db):
    with ruling_db.cursor() as cur:
        stats = read_stats(cur)
    assert stats["total"][0][:5] == ("all", 1, 1, 0, 2)
    assert {row[0]: row[4] for row in stats["language"]} == {"ENG": 1, "POL": 1}
    assert stats["respondent"][0][:2] == ("POL", 1)


def get(app, path, query=""):
    environ = {"PATH_INFO": path, "QUERY_STRING": query}
    setup_testing_defaults(environ)
    status = []
    body = b"".join(app(environ, lambda s, headers: status.append(s)))
    return status[0], body.decode("utf-8")


def test_pages(ruling_db):
    app = make_app(Settings(db_dsn=TEST_DSN))
    status, html = get(app, "/", "q=satisfaction")
    assert status.startswith("200") and "<mark>satisfaction</mark>" in html.lower()
    ruling_id = html.split('href="/orzeczenie/')[1].split("?")[0]
    status, html = get(app, f"/orzeczenie/{ruling_id}", "jezyk=POL")
    assert status.startswith("200") and "SPRAWA ANDRZEJ" in html
    assert get(app, "/statystyki")[0].startswith("200")
    assert get(app, "/", "q=of")[1].count("class=error") == 1
    assert get(app, "/orzeczenie/999999")[0].startswith("404")
