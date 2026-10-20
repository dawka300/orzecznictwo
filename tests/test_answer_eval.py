"""Automatic checks of RAG answers (judgments_core/answer_eval.py)."""

import json

import pytest

from etpc.pipeline import OUTCOMES as ETPC_OUTCOMES
from judgments_core import answer_eval
from judgments_core.chat import ChatModel
from judgments_core.rag import Fragment, Reference, Retrieval
from nsa.pipeline import OUTCOMES as NSA_OUTCOMES

GUZ = "Violation of Article 10 - Freedom of expression-{general} (Article 10-1 - Freedom of expression)"
KURKOWSKI = "No violation of Article 3 - Prohibition of torture"
MIXED = "No violation of Art. 3; Violation of Art. 5-3; Violation of Art. 6-1"


def source(number, result, title="X v. Poland"):
    fragment = Fragment(0, 9, "THE LAW", "Some text")
    return Reference(
        number, number, title, "wyrok", f"https://x/{number}", "ENG", [fragment], result
    )


def retrieval(*results):
    return Retrieval([source(n, r) for n, r in enumerate(results, 1)], [])


def labels(sentence, outcomes=ETPC_OUTCOMES):
    return answer_eval.claimed_outcomes(sentence, outcomes)


def test_claimed_outcomes_in_polish():
    assert labels("ETPC stwierdził naruszenie art. 10 Konwencji [1].") == {"naruszenie"}
    assert labels("Trybunał nie stwierdził naruszenia art. 3 [2].") == {"brak naruszenia"}
    assert labels("Nie doszło do naruszenia art. 6 [1].") == {"brak naruszenia"}
    assert labels("Stwierdzono brak naruszenia art. 3 [1].") == {"brak naruszenia"}
    assert labels("Sankcje nie naruszały art. 10 [1].") == {"brak naruszenia"}
    assert labels("Doszło do naruszenia art. 5, ale brak naruszenia art. 3 [1].") == {
        "naruszenie",
        "brak naruszenia",
    }
    assert labels("Skarżący zarzucał naruszenie art. 6 [1].") == set()  # zarzut to nie wynik


def test_outcome_rule_catches_the_guz_mistake():
    answer = (
        "W sprawie Danileţ ETPC stwierdził naruszenie art. 10 [1]. "
        "W sprawie Guz v. Poland nie doszło do naruszenia art. 10 [2]. "
        "Kurkowski: brak naruszenia art. 3 [3]. "
        "W Kudle stwierdzono naruszenie art. 5 i brak naruszenia art. 3 [4]. "
        "Oba wyroki stwierdziły naruszenie [1][2]."
    )
    found = answer_eval.outcome_contradictions(
        answer, retrieval(GUZ, GUZ, KURKOWSKI, MIXED), ETPC_OUTCOMES
    )
    assert [(n, claimed) for n, _, claimed, _ in found] == [(2, "brak naruszenia")]


def test_outcome_rule_for_nsa():
    answer = "NSA oddalił skargę kasacyjną [1]. Sąd uchylił decyzję [2]. Podjęto uchwałę [3]."
    found = answer_eval.outcome_contradictions(
        answer,
        retrieval("Oddalono skargę kasacyjną", "Oddalono skargę", "Podjęto uchwałę"),
        NSA_OUTCOMES,
    )
    assert [(n, claimed) for n, _, claimed, _ in found] == [(2, "uchylenie")]


class Judge(ChatModel):
    name = "judge"

    def __init__(self, reply):
        super().__init__("sedzia")
        self.reply = reply
        self.messages = None

    def stream(self, messages):
        self.messages = messages
        yield self.reply


def test_judge_gets_only_cited_sources_and_its_list_is_parsed():
    ratings = [
        {"zdanie": "Naruszenie art. 10", "zrodlo": 2, "ocena": "poparte"},
        {"zdanie": "W mediach społecznościowych", "zrodlo": 2, "ocena": "niepoparte"},
        {"zdanie": "x", "zrodlo": 2, "ocena": "???"},
    ]
    judge = Judge("<think>hmm</think>Oto ocena:\n" + json.dumps(ratings, ensure_ascii=False))
    got = answer_eval.judge(judge, retrieval(GUZ, GUZ), "Naruszenie art. 10 [2].")
    assert [r["ocena"] for r in got] == ["poparte", "niepoparte"]
    user = judge.messages[1]["content"]
    assert "[2] X v. Poland" in user and "[1] X v. Poland" not in user
    with pytest.raises(ValueError, match="ocen w JSON"):
        answer_eval.judge(Judge("nie wiem"), retrieval(GUZ), "Tak [1].")


class Pipeline:
    outcomes = ETPC_OUTCOMES

    def retrieve(self, cur, question, budget_chars=None, **filters):
        return retrieval(GUZ)

    def answer(self, question, retrieval):
        from judgments_core.pipeline import Answer

        text = "Nie doszło do naruszenia art. 10 [1]. Zob. też [3]."
        return Answer(text, retrieval, [1], [3])


def test_check_and_report(tmp_path):
    judge = Judge(json.dumps([{"zdanie": "Nie doszło", "zrodlo": 1, "ocena": "sprzeczne"}]))
    result = answer_eval.check(Pipeline(), None, "Czy Guz wygrał?", judge_model=judge)
    assert result.unknown == [3] and result.contradictions[0][2] == "brak naruszenia"
    assert result.count("sprzeczne") == 1
    text = "\n".join(answer_eval.report([result], "qwen3-14b"))
    assert "NIEISTNIEJĄCE [3]" in text and "twierdzi „brak naruszenia”" in text
    assert "możliwe sprzeczności wyniku (reguła): 1" in text and "sprzeczne 1 (100%)" in text
    path = tmp_path / "o.json"
    answer_eval.save([result], path, "qwen3-14b")
    assert json.loads(path.read_text(encoding="utf-8"))["odpowiedzi"][0]["unknown"] == [3]


def test_sentences_ignore_abbreviations():
    text = (
        "W sprawie Guz v. Poland stwierdzono naruszenie art. 10 ust. 1 [2]. Natomiast R.R. v."
        " Poland dotyczy art. 8 [3]!\nNowy wiersz bez kropki"
    )
    assert answer_eval.sentences(text) == [
        "W sprawie Guz v. Poland stwierdzono naruszenie art. 10 ust. 1 [2].",
        "Natomiast R.R. v. Poland dotyczy art. 8 [3]!",
        "Nowy wiersz bez kropki",
    ]


class Broken(Pipeline):
    def answer(self, question, retrieval):
        from judgments_core.chat import ChatError

        raise ChatError("serwer modelu: HTTP 400: No models loaded")


def test_model_error_is_recorded_not_raised():
    result = answer_eval.check(Broken(), None, "Pytanie?")
    assert result.error.endswith("No models loaded") and result.answer == ""
    text = "\n".join(answer_eval.report([result], "?"))
    assert "BRAK ODPOWIEDZI" in text and "bez odpowiedzi: 1" in text


def test_cut_off_judge_list_keeps_complete_ratings():
    reply = '[\n {"zdanie": "Naruszenie", "zrodlo": 1, "ocena": "poparte", "dlaczego": "tak"},\n {"zdanie": "Prze'
    got = answer_eval.judge(Judge(reply), retrieval(GUZ), "Naruszenie art. 10 [1].")
    assert [r["ocena"] for r in got] == ["poparte"]
