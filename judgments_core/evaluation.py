"""Evaluation of the retriever on golden questions (python -m <projekt> ocen).

A JSON file per project (<projekt>/pytania.json): questions with the
judgments that should be found, named by keys the project understands
(ETPC application numbers, NSA docket numbers) and optional search filters:

[{"pytanie": "...", "oczekiwane": ["8932/05", "59519/00"], "filtry": {"respondent": "POL"}}]

For each question: the place of each expected judgment in the search ranking
and whether it made it into the packed context. Summary: found at place 1 /
in the top 5 / in the context, MRR (1 / place of the first expected
judgment). No language model: cheap, repeatable, the base for tuning.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field


@dataclass
class Question:
    text: str
    expected: list
    filters: dict = field(default_factory=dict)


@dataclass
class Score:
    question: Question
    places: dict  # klucz -> miejsce w rankingu (1 = pierwsze), 0 = nie znaleziono
    in_context: dict  # klucz -> czy w kontekście
    unknown: list  # klucze, których nie ma w bazie
    seconds: float
    chars: int
    mode: str
    error: str = ""

    @property
    def first(self):
        """Best place of any expected judgment, 0 = none found."""
        found = [p for p in self.places.values() if p]
        return min(found) if found else 0


def load(path):
    with open(path, encoding="utf-8") as f:
        items = json.load(f)
    return [Question(q["pytanie"], q["oczekiwane"], q.get("filtry") or {}) for q in items]


def evaluate(retriever, cur, questions, budget_chars=None):
    scores = []
    for q in questions:
        known = retriever.expected(cur, q.expected)
        unknown = [k for k in q.expected if not known.get(k)]
        started = time.monotonic()
        try:
            result = retriever.retrieve(cur, retriever.query(q.text, **q.filters), budget_chars)
        except ValueError as e:  # QueryError projektu
            scores.append(Score(q, {}, {}, unknown, time.monotonic() - started, 0, "", str(e)))
            continue
        seconds = time.monotonic() - started
        place = {judgment_id: n for n, judgment_id in enumerate(result.ranked, 1)}
        context = {s.judgment_id for s in result.sources}
        places, in_context = {}, {}
        for key, ids in known.items():
            if not ids:
                continue
            places[key] = min((place[i] for i in ids if i in place), default=0)
            in_context[key] = bool(ids & context)
        scores.append(Score(q, places, in_context, unknown, seconds, result.chars, result.mode))
    return scores


def report(scores):
    """Lines of the per-question table and the summary."""
    lines = []
    for n, s in enumerate(scores, 1):
        lines.append(f"{n:2}. {s.question.text}")
        if s.error:
            lines.append(f"    błąd: {s.error}")
            continue
        found = ", ".join(
            f"{k} {'#' + str(p) if p else '-'}{' K' if s.in_context.get(k) else ''}"
            for k, p in s.places.items()
        )
        lines.append(f"    {found}  ({s.mode or '?'}, {s.seconds:.1f} s, kontekst {s.chars} zn.)")
        if s.unknown:
            lines.append(f"    brak w bazie: {', '.join(s.unknown)}")
    rated = [s for s in scores if s.places]
    if not rated:
        lines.append("Brak pytań do oceny (oczekiwane orzeczenia nie są w bazie).")
        return lines
    n = len(rated)
    expected = sum(len(s.places) for s in rated)
    in_context = sum(sum(s.in_context.values()) for s in rated)
    lines += [
        "",
        f"Pytań ocenionych: {n} z {len(scores)} (#miejsce w rankingu, K = w kontekście)",
        f"  pierwsze miejsce:   {sum(s.first == 1 for s in rated) / n:.0%}",
        f"  w pierwszej 5:      {sum(0 < s.first <= 5 for s in rated) / n:.0%}",
        f"  MRR:                {sum(1 / s.first for s in rated if s.first) / n:.3f}",
        f"  oczekiwane w kontekście: {in_context} z {expected} ({in_context / expected:.0%})",
        f"  średnio {sum(s.seconds for s in rated) / n:.1f} s na pytanie",
    ]
    return lines
