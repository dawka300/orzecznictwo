"""HUDOC (database of the European Court of Human Rights) as a Source.

The JSON search endpoint the HUDOC page itself calls (not an official API,
checked 2026-10-18, see etpc/notepad.md):
- /app/query/results?query=...&select=*&sort=...&start=...&length=...
  returns documents with all metadata and the full plain text (contentbody).
- start + length must stay within 10,000, so pages are keyset-based: sorted by
  updateddate, then itemid (stable), filtered from the last updateddate seen.
- A Cloudflare challenge (HTML instead of JSON) may come instead of results;
  the client then raises SourceError and the next pass retries.
"""

from __future__ import annotations

import json

from judgments_core.source import Page, RawRecord

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
SORT = "updateddate Ascending,itemid Ascending"
# Teksty mają od kilku KB do kilkuset KB - strona nie może być duża.
PAGE_SIZE = 50
# Pola zależne od zapytania, nie od dokumentu: zmieniałyby sha256 bez zmiany treści.
VOLATILE_FIELDS = ("rank",)


class HudocSource:
    name = "hudoc"

    def __init__(self, settings, client, page_size=PAGE_SIZE):
        self.settings = settings
        self.client = client
        self.page_size = page_size

    def iter_pages(self, cursor):
        # Kursor: {"since": updateddate ostatniego dokumentu, "seen": itemid
        # już pobrane z tym samym updateddate} - zakres [since TO *] je obejmuje.
        state = json.loads(cursor) if cursor else {}
        since, seen = state.get("since"), set(state.get("seen", []))
        while True:
            query = QUERY if since is None else f"{QUERY} AND updateddate:[{since} TO *]"
            # Pobrane wcześniej leżą na początku wyników - bierzemy ich więcej o tyle.
            length = self.page_size + len(seen)
            data = self.client.get_json(
                SEARCH_URL,
                params={
                    "query": query,
                    "select": "*",
                    "sort": SORT,
                    "start": 0,
                    "length": length,
                },
            )
            rows = [r["columns"] for r in data.get("results") or []]
            new = [c for c in rows if c["itemid"] not in seen]
            if not new:
                return
            since = new[-1]["updateddate"]
            seen = {c["itemid"] for c in rows if c["updateddate"] == since}
            cursor = json.dumps({"since": since, "seen": sorted(seen)})
            yield Page([to_record(c) for c in new], cursor)
            if len(rows) < length:
                return


def to_record(columns):
    """One HUDOC document (its `columns`) as a raw record, keyed by itemid."""
    body = {k: v for k, v in columns.items() if k not in VOLATILE_FIELDS}
    return RawRecord(
        source_id=columns["itemid"],
        body=json.dumps(body, ensure_ascii=False, sort_keys=True),
        url=DOCUMENT_URL.format(itemid=columns["itemid"]),
    )
