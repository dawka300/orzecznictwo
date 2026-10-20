"""What a project implements: a source of raw records, page by page.

Source is the parent class; each project (NSA, ETPC, ...) writes its child
(HudocSource, JuddgesSource ...). The importer only knows this interface, so
the download loop, resuming and saving are shared.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0)


@dataclass
class RawRecord:
    """One record exactly as the source gave it (JSON or HTML text).

    Stored first, parsed later: the project's DTOs and tables are derived from
    raw records, so changing a parser never needs a re-download.
    """

    source_id: str
    body: str
    content_type: str = "application/json"
    url: str | None = None
    fetched_at: datetime = field(default_factory=utc_now)

    @property
    def sha256(self):
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()


@dataclass
class Page:
    """Records of one page plus the cursor to resume *after* this page.

    The cursor is an opaque string chosen by the source (a page number, a date,
    JSON with both ...); the importer only stores and hands it back.
    """

    records: list
    cursor: str | None


class Source(ABC):
    """A source of raw records; client = PoliteClient (its Transport strategy decides how)."""

    name = ""  # krótka nazwa, np. "hudoc" - kolumna `source` w raw_records

    def __init__(self, settings=None, client=None):
        self.settings = settings
        self.client = client

    @abstractmethod
    def iter_pages(self, cursor: str | None) -> Iterator[Page]:
        """Yield pages starting after `cursor` (None = from the beginning)."""

    def close(self):
        """Release the client's transport (browser, connections)."""
        if self.client is not None:
            self.client.close()
