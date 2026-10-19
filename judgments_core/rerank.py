"""Rerankers: a cross-encoder reads the question and each passage together and scores them.

Vector search compares two vectors computed separately; a cross-encoder sees
both texts at once, so it ranks passages more precisely, but it is slower
(one model run per passage), so it only reorders the retriever's candidates.

LocalReranker = sentence-transformers CrossEncoder in this process (CPU, model
BAAI/bge-reranker-v2-m3: multilingual like bge-m3, ~2.3 GB, loads on first
use). Setting <PREFIKS>_RERANKER: "" = none (the hosting), "local".
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)

# Ile tokenów pary (pytanie + fragment) czyta model; dłuższy fragment jest ucinany.
MAX_TOKENS = 512


class RerankError(RuntimeError):
    """The reranker can't run (no package, no model); the message is for the user."""


class Reranker(ABC):
    name = ""

    def __init__(self, model):
        self.model = model

    @abstractmethod
    def scores(self, question, texts):
        """A score per text (higher = answers the question better)."""

    def prepare(self):
        """Load the model now (the first question doesn't wait)."""

    def prepare_in_background(self):
        def prepare():
            try:
                self.prepare()
            except RerankError as e:
                log.warning("reranker: %s", e)

        threading.Thread(target=prepare, name="reranker", daemon=True).start()


class LocalReranker(Reranker):
    """sentence-transformers CrossEncoder in this process.

    load: (model, device) -> object with predict(pairs, ...); for tests.
    """

    name = "local"

    def __init__(self, model, *, device="cpu", load=None):
        super().__init__(model)
        self.device = device
        self._load = load
        self._model = None
        self._lock = threading.Lock()

    def prepare(self):
        with self._lock:
            if self._model is not None:
                return
            started = time.monotonic()
            logging.getLogger("httpx").setLevel(logging.WARNING)
            try:
                self._model = (self._load or _cross_encoder)(self.model, self.device)
            except ImportError as e:
                raise RerankError(
                    f"brak sentence-transformers ({e}); pip install -r requirements-dev.txt"
                ) from e
            except Exception as e:  # brak modelu, brak sieci przy pierwszym pobraniu ...
                raise RerankError(f"nie można wczytać modelu {self.model}: {e}") from e
            log.info("reranker %s wczytany w %.0f s", self.model, time.monotonic() - started)

    def scores(self, question, texts):
        if not texts:
            return []
        self.prepare()
        with self._lock:  # jedno liczenie naraz: i tak zajmuje wszystkie rdzenie
            result = self._model.predict(
                [(question, t) for t in texts], batch_size=16, show_progress_bar=False
            )
        return [float(x) for x in result]


def _cross_encoder(model, device):
    from sentence_transformers import CrossEncoder  # opcjonalna zależność

    return CrossEncoder(model, device=device, max_length=MAX_TOKENS)


RERANKERS = ("local",)


def make_reranker(settings):
    """The settings' reranker, or None ("" = none)."""
    name = settings.reranker.strip()
    if not name:
        return None
    if name not in RERANKERS:
        raise ValueError(f"nieznany reranker: {name} ({' / '.join(RERANKERS)} albo puste)")
    return LocalReranker(settings.rerank_model)
