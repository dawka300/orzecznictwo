"""Embeddings from an OpenAI-compatible server (LM Studio, llama.cpp server ...).

POST <url> {"model": ..., "input": [texts]} -> {"data": [{"index", "embedding"}]}.
Runs where the vectors are computed (a PC with a GPU), not on the hosting.
"""

from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    pass


class Embedder:
    def __init__(self, url, model, *, timeout_s=600.0, retries=3, session=None, sleep=time.sleep):
        self.url = url
        self.model = model
        self.timeout_s = timeout_s
        self.retries = retries
        self.session = session or requests.Session()
        self.sleep = sleep
        self._dimension = None

    def embed(self, texts):
        """Vectors for texts, in the same order."""
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

    @property
    def dimension(self):
        if self._dimension is None:
            self._dimension = len(self.embed(["test"])[0])
        return self._dimension
