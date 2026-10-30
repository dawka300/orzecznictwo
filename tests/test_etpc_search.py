"""etpc/search.py, etpc/stats.py and etpc/web.py on the scratch MariaDB."""

from wsgiref.util import setup_testing_defaults

import pytest
from conftest import TEST_DSN
from test_etpc_tables import derive, english, polish, save, setup

from etpc import glossary, search
from etpc.ranks import refresh_ranks
from etpc.stats import read_stats, refresh_stats
from etpc.web import EtpcWebApp
from etpc.weights import refresh_weights
from judgments_core.semantic import Passage, SemanticUnavailable
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


def test_hyphenated_words_go_to_the_glossary_only_when_it_knows_them():
    assert search.parse_query("przymus adwokacko-radcowski").translations[0][0] == (
        "przymus adwokacko"
    )
    assert search.parse_query("kasacja pre-trial").boolean.endswith('+"pre trial"')


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
        refresh_weights(cur)
    conn.commit()
    return conn


def run(conn, **kwargs):
    with conn.cursor() as cur:
        return search.search(cur, search.SearchQuery(**kwargs))


def test_weight_is_on_hits_and_filters_them(ruling_db):
    hit = run(ruling_db, text="satisfaction").hits[0]  # Izba, ważność 4, bez cytowań
    assert (hit.tier, hit.reasons) == ("D", "bez wyróżnień")
    assert run(ruling_db, text="satisfaction", weight="C").total == 0
    assert run(ruling_db, text="satisfaction", weight="D").total == 1


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


class Down:
    """Semantic search with LM Studio/Qdrant switched off."""

    def judgments(self, text, q, k=200):
        raise SemanticUnavailable("niedostępne")

    def similar(self, ruling_id, k=10):
        raise SemanticUnavailable("niedostępne")


class Up:
    """Semantic search finding the only ruling at its "THE LAW" passage."""

    def __init__(self, conn):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ruling_id, itemid, LOCATE('THE LAW', body) - 1 FROM ruling_documents WHERE language = 'ENG'"
            )
            self.ruling_id, self.itemid, self.start = cur.fetchone()
        conn.commit()
        self.queries = []

    def judgments(self, text, q, k=200):
        self.queries.append((text, q.respondent))
        return [
            Passage(
                self.ruling_id, 0.8, self.itemid, "ENG", self.start, 300, "TITLE | THE LAW", "prawo"
            )
        ]

    def similar(self, ruling_id, k=10):
        return [Passage(self.ruling_id, 0.9, self.itemid, "ENG", 0, 10, "", "prawo")]


def test_hybrid_merges_meaning_and_words(ruling_db):
    up = Up(ruling_db)
    with ruling_db.cursor() as cur:
        result = search.search(cur, search.SearchQuery(text="satisfaction", respondent="POL"), up)
        assert (result.mode, result.total) == ("hybrydowe", 1)
        hit = result.hits[0]
        assert hit.passage == "THE LAW" and hit.snippet.startswith("THE LAW")
        # pierwsze miejsce na obu listach; słowa z wagą WORD_WEIGHT
        assert hit.score == pytest.approx((1 + search.WORD_WEIGHT) / 61)
        # słowo, którego nie ma, i tak daje wynik znaczeniowy
        assert search.search(cur, search.SearchQuery(text="zzzzz"), up).total == 1
        # filtr nieznany Qdrantowi odsiewa w SQL
        assert search.search(cur, search.SearchQuery(text="zzzzz", appno="1/99"), up).total == 0
        assert (
            search.search(cur, search.SearchQuery(text="zzzzz", mode="pelnotekstowe"), up).total
            == 0
        )
        down = search.search(cur, search.SearchQuery(text="satisfaction"), Down())
        assert (down.mode, down.total, down.notice) == ("pelnotekstowe", 1, "niedostępne")
    assert up.queries[0] == ("satisfaction", "POL")


def test_pages_with_meaning(ruling_db):
    up = Up(ruling_db)
    app = EtpcWebApp(Settings(db_dsn=TEST_DSN), semantic=up)
    status, html = get(app, "/", "q=zzzzz")
    assert status.startswith("200") and "najbliższych znaczeniowo" in html  # domyślnie znaczenie
    assert '<option value="" selected>znaczenie' in html
    _, hybrid = get(app, "/", "q=zzzzz&tryb=hybrydowe")
    assert "wg słów i znaczenia" in hybrid
    assert '<option value="hybrydowe" selected>' in hybrid
    link = html.split('href="/orzeczenie/')[1].split('"')[0].replace("&amp;", "&")
    assert f"fragment={up.start}-300" in link and link.endswith("#fragment")
    path, query = link.split("#")[0].split("?")
    status, html = get(app, f"/orzeczenie/{path}", query)
    assert "id=fragment" in html and "Podobne orzeczenia" in html


def test_pages(ruling_db):
    app = EtpcWebApp(Settings(db_dsn=TEST_DSN), semantic=Down())
    status, html = get(app, "/", "q=satisfaction")
    assert status.startswith("200") and "<mark>satisfaction</mark>" in html.lower()
    ruling_id = html.split('href="/orzeczenie/')[1].split("?")[0]
    status, html = get(app, f"/orzeczenie/{ruling_id}", "jezyk=POL")
    assert status.startswith("200") and "SPRAWA ANDRZEJ" in html
    assert "<html lang=pl>" in html
    # wersja angielska: strona po angielsku (przeglądarka proponuje tłumaczenie), interfejs nie
    html = get(app, f"/orzeczenie/{ruling_id}", "jezyk=ENG")[1]
    assert "<html lang=en>" in html and "<div class=text lang=en>" in html
    assert "<aside lang=pl translate=no>" in html
    assert "window.print()" in html and "hudoc.echr.coe.int/app/conversion/pdf/" in html
    assert "<span class='badge tier D' title='waga: bez wyróżnień'>D rutynowe</span>" in html
    assert get(app, "/", "q=satisfaction&waga=C")[1].count("class=title") == 0
    assert get(app, "/statystyki")[0].startswith("200")
    with ruling_db.cursor() as cur:  # tematy (python -m etpc tematy) w statystykach
        cur.execute(
            "INSERT INTO topics (level, topic, label, description, keywords, size, facts,"
            " representatives, model, built_at) VALUES ('B', 7, 'Długość aresztu', 'Opis',"
            " 'detention', 50, %s, %s, 'm', '2026-10-29')",
            ('{"articles": [["5-3", 0.9]], "respondents": [["POL", 0.25]]}', f'["{ruling_id}:2"]'),
        )
        cur.execute("SELECT title FROM rulings WHERE id = %s", (ruling_id,))
        title = cur.fetchone()[0]
    ruling_db.commit()
    html = get(app, "/statystyki")[1]
    assert "Tematy fragmentów ocen prawnych (THE LAW) (1)" in html
    assert "<summary>Długość aresztu</summary>" in html and "5-3 (90%)" in html
    assert f'<a href="/orzeczenie/{ruling_id}">{title}</a>' in html
    assert get(app, "/", "q=of")[1].count("class=error") == 1
    assert get(app, "/orzeczenie/999999")[0].startswith("404")
