"""NSA semantic search: the search form -> vector filters (child of SemanticSearch).

Filters the vector payload has are applied in Qdrant; the rest (judge,
challenged authority, result, finality ...) by SQL afterwards (nsa/search.py).
"""

from __future__ import annotations

from judgments_core.semantic import SemanticSearch


class NsaSemantic(SemanticSearch):
    document_field = "cbosa_id"
    embed_command = "python -m nsa wektory"
    # "Podobne": z uzasadnienia (i tezy), gdy są.
    similar_parts = ("uzasadnienie", "teza")

    def payload_filters(self, q):
        filters = {}
        if q.kind:
            filters["kind"] = q.kind
        if q.court:
            filters["court"] = q.court
        if q.instance == "nsa":
            filters["is_nsa"] = True
        elif q.instance == "wsa":
            filters["is_nsa"] = False
        if q.year_from or q.year_to:
            filters["year"] = {k: v for k, v in (("gte", q.year_from), ("lte", q.year_to)) if v}
        if q.symbol:
            filters["symbols"] = q.symbol
        if q.keyword:
            filters["keywords"] = q.keyword
        return filters
