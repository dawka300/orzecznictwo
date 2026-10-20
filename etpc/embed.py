"""ETPC embedding job: rulings -> chunks -> vectors (python -m etpc wektory), child of EmbedJob.

One version per ruling (English, else French; translations are left out),
chunked by EtpcChunker, with filterable metadata (state, articles, year ...).
Judgments go first (key cases first, newest first), then decisions.
ruling_vectors records what is done; the loop, resuming and progress are
common (judgments_core/embed.py).
"""

from __future__ import annotations

from datetime import date

from etpc.chunker import EtpcChunker
from judgments_core.embed import EmbedJob

# Pola payloadu w Qdrancie z indeksem (filtry wyszukiwania).
PAYLOAD_INDEXES = {
    "judgment_id": "keyword",
    "ruling_id": "integer",
    "kind": "keyword",
    "formation": "keyword",
    "language": "keyword",
    "part": "keyword",
    "year": "integer",
    "importance": "integer",
    "respondents": "keyword",
    "articles": "keyword",
    "violations": "keyword",
}
# Postęp w sync_state (strona /statystyki, python -m etpc stan).
PROGRESS_KEY = "etpc:wektory"

CANDIDATES_SQL = """
SELECT c.ruling_id, c.document_id, d.body_sha256, d.body_length FROM (
    SELECT ruling_id, COALESCE(MIN(CASE WHEN language = 'ENG' THEN id END), MIN(id)) AS document_id
    FROM ruling_documents
    WHERE language IN ('ENG', 'FRE') AND body_length > 0
    GROUP BY ruling_id) c
JOIN ruling_documents d ON d.id = c.document_id
JOIN rulings r ON r.id = c.ruling_id
LEFT JOIN ruling_vectors v ON v.ruling_id = c.ruling_id
WHERE v.ruling_id IS NULL OR v.body_sha256 <> d.body_sha256 OR v.model <> %s
ORDER BY r.kind = 'judgment' DESC, COALESCE(r.importance, 9), r.ruling_date DESC, r.id
"""


class EtpcEmbedJob(EmbedJob):
    chunker_class = EtpcChunker
    payload_indexes = PAYLOAD_INDEXES
    progress_key = PROGRESS_KEY
    tracking_table = "ruling_vectors"
    tracking_key = "ruling_id"
    tracking_doc = "itemid"
    parent_table = "rulings"

    def candidates(self, cur, model):
        cur.execute(CANDIDATES_SQL, (model,))
        return cur.fetchall()

    def load(self, cur, batch):
        ruling_ids = [c.judgment_id for c in batch]
        cur.execute(
            "SELECT d.id, d.itemid, d.language, d.title, d.body, d.body_sha256,"
            " r.kind, r.formation, r.ruling_date, r.importance"
            " FROM ruling_documents d JOIN rulings r ON r.id = d.ruling_id WHERE d.id IN %s",
            ([c.document_id for c in batch],),
        )
        documents = {row[0]: row[1:] for row in cur.fetchall()}
        respondents, articles, violations = _lists(cur, ruling_ids)
        for c in batch:
            itemid, language, title, body, sha, kind, formation, ruling_date, importance = (
                documents[c.document_id]
            )
            meta = {
                "ruling_id": c.judgment_id,
                "itemid": itemid,
                "language": language,
                "kind": kind,
                "formation": formation,
                "date": ruling_date.isoformat()
                if isinstance(ruling_date, date)
                else str(ruling_date),
                "year": ruling_date.year,
                "importance": importance,
                "respondents": respondents.get(c.judgment_id, []),
                "articles": articles.get(c.judgment_id, []),
                "violations": violations.get(c.judgment_id, []),
            }
            yield c, itemid, sha, self.chunker.chunk(c.judgment_id, body, title, meta)


def _lists(cur, ruling_ids):
    """Respondent states, concerned articles and violations per ruling."""
    respondents, articles, violations = {}, {}, {}
    cur.execute(
        "SELECT ruling_id, state FROM ruling_respondents WHERE ruling_id IN %s", (ruling_ids,)
    )
    for ruling_id, state in cur.fetchall():
        respondents.setdefault(ruling_id, []).append(state)
    cur.execute(
        "SELECT ruling_id, relation, article FROM ruling_articles"
        " WHERE ruling_id IN %s AND relation IN ('concerned', 'violation')",
        (ruling_ids,),
    )
    for ruling_id, relation, article in cur.fetchall():
        target = articles if relation == "concerned" else violations
        target.setdefault(ruling_id, []).append(article)
    return respondents, articles, violations
