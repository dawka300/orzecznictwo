import json

from etpc.source import HudocSource, to_record


def doc(itemid, updated):
    return {"itemid": itemid, "updateddate": updated, "rank": "1.5", "contentbody": "..."}


class FakeHudoc:
    """Answers like HUDOC: documents sorted, filtered from `since`, first `length`."""

    def __init__(self, docs):
        self.docs = sorted(docs, key=lambda d: (d["updateddate"], d["itemid"]))
        self.calls = []

    def get_json(self, url, params=None):
        self.calls.append(params)
        docs = self.docs
        if " AND updateddate:[" in params["query"]:
            since = params["query"].split("updateddate:[")[1].split(" TO")[0]
            docs = [d for d in docs if d["updateddate"] >= since]
        page = docs[params["start"] : params["start"] + params["length"]]
        return {"resultcount": len(docs), "results": [{"columns": d} for d in page]}


def ids(pages):
    return [r.source_id for p in pages for r in p.records]


DOCS = [
    doc("001-1", "2024-01-01T00:00:00.100Z"),
    doc("001-2", "2024-01-01T00:00:00.200Z"),
    doc("001-3", "2024-01-01T00:00:00.200Z"),
    doc("001-4", "2024-01-01T00:00:00.200Z"),
    doc("001-5", "2024-01-02T00:00:00.000Z"),
]


def test_pages_cover_every_document_once_despite_equal_timestamps():
    pages = list(HudocSource(None, FakeHudoc(DOCS), page_size=2).iter_pages(None))
    assert ids(pages) == ["001-1", "001-2", "001-3", "001-4", "001-5"]
    assert json.loads(pages[0].cursor) == {
        "since": "2024-01-01T00:00:00.200Z",
        "seen": ["001-2"],
    }


def test_resumes_after_cursor():
    cursor = json.dumps({"since": "2024-01-01T00:00:00.200Z", "seen": ["001-2", "001-3"]})
    pages = list(HudocSource(None, FakeHudoc(DOCS), page_size=2).iter_pages(cursor))
    assert ids(pages) == ["001-4", "001-5"]


def test_nothing_new_yields_no_page():
    cursor = json.dumps({"since": "2024-01-02T00:00:00.000Z", "seen": ["001-5"]})
    assert list(HudocSource(None, FakeHudoc(DOCS)).iter_pages(cursor)) == []


def test_record_drops_query_dependent_rank():
    record = to_record(doc("001-1", "2024-01-01T00:00:00.100Z"))
    assert record.source_id == "001-1"
    assert "rank" not in json.loads(record.body)
    assert record.url == "https://hudoc.echr.coe.int/eng?i=001-1"
