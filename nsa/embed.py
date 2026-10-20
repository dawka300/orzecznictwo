"""NSA embedding job: judgments -> chunks -> vectors (python -m nsa wektory), child of EmbedJob.

The whole text (thesis, operative part, reasoning, dissenting opinion -
nsa.parser.judgment_body) is chunked by NsaChunker, with filterable metadata
(court, kind, year, case symbols, keywords). ~1.8M judgments are too many
to embed at once, so the order says what is searchable first: resolutions,
judgments with a thesis or published in the official collection, NSA
judgments, WSA judgments with reasoning, then the rest (mostly procedural
orders). judgment_vectors records what is done; the loop, resuming and
progress are common (judgments_core/embed.py).
"""

from __future__ import annotations

from judgments_core.embed import EmbedJob
from nsa.chunker import NsaChunker
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

CANDIDATES_SQL = f"""
SELECT j.id, j.cbosa_id, j.text_sha256, j.text_length FROM judgments j
LEFT JOIN judgment_vectors v ON v.judgment_id = j.id
WHERE v.judgment_id IS NULL OR v.body_sha256 <> j.text_sha256 OR v.model <> %s
ORDER BY CASE
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

    def candidates(self, cur, model):
        cur.execute(CANDIDATES_SQL, (model,))
        return cur.fetchall()

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
            body = judgment_body(thesis, sentence, reasons, dissent)
            yield c, cbosa_id, sha, self.chunker.chunk(c.judgment_id, body, title, meta)


def _lists(cur, sql, ids):
    out = {}
    cur.execute(sql, (ids,))
    for judgment_id, value in cur.fetchall():
        out.setdefault(judgment_id, []).append(value)
    return out
