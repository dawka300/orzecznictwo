"""NSA RAG: an answer from Polish administrative court judgments (child of RagPipeline)."""

from __future__ import annotations

import re

from judgments_core.pipeline import RagPipeline

DOMAIN = """\
Jesteś asystentem prawnika i odpowiadasz na pytania o orzecznictwo polskich sądów
administracyjnych (Naczelny Sąd Administracyjny, wojewódzkie sądy administracyjne).
Źródła to fragmenty orzeczeń: teza, sentencja, uzasadnienie. Podawaj sygnaturę, sąd
i datę orzeczenia; przepisy cytuj tak jak w źródle. Uchwała NSA ma większą wagę niż
pojedynczy wyrok - zaznacz, gdy odpowiedź opiera się na uchwale. Gdy orzeczenia się
różnią, pokaż obie linie."""


# Rozstrzygnięcie w odpowiedzi vs "Treść wyniku" z CBOSA (np. "Oddalono skargę kasacyjną").
OUTCOMES = tuple(
    (label, re.compile(rf"\b{stem}\w*", re.IGNORECASE), re.compile(stem, re.IGNORECASE))
    for label, stem in (
        ("oddalenie", "oddal"),
        ("uchylenie", "uchyl"),
        ("odrzucenie", "odrzuc"),
        ("umorzenie", "umorz"),
    )
)


class NsaRag(RagPipeline):
    domain = DOMAIN
    outcomes = OUTCOMES
