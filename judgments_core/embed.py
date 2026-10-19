"""Parent of the project embedding jobs: judgments -> chunks -> vectors (command wektory).

The common part: the collection, the loop over groups of ~group_chunks
chunks (embedded in batches, old points of those judgments deleted, new ones
written, then the project's tracking table updated - so a pass can be killed
anytime and the next one continues), removing vectors of deleted judgments,
the time budget and the progress in sync_state (read by pages: read_progress).

A project's child says which judgments to embed and in what order
(candidates), how to turn them into chunks with filterable metadata
(load), its chunker and its tables (tracking_table, tracking_key,
tracking_doc, parent_table; tracking columns as in db.MariaDbStore.vectors_*).
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import ClassVar

from judgments_core.db import MariaDbStore, read_state
from judgments_core.source import utc_now

log = logging.getLogger(__name__)

PROGRESS_EVERY_S = 60
# "liczy" bez zapisu od tylu sekund = przebieg zabity (Ctrl+C zapisuje "przerwano").
STALE_AFTER_S = 600
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass
class EmbedResult:
    judgments: int = 0
    chunks: int = 0
    groups: int = 0
    removed: int = 0  # usunięte z bazy -> ich wektory skasowane
    remaining: int = 0  # zostało do policzenia po tym przebiegu
    finished: bool = False
    stopped_by: str = ""  # "limit" / "czas"


@dataclass
class Candidate:
    """A judgment to embed: its id, the document (version) to chunk, sha256, text length."""

    judgment_id: int
    document_id: object
    sha256: str
    length: int


class EmbedJob(ABC):
    chunker_class = None  # klasa potomna judgments_core.chunker.Chunker
    payload_indexes: ClassVar[dict] = {}  # pola payloadu z indeksem w Qdrancie (filtry)
    progress_key = ""  # klucz w sync_state, np. "etpc:wektory"
    tracking_table = ""  # np. ruling_vectors
    tracking_key = ""  # np. ruling_id
    tracking_doc = ""  # np. itemid
    parent_table = ""  # np. rulings
    group_chunks = 256  # tyle fragmentów naraz do bazy wektorów i tabeli śledzenia
    load_batch = 5  # tyle orzeczeń naraz czytanych z bazy

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
        self.store = MariaDbStore(conn)
        self.embedder = embedder
        self.vectors = vectors
        self.chunker = self.chunker_class()
        self.limit = limit
        self.time_budget_s = time_budget_s
        self.from_start = from_start
        self.batch_size = batch_size
        self.clock = clock
        self.result = EmbedResult()

    @abstractmethod
    def candidates(self, cur, model):
        """Candidates missing or changed (sha256, model) in the tracking table, in embedding order."""

    @abstractmethod
    def load(self, cur, batch):
        """[(Candidate, document id, sha256, [Chunk with meta])] for a batch of Candidates."""

    def run(self):
        started = self.clock()
        self.vectors.ensure_collection(self.embedder.dimension, self.payload_indexes)
        if self.from_start:
            self.store.vectors_forget(self.tracking_table, self.tracking_key)
        self._remove_deleted()
        with self.conn.cursor() as cur:
            todo = [Candidate(*row) for row in self.candidates(cur, self.embedder.model)]
        self.conn.commit()
        done, chunks = self.store.vectors_done(self.tracking_table, self.embedder.model)
        log.info("do policzenia: %d (model %s)", len(todo), self.embedder.model)
        progress = Progress(
            self.store,
            self.progress_key,
            self.embedder.model,
            total=done + len(todo),
            done=done,
            chunks=chunks,
            remaining_chars=int(sum(c.length for c in todo)),
        )
        if self.limit is not None:
            todo = todo[: self.limit]

        r = self.result
        position, last_report, chars, text_chars = 0, started, 0, 0
        try:
            progress.save("liczy")
            while position < len(todo):
                if self.time_budget_s and self.clock() - started >= self.time_budget_s:
                    r.stopped_by = "czas"
                    break
                group_start = position
                loaded, position = self._load_group(todo, position)
                group_chars = int(sum(c.length for c in todo[group_start:position]))
                text_chars += group_chars
                chunks = [c for *_, cs in loaded for c in cs]
                texts = [self.chunker.embedding_input(c) for c in chunks]
                for i in range(0, len(texts), self.batch_size):
                    vectors = self.embedder.embed(texts[i : i + self.batch_size])
                    for chunk, vector in zip(chunks[i : i + self.batch_size], vectors):
                        chunk.vector = vector
                self._save(loaded, chunks)
                r.judgments += len(loaded)
                r.chunks += len(chunks)
                r.groups += 1
                chars += sum(len(t) for t in texts)
                now = self.clock()
                # Tempo w znakach całych tekstów, jak remaining_chars (bez pominiętych części).
                progress.advance(
                    len(loaded), len(chunks), group_chars, text_chars / (now - started or 1)
                )
                progress.save("liczy")
                if now - last_report >= PROGRESS_EVERY_S:
                    log.info(
                        "postęp: %d/%d, %d fragmentów, ~%.0f tokenów/s",
                        position,
                        len(todo),
                        r.chunks,
                        chars / 4 / (now - started or 1),
                    )
                    last_report = now
            r.remaining = len(todo) - position
            if self.limit is not None and not r.stopped_by:
                r.stopped_by = "limit" if r.judgments >= self.limit else ""
            r.finished = not r.stopped_by and r.remaining == 0
        finally:
            progress.save("gotowe" if progress.done >= progress.total else "przerwano")
        return r

    def _remove_deleted(self):
        gone = self.store.vectors_orphans(self.tracking_table, self.tracking_key, self.parent_table)
        if gone:
            self.vectors.delete_judgments(gone)
            self.store.vectors_forget(self.tracking_table, self.tracking_key, gone)
            log.info("usunięto wektory %d orzeczeń, których nie ma już w bazie", len(gone))
        self.result.removed = len(gone)

    def _load_group(self, todo, position):
        """Judgments from todo[position:] until group_chunks chunks."""
        group, size = [], 0
        while position < len(todo) and size < self.group_chunks:
            batch = todo[position : position + self.load_batch]
            position += len(batch)
            with self.conn.cursor() as cur:
                loaded = list(self.load(cur, batch))
            self.conn.commit()
            group += loaded
            size += sum(len(chunks) for *_, chunks in loaded)
        return group, position

    def _save(self, loaded, chunks):
        # Najpierw stare punkty precz (liczba fragmentów mogła się zmienić),
        # potem nowe, na końcu wpis w MariaDB: przerwanie w środku = powtórka.
        self.vectors.delete_judgments([c.judgment_id for c, *_ in loaded])
        self.vectors.upsert(chunks)
        self.store.vectors_record(
            self.tracking_table,
            self.tracking_key,
            self.tracking_doc,
            [(c.judgment_id, doc, sha, self.embedder.model, len(cs)) for c, doc, sha, cs in loaded],
        )


class Progress:
    """How far the embedding is, as JSON in sync_state (read by read_progress)."""

    def __init__(self, store, key, model, *, total, done, chunks, remaining_chars):
        self.store = store
        self.key = key
        self.model = model
        self.total = total
        self.done = done
        self.chunks = chunks
        self.remaining_chars = remaining_chars
        self.chars_per_s = 0.0
        self.pass_started = _now_text()

    def advance(self, judgments, chunks, chars, chars_per_s):
        self.done += judgments
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
        self.store.set_state(self.key, json.dumps(value))


def read_progress(cur, key, now=None):
    """The last saved progress (dict) or None; "eta_s" = seconds left at the last pace.

    A pass killed without a chance to save ("liczy" not updated for
    STALE_AFTER_S) is reported as "przerwano".
    """
    value = read_state(cur, key)
    if not value:
        return None
    progress = json.loads(value)
    now = now or utc_now()
    updated = datetime.strptime(progress["updated"], TIME_FORMAT).replace(tzinfo=timezone.utc)
    if progress["status"] == "liczy" and (now - updated).total_seconds() > STALE_AFTER_S:
        progress["status"] = "przerwano"
    rate = progress.get("chars_per_s") or 0
    progress["eta_s"] = progress["remaining_chars"] / rate if rate else None
    return progress


def _now_text():
    return utc_now().strftime(TIME_FORMAT)
