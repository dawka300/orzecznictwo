"""etpc/ranks.py: citations resolved to rulings, ranking."""

from datetime import date

from etpc.ranks import citation_edges, pagerank

RULINGS = {
    1: ("judgment", date(2000, 1, 1)),  # wyrok co do istoty w sprawie 1/99
    2: ("judgment", date(2001, 1, 1)),  # słuszne zadośćuczynienie w tej samej sprawie
    3: ("decision", date(1999, 6, 1)),  # decyzja o dopuszczalności w tej sprawie
    4: ("judgment", date(2005, 1, 1)),  # cytuje 1/99 i 7/03
    5: ("decision", date(1999, 1, 1)),  # cytuje 1/99 przed wyrokiem
    6: ("decision", date(2004, 1, 1)),  # jedyne orzeczenie w sprawie 7/03
}
APPLICATIONS = [("1/99", 1), ("1/99", 2), ("1/99", 3), ("7/03", 6), ("9/05", 4)]


def test_citation_goes_to_the_earliest_judgment_known_at_the_time():
    citations = [(4, "1/99"), (4, "7/03"), (5, "1/99"), (4, "404/00"), (1, "1/99")]
    assert citation_edges(RULINGS, APPLICATIONS, citations) == {(4, 1), (4, 6), (1, 3)}


def test_pagerank_favours_rulings_cited_by_cited_rulings():
    scores = pagerank([1, 2, 3, 4], {(2, 1), (3, 1), (4, 2)})
    assert scores[1] > scores[2] > scores[4]
    assert abs(sum(scores.values()) - 4) < 1e-9
