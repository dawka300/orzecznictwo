"""Rulings -> chunks -> embeddings -> vector store, resumable (python -m etpc wektory).

One version per ruling (English, else French; translations are left out):
it is chunked (etpc/chunker.py), embedded in batches and written to the
vector store with filterable metadata. ruling_vectors records what is done,
so a pass can be killed anytime; the next one embeds what is missing or
changed (body_sha256, model) and removes vectors of deleted rulings.
Judgments go first (key cases first, newest first), then decisions.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import date

from etpc.chunker import chunk_document, embedding_input
from judgments_core.db import MariaDbStore
from judgments_core.source import utc_now

log = logging.getLogger(__name__)

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
# Tyle fragmentów naraz idzie do bazy wektorów i do ruling_vectors (jedna transakcja).
GROUP_CHUNKS = 256
PROGRESS_EVERY_S = 60
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


@dataclass
class EmbedResult:
    rulings: int = 0
    chunks: int = 0
    groups: int = 0
    removed: int = 0  # orzeczenia usunięte z bazy -> ich wektory skasowane
    remaining: int = 0  # zostało do policzenia po tym przebiegu
    finished: bool = False
    stopped_by: str = ""  # "limit" / "czas"


class EmbedJob:
    def __init__(
        self,
        conn,
        embedder,
        vectors,
        *,
        limit=None,
        time_budget_s=0,
        from_start=False,
        batch_size=32,
        clock=time.monotonic,
    ):
        self.conn = conn
        self.embedder = embedder
        self.vectors = vectors
        self.limit = limit
        self.time_budget_s = time_budget_s
        self.from_start = from_start
        self.batch_size = batch_size
        self.clock = clock
        self.result = EmbedResult()

    def run(self):
        started = self.clock()
        self.vectors.ensure_collection(self.embedder.dimension, PAYLOAD_INDEXES)
        if self.from_start:
            with self.conn.cursor() as cur:
                cur.execute("DELETE FROM ruling_vectors")
            self.conn.commit()
        self._remove_deleted()
        with self.conn.cursor() as cur:
            cur.execute(CANDIDATES_SQL, (self.embedder.model,))
            todo = cur.fetchall()
            cur.execute(
                "SELECT COUNT(*), COALESCE(SUM(chunks), 0) FROM ruling_vectors WHERE model = %s",
                (self.embedder.model,),
            )
            done_before, chunks_before = cur.fetchone()
        self.conn.commit()
        log.info("orzeczeń do policzenia: %d (model %s)", len(todo), self.embedder.model)
        progress = Progress(
            self.conn,
            self.embedder.model,
            total=int(done_before) + len(todo),
            done=int(done_before),
            chunks=int(chunks_before),
            remaining_chars=int(sum(row[3] for row in todo)),
        )
        if self.limit is not None:
            todo = todo[: self.limit]

        r = self.result
        position, last_report, chars, body_chars = 0, started, 0, 0
        try:
            progress.save("liczy")
            while position < len(todo):
                if self.time_budget_s and self.clock() - started >= self.time_budget_s:
                    r.stopped_by = "czas"
                    break
                group_start = position
                rulings, position = self._load_group(todo, position)
                group_chars = int(sum(row[3] for row in todo[group_start:position]))
                body_chars += group_chars
                chunks = [c for _, _, _, cs in rulings for c in cs]
                texts = [embedding_input(c) for c in chunks]
                for i in range(0, len(texts), self.batch_size):
                    for chunk, vector in zip(
                        chunks[i : i + self.batch_size],
                        self.embedder.embed(texts[i : i + self.batch_size]),
                    ):
                        chunk.vector = vector
                self._save(rulings, chunks)
                r.rulings += len(rulings)
                r.chunks += len(chunks)
                r.groups += 1
                chars += sum(len(t) for t in texts)
                now = self.clock()
                # Tempo w znakach całych tekstów, jak remaining_chars (bez nagłówków itp.).
                progress.advance(
                    len(rulings), len(chunks), group_chars, body_chars / (now - started or 1)
                )
                progress.save("liczy")
                if now - last_report >= PROGRESS_EVERY_S:
                    log.info(
                        "postęp: %d/%d orzeczeń, %d fragmentów, ~%.0f tokenów/s",
                        position,
                        len(todo),
                        r.chunks,
                        chars / 4 / (now - started or 1),
                    )
                    last_report = now
            r.remaining = len(todo) - position
            if self.limit is not None and not r.stopped_by:
                r.stopped_by = "limit" if r.rulings >= self.limit else ""
            r.finished = not r.stopped_by and r.remaining == 0
        finally:
            done = progress.done >= progress.total
            progress.save("gotowe" if done else "przerwano")
        return r

    def _remove_deleted(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT v.ruling_id FROM ruling_vectors v LEFT JOIN rulings r ON r.id = v.ruling_id WHERE r.id IS NULL"
            )
            gone = [row[0] for row in cur.fetchall()]
            if gone:
                self.vectors.delete_judgments(gone)
                cur.execute("DELETE FROM ruling_vectors WHERE ruling_id IN %s", (gone,))
        self.conn.commit()
        if gone:
            log.info("usunięto wektory %d orzeczeń, których nie ma już w bazie", len(gone))
        self.result.removed = len(gone)

    def _load_group(self, todo, position):
        """Rulings from todo[position:] until GROUP_CHUNKS chunks: [(id, itemid, sha, chunks)]."""
        group, size = [], 0
        while position < len(todo) and size < GROUP_CHUNKS:
            batch = todo[position : position + 5]
            position += len(batch)
            for ruling_id, itemid, sha, chunks in self._chunk_rulings(batch):
                group.append((ruling_id, itemid, sha, chunks))
                size += len(chunks)
        return group, position

    def _chunk_rulings(self, batch):
        ruling_ids = [row[0] for row in batch]
        document_ids = [row[1] for row in batch]
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT d.id, d.ruling_id, d.itemid, d.language, d.title, d.body, d.body_sha256,"
                " r.kind, r.formation, r.ruling_date, r.importance"
                " FROM ruling_documents d JOIN rulings r ON r.id = d.ruling_id WHERE d.id IN %s",
                (document_ids,),
            )
            documents = {row[0]: row for row in cur.fetchall()}
            respondents, articles, violations = _lists(cur, ruling_ids)
        for ruling_id, document_id, *_ in batch:
            _, _, itemid, language, title, body, sha, kind, formation, ruling_date, importance = (
                documents[document_id]
            )
            meta = {
                "ruling_id": ruling_id,
                "itemid": itemid,
                "language": language,
                "kind": kind,
                "formation": formation,
                "date": ruling_date.isoformat()
                if isinstance(ruling_date, date)
                else str(ruling_date),
                "year": ruling_date.year,
                "importance": importance,
                "respondents": respondents.get(ruling_id, []),
                "articles": articles.get(ruling_id, []),
                "violations": violations.get(ruling_id, []),
            }
            yield ruling_id, itemid, sha, chunk_document(ruling_id, body, title, meta)

    def _save(self, rulings, chunks):
        # Najpierw stare punkty precz (liczba fragmentów mogła się zmienić),
        # potem nowe, na końcu wpis w MariaDB: przerwanie w środku = powtórka.
        self.vectors.delete_judgments([ruling_id for ruling_id, _, _, _ in rulings])
        self.vectors.upsert(chunks)
        now = utc_now().replace(tzinfo=None, microsecond=0)
        with self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO ruling_vectors (ruling_id, itemid, body_sha256, model, chunks, embedded_at)"
                " VALUES (%s, %s, %s, %s, %s, %s)"
                " ON DUPLICATE KEY UPDATE itemid = VALUES(itemid), body_sha256 = VALUES(body_sha256),"
                " model = VALUES(model), chunks = VALUES(chunks), embedded_at = VALUES(embedded_at)",
                [
                    (rid, itemid, sha, self.embedder.model, len(cs), now)
                    for rid, itemid, sha, cs in rulings
                ],
            )
        self.conn.commit()


class Progress:
    """How far the embedding is, as JSON in sync_state (read by read_progress)."""

    def __init__(self, conn, model, *, total, done, chunks, remaining_chars):
        self.store = MariaDbStore(conn)
        self.model = model
        self.total = total
        self.done = done
        self.chunks = chunks
        self.remaining_chars = remaining_chars
        self.chars_per_s = 0.0
        self.pass_started = _now_text()

    def advance(self, rulings, chunks, chars, chars_per_s):
        self.done += rulings
        self.chunks += chunks
        self.remaining_chars = max(0, self.remaining_chars - chars)
        self.chars_per_s = chars_per_s

    def save(self, status):
        value = {
            "status": status,
            "model": self.model,
            "total": self.total,
            "done": self.done,
            "chunks": self.chunks,
            "remaining_chars": self.remaining_chars,
            "chars_per_s": round(self.chars_per_s),
            "pass_started": self.pass_started,
            "updated": _now_text(),
        }
        self.store.set_state(PROGRESS_KEY, json.dumps(value))


def read_progress(cur):
    """The last saved progress (dict) or None; "eta_s" = seconds left at the last pace."""
    cur.execute("SELECT value FROM sync_state WHERE `key` = %s", (PROGRESS_KEY,))
    row = cur.fetchone()
    if not row:
        return None
    progress = json.loads(row[0])
    rate = progress.get("chars_per_s") or 0
    progress["eta_s"] = progress["remaining_chars"] / rate if rate else None
    return progress


def _now_text():
    return utc_now().strftime("%Y-%m-%d %H:%M:%S")


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


def embed(conn, embedder, vectors, **options):
    """The project's hook for the wektory command."""
    return EmbedJob(conn, embedder, vectors, **options).run()
