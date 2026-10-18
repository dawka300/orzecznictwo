import json
from datetime import datetime, timezone

from etpc.source import FIRST_YEAR, HudocSource, to_record

NOW = datetime(1956, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def doc(itemid, updated, kpdate="1955-05-05T00:00:00"):
    return {
        "itemid": itemid,
        "updateddate": updated,
        "kpdate": kpdate,
        "rank": "1.5",
        "contentbody": "...",
    }


class FakeHudoc:
    """Answers like HUDOC: filtered by kpdate year or from `since`, sorted, a window."""

    def __init__(self, docs):
        self.docs = docs
        self.calls = []

    def get_json(self, url, params=None):
        self.calls.append(params)
        query, docs = params["query"], list(self.docs)
        if 'itemid="' in query:
            wanted = {part.split('"')[0] for part in query.split('itemid="')[1:]}
            docs = [d for d in docs if d["itemid"] in wanted]
        if 'kpdate>="' in query:
            year = query.split('kpdate>="')[1][:4]
            docs = [d for d in docs if d["kpdate"].startswith(year)]
        if " AND updateddate:[" in query:
            since = query.split("updateddate:[")[1].split(" TO")[0]
            docs = [d for d in docs if d["updateddate"] >= since]
        if params["sort"].startswith("updateddate"):
            docs.sort(key=lambda d: (d["updateddate"], d["itemid"]))
        else:
            docs.sort(key=lambda d: d["itemid"])
        page = docs[params["start"] : params["start"] + params["length"]]
        return {"resultcount": len(docs), "results": [{"columns": d} for d in page]}


def source(docs, page_size=2):
    return HudocSource(None, FakeHudoc(docs), page_size=page_size, now=lambda: NOW)


def ids(pages):
    return [r.source_id for p in pages for r in p.records]


def changes_cursor(since, seen):
    return json.dumps({"mode": "zmiany", "since": since, "seen": seen})


DOCS = [
    doc("001-1", "1956-01-01T00:00:00.100Z"),
    doc("001-2", "1956-01-01T00:00:00.200Z"),
    doc("001-3", "1956-01-01T00:00:00.200Z", kpdate="1956-02-02T00:00:00"),
    doc("001-4", "1956-01-01T00:00:00.200Z", kpdate="1956-02-02T00:00:00"),
    doc("001-5", "1956-01-02T00:00:00.000Z", kpdate="1956-03-03T00:00:00"),
]


def test_first_download_goes_year_by_year_then_follows_changes():
    s = source(DOCS + [doc("001-6", "1956-06-01T00:00:00.000Z", kpdate="1956-05-05T00:00:00")])
    pages = list(s.iter_pages(None))
    # lata 1955 (001-1, 001-2), 1956 (001-3 ... 001-6), potem zmiany od startu - 1 dzień
    assert ids(pages)[:6] == ["001-1", "001-2", "001-3", "001-4", "001-5", "001-6"]
    assert ids(pages)[6:] == ["001-6"]  # zmieniony w trakcie: pobrany jeszcze raz
    cursors = [json.loads(p.cursor) for p in pages]
    assert cursors[0] == {
        "mode": "lata",
        "year": FIRST_YEAR,
        "start": 2,
        "since": "1956-05-31T12:00:00.000Z",
    }
    assert cursors[-1]["mode"] == "zmiany"


def test_years_resume_inside_a_year():
    cursor = json.dumps({"mode": "lata", "year": 1956, "start": 2, "since": "1956-06-01"})
    pages = list(source(DOCS).iter_pages(cursor))
    assert ids(pages) == ["001-5"]
    assert json.loads(pages[-1].cursor)["year"] == 1957  # po roku bieżącym


def test_changes_cover_every_document_once_despite_equal_timestamps():
    cursor = changes_cursor("1956-01-01T00:00:00.000Z", [])
    pages = list(source(DOCS).iter_pages(cursor))
    assert ids(pages) == ["001-1", "001-2", "001-3", "001-4", "001-5"]
    assert json.loads(pages[0].cursor) == {
        "mode": "zmiany",
        "since": "1956-01-01T00:00:00.200Z",
        "seen": ["001-2"],
    }


def test_changes_resume_after_cursor():
    cursor = changes_cursor("1956-01-01T00:00:00.200Z", ["001-2", "001-3"])
    assert ids(source(DOCS).iter_pages(cursor)) == ["001-4", "001-5"]


def test_nothing_new_yields_no_page():
    cursor = changes_cursor("1956-01-02T00:00:00.000Z", ["001-5"])
    assert list(source(DOCS).iter_pages(cursor)) == []


def test_fetch_ids_and_year_counts():
    s = source(DOCS)
    pages = list(s.fetch_ids(["001-4", "001-1", "001-9"]))
    assert ids(pages) == ["001-1", "001-4"]
    assert all(p.cursor is None for p in pages)
    assert (s.count_year(1955), s.count_year(1956)) == (2, 3)
    assert s.itemids_of_year(1956) == ["001-3", "001-4", "001-5"]
    assert all(c["sort"] is not None for c in s.client.calls)  # bez sort HUDOC daje 404


def test_record_drops_query_dependent_rank():
    record = to_record(doc("001-1", "1956-01-01T00:00:00.100Z"))
    assert record.source_id == "001-1"
    assert "rank" not in json.loads(record.body)
    assert record.url == "https://hudoc.echr.coe.int/eng?i=001-1"
