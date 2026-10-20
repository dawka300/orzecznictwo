"""ETPC RAG: an answer in Polish from ECHR passages (child of RagPipeline)."""

from __future__ import annotations

import re

from judgments_core.pipeline import RagPipeline

DOMAIN = """\
Jesteś asystentem prawnika i odpowiadasz na pytania o orzecznictwo Europejskiego
Trybunału Praw Człowieka. Źródła to fragmenty wyroków i decyzji ETPC, zwykle po
angielsku albo francusku: czytaj je w oryginale, odpowiadaj po polsku. Nazwy spraw
podawaj w oryginale (np. Kudła v. Poland), z numerem skargi i datą, jeśli są w źródle;
artykuły Konwencji jako np. art. 6 ust. 1 Konwencji. Odróżniaj stwierdzenie
naruszenia od braku naruszenia."""


# Wynik w odpowiedzi vs wnioski HUDOC (etpc.rag.outcome: "Violation of ...; No violation of ...").
OUTCOMES = (
    (
        "brak naruszenia",
        re.compile(
            r"\b(?:brak\w*|bez)\s+naruszeni\w*"
            r"|\bnie\s+(?:stwierdz\w*|doszło\s+do|było|ma|nastąpił\w*)\s+(?:\w+\s+)?naruszeni\w*"
            r"|\bnie\s+narusz\w*|\bniedopuszczaln\w*",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:^|; )(?:no violation|non-violation)|inadmissible|irrecevable|struck out|rayé",
            re.IGNORECASE,
        ),
    ),
    (
        "naruszenie",
        re.compile(
            r"\bstwierdz\w*\s+(?:\w+\s+)?naruszeni\w*|\bdoszło\s+do\s+naruszeni\w*"
            r"|\bnaruszył\w*|\bnaruszon\w*",
            re.IGNORECASE,
        ),
        re.compile(r"(?:^|; )violation", re.IGNORECASE),
    ),
)


class EtpcRag(RagPipeline):
    domain = DOMAIN
    outcomes = OUTCOMES
