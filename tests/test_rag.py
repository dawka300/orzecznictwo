"""Core retriever (judgments_core/rag.py) and evaluation, with a fake project child."""

import re
from dataclasses import dataclass

from judgments_core import evaluation
from judgments_core.rag import Ranking, Retriever, merge, pack, render, widen
from judgments_core.semantic import Passage, SemanticUnavailable

BODY_A = "\n".join(f"Paragraph {i} of ruling A about legal aid and cassation." for i in range(60))
BODY_B = (
    "\n".join(f"Line {i} of ruling B." for i in range(40)) + "\nThe cassation appeal was refused."
)


@dataclass
class Hit:
    id: int
    title: str


@dataclass
class Query:
    text: str
    respondent: str = ""
    mode: str = ""


class FakeSemantic:
    def __init__(self, passages=None, down=False):
        self.passages = passages or {}
        self.down = down
        self.calls = []

    def passages_of(self, text, judgment_ids, per_judgment=3):
        self.calls.append((text, list(judgment_ids), per_judgment))
        if self.down:
            raise SemanticUnavailable("Qdrant nie działa")
        return {i: self.passages[i][:per_judgment] for i in judgment_ids if i in self.passages}


class FakeRetriever(Retriever):
    query_class = Query

    def ranking(self, cur, query):
        return Ranking([Hit(1, "A v. Poland"), Hit(2, "B v. Poland")], re.compile("cassation"))

    def bodies(self, cur, wanted):
        self.wanted = wanted
        return {1: ("001-1", "ENG", BODY_A), 2: ("001-2", "ENG", BODY_B)}

    def describe(self, hit, document):
        return hit.title, "wyrok", f"https://example/{document}"

    def expected(self, cur, keys):
        known = {"1/01": {1}, "2/02": {2}}
        return {k: known.get(k, set()) for k in keys}


def passage(judgment_id, start, length, context="A | THE LAW > I."):
    return Passage(judgment_id, 0.9, "001-1", "ENG", start, length, context, "prawo")


def test_widen_moves_to_line_ends():
    text = "aaaa\nbbbb cccc\ndddd"
    assert widen(text, 7, 12, 0) == (5, 14)  # cały wiersz "bbbb cccc"
    words = "x" * 300 + " alpha beta gamma delta " + "y" * 300  # bez końców wiersza w pobliżu
    start, end = widen(words, 303, 318, 0)  # "pha beta gamma "
    assert words[start:end] == "beta gamma"
    assert widen(text, 0, 4, 100) == (0, len(text))


def test_merge_joins_overlapping_spans():
    @dataclass
    class Span:
        start: int
        end: int
        heading: str

    spans = merge([Span(50, 80, "b"), Span(0, 40, "a"), Span(35, 60, "c"), Span(200, 210, "d")])
    assert [(s.start, s.end, s.heading) for s in spans] == [(0, 80, "a"), (200, 210, "d")]


def test_pack_takes_best_passage_of_each_judgment_first():
    # A: 3 fragmenty, B: 1; limit mieści trzy fragmenty po 100
    assert pack([("A", [100, 100, 100]), ("B", [100])], 300) == [("A", [0, 1]), ("B", [0])]
    # pierwszy fragment zawsze, nawet ponad limit; za długie pomija, krótsze bierze dalej
    assert pack([("A", [500]), ("B", [400]), ("C", [50])], 100) == [("A", [0])]
    assert pack([("A", [80]), ("B", [400]), ("C", [20])], 100) == [("A", [0]), ("C", [0])]


def test_retrieve_uses_vector_passages_and_words_without_vectors():
    start = BODY_A.index("Paragraph 30")
    semantic = FakeSemantic({1: [passage(1, start, 120), passage(1, 0, 60)]})
    retriever = FakeRetriever(semantic)
    result = retriever.retrieve(None, Query("legal aid cassation"), budget_chars=5_000)

    assert semantic.calls == [("legal aid cassation", [1, 2], 3)]
    assert retriever.wanted == {1: "001-1", 2: ""}
    assert [s.judgment_id for s in result.sources] == [1, 2]
    a, b = result.sources
    assert [f.heading for f in a.fragments] == [
        "THE LAW > I.",
        "THE LAW > I.",
    ]  # w kolejności tekstu
    assert a.fragments[0].start == 0 and "Paragraph 30" in a.fragments[1].text
    assert a.fragments[1].text.startswith("Paragraph")  # od początku wiersza
    # B nie ma wektorów: okno przy pierwszym słowie zapytania
    assert "cassation appeal was refused" in b.fragments[0].text
    text = render(result)
    assert text.startswith("[1] A v. Poland (id 1)\nwyrok\nhttps://example/001-1")
    assert "--- THE LAW > I., znaki 0-" in text and "[2] B v. Poland (id 2)" in text


def test_retrieve_keeps_the_limit_and_survives_vectors_down():
    semantic = FakeSemantic(down=True)
    result = FakeRetriever(semantic).retrieve(None, Query("cassation"), budget_chars=100)
    assert result.notice == "Qdrant nie działa"
    assert len(result.sources) == 1  # pierwszy fragment zawsze, drugi się nie zmieścił
    assert result.ranked == [1, 2]


def test_evaluation_reports_places_and_context():
    questions = [
        evaluation.Question("cassation", ["2/02", "9/99"], {"respondent": "POL"}),
        evaluation.Question("legal aid", ["1/01"]),
    ]
    retriever = FakeRetriever(FakeSemantic())
    scores = evaluation.evaluate(retriever, None, questions, budget_chars=100)
    assert scores[0].places == {"2/02": 2} and scores[0].unknown == ["9/99"]
    assert scores[0].in_context == {"2/02": False}  # limit 100: tylko A
    assert scores[1].first == 1 and scores[1].in_context == {"1/01": True}
    report = "\n".join(evaluation.report(scores))
    assert "2/02 #2" in report and "1/01 #1 K" in report and "brak w bazie: 9/99" in report
    assert "MRR:                0.750" in report


def test_query_takes_the_project_mode_unless_given():
    class Semantic(FakeRetriever):
        mode = "znaczeniowe"

    retriever = Semantic(FakeSemantic())
    assert retriever.query("x", respondent="POL") == Query("x", "POL", "znaczeniowe")
    assert retriever.query("x", mode="").mode == "znaczeniowe"  # puste = domyślny projektu
    assert retriever.query("x", mode="hybrydowe").mode == "hybrydowe"
    assert FakeRetriever().query("x").mode == ""
