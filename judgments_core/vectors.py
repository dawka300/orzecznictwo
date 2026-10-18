"""Interface of the separate vector database (not chosen yet; nothing implemented).

MariaDB on the hosting holds judgments; vectors go to a dedicated store
elsewhere. Code that embeds or searches talks only to this interface, so the
choice (Qdrant, pgvector on another server, ...) stays swappable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Chunk:
    """A piece of one judgment's text with its place in it (to link back)."""

    judgment_id: str
    number: int
    text: str
    start: int  # pozycja w pełnym tekście (znaki)
    part: str = ""  # np. "sentencja", "uzasadnienie"
    vector: list | None = None
    meta: dict = field(default_factory=dict)  # filtry: sąd, data, rodzaj ...


@dataclass
class Hit:
    chunk: Chunk
    score: float


class VectorStore(Protocol):
    # Wektory z różnych modeli nie są porównywalne: magazyn pamięta model,
    # a wyszukiwanie innym modelem ma się zatrzymać z błędem.
    model_name: str

    def upsert(self, chunks: list) -> None: ...

    def delete_judgment(self, judgment_id: str) -> None: ...

    def search(self, vector: list, k: int = 10, filters: dict | None = None) -> list: ...
