"""NSA embedding job: judgments -> chunks -> vectors (python -m nsa wektory), child of EmbedJob.

Every judgment's whole text (thesis, operative part, reasoning, dissenting
opinion - nsa.parser.judgment_body) is chunked by NsaChunker: ~18M vectors for
~2.25M judgments, ~20 GB RAM in Qdrant (int8). On a smaller machine summaries =
True keeps full chunks only for the judgments worth quoting (full_chunks:
resolutions, with a thesis or published, NSA judgments) and gives the rest one
summary vector each (NsaChunker.summary: thesis, the decision, the start of the
reasoning; ~5.7M vectors). Filterable metadata: court, kind, year, case
symbols, keywords. The order says what is searchable first: resolutions,
judgments with a thesis or published in the official collection, NSA
judgments, WSA judgments with reasoning, then the rest (mostly procedural
orders); rulings with neither reasons nor a thesis (a bare operative part says
little) come after all of them. judgment_vectors records what is done; the loop, resuming and
progress are common (judgments_core/embed.py).
"""

from __future__ import annotations

from judgments_core.embed import EmbedJob
from nsa.chunker import SUMMARY_CHARS, NsaChunker
from nsa.parser import judgment_body
from nsa.stats import NSA_COURT

PAYLOAD_INDEXES = {
    "judgment_id": "keyword",
    "kind": "keyword",
    "court": "keyword",
    "is_nsa": "bool",
    "part": "keyword",
    "year": "integer",
    "symbols": "keyword",
    "keywords": "keyword",
}
PROGRESS_KEY = "nsa:wektory"

# Długość tekstu do liczenia (postęp, czas do końca): przy skrótach najwyżej SUMMARY_CHARS.
SUMMARY_LENGTH_SQL = f"""CASE WHEN j.kind = 'uchwala' OR j.thesis IS NOT NULL
        OR EXISTS (SELECT 1 FROM judgment_publications p WHERE p.judgment_id = j.id)
        OR (j.kind = 'wyrok' AND j.court = '{NSA_COURT}')
    THEN j.text_length ELSE LEAST(j.text_length, {SUMMARY_CHARS}) END"""

CANDIDATES_SQL = f"""
SELECT j.id, j.cbosa_id, j.text_sha256, {{length}}
FROM judgments j
LEFT JOIN judgment_vectors v ON v.judgment_id = j.id
WHERE v.judgment_id IS NULL OR v.body_sha256 <> j.text_sha256 OR v.model <> %s
ORDER BY (j.reasons IS NULL AND j.thesis IS NULL AND j.kind <> 'uchwala'), CASE
    WHEN j.kind = 'uchwala' THEN 0
    WHEN j.thesis IS NOT NULL
        OR EXISTS (SELECT 1 FROM judgment_publications p WHERE p.judgment_id = j.id) THEN 1
    WHEN j.kind = 'wyrok' AND j.court = '{NSA_COURT}' THEN 2
    WHEN j.kind = 'wyrok' AND j.reasons IS NOT NULL THEN 3
    WHEN j.court = '{NSA_COURT}' THEN 4
    ELSE 5 END,
    j.judgment_date DESC, j.id
"""


class NsaEmbedJob(EmbedJob):
    chunker_class = NsaChunker
    payload_indexes = PAYLOAD_INDEXES
    progress_key = PROGRESS_KEY
    tracking_table = "judgment_vectors"
    tracking_key = "judgment_id"
    tracking_doc = "cbosa_id"
    parent_table = "judgments"
    # True: pełne fragmenty tylko dla full_chunks, reszta po jednym wektorze skrótu
    summaries = False

    def candidates(self, cur, model):
        length = SUMMARY_LENGTH_SQL if self.summaries else "j.text_length"
        cur.execute(CANDIDATES_SQL.format(length=length), (model,))
        return cur.fetchall()

    def pending_shas(self, cur, model, ids):
        """By the ids only (the full candidates() list is a scan of ~2.25M judgments)."""
        found = {}
        for start in range(0, len(ids), 1000):
            part = list(ids[start : start + 1000])
            cur.execute(
                "SELECT j.id, j.text_sha256 FROM judgments j"
                " LEFT JOIN judgment_vectors v ON v.judgment_id = j.id"
                " WHERE j.id IN %s"
                " AND (v.judgment_id IS NULL OR v.body_sha256 <> j.text_sha256 OR v.model <> %s)",
                (part, model),
            )
            found.update(cur.fetchall())
        return found

    def load(self, cur, batch):
        ids = [c.judgment_id for c in batch]
        cur.execute(
            "SELECT id, cbosa_id, docket_number, judgment_type, kind, court, judgment_date,"
            " thesis, sentence, reasons, dissenting_opinion, text_sha256"
            " FROM judgments WHERE id IN %s",
            (ids,),
        )
        rows = {row[0]: row[1:] for row in cur.fetchall()}
        symbols = _lists(
            cur, "SELECT judgment_id, code FROM judgment_symbols WHERE judgment_id IN %s", ids
        )
        keywords = _lists(
            cur, "SELECT judgment_id, keyword FROM judgment_keywords WHERE judgment_id IN %s", ids
        )
        cur.execute(
            "SELECT DISTINCT judgment_id FROM judgment_publications WHERE judgment_id IN %s",
            (ids,),
        )
        published = {row[0] for row in cur.fetchall()}
        for c in batch:
            if c.judgment_id not in rows:
                continue  # usunięte w międzyczasie - następny przebieg skasuje wektory
            (
                cbosa_id,
                docket,
                judgment_type,
                kind,
                court,
                when,
                thesis,
                sentence,
                reasons,
                dissent,
                sha,
            ) = rows[c.judgment_id]
            meta = {
                "cbosa_id": cbosa_id,
                "docket": docket,
                "kind": kind,
                "court": court,
                "is_nsa": court == NSA_COURT,
                "date": when.isoformat(),
                "year": when.year,
                "symbols": symbols.get(c.judgment_id, []),
                "keywords": keywords.get(c.judgment_id, []),
            }
            title = f"{docket} - {judgment_type} z {when.isoformat()}"
            parts = (thesis, sentence, reasons, dissent)
            body = judgment_body(*parts)
            if not self.summaries or full_chunks(kind, court, thesis, c.judgment_id in published):
                chunks = self.chunker.chunk(c.judgment_id, body, title, meta)
            else:
                chunks = self.chunker.summary(c.judgment_id, body, parts, title, meta)
            yield c, cbosa_id, sha, chunks


def full_chunks(kind, court, thesis, published):
    """With summaries: whole text in chunks for the judgments worth quoting paragraph by
    paragraph (resolutions, with a thesis or published, NSA judgments); one summary vector
    for the rest (WSA judgments and all orders, ~2M judgments)."""
    return (
        kind == "uchwala" or bool(thesis) or published or (kind == "wyrok" and court == NSA_COURT)
    )


def _lists(cur, sql, ids):
    out = {}
    cur.execute(sql, (ids,))
    for judgment_id, value in cur.fetchall():
        out.setdefault(judgment_id, []).append(value)
    return out
