"""CBOSA (Centralna Baza Orzeczeń Sądów Administracyjnych) as a Source.

Not written yet - first test queries (see nsa/notepad.md). What is known
before checking, all of it to verify:
- https://orzeczenia.nsa.gov.pl - no JSON API, HTML pages only: a search form
  (court, dates, case number, ...) gives result lists, each judgment has its
  own page /doc/<id>.
- So probably two steps: list pages (ids + dates) and detail pages (full text,
  metadata in a table). Both raw HTML go to raw_records; parsing comes later.
- Check robots.txt and terms; keep the pause (NSA_PAUSE_S) generous.
"""

from __future__ import annotations

BASE_URL = "https://orzeczenia.nsa.gov.pl"


class CbosaSource:
    name = "cbosa"

    def __init__(self, settings, client):
        self.settings = settings
        self.client = client

    def iter_pages(self, cursor):
        # Kursor: najpewniej data orzeczenia + numer strony wyników, np.
        # '{"date": "2024-03-01", "page": 3}' - pobieranie dzień po dniu daje
        # małe, powtarzalne listy i łatwe wznawianie.
        raise NotImplementedError(
            "CbosaSource.iter_pages - najpierw próbne zapytania (nsa/notepad.md)."
        )
