"""ETPC retriever: passages of rulings for a question (child of Retriever).

Ranking = the page's hybrid search (Polish phrases translated by the glossary,
filters by state, article, violation ...). Passages come from the version with
vectors (ENG, else FRE); a ruling without vectors is read in ENG > FRE > any
other language at the first query word.
"""

from __future__ import annotations

from etpc import labels, search
from etpc.labels import label
from etpc.semantic import EtpcSemantic
from judgments_core.rag import Ranking, Retriever

HUDOC_URL = "https://hudoc.echr.coe.int/eng?i={}"
# Wersja do czytania, gdy orzeczenie nie ma wektorów (tak jak przy liczeniu wektorów).
LANGUAGES = ("ENG", "FRE")


class EtpcRetriever(Retriever):
    query_class = search.SearchQuery
    semantic_class = EtpcSemantic

    def ranking(self, cur, query):
        result = search.search(cur, query, self.semantic)
        pattern = search.highlight_pattern(result.terms.highlight) if result.terms else None
        return Ranking(result.hits, pattern, result.mode, result.notice)

    def bodies(self, cur, wanted):
        found = {}
        itemids = [doc for doc in wanted.values() if doc]
        if itemids:
            cur.execute(
                "SELECT ruling_id, itemid, language, body FROM ruling_documents WHERE itemid IN %s",
                (itemids,),
            )
            for ruling_id, itemid, language, body in cur.fetchall():
                found[ruling_id] = (itemid, language, body)
        rest = [i for i, doc in wanted.items() if not doc or i not in found]
        if rest:
            cur.execute(
                "SELECT ruling_id, itemid, language, body FROM ruling_documents"
                " WHERE ruling_id IN %s AND body <> ''",
                (rest,),
            )
            for ruling_id, itemid, language, body in sorted(
                cur.fetchall(), key=lambda row: _language_rank(row[2]), reverse=True
            ):
                found[ruling_id] = (itemid, language, body)  # najlepszy język na końcu wygrywa
        return found

    def describe(self, hit, document):
        details = "; ".join(
            x
            for x in (
                f"{label(labels.KINDS, hit.kind)} z {hit.ruling_date}",
                f"skargi {', '.join(hit.appnos)}" if hit.appnos else "",
                f"przeciwko {', '.join(hit.states)}" if hit.states else "",
                f"naruszenia {', '.join(hit.violations)}" if hit.violations else "",
                f"ważność {label(labels.IMPORTANCE, hit.importance)}",
                f"cytowane {hit.cited_by} razy" if hit.cited_by else "",
            )
            if x
        )
        return hit.title, details, HUDOC_URL.format(document)

    def expected(self, cur, keys):
        found = {k: set() for k in keys}
        if keys:
            cur.execute(
                "SELECT appno, ruling_id FROM ruling_applications WHERE appno IN %s", (list(keys),)
            )
            for appno, ruling_id in cur.fetchall():
                found[appno].add(ruling_id)
        return found


def _language_rank(language):
    return LANGUAGES.index(language) if language in LANGUAGES else len(LANGUAGES)
