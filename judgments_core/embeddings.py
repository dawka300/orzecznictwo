"""How a text becomes a vector: interchangeable embedders (strategy pattern).

- HttpEmbedder - an OpenAI-compatible server (LM Studio, llama.cpp server ...):
  POST <url> {"model": ..., "input": [texts]} -> {"data": [{"index", "embedding"}]};
  fast on a GPU, so it computes the bulk (python -m <projekt> wektory);
- LocalEmbedder - the same model in this process (sentence-transformers on
  the CPU); slow for the bulk, but a query is one sentence, so search pages
  and the MCP server don't need LM Studio running;
- FallbackEmbedder - a chain of the above: the next one takes over when the
  current fails (sentence-transformers not installed, LM Studio off).

Both must be the same model (bge-m3 in LM Studio = BAAI/bge-m3): query
vectors are compared with the vectors already in Qdrant. Runs where the
vectors are (a PC), not on the hosting.
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod

import requests

log = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    pass


class Embedder(ABC):
    name = ""

    def __init__(self, model):
        self.model = model
        self._dimension = None

    @abstractmethod
    def embed(self, texts):
        """Vectors for texts, in the same order; raises EmbeddingError."""

    def prepare(self):
        """Get ready before the first text (load a model); nothing by default."""

    @property
    def dimension(self):
        if self._dimension is None:
            self._dimension = len(self.embed(["test"])[0])
        return self._dimension

    def describe(self):
        """For the log: what computes the vectors."""
        return f"{self.name} {self.model}"


class HttpEmbedder(Embedder):
    name = "http"

    def __init__(self, url, model, *, timeout_s=600.0, retries=3, session=None, sleep=time.sleep):
        super().__init__(model)
        self.url = url
        self.timeout_s = timeout_s
        self.retries = retries
        self.session = session or requests.Session()
        self.sleep = sleep

    def embed(self, texts):
        if not texts:
            return []
        body = {"model": self.model, "input": list(texts), "encoding_format": "float"}
        for attempt in range(self.retries + 1):
            try:
                response = self.session.post(self.url, json=body, timeout=self.timeout_s)
                if response.status_code < 500:
                    break
                problem = f"HTTP {response.status_code}: {response.text[:200]}"
            except requests.RequestException as e:
                problem = f"{type(e).__name__}: {e}"
            if attempt == self.retries:
                raise EmbeddingError(f"serwer embeddingów {self.url} nie odpowiada ({problem})")
            wait = 10 * 2**attempt
            log.warning("serwer embeddingów: %s; ponawiam za %d s", problem, wait)
            self.sleep(wait)
        if response.status_code != 200:
            raise EmbeddingError(
                f"serwer embeddingów: HTTP {response.status_code}: {response.text[:300]}"
            )
        data = sorted(response.json()["data"], key=lambda d: d["index"])
        if len(data) != len(texts):
            raise EmbeddingError(
                f"serwer embeddingów zwrócił {len(data)} wektorów zamiast {len(texts)}"
            )
        return [d["embedding"] for d in data]

    def describe(self):
        return f"{self.model} ({self.url})"


class LocalEmbedder(Embedder):
    """sentence-transformers in this process; the model loads on first use (~10-20 s, ~2.3 GB RAM).

    Instances with the same model and device share one loaded model and its lock
    (several projects' pages in one process: one copy in RAM).

    load: model name -> object with encode(texts, ...); for tests (not shared).
    """

    name = "local"

    def __init__(self, model, *, device="cpu", load=None):
        super().__init__(model)
        self.device = device
        self._load = load
        self._model = None
        self._lock = threading.Lock() if load else _shared_lock((model, device))

    def prepare(self):
        with self._lock:
            if self._model is not None:
                return
            key = (self.model, self.device)
            if self._load is None and key in _SHARED:
                self._model = _SHARED[key]  # wczytany już przez inną instancję
                return
            started = time.monotonic()
            # huggingface_hub sprawdza pliki modelu zapytaniami HEAD - bez nich w logu
            logging.getLogger("httpx").setLevel(logging.WARNING)
            try:
                self._model = (self._load or _sentence_transformer)(self.model, self.device)
                if self._load is None:
                    _SHARED[key] = self._model
            except ImportError as e:
                raise EmbeddingError(
                    f"brak sentence-transformers ({e}); pip install -r requirements-dev.txt"
                ) from e
            except Exception as e:  # brak modelu, brak sieci przy pierwszym pobraniu ...
                raise EmbeddingError(f"nie można wczytać modelu {self.model}: {e}") from e
            log.info("model %s wczytany w %.0f s", self.model, time.monotonic() - started)

    def embed(self, texts):
        if not texts:
            return []
        self.prepare()
        with self._lock:  # jedno liczenie naraz: i tak zajmuje wszystkie rdzenie
            vectors = self._model.encode(
                list(texts),
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        return [[float(x) for x in v] for v in vectors]


# Wczytane modele sentence-transformers w tym procesie: (model, urządzenie) -> model.
_SHARED = {}
_SHARED_LOCKS = {}
_SHARED_GUARD = threading.Lock()


def _shared_lock(key):
    with _SHARED_GUARD:
        return _SHARED_LOCKS.setdefault(key, threading.Lock())


def _sentence_transformer(model, device):
    from sentence_transformers import SentenceTransformer  # opcjonalna zależność

    return SentenceTransformer(model, device=device)


class FallbackEmbedder(Embedder):
    """A chain of embedders: the next one takes over when the current one fails.

    Embedders are created only when reached; the chain doesn't go back while
    the process runs. The last one's error goes to the caller.
    """

    name = "lancuch"

    def __init__(self, factories):
        """factories: [(name, () -> Embedder)] in order."""
        if not factories:
            raise ValueError("pusty łańcuch embedderów")
        self.factories = list(factories)
        self.position = 0
        self.current = self.factories[0][1]()
        self._switch_lock = threading.Lock()
        super().__init__(self.current.model)

    def prepare(self):
        self._try(lambda e: e.prepare())

    def embed(self, texts):
        return self._try(lambda e: e.embed(texts))

    def describe(self):
        return self.current.describe()

    def _try(self, action):
        while True:
            current = self.current
            try:
                return action(current)
            except EmbeddingError as e:
                with self._switch_lock:
                    if current is not self.current:
                        continue  # inny wątek już przełączył
                    if self.position == len(self.factories) - 1:
                        raise
                    self.position += 1
                    log.warning(
                        "embeddingi: %s nie działa (%s) - przechodzę na %s",
                        current.name,
                        e,
                        self.factories[self.position][0],
                    )
                    self.current = self.factories[self.position][1]()
                    self.model = self.current.model


# Strategie po nazwie (ustawienie <PREFIKS>_QUERY_EMBEDDERS).
EMBEDDERS = ("local", "http")


def make_embedder(settings, names=None, *, timeout_s=None, retries=3):
    """One embedder by name, or a FallbackEmbedder for several names in order.

    names: default settings.query_embedders ("local,http"); the bulk job asks for "http".
    """
    names = [n.strip() for n in (names or settings.query_embedders).split(",") if n.strip()]
    unknown = [n for n in names if n not in EMBEDDERS]
    if unknown or not names:
        raise ValueError(
            f"nieznany embedder: {', '.join(unknown) or '(pusto)'} ({' / '.join(EMBEDDERS)})"
        )
    timeout_s = settings.embed_timeout_s if timeout_s is None else timeout_s

    def factory(name):
        if name == "local":
            return lambda: LocalEmbedder(settings.local_model)
        return lambda: HttpEmbedder(
            settings.embed_url, settings.embed_model, timeout_s=timeout_s, retries=retries
        )

    if len(names) == 1:
        return factory(names[0])()
    return FallbackEmbedder([(n, factory(n)) for n in names])
