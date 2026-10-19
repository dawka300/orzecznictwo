"""Automatic checks of RAG answers (python -m <projekt> ocen-odpowiedzi).

Three levels, cheapest first:
1. citations: [n] numbers with no source (check_citations);
2. outcome rule, no model: a sentence citing exactly one source [n] that
   states an outcome ("naruszenie", "brak naruszenia"; NSA: "oddalono",
   "uchylono" ...) is compared with that source's outcome from the metadata
   (Reference.result). The words are the project's (RagPipeline.outcomes:
   label, words in the answer, words in the metadata). A claimed outcome the
   source doesn't have = possible contradiction ("możliwa", because a
   sentence may also describe the lower court's decision);
3. judge model: another call gets the cited sources and the answer and rates
   every sentence with a citation: poparte / niepoparte / sprzeczne. It may
   be the same local model (weak but free) or a stronger one behind an
   OpenAI-compatible API (<PREFIKS>_JUDGE_URL, _JUDGE_MODEL, _JUDGE_API_KEY).

Answers with their checks are saved as JSON, so two models or two settings
can be compared on the same questions.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field

from judgments_core.chat import ChatError
from judgments_core.rag import Retrieval, render

# Koniec zdania: . ! ? i spacja przed wielką literą (albo nowy wiersz), ale nie po skrócie.
_END = re.compile(r"([.!?])\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ„\"(\[*])|\n+")
_LAST_WORD = re.compile(r"(\w+)\W*$")
ABBREVIATIONS = frozenset(
    [
        "v",
        "c",
        "vs",
        "art",
        "ust",
        "par",
        "pkt",
        "lit",
        "nr",
        "poz",
        "dz",
        "sygn",
        "por",
        "zob",
        "np",
        "tj",
        "m",
        "in",
        "ok",
        "r",
        "zm",
        "ze",
        "ws",
        "pt",
        "św",
        "dr",
        "prof",
        "no",
        "app",
    ]
)
_CITED = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")
_JSON_LIST = re.compile(r"\[\s*\{.*\}\s*\]", re.DOTALL)
RATINGS = ("poparte", "niepoparte", "sprzeczne")

JUDGE_PROMPT = """\
Sprawdzasz odpowiedź asystenta prawnego. Dostajesz ponumerowane źródła i odpowiedź.
Dla KAŻDEGO zdania odpowiedzi, które ma numer źródła w nawiasie [n], oceń, czy to,
co zdanie mówi, wynika z tego źródła (pola WYNIK i fragmentów tekstu):
- "poparte": wynika ze źródła,
- "niepoparte": źródło tego nie mówi (szczegół dodany, uogólnienie bez podstaw),
- "sprzeczne": źródło mówi co innego (np. inny wynik sprawy, inne państwo, inna data).
Odpowiedz WYŁĄCZNIE listą JSON, bez komentarza, np.:
[{"zdanie": "początek zdania...", "zrodlo": 1, "ocena": "poparte", "dlaczego": "krótko"}]"""


@dataclass
class Check:
    question: str
    answer: str
    seconds: float
    sources: list  # [(numer, tytuł, wynik)]
    cited: list = field(default_factory=list)
    unknown: list = field(default_factory=list)  # numery bez źródła
    contradictions: list = field(default_factory=list)  # [(numer, zdanie, twierdzi, wynik)]
    ratings: list = field(default_factory=list)  # oceny sędziego
    judge_error: str = ""
    error: str = ""  # odpowiedzi nie było (np. serwer modelu bez modelu)

    def count(self, rating):
        return sum(1 for r in self.ratings if r.get("ocena") == rating)


def sentences(text):
    """Sentences of an answer; "Guz v. Poland", "art. 10", "R.R." don't end one."""
    found, start = [], 0
    for match in _END.finditer(text):
        if match.group(1):
            word = _LAST_WORD.search(text[start : match.start(1)])
            last = word.group(1) if word else ""
            if last.isalpha() and (len(last) == 1 or last.lower() in ABBREVIATIONS):
                continue
            end = match.start(1) + 1
        else:
            end = match.start()
        found.append(text[start:end])
        start = match.end()
    found.append(text[start:])
    return [s.strip() for s in found if s.strip()]


def cited_numbers(sentence):
    numbers = []
    for match in _CITED.finditer(sentence):
        for n in re.split(r"[,;]", match.group(1)):
            if int(n) not in numbers:
                numbers.append(int(n))
    return numbers


def outcome_contradictions(answer, retrieval, outcomes):
    """[(n, sentence, claimed label, source outcome)] for one-source sentences whose
    claimed outcome the source's metadata doesn't have."""
    by_number = {s.number: s for s in retrieval.sources}
    found = []
    for sentence in sentences(answer):
        numbers = cited_numbers(sentence)
        if len(numbers) != 1 or numbers[0] not in by_number:
            continue
        result = by_number[numbers[0]].result
        if not result:
            continue
        present = {label for label, _, in_result in outcomes if in_result.search(result)}
        if not present:
            continue  # słownictwo wyniku nieznane regułom: bez oceny
        claimed = claimed_outcomes(sentence, outcomes)
        for label in claimed - present:
            found.append((numbers[0], sentence, label, result))
    return found


def claimed_outcomes(sentence, outcomes):
    """Labels the sentence states; a label listed earlier wins over later ones it
    overlaps (e.g. "brak naruszenia" before "naruszenie")."""
    claimed, text = set(), sentence
    for label, in_answer, _ in outcomes:
        if in_answer.search(text):
            claimed.add(label)
            text = in_answer.sub(" ", text)
    return claimed


def judge(judge_model, retrieval, answer, suffix=""):
    """The judge's ratings of the answer's cited sentences (only cited sources sent)."""
    cited = set(cited_numbers(answer))
    sources = [s for s in retrieval.sources if s.number in cited]
    if not sources:
        return []
    text = render(Retrieval(sources, []), ids=False)
    messages = [
        {"role": "system", "content": JUDGE_PROMPT},
        {
            "role": "user",
            "content": f"Źródła:\n\n{text}\n\nOdpowiedź do sprawdzenia:\n\n{answer}"
            + (f"\n\n{suffix}" if suffix else ""),
        },
    ]
    raw = judge_model.complete(messages)
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    match = _JSON_LIST.search(raw)
    if not match:
        raise ValueError(f"sędzia nie zwrócił listy JSON: {raw[:200]!r}")
    ratings = json.loads(match.group(0))
    return [r for r in ratings if isinstance(r, dict) and r.get("ocena") in RATINGS]


def check(pipeline, cur, question, filters=None, judge_model=None, budget_chars=None):
    """One question through the pipeline and all the checks."""
    retrieval = pipeline.retrieve(cur, question, budget_chars, **(filters or {}))
    started = time.monotonic()
    try:
        answer = pipeline.answer(question, retrieval)
    except ChatError as e:
        sources = [(s.number, s.title, s.result) for s in retrieval.sources]
        return Check(question, "", time.monotonic() - started, sources, error=str(e))
    result = Check(
        question,
        answer.text,
        time.monotonic() - started,
        [(s.number, s.title, s.result) for s in retrieval.sources],
        answer.cited,
        answer.unknown,
        outcome_contradictions(answer.text, retrieval, pipeline.outcomes),
    )
    if judge_model is not None:
        try:
            result.ratings = judge(
                judge_model, retrieval, answer.text, getattr(pipeline, "suffix", "")
            )
        except (ValueError, RuntimeError) as e:  # zły JSON, serwer modelu
            result.judge_error = str(e)
    return result


def report(checks, model=""):
    lines = []
    for n, c in enumerate(checks, 1):
        lines.append(f"{n:2}. {c.question}")
        lines.append(
            f"    {c.seconds:.0f} s, {len(c.answer)} zn., cytuje {c.cited}"
            + (f", NIEISTNIEJĄCE {c.unknown}" if c.unknown else "")
        )
        for number, sentence, claimed, result in c.contradictions:
            lines.append(f"    ! [{number}] twierdzi „{claimed}”, a WYNIK: {result[:120]}")
            lines.append(f"      {sentence[:160]}")
        if c.error:
            lines.append(f"    BRAK ODPOWIEDZI: {c.error[:160]}")
            continue
        if c.judge_error:
            lines.append(f"    sędzia: błąd ({c.judge_error[:120]})")
        elif c.ratings:
            lines.append("    sędzia: " + ", ".join(f"{r} {c.count(r)}" for r in RATINGS))
            for r in c.ratings:
                if r.get("ocena") != "poparte":
                    lines.append(
                        f"      {r['ocena']} [{r.get('zrodlo')}]: {str(r.get('zdanie'))[:110]}"
                        f" - {str(r.get('dlaczego', ''))[:100]}"
                    )
    rated = [r for c in checks for r in c.ratings]
    answered = [c for c in checks if not c.error]
    lines += [
        "",
        f"Model: {model}  pytań: {len(checks)}, bez odpowiedzi: {len(checks) - len(answered)}",
        f"  średni czas odpowiedzi: {sum(c.seconds for c in answered) / max(len(answered), 1):.0f} s",
        f"  nieistniejące cytowania: {sum(len(c.unknown) for c in checks)}",
        f"  możliwe sprzeczności wyniku (reguła): {sum(len(c.contradictions) for c in checks)}",
    ]
    if rated:
        lines.append(
            "  sędzia: "
            + ", ".join(
                f"{r} {sum(c.count(r) for c in checks)}"
                f" ({sum(c.count(r) for c in checks) / len(rated):.0%})"
                for r in RATINGS
            )
        )
    return lines


def save(checks, path, model, settings_note=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"model": model, "ustawienia": settings_note, "odpowiedzi": [asdict(c) for c in checks]}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
