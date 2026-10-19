"""Semantic search: the query embedded (LM Studio) and matched in Qdrant.

The query goes to the model as typed (Polish is fine, bge-m3 is multilingual);
search operators (quotes, -word, word*) are dropped. Filters that the vector
payload has are applied in Qdrant; the rest (article, application numbers,
citations) by SQL afterwards (etpc/search.py). "Similar rulings" = the mean
vector of a ruling's chunks (its legal reasoning if it has one).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Języki wersji z wektorami (jedna na orzeczenie).
VECTOR_LANGUAGES = ("ENG", "FRE")
# Części, z których liczymy "podobne": uzasadnienie prawne, gdy jest.
SIMILAR_PARTS = ("prawo",)
_OPERATORS = re.compile(r'(?:^|\s)-\S+|["*]')


class SemanticUnavailable(RuntimeError):
    """LM Studio or Qdrant is not running; the message is for the user."""


@dataclass
class Passage:
    ruling_id: int
    score: float
    itemid: str
    language: str
    start: int
    length: int
    context: str
    part: str


class Semantic:
    def __init__(self, embedder, vectors):
        self.embedder = embedder
        self.vectors = vectors

    def rulings(self, text, q, k=200):
        """Best passage of each of the k rulings closest to text, filtered by q."""
        text = " ".join(_OPERATORS.sub(" ", text).split())
        if not text:
            return []
        try:
            vector = self.embedder.embed([text])[0]
            hits = self.vectors.search_judgments(vector, k, filters=payload_filters(q))
        except RuntimeError as e:
            raise SemanticUnavailable(_why(e)) from e
        return [_passage(h) for h in hits]

    def similar(self, ruling_id, k=10):
        """Rulings closest to the mean vector of this ruling's chunks."""
        try:
            chunks = self.vectors.vectors_of(ruling_id)
            chosen = [v for part, v in chunks if part in SIMILAR_PARTS] or [v for _, v in chunks]
            if not chosen:
                return []
            mean = [sum(column) / len(chosen) for column in zip(*chosen)]
            hits = self.vectors.search_judgments(mean, k, exclude={"judgment_id": str(ruling_id)})
        except RuntimeError as e:
            raise SemanticUnavailable(_why(e)) from e
        return [_passage(h) for h in hits]


def payload_filters(q):
    """The part of SearchQuery that Qdrant can filter on."""
    filters = {}
    if q.kind:
        filters["kind"] = q.kind
    if q.formation:
        filters["formation"] = q.formation
    if q.respondent:
        filters["respondents"] = q.respondent
    if q.importance:
        filters["importance"] = {"lte": q.importance}
    if q.year_from or q.year_to:
        filters["year"] = {k: v for k, v in (("gte", q.year_from), ("lte", q.year_to)) if v}
    if q.language in VECTOR_LANGUAGES:
        filters["language"] = q.language
    return filters


def _passage(hit):
    c = hit.chunk
    return Passage(
        ruling_id=int(c.judgment_id),
        score=hit.score,
        itemid=c.meta.get("itemid", ""),
        language=c.meta.get("language", ""),
        start=c.start,
        length=c.meta.get("length", 0),
        context=c.meta.get("context", ""),
        part=c.part,
    )


def _why(error):
    return (
        "Wyszukiwanie znaczeniowe jest niedostępne (LM Studio z modelem bge-m3"
        f" albo Qdrant nie działa: {error}). Pokazuję wyniki pełnotekstowe."
    )
