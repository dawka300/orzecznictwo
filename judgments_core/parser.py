"""Parent of the project parsers: a raw record's body -> the project's DTOs.

Pure code, no database (judgments_core/tables.py writes the DTOs). A project's
child (HudocParser, JuddgesParser ...) says how to read its source's records;
load() decodes the stored body (JSON by default, an HTML source overrides it).
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod


class Parser(ABC):
    def load(self, body):
        """The stored body as data (raw_records.body is JSON for most sources)."""
        return json.loads(body)

    @abstractmethod
    def parse(self, data):
        """Loaded data -> the project's main DTO; ValueError when it can't be used."""
