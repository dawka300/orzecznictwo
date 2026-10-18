"""HUDOC (database of the European Court of Human Rights) as a Source.

Not written yet - first test queries (see etpc/notepad.md). What is known
before checking, all of it to verify:
- https://hudoc.echr.coe.int - the site itself calls a JSON search endpoint
  (app/query/results with query/select/sort/start/length parameters) that
  returns metadata (itemid like 001-12345, docname, appno, date, language,
  importance, articles, ...). Not an official, documented API.
- Full texts come separately per itemid (HTML conversion of the Word document).
- Many documents per case: judgments, decisions, communicated cases, in English,
  French and translations (also Polish) - choose what to keep.
"""

from __future__ import annotations

BASE_URL = "https://hudoc.echr.coe.int"


class HudocSource:
    name = "hudoc"

    def __init__(self, settings, client):
        self.settings = settings
        self.client = client

    def iter_pages(self, cursor):
        # Kursor: najpewniej pozycja w wynikach posortowanych po dacie
        # publikacji, np. '{"since": "2024-01-01", "start": 500}'.
        raise NotImplementedError(
            "HudocSource.iter_pages - najpierw próbne zapytania (etpc/notepad.md)."
        )
