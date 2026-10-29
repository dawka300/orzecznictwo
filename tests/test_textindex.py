"""judgments_core.textindex: boolean query, the index job and the REST calls with fakes."""

import contextlib
import json

import pytest
import requests

from etpc.search import SearchQuery as EtpcQuery
from etpc.textindex import EtpcWords
from judgments_core.textindex import (
    IndexJob,
    TextIndex,
    TextIndexError,
    TextIndexUnavailable,
    boolean_query,
    filter_clauses,
)
from nsa.search import SearchQuery as NsaQuery
from nsa.search import parse_query
from nsa.textindex import NsaWords

FIELDS = ["thesis^2", "reasons"]


def test_boolean_query_must_should_not_and_groups():
    query = boolean_query('+skarga -"zwrot kosztów" kasacyj* +(fair "fair trial")', FIELDS)
    bool_ = query["bool"]
    assert bool_["must"][0] == {"multi_match": {"query": "skarga", "fields": FIELDS}}
    group = bool_["must"][1]["bool"]
    assert group["minimum_should_match"] == 1
    assert group["should"][1]["multi_match"]["type"] == "phrase"
    assert bool_["must_not"] == [
        {"multi_match": {"query": "zwrot kosztów", "fields": FIELDS, "type": "phrase"}}
    ]
    assert bool_["should"][0]["simple_query_string"]["query"] == "kasacyj*"
    assert "minimum_should_match" not in bool_  # są warunki konieczne


def test_boolean_query_only_optional_words_needs_one():
    query = boolean_query("detention torture", FIELDS)
    assert query["bool"]["minimum_should_match"] == 1
    assert len(query["bool"]["should"]) == 2


def test_nsa_whole_words_for_the_index():
    assert parse_query("skarga kasacyjna", inflect=False).boolean == "+skarga +kasacyjna"


def test_filters_like_the_vectors():
    q = NsaQuery(kind="wyrok", instance="nsa", year_from=2020, final="1", with_thesis=True)
    filters = NsaWords(None).filters(q)
    assert filters == {
        "kind": "wyrok",
        "is_nsa": True,
        "year": {"gte": 2020},
        "is_final": True,
        "has_thesis": True,
    }
    assert filter_clauses({"kind": "wyrok", "year": {"gte": 2020}, "symbols": ["6110"]}) == [
        {"term": {"kind": "wyrok"}},
        {"range": {"year": {"gte": 2020}}},
        {"terms": {"symbols": ["6110"]}},
    ]
    # ETPC: w indeksie są wszystkie języki, w wektorach tylko ENG/FRE
    assert EtpcWords(None).filters(EtpcQuery(language="RUS")) == {"language": "RUS"}


class Response:
    def __init__(self, status, data=None, text=""):
        self.status_code = status
        self.data = data
        self.text = text or json.dumps(data or {})
        self.content = self.text.encode()

    def json(self):
        return self.data


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, json=None, data=None, headers=None, timeout=None):
        self.requests.append((method, url, json, data))
        answer = self.responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def index(*responses):
    session = FakeSession(*responses)
    return TextIndex("http://es:9200/", "nsa", session=session), session


def test_versions_and_bulk():
    es, session = index(
        Response(
            200,
            {
                "docs": [
                    {"_id": "1", "found": True, "_source": {"sha": "a"}},
                    {"_id": "2", "found": False},
                ]
            },
        ),
        Response(200, {"errors": False, "items": []}),
    )
    assert es.versions([1, 2]) == {1: "a"}
    es.bulk({3: {"thesis": "Teza"}}, deletes=[4])
    _, url, _, data = session.requests[1]
    assert url == "http://es:9200/_bulk"
    lines = data.decode().splitlines()
    assert json.loads(lines[0]) == {"index": {"_index": "nsa", "_id": "3"}}
    assert json.loads(lines[2]) == {"delete": {"_index": "nsa", "_id": "4"}}


def test_bulk_errors_raise_but_missing_deletes_dont():
    missing = {"errors": True, "items": [{"delete": {"status": 404, "error": None}}]}
    failed = {"errors": True, "items": [{"index": {"status": 400, "error": {"type": "x"}}}]}
    es, _ = index(Response(200, missing), Response(200, failed))
    es.bulk(deletes=[1])
    with pytest.raises(TextIndexError, match="1 błędów"):
        es.bulk({1: {}})


def test_down_or_missing_index_is_unavailable():
    es, _ = index(
        requests.ConnectionError("refused"),
        Response(404, text='{"error":{"type":"index_not_found_exception"}}'),
    )
    with pytest.raises(TextIndexUnavailable, match="nie odpowiada"):
        es.search({})
    with pytest.raises(TextIndexUnavailable, match="indeksuj"):
        es.search({})


def test_ranked_collapses_versions_to_rulings():
    es, session = index(
        Response(
            200,
            {
                "hits": {
                    "total": {"value": 9},
                    "hits": [{"fields": {"ruling_id": [7]}}, {"fields": {"ruling_id": [3]}}],
                },
                "aggregations": {"groups": {"value": 5}},
            },
        )
    )
    ids, total = EtpcWords(es).ranked("+torture", EtpcQuery(kind="JUDGMENTS"), 50, sort="desc")
    assert (ids, total) == ([7, 3], 5)
    body = session.requests[0][2]
    assert body["collapse"] == {"field": "ruling_id"}
    assert body["sort"][0] == {"date": "desc"}
    assert body["query"]["bool"]["filter"] == [{"term": {"kind": "JUDGMENTS"}}]


class MemoryIndex:
    """TextIndex in memory: {key: source}."""

    def __init__(self, docs=None):
        self.docs = dict(docs or {})
        self.index = "test"
        self.created = 0

    def drop(self):
        self.docs.clear()

    def ensure(self, definition):
        self.created += 1

    def versions(self, keys):
        return {k: self.docs[k]["sha"] for k in keys if k in self.docs}

    def bulk(self, documents=None, deletes=()):
        self.docs.update(documents or {})
        for key in deletes:
            self.docs.pop(key, None)

    def all_keys(self):
        return iter(list(self.docs))

    def refresh(self):
        pass


class Connection:
    def cursor(self):
        return contextlib.nullcontext(None)

    def commit(self):
        pass


class RowsJob(IndexJob):
    batch = 2

    def __init__(self, rows, index, **kwargs):
        super().__init__(Connection(), index, **kwargs)
        self.rows = rows  # {key: text}
        self.loaded = []

    def versions(self, cur, after, size):
        keys = sorted(k for k in self.rows if k > after)[:size]
        return [(k, self.rows[k]) for k in keys]

    def documents(self, cur, keys):
        self.loaded += keys
        return {k: {"text": self.rows[k]} for k in keys}


def test_job_writes_only_changes_and_removes_deleted():
    store = MemoryIndex()
    rows = {1: "a", 2: "b", 3: "c"}
    first = RowsJob(rows, store).run()
    assert (first.indexed, first.finished) == (3, True)
    rows[2] = "b2"
    del rows[3]
    job = RowsJob(rows, store)
    second = job.run()
    assert job.loaded == [2]
    assert (second.indexed, second.unchanged, second.removed) == (1, 1, 1)
    assert sorted(store.docs) == [1, 2]


def test_job_limit_and_budget_stop_without_removing():
    store = MemoryIndex({9: {"sha": "x"}})
    result = RowsJob({1: "a", 2: "b", 3: "c"}, store, limit=2).run()
    assert (result.indexed, result.stopped_by, result.removed) == (2, "limit", 0)
    assert 9 in store.docs
    ticks = iter([0, 0, 100, 100, 100])
    result = RowsJob({1: "a"}, MemoryIndex(), time_budget_s=10, clock=lambda: next(ticks)).run()
    assert result.stopped_by == "czas"
