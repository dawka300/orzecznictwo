"""etpc/ranks.py: citations resolved to rulings, ranking."""

from datetime import date

from etpc.ranks import citation_edges, pagerank

RULINGS = {
    1: ("judgment", date(2000, 1, 1), "CHAMBER"),  # wyrok co do istoty w sprawie 1/99
    2: ("judgment", date(2001, 1, 1), "CHAMBER"),  # słuszne zadośćuczynienie w tej sprawie
    3: ("decision", date(1999, 6, 1), "ADMISSIBILITY"),  # decyzja o dopuszczalności
    4: ("judgment", date(2005, 1, 1), "CHAMBER"),  # cytuje 1/99 i 7/03
    5: ("decision", date(1999, 1, 1), "ADMISSIBILITY"),  # cytuje 1/99 przed wyrokiem
    6: ("decision", date(2004, 1, 1), "ADMISSIBILITY"),  # jedyne orzeczenie w sprawie 7/03
    7: ("judgment", date(2006, 1, 1), "CHAMBER"),  # wyrok Izby w sprawie 8/06
    8: ("judgment", date(2008, 1, 1), "GRANDCHAMBER"),  # Wielka Izba w sprawie 8/06
    9: ("judgment", date(2007, 1, 1), "CHAMBER"),  # cytuje 8/06 przed Wielką Izbą
    10: ("judgment", date(2010, 1, 1), "CHAMBER"),  # cytuje 8/06 po Wielkiej Izbie
}
APPLICATIONS = [
    ("1/99", 1),
    ("1/99", 2),
    ("1/99", 3),
    ("7/03", 6),
    ("9/05", 4),
    ("8/06", 7),
    ("8/06", 8),
]


def test_citation_goes_to_the_earliest_judgment_known_at_the_time():
    citations = [(4, "1/99"), (4, "7/03"), (5, "1/99"), (4, "404/00"), (1, "1/99")]
    assert citation_edges(RULINGS, APPLICATIONS, citations) == {(4, 1), (4, 6), (1, 3)}


def test_citation_goes_to_the_grand_chamber_once_it_has_ruled():
    citations = [(9, "8/06"), (10, "8/06")]
    assert citation_edges(RULINGS, APPLICATIONS, citations) == {(9, 7), (10, 8)}


def test_pagerank_favours_rulings_cited_by_cited_rulings():
    scores = pagerank([1, 2, 3, 4], {(2, 1), (3, 1), (4, 2)})
    assert scores[1] > scores[2] > scores[4]
    assert abs(sum(scores.values()) - 4) < 1e-9
