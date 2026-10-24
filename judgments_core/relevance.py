"""Relevance of the retrieved judgments graded by a language model (python -m <projekt> ocen --claude).

`ocen` (evaluation.py) knows only whether the expected judgments were found;
a good result missing from the golden list counts as nothing. Here the model
reads, for each golden question, the first k judgments of the context (title,
details, the passages the retriever chose - what a RAG model would see) and
grades each 0-3. Measures: mean grade, precision@k (grade >= 2), nDCG@k (the
order against the best order of the same grades) and the place of the first
grade-3 judgment. The expected judgments' grades check the judge itself
(they should get 3).

One model call per question; the grades are saved with the reasons, so a run
can be read later and compared with another.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field

from judgments_core.chat import ChatError

TOP = 10
SOURCE_CHARS = 1200  # tyle tekstu fragmentów jednego orzeczenia dla sędziego
RELEVANT = 2  # ocena od tej = trafne (precyzja)

SYSTEM = """Jesteś doświadczonym prawnikiem i oceniasz wyniki wyszukiwarki orzeczeń.
Dostajesz pytanie prawnika i ponumerowane wyniki (nazwa sprawy, dane, fragmenty tekstu).
Oceń każdy wynik w skali 0-3 tylko pod kątem tego, czy pomaga odpowiedzieć na pytanie:
3 = dokładnie o to zagadnienie, prawnik by je zacytował;
2 = to samo zagadnienie prawne, ale inny wariant lub tylko część pytania;
1 = pokrewny temat (ten sam artykuł albo dziedzina), ale nie odpowiada na pytanie;
0 = nie na temat.
Nie oceniaj wyniku sprawy ani ważności orzeczenia - tylko trafność do pytania.
Państwo w pytaniu to zwykle sytuacja pytającego, a nie warunek: orzeczenie wobec innego
państwa ustalające ten sam standard jest tak samo trafne (prawnik je zacytuje). Obniż ocenę
za państwo tylko wtedy, gdy pytanie wprost dotyczy prawa lub praktyki konkretnego państwa."""

FORMAT = (
    "Odpowiedz wyłącznie wierszami w formacie (jeden wiersz na każdy wynik, po kolei):\n"
    "[numer] ocena - krótko dlaczego (do 15 słów)"
)
GRADE_RE = re.compile(
    r"^[\s*]*\[?(\d{1,2})\]?[\s*]*[:.)-]?[\s*]*([0-3])\b[\s*]*[-–:]*\s*(.*)$", re.MULTILINE
)


@dataclass
class Graded:
    place: int  # miejsce w kontekście (1 = pierwsze)
    judgment_id: int
    title: str
    details: str
    grade: int  # -1 = sędzia nie ocenił
    why: str = ""
    expected: bool = False


@dataclass
class Judged:
    question: str
    results: list = field(default_factory=list)
    error: str = ""

    @property
    def grades(self):
        return [r.grade for r in self.results if r.grade >= 0]

    @property
    def mean(self):
        return sum(self.grades) / len(self.grades) if self.grades else 0.0

    @property
    def precision(self):
        return sum(g >= RELEVANT for g in self.grades) / len(self.grades) if self.grades else 0.0

    @property
    def ndcg(self):
        return ndcg(self.grades)

    @property
    def first_exact(self):
        """Place of the first grade-3 result, 0 = none."""
        return next((r.place for r in self.results if r.grade == 3), 0)


def ndcg(grades):
    """nDCG of the grades in this order against the same grades sorted best first."""

    def dcg(gs):
        return sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(gs))

    ideal = dcg(sorted(grades, reverse=True))
    return dcg(grades) / ideal if ideal else 0.0


def messages(question, sources):
    lines = [f"Pytanie: {question}", "", "Wyniki:"]
    for n, s in enumerate(sources, 1):
        text = " […] ".join(" ".join(f.text.split()) for f in s.fragments)
        if len(text) > SOURCE_CHARS:
            text = text[:SOURCE_CHARS].rsplit(" ", 1)[0] + " […]"
        lines += [f"\n[{n}] {s.title}", f"Dane: {s.details}", f"Fragmenty: {text}"]
    lines += ["", FORMAT]
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n".join(lines)}]


def parse(reply, count):
    """{place: (grade, why)} from the judge's lines."""
    grades = {}
    for m in GRADE_RE.finditer(reply):
        place = int(m.group(1))
        if 1 <= place <= count and place not in grades:
            grades[place] = (int(m.group(2)), m.group(3).strip())
    return grades


def judge(retriever, cur, chat, questions, top=TOP, budget_chars=None):
    """[Judged] for the questions; stops at the first ChatError (key, spending limit ...),
    keeping what was graded."""
    out = []
    for q in questions:
        known = retriever.expected(cur, q.expected)
        expected_ids = set().union(*known.values()) if known else set()
        try:
            result = retriever.retrieve(cur, retriever.query(q.text, **q.filters), budget_chars)
        except ValueError as e:  # QueryError projektu
            out.append(Judged(q.text, error=str(e)))
            continue
        sources = result.sources[:top]
        judged = Judged(q.text)
        out.append(judged)
        if not sources:
            judged.error = "nic nie znaleziono"
            continue
        try:
            reply = chat.complete(messages(q.text, sources))
        except ChatError as e:
            judged.error = str(e)
            break
        grades = parse(reply, len(sources))
        for n, s in enumerate(sources, 1):
            grade, why = grades.get(n, (-1, ""))
            judged.results.append(
                Graded(
                    n, s.judgment_id, s.title, s.details, grade, why, s.judgment_id in expected_ids
                )
            )
    return out


def report(judged, model):
    lines = []
    for n, j in enumerate(judged, 1):
        lines.append(f"{n:2}. {j.question}")
        if j.error:
            lines.append(f"    błąd: {j.error}")
            if not j.results:
                continue
        for r in j.results:
            mark = " *oczekiwane*" if r.expected else ""
            grade = "?" if r.grade < 0 else str(r.grade)
            lines.append(f"    [{r.place}] {grade}  {r.title}{mark}")
            lines.append(f"         {r.details}")
            if r.why:
                lines.append(f"         -> {r.why}")
        if j.results:
            lines.append(
                f"    średnia {j.mean:.2f}, trafne {j.precision:.0%}, nDCG {j.ndcg:.2f},"
                f" pierwsze „3” na miejscu {j.first_exact or '-'}"
            )
    rated = [j for j in judged if j.grades]
    if not rated:
        lines.append("Nic nie ocenione.")
        return lines
    n = len(rated)
    expected = [r for j in rated for r in j.results if r.expected and r.grade >= 0]
    lines += [
        "",
        f"Sędzia: {model}; pytań ocenionych: {n} z {len(judged)}, po {TOP} pierwszych wyników",
        f"  średnia ocena:          {sum(j.mean for j in rated) / n:.2f} (0-3)",
        f"  trafne (ocena >= {RELEVANT}):    {sum(j.precision for j in rated) / n:.0%}",
        f"  nDCG@{TOP}:                {sum(j.ndcg for j in rated) / n:.3f}",
        f"  „3” na 1. miejscu:      {sum(j.first_exact == 1 for j in rated) / n:.0%}",
    ]
    if expected:
        lines.append(
            f"  oczekiwane w wynikach: {len(expected)}, średnia ich ocena"
            f" {sum(r.grade for r in expected) / len(expected):.2f} (sprawdzian sędziego: ~3)"
        )
    return lines


def save(judged, path, model):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"model": model, "pytania": [asdict(j) for j in judged]}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
