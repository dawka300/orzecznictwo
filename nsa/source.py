"""CBOSA (Centralna Baza Orzeczeń Sądów Administracyjnych) as a Source: judgment pages by id.

robots.txt of orzeczenia.nsa.gov.pl allows the judgment pages /doc/<ID> but not
the search and result lists, the only place the ids come from (nsa/notepad.md).
So this source does not search: it reads ids from files in ID_DIR and its
subdirectories (collected by the user with narzedzia/cbosa_id.py or
narzedzia/cbosa.postman_collection.json, or any text with /doc/<ID> links or
bare ids) and fetches each page as a raw HTML record.

The queue is "ids in the files minus ids already fetched from CBOSA"
(NsaImporter gives the source that check), so there is no cursor: a pass
killed anywhere, or a page that came back empty, is simply tried again next
time. Parsing: nsa/cbosa.py.
"""

from __future__ import annotations

import logging
from pathlib import Path

from judgments_core.http import SourceError
from judgments_core.source import Page, RawRecord, Source
from nsa.cbosa import DOC_URL, ids_in, is_judgment_page

log = logging.getLogger(__name__)

ID_DIR = Path(__file__).resolve().parent / "database" / "cbosa-id"
# Serwer czasem odpowiada 200 z pustą stroną - tyle prób, potem następne orzeczenie.
EMPTY_RETRIES = 3
# Najkrótsza przerwa między stronami (NSA_PAUSE_S może ją wydłużyć, nie skrócić).
MIN_PAUSE_S = 3.0
# Tyle orzeczeń z rzędu nie do pobrania = źródło nie działa, koniec przebiegu.
MAX_FAILURES_IN_A_ROW = 5


class CbosaSource(Source):
    """Judgment pages /doc/<ID> for the ids in ID_DIR - the NSA child of Source."""

    name = "cbosa"

    def __init__(self, settings=None, client=None, id_dir=ID_DIR):
        super().__init__(settings, client)
        if client is not None:
            client.pause_s = max(client.pause_s, MIN_PAUSE_S)
        self.id_dir = Path(id_dir)
        # ids -> set of those already fetched (NsaImporter); None = fetch all
        self.fetched = None

    def queued_ids(self):
        """Ids from all files of id_dir and its subdirectories (e.g. 2025/2025-03.txt, also
        .part files still being written), sorted by path, duplicates removed, order kept."""
        if not self.id_dir.is_dir():
            return []
        out, seen = [], set()
        for path in sorted(p for p in self.id_dir.rglob("*") if p.is_file()):
            for cbosa_id in ids_in(path.read_text(encoding="utf-8", errors="replace")):
                if cbosa_id not in seen:
                    seen.add(cbosa_id)
                    out.append(cbosa_id)
        return out

    def iter_pages(self, cursor):
        ids = self.queued_ids()
        known = self.fetched(ids) if self.fetched is not None and ids else set()
        todo = [i for i in ids if i not in known]
        log.info(
            "CBOSA: w plikach %s %d identyfikatorów, już pobranych %d, do pobrania %d",
            self.id_dir,
            len(ids),
            len(ids) - len(todo),
            len(todo),
        )
        failures = 0
        for cbosa_id in todo:
            record = self.fetch(cbosa_id)
            failures = 0 if record is not None else failures + 1
            if failures >= MAX_FAILURES_IN_A_ROW:
                raise SourceError(f"CBOSA: {failures} orzeczeń z rzędu nie do pobrania - przerywam")
            yield Page([record] if record is not None else [], None)

    def fetch(self, cbosa_id):
        """The judgment page as a RawRecord; None when it can't be had now (logged)."""
        url = DOC_URL.format(id=cbosa_id)
        for attempt in range(1, EMPTY_RETRIES + 1):
            try:
                body = self.client.get_text(url)
            except SourceError as e:
                log.warning("CBOSA %s: %s", cbosa_id, e)
                return None
            if is_judgment_page(body):
                return RawRecord(cbosa_id, body, content_type="text/html", url=url)
            log.warning(
                "CBOSA %s: to nie strona orzeczenia (%d znaków), próba %d/%d",
                cbosa_id,
                len(body),
                attempt,
                EMPTY_RETRIES,
            )
        return None
