"""judgments_core/relevance.py: the judge's grades of the retrieved judgments."""

import pytest

from judgments_core.chat import ChatError
from judgments_core.evaluation import Question
from judgments_core.rag import Fragment, Reference, Retrieval
from judgments_core.relevance import judge, ndcg, parse, report


class FakeRetriever:
    def __init__(self, sources):
        self.sources = sources

    def expected(self, cur, keys):
        return {"8932/05": {2}} if "8932/05" in keys else {}

    def query(self, text, **filters):
        return text

    def retrieve(self, cur, query, budget_chars=None):
        return Retrieval(self.sources, [s.judgment_id for s in self.sources])


class FakeChat:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def complete(self, messages):
        self.prompts.append(messages[-1]["content"])
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def source(n, state="POL"):
    text = "x" * 2000
    return Reference(
        n, n, f"CASE {n}", f"wyrok; przeciwko {state}", "", "ENG", [Fragment(0, 10, "", text)]
    )


def test_parse_reads_the_judges_lines():
    reply = "[1] 3 - dokładnie o to\n**2** **1** – pokrewne\n3: 0 nie na temat\n[9] 3 - spoza zakresu\n[1] 0 - powtórka"
    assert parse(reply, 3) == {1: (3, "dokładnie o to"), 2: (1, "pokrewne"), 3: (0, "nie na temat")}


def test_ndcg_rewards_the_best_order():
    assert ndcg([3, 2, 0]) == pytest.approx(1.0)
    assert ndcg([0, 2, 3]) < 0.7
    assert ndcg([0, 0]) == 0.0


def test_judge_grades_the_top_results_and_marks_the_expected():
    retriever = FakeRetriever([source(1), source(2, "TUR"), source(3)])
    chat = FakeChat("[1] 1 - pokrewne\n[2] 3 - dokładnie\n[3] 0 - nie")
    judged = judge(retriever, None, chat, [Question("pytanie?", ["8932/05"])], top=2)
    j = judged[0]
    assert [(r.place, r.grade, r.expected) for r in j.results] == [(1, 1, False), (2, 3, True)]
    assert (j.mean, j.precision, j.first_exact) == (2.0, 0.5, 2)
    # Sędzia widzi pytanie, dane z państwem i skrócone fragmenty, bez trzeciego wyniku.
    prompt = chat.prompts[0]
    assert "Pytanie: pytanie?" in prompt and "przeciwko TUR" in prompt and "[3]" not in prompt
    assert prompt.count("x") < 2 * 1300
    lines = "\n".join(report(judged, "claude-test"))
    assert "*oczekiwane*" in lines and "średnia ich ocena 3.00" in lines


def test_judge_stops_at_the_spending_limit_and_keeps_the_grades():
    retriever = FakeRetriever([source(1)])
    chat = FakeChat("[1] 2 - tak", ChatError("osiągnięty limit wydatków"))
    questions = [Question("a", []), Question("b", []), Question("c", [])]
    judged = judge(retriever, None, chat, questions)
    assert [j.grades for j in judged] == [[2], []]
    assert "limit" in judged[1].error
