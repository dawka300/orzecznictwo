"""HUDOC (database of the European Court of Human Rights) as a Source.

The JSON search endpoint the HUDOC page itself calls (not an official API,
checked 2026-10-18, see etpc/notepad.md):
- /app/query/results?query=...&select=*&sort=...&start=...&length=...
  returns documents with all metadata and the full plain text (contentbody).
  `sort` is required (without it: HTTP 404), it may be empty.
- start + length must stay within 10,000. No year has more judgments and
  decisions than that (2009: 5,383), so the first download goes year by year
  with plain paging; after it, changes are followed by updateddate (keyset).
- A Cloudflare challenge (403) may come instead of results; PoliteClient
  retries it after a longer wait.

Cursor (JSON):
- {"mode": "lata", "year": 1998, "start": 150, "since": "<start of the download>"}
- {"mode": "zmiany", "since": "<updateddate>", "seen": [itemids at that updateddate]}
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

from judgments_core.source import Page, RawRecord, utc_now

BASE_URL = "https://hudoc.echr.coe.int"
SEARCH_URL = BASE_URL + "/app/query/results"
DOCUMENT_URL = BASE_URL + "/eng?i={itemid}"

# Wyroki i decyzje, wszystkie języki, bez placeholderów (wersji bez treści).
QUERY = (
    "contentsitename:ECHR"
    " AND (NOT (doctype=PR OR doctype=HFCOMOLD OR doctype=HECOMOLD))"
    ' AND (documentcollectionid="JUDGMENTS" OR documentcollectionid="DECISIONS")'
    ' AND isplaceholder="False"'
)
# Najstarsze: decyzje Komisji z 1955 r., pierwszy wyrok 1960 (Lawless).
FIRST_YEAR = 1955
# Teksty mają od kilku KB do kilkuset KB - strona nie może być duża.
PAGE_SIZE = 50
IDS_PER_QUERY = 20
# Pola zależne od zapytania, nie od dokumentu: zmieniałyby sha256 bez zmiany treści.
VOLATILE_FIELDS = ("rank",)
# Zmiany sprzed startu pobierania po latach - z zapasem na różnice zegarów.
SINCE_MARGIN = timedelta(days=1)

log = logging.getLogger(__name__)


class HudocSource:
    name = "hudoc"

    def __init__(self, settings, client, page_size=PAGE_SIZE, now=utc_now):
        self.settings = settings
        self.client = client
        self.page_size = page_size
        self.now = now

    def iter_pages(self, cursor):
        state = json.loads(cursor) if cursor else self._first_state()
        if state.get("mode", "zmiany") == "lata":
            yield from self._years(state)
            state = {"mode": "zmiany", "since": state["since"], "seen": []}
        yield from self._changes(state)

    def fetch_ids(self, itemids):
        """Pages of the given documents (e.g. found missing by `sprawdz`), no cursor."""
        for start in range(0, len(itemids), IDS_PER_QUERY):
            chunk = itemids[start : start + IDS_PER_QUERY]
            ids = " OR ".join(f'itemid="{i}"' for i in chunk)
            rows = self._search(f"{QUERY} AND ({ids})", "itemid Ascending", 0, len(chunk))
            found = {c["itemid"] for c in rows}
            for itemid in chunk:
                if itemid not in found:
                    log.warning("%s: nie ma w HUDOC (usunięty albo placeholder?)", itemid)
            yield Page([to_record(c) for c in rows], None)

    def count_year(self, year):
        """Documents with kpdate in `year`, as HUDOC counts them."""
        data = self._search_raw(_year_query(year), "", 0, 1, select="itemid")
        return data.get("resultcount", 0)

    def itemids_of_year(self, year):
        return [
            c["itemid"]
            for c in self._search(_year_query(year), "itemid Ascending", 0, 10000, select="itemid")
        ]

    def _first_state(self):
        since = self.now() - SINCE_MARGIN
        return {"mode": "lata", "year": FIRST_YEAR, "start": 0, "since": _iso(since)}

    def _years(self, state):
        year, start = state["year"], state["start"]
        while year <= self.now().year:
            rows = self._search(_year_query(year), "itemid Ascending", start, self.page_size)
            start += len(rows)
            done = len(rows) < self.page_size
            if done:
                log.info("rok %d: pobrany (%d dokumentów)", year, start)
                year, start = year + 1, 0
            cursor = {**state, "year": year, "start": start}
            yield Page([to_record(c) for c in rows], json.dumps(cursor))

    def _changes(self, state):
        # Zakres [since TO *] obejmuje już pobrane z tym samym updateddate
        # ("seen") - leżą na początku wyników, więc bierzemy ich więcej o tyle.
        since, seen = state.get("since"), set(state.get("seen", []))
        while True:
            query = QUERY if since is None else f"{QUERY} AND updateddate:[{since} TO *]"
            length = self.page_size + len(seen)
            rows = self._search(query, "updateddate Ascending,itemid Ascending", 0, length)
            new = [c for c in rows if c["itemid"] not in seen]
            if not new:
                return
            since = new[-1]["updateddate"]
            seen = {c["itemid"] for c in rows if c["updateddate"] == since}
            cursor = json.dumps({"mode": "zmiany", "since": since, "seen": sorted(seen)})
            yield Page([to_record(c) for c in new], cursor)
            if len(rows) < length:
                return

    def _search(self, query, sort, start, length, select="*"):
        data = self._search_raw(query, sort, start, length, select)
        return [r["columns"] for r in data.get("results") or []]

    def _search_raw(self, query, sort, start, length, select="*"):
        return self.client.get_json(
            SEARCH_URL,
            params={
                "query": query,
                "select": select,
                "sort": sort,
                "start": start,
                "length": length,
            },
        )


def to_record(columns):
    """One HUDOC document (its `columns`) as a raw record, keyed by itemid."""
    body = {k: v for k, v in columns.items() if k not in VOLATILE_FIELDS}
    return RawRecord(
        source_id=columns["itemid"],
        body=json.dumps(body, ensure_ascii=False, sort_keys=True),
        url=DOCUMENT_URL.format(itemid=columns["itemid"]),
    )


def _year_query(year):
    return (
        f'{QUERY} AND kpdate>="{year}-01-01T00:00:00.0Z" AND kpdate<"{year + 1}-01-01T00:00:00.0Z"'
    )


def _iso(moment):
    """HUDOC's updateddate format: 2026-10-08T07:51:10.947Z."""
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")
