"""Parent of the project semantic searches: a query embedded and matched in the vector store.

The common part: the query goes to the model as typed (bge-m3 is
multilingual), search operators (quotes, -word, word*) are dropped; the
best passage of each closest judgment comes back; "similar judgments" = the
mean vector of a judgment's chunks (of similar_parts if it has them).
The query vector comes from the settings' query_embedders (by default the
model in this process, then LM Studio - judgments_core/embeddings.py). No
embedder or Qdrant raises SemanticUnavailable, so a page can fall back to
FULLTEXT.

A project's child turns its search form into vector filters (payload_filters)
and says which payload field holds the document id (document_field).
"""

from __future__ import annotations

import logging
import re
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass

from judgments_core.embeddings import make_embedder
from judgments_core.qdrant import CollectionMissing, QdrantStore, collection_name

log = logging.getLogger(__name__)
_OPERATORS = re.compile(r'(?:^|\s)-\S+|["*]')
# Stała k w RRF (reciprocal rank fusion): wynik = suma 1 / (k + miejsce).
RRF_K = 60
# Ile ostatnich wektorów zapytań pamiętać (wyszukiwarka i fragmenty pytają o ten sam tekst).
CACHED_QUERIES = 32


class SemanticUnavailable(RuntimeError):
    """LM Studio or Qdrant is not running; the message is for the user."""


@dataclass
class Passage:
    judgment_id: int
    score: float
    document: str  # wersja/dokument, z którego jest fragment (np. itemid)
    language: str
    start: int
    length: int
    context: str
    part: str


class SemanticSearch(ABC):
    document_field = "document"  # pole payloadu z identyfikatorem dokumentu
    similar_parts = ()  # części do "podobnych" (puste = wszystkie fragmenty)
    embed_command = "wektory"  # polecenie liczące wektory (do komunikatu)

    def __init__(self, embedder, vectors):
        self.embedder = embedder
        self.vectors = vectors
        self._cache = {}
        self._cache_lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings, project, timeout_s=60):
        """The settings' query embedders and the project's Qdrant collection; the
        query model starts loading in the background."""
        semantic = cls(
            make_embedder(settings, timeout_s=timeout_s, retries=0),
            QdrantStore(
                settings.qdrant_url,
                settings.qdrant_collection or collection_name(project, settings.embed_model),
                settings.embed_model,
                timeout_s=timeout_s,
            ),
        )
        semantic.prepare_in_background()
        return semantic

    def prepare_in_background(self):
        """Load the query model in a thread, so the first search doesn't wait for it."""

        def prepare():
            try:
                self.embedder.prepare()
            except RuntimeError as e:
                log.warning("model zapytań: %s", e)

        threading.Thread(target=prepare, name="model-zapytan", daemon=True).start()

    @abstractmethod
    def payload_filters(self, query):
        """The part of the project's search query that the vector store can filter on."""

    def judgments(self, text, query, k=200):
        """Best passage of each of the k judgments closest to text, filtered by query."""
        text = " ".join(_OPERATORS.sub(" ", text).split())
        if not text:
            return []
        try:
            vector = self.query_vector(text)
            hits = self.vectors.search_judgments(vector, k, filters=self.payload_filters(query))
        except RuntimeError as e:
            raise SemanticUnavailable(_why(e, self.embed_command)) from e
        return [self.passage(h) for h in hits]

    def passages_of(self, text, judgment_ids, per_judgment=3):
        """{judgment id: [its passages closest to text, best first]} for these judgments."""
        text = " ".join(_OPERATORS.sub(" ", text).split())
        if not text or not judgment_ids:
            return {}
        try:
            hits = self.vectors.search_judgments(
                self.query_vector(text),
                len(judgment_ids),
                filters={"judgment_id": [str(i) for i in judgment_ids]},
                per_judgment=per_judgment,
            )
        except RuntimeError as e:
            raise SemanticUnavailable(_why(e, self.embed_command)) from e
        found = {}
        for hit in hits:
            p = self.passage(hit)
            found.setdefault(p.judgment_id, []).append(p)
        return found

    def query_vector(self, text):
        """The query's vector, remembered for the next CACHED_QUERIES queries."""
        with self._cache_lock:
            vector = self._cache.pop(text, None)
            if vector is not None:
                self._cache[text] = vector  # na koniec kolejki: najświeższy
                return vector
        vector = self.embedder.embed([text])[0]
        with self._cache_lock:
            self._cache[text] = vector
            while len(self._cache) > CACHED_QUERIES:
                del self._cache[next(iter(self._cache))]
        return vector

    def similar(self, judgment_id, k=10):
        """Judgments closest to the mean vector of this one's chunks."""
        try:
            chunks = self.vectors.vectors_of(judgment_id)
            chosen = [v for part, v in chunks if part in self.similar_parts] or [
                v for _, v in chunks
            ]
            if not chosen:
                return []
            mean = [sum(column) / len(chosen) for column in zip(*chosen)]
            hits = self.vectors.search_judgments(mean, k, exclude={"judgment_id": str(judgment_id)})
        except RuntimeError as e:
            raise SemanticUnavailable(_why(e, self.embed_command)) from e
        return [self.passage(h) for h in hits]

    def passage(self, hit):
        c = hit.chunk
        return Passage(
            judgment_id=int(c.judgment_id),
            score=hit.score,
            document=c.meta.get(self.document_field, ""),
            language=c.meta.get("language", ""),
            start=c.start,
            length=c.meta.get("length", 0),
            context=c.meta.get("context", ""),
            part=c.part,
        )


def _why(error, command):
    if isinstance(error, CollectionMissing):
        return f"Wektory nie są jeszcze policzone ({command}) - pokazuję wyniki pełnotekstowe."
    return (
        "Wyszukiwanie znaczeniowe jest niedostępne (model embeddingów"
        f" albo Qdrant nie działa: {error}). Pokazuję wyniki pełnotekstowe."
    )


def rrf(rankings, k=RRF_K):
    """Reciprocal rank fusion: {id: score} from ranked id lists (a high place in both wins)."""
    scores = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1 / (k + rank + 1)
    return scores
