"""ETPC semantic search: the search form -> vector filters (child of SemanticSearch).

Filters the vector payload has are applied in Qdrant; the rest (article,
application numbers, citations) by SQL afterwards (etpc/search.py).
"""

from __future__ import annotations

from judgments_core.semantic import SemanticSearch

# Języki wersji z wektorami (jedna na orzeczenie).
VECTOR_LANGUAGES = ("ENG", "FRE")


class EtpcSemantic(SemanticSearch):
    document_field = "itemid"
    embed_command = "python -m etpc wektory"
    # "Podobne": z uzasadnienia prawnego, gdy jest.
    similar_parts = ("prawo",)

    def payload_filters(self, q):
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
