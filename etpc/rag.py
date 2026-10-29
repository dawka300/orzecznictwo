"""ETPC retriever: passages of rulings for a question (child of Retriever).

Ranking = the page's hybrid search (Polish phrases translated by the glossary,
filters by state, article, violation ...). Passages come from the version with
vectors (ENG, else FRE); a ruling without vectors is read in ENG > FRE > any
other language at the first query word.

Mode: hybrid (words + meaning) since 2026-10-24. With a 4 GB InnoDB buffer
long Polish questions (several glossary groups of very common phrases) made
FULLTEXT read from disk for ~61 s, so meaning alone was the default; with the
texts and the FULLTEXT index in an 8 GB buffer (docker-compose.yml) a question
takes ~0.6 s, and on the golden questions MRR went 0.474 -> 0.740 (notepad.md).
With a small buffer go back to mode = "znaczeniowe".
"""

from __future__ import annotations

import math
from dataclasses import fields, replace

from etpc import labels, search
from etpc.labels import label
from etpc.semantic import EtpcSemantic
from etpc.weights import TIERS
from judgments_core.rag import Ranking, Retriever

HUDOC_URL = "https://hudoc.echr.coe.int/eng?i={}"
# Wersja do czytania, gdy orzeczenie nie ma wektorów (tak jak przy liczeniu wektorów).
LANGUAGES = ("ENG", "FRE")
# Części wniosków HUDOC pomijane w wyniku sprawy (zadośćuczynienie, koszty).
DAMAGES = ("pecuniary", "non-pecuniary", "costs", "just satisfaction", "dommage", "frais")
# Pola zapytania będące filtrami: z nimi cytowane orzeczenia nie dołączają (np. inne państwo).
FILTER_FIELDS = tuple(
    f.name
    for f in fields(search.SearchQuery)
    if f.name not in ("text", "translate", "sort", "page", "mode", "page_size")
)


class EtpcRetriever(Retriever):
    query_class = search.SearchQuery
    mode = ""  # hybrydowe; wymaga bufora InnoDB z całym ETPC (wyżej)
    last_parts = ("opinia",)  # zdania odrębne/zbieżne sędziów
    semantic_class = EtpcSemantic
    # Orzeczenia cytowane przez >= 3 z 20 najlepszych wyników dołączają (ruling_links):
    # Salduz, Soering, Opuz wychodzą, choć ich tekst brzmi mniej jak pytanie
    # (pytania-panstwa.json: MRR 0,527 -> 0,725; sędzia na 12 pytaniach: nDCG 0,867 -> 0,898).
    cited_weight = 1.0
    # Osłabienie spraw cytowanych wszędzie (Kudła 3923 razy w bazie, Labita 1955, Gäfgen 850
    # - na pytaniach-100 częsty szum): lift = ile razy częściej cytują je najlepsze wyniki
    # niż przeciętne orzeczenie bazy, poniżej progu nie dołączają (0 = bez progu);
    # cited_idf = kolejność wg n * log(N / cytowania w bazie) zamiast samego n.
    cited_min_lift = 0.0
    cited_idf = False

    def ranking(self, cur, query):
        # Dwie strony: po zwinięciu etapów jednej skargi zostaje dość kandydatów.
        query = replace(query, page_size=2 * search.PAGE_SIZE)
        result = search.search(cur, query, self.semantic)
        pattern = search.highlight_pattern(result.terms.highlight) if result.terms else None
        return Ranking(result.hits, pattern, result.mode, result.notice)

    def twin_keys(self, hit):
        """Stages of one case have the same set of application numbers (admissibility
        decision, chamber, grand chamber, just satisfaction ...). The whole set, not any
        shared number: HUDOC lists both applications on the admissibility decisions of
        Staroszczyk and Siałkowska v. Poland, which would glue two cases into one."""
        return (tuple(sorted(set(hit.appnos))),) if hit.appnos else ()

    def twin_rank(self, hit):
        """Judgments before decisions, the Grand Chamber first, then the weightier, the later."""
        return (
            hit.kind != "judgment",
            "GRAND" not in (hit.formation or "").upper(),
            hit.tier or "Z",
            -(hit.ruling_date.toordinal() if hasattr(hit.ruling_date, "toordinal") else 0),
        )

    def twin_label(self, hit):
        return f"{label(labels.KINDS, hit.kind)} z {hit.ruling_date}"

    def cited_by(self, cur, ids):
        cur.execute(
            "SELECT l.cited_id, COUNT(*) n, COALESCE(MAX(k.cited_by), 0)"
            " FROM ruling_links l LEFT JOIN ruling_ranks k ON k.ruling_id = l.cited_id"
            " WHERE l.citing_id IN %s GROUP BY l.cited_id ORDER BY n DESC, l.cited_id LIMIT 50",
            (list(ids),),
        )
        rows = cur.fetchall()
        if not (self.cited_idf or self.cited_min_lift):
            return [(cited, n) for cited, n, _ in rows]
        cur.execute("SELECT COUNT(*) FROM rulings")
        (total,) = cur.fetchone()
        scored = []
        for cited, n, everywhere in rows:
            everywhere = max(int(everywhere), n)  # MAX() z MariaDB przychodzi jako Decimal
            if (n / len(ids)) / (everywhere / total) < self.cited_min_lift:
                continue
            score = n * math.log(total / everywhere) if self.cited_idf else n
            scored.append((-score, cited, n))
        return [(cited, n) for _, cited, n in sorted(scored)]

    def filtered(self, query):
        default = search.SearchQuery()
        return any(getattr(query, f) != getattr(default, f) for f in FILTER_FIELDS)

    def hits(self, cur, ids):
        return search._hits(cur, list(ids))

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
                f"WYNIK: {outcome(hit.conclusion)}" if outcome(hit.conclusion) else "",
                f"waga {hit.tier} {TIERS[hit.tier]}" if hit.tier else "",
                f"ważność {label(labels.IMPORTANCE, hit.importance)}",
                f"cytowane {hit.cited_by} razy" if hit.cited_by else "",
            )
            if x
        )
        return hit.title, details, HUDOC_URL.format(document)

    def result(self, hit):
        return outcome(hit.conclusion)

    def outcome_group(self, result):
        """Violation (also only one of several articles) = uwzglednione; no violation or
        inadmissible = nieuwzglednione; struck out, friendly settlement ... = inne."""
        items = [i.strip().lower() for i in (result or "").split(";")]
        if any(i.startswith(("violation", "violation de")) for i in items):
            return "uwzglednione"
        if any(
            i.startswith(("no violation", "non-violation", "inadmissible", "irrecevable"))
            or "remainder inadmissible" in i
            for i in items
        ):
            return "nieuwzglednione"
        return "inne"

    def expected(self, cur, keys):
        found = {k: set() for k in keys}
        if keys:
            cur.execute(
                "SELECT appno, ruling_id FROM ruling_applications WHERE appno IN %s", (list(keys),)
            )
            for appno, ruling_id in cur.fetchall():
                found[appno].add(ruling_id)
        return found


def outcome(conclusion):
    """HUDOC's conclusion without damages and costs: violations, no violations,
    inadmissibility, striking out ("Violation of Art. 6-1; No violation of Art. 3")."""
    items, depth, current = [], 0, ""
    for char in conclusion or "":
        depth += (char == "(") - (char == ")")
        if char == ";" and depth == 0:
            items.append(current.strip())
            current = ""
        else:
            current += char
    items.append(current.strip())
    kept = [i for i in items if i and not i.lower().startswith(DAMAGES)]
    return "; ".join(kept)


def _language_rank(language):
    return LANGUAGES.index(language) if language in LANGUAGES else len(LANGUAGES)
