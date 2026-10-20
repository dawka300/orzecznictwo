"""Parent of the project RAG pipelines: question -> passages -> one model call -> answer.

For local models (LM Studio ...): the retriever picks and packs the passages,
the model only writes. The prompt (Polish) tells it to answer from the
sources alone, to mark every claim with [n] and to say so when the sources
are not enough. After the answer the program checks the [n] numbers: a
number with no source is reported, so an invented citation shows.

A project's child gives the model what the sources are (domain): ETPC =
English/French rulings summarised in Polish, NSA = Polish administrative
court judgments.
"""

from __future__ import annotations

import re
from abc import ABC
from dataclasses import dataclass, field

from judgments_core.chat import make_chat, without_thinking
from judgments_core.rag import render

RULES = """\
Zasady:
- Odpowiadaj po polsku, wyłącznie na podstawie źródeł podanych w wiadomości.
- Każde twierdzenie oznacz numerem źródła w nawiasie kwadratowym, np. [1] albo [2][3].
- Jeśli źródła nie wystarczają, napisz to wprost; nie uzupełniaj odpowiedzi z pamięci.
- Nie wymyślaj nazw spraw, sygnatur, numerów skarg, dat ani cytatów.
- O wyniku sprawy (naruszenie, brak naruszenia, oddalenie, uchylenie, odrzucenie) pisz
  wyłącznie na podstawie pola WYNIK w opisie źródła.
- Nazwy spraw, państw, sądów i organów przepisuj dokładnie tak, jak są w źródle.
- Najpierw krótka odpowiedź wprost (2-4 zdania), potem uzasadnienie ze źródłami."""

_CITATION = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")


@dataclass
class Answer:
    text: str
    retrieval: object
    cited: list = field(default_factory=list)  # numery źródeł użyte w odpowiedzi
    unknown: list = field(default_factory=list)  # numery bez źródła (zmyślone)


class RagPipeline(ABC):
    domain = ""  # dla modelu: czym są źródła i jak je cytować
    # Reguła sprawdzania odpowiedzi (judgments_core/answer_eval.py): [(etykieta, regex
    # słów w odpowiedzi, regex w wyniku z metadanych)]; wcześniejsza wygrywa z późniejszą.
    outcomes = ()

    def __init__(self, retriever, chat, suffix=""):
        self.retriever = retriever
        self.chat = chat
        self.suffix = suffix  # dopisek na końcu pytania, np. /no_think (Qwen3)

    @classmethod
    def from_settings(cls, settings, project, retriever_class):
        return cls(
            retriever_class.from_settings(settings, project),
            make_chat(settings),
            settings.chat_suffix,
        )

    def system_prompt(self):
        return f"{self.domain}\n\n{RULES}"

    def messages(self, question, retrieval):
        # Bez id z bazy: model lokalny przepisuje je do odpowiedzi.
        sources = render(retrieval, empty="(brak źródeł)", ids=False)
        user = f"Źródła:\n\n{sources}\n\nPytanie: {question}"
        if self.suffix:
            user += f"\n\n{self.suffix}"
        return [
            {"role": "system", "content": self.system_prompt()},
            {"role": "user", "content": user},
        ]

    def retrieve(self, cur, question, budget_chars=None, **filters):
        """The passages for question (filters as in the project's search)."""
        return self.retriever.retrieve(cur, self.retriever.query(question, **filters), budget_chars)

    def stream(self, question, retrieval):
        """Pieces of the answer (without the model's <think> block)."""
        return without_thinking(self.chat.stream(self.messages(question, retrieval)))

    def answer(self, question, retrieval, pieces=None):
        """The whole answer with its citations checked; pieces = an already read stream."""
        text = "".join(pieces if pieces is not None else self.stream(question, retrieval)).strip()
        cited, unknown = check_citations(text, len(retrieval.sources))
        return Answer(text, retrieval, cited, unknown)


def check_citations(text, count):
    """([cited numbers], [numbers with no source]) for an answer and count sources."""
    numbers = []
    for match in _CITATION.finditer(text):
        for n in re.split(r"[,;]", match.group(1)):
            n = int(n)
            if n not in numbers:
                numbers.append(n)
    return sorted(n for n in numbers if 1 <= n <= count), sorted(
        n for n in numbers if not 1 <= n <= count
    )
