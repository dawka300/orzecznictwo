"""NSA retriever: passages of NSA/WSA judgments for a question (child of Retriever).

Ranking = the page's hybrid search (Polish stems, filters by court, symbol,
keyword ...). Offsets point into nsa.parser.judgment_body (TEZA, SENTENCJA,
UZASADNIENIE ...); a judgment without vectors is read at the first query word,
else from its start (the thesis, when it has one).
"""

from __future__ import annotations

from judgments_core.rag import Ranking, Retriever
from nsa import search
from nsa.parser import judgment_body
from nsa.semantic import NsaSemantic

CBOSA_URL = "https://orzeczenia.nsa.gov.pl/doc/{}"
FINAL = {1: "prawomocne", 0: "nieprawomocne"}


class NsaRetriever(Retriever):
    query_class = search.SearchQuery
    semantic_class = NsaSemantic

    def ranking(self, cur, query):
        result = search.search(cur, query, self.semantic)
        pattern = search.highlight_pattern(result.terms.highlight) if result.terms else None
        return Ranking(result.hits, pattern, result.mode, result.notice)

    def bodies(self, cur, wanted):
        if not wanted:
            return {}
        cur.execute(
            "SELECT id, cbosa_id, thesis, sentence, reasons, dissenting_opinion"
            " FROM judgments WHERE id IN %s",
            (list(wanted),),
        )
        return {row[0]: (row[1], "", judgment_body(*row[2:])) for row in cur.fetchall()}

    def describe(self, hit, document):
        details = "; ".join(
            x
            for x in (
                f"{hit.judgment_type} z {hit.judgment_date}",
                hit.court,
                FINAL.get(hit.is_final, ""),
                f"symbole {', '.join(hit.symbols)}" if hit.symbols else "",
                f"organ: {hit.challenged_authority}" if hit.challenged_authority else "",
                f"WYNIK: {hit.result}" if hit.result else "",
            )
            if x
        )
        return hit.docket_number, details, CBOSA_URL.format(hit.cbosa_id)

    def expected(self, cur, keys):
        found = {k: set() for k in keys}
        if keys:
            cur.execute(
                "SELECT docket_number, id FROM judgments WHERE docket_number IN %s", (list(keys),)
            )
            for docket, judgment_id in cur.fetchall():
                found[docket].add(judgment_id)
        return found
