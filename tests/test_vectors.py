"""judgments_core.qdrant and judgments_core.embeddings with fake HTTP sessions."""

import pytest
import requests

from judgments_core.embeddings import Embedder, EmbeddingError
from judgments_core.qdrant import QdrantError, QdrantStore, collection_name, point_id
from judgments_core.vectors import Chunk


class Response:
    def __init__(self, status, data=None, text=""):
        self.status_code = status
        self.data = data
        self.text = text

    def json(self):
        return self.data


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, json=None, timeout=None):
        self.requests.append((method, url, json))
        answer = self.responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def post(self, url, json=None, timeout=None):
        return self.request("POST", url, json, timeout)


def ok(result=None):
    return Response(200, {"result": result})


def store(*responses):
    session = FakeSession(*responses)
    return QdrantStore("http://q:6333/", "etpc_bge", "bge", session=session), session


def test_names_and_ids():
    assert collection_name("etpc", "text-embedding-bge-m3:2") == "etpc_text-embedding-bge-m3-2"
    assert point_id("7", 0) == point_id("7", 0) != point_id("7", 1)


def test_creates_collection_and_indexes():
    s, session = store(Response(404), ok(True), ok(), ok())
    s.ensure_collection(1024, {"kind": "keyword", "year": "integer"})
    methods = [(m, url.split("6333")[1]) for m, url, _ in session.requests]
    assert methods == [
        ("GET", "/collections/etpc_bge"),
        ("PUT", "/collections/etpc_bge"),
        ("PUT", "/collections/etpc_bge/index?wait=true"),
        ("PUT", "/collections/etpc_bge/index?wait=true"),
    ]
    create = session.requests[1][2]
    assert create["vectors"] == {"size": 1024, "distance": "Cosine", "on_disk": True}
    assert create["quantization_config"]["scalar"]["type"] == "int8"


def test_existing_collection_checks_dimension_and_missing_indexes():
    info = {"config": {"params": {"vectors": {"size": 1024}}}, "payload_schema": {"kind": {}}}
    s, session = store(ok(info), ok())
    s.ensure_collection(1024, {"kind": "keyword", "year": "integer"})
    assert session.requests[1][2] == {"field_name": "year", "field_schema": "integer"}
    s, _ = store(ok(info))
    with pytest.raises(QdrantError, match="1024"):
        s.ensure_collection(768)


def test_upsert_in_parts_with_payload():
    chunks = [Chunk("7", i, "tekst", 10 * i, "prawo", [0.1], {"year": 2020}) for i in range(300)]
    s, session = store(ok(), ok())
    s.upsert(chunks)
    parts = [r[2]["points"] for r in session.requests]
    assert [len(p) for p in parts] == [256, 44]
    assert parts[0][1]["payload"] == {
        "year": 2020,
        "judgment_id": "7",
        "number": 1,
        "part": "prawo",
        "start": 10,
        "length": 5,
    }


def test_search_and_delete_filters():
    point = {
        "id": "x",
        "score": 0.9,
        "payload": {"judgment_id": "7", "number": 2, "start": 5, "part": "prawo", "year": 2020},
    }
    s, session = store(ok({"points": [point]}), ok())
    hits = s.search(
        [0.1], k=5, filters={"kind": "judgment", "respondents": ["POL"], "year": {"gte": 2000}}
    )
    body = session.requests[0][2]
    assert body["filter"]["must"] == [
        {"key": "kind", "match": {"value": "judgment"}},
        {"key": "respondents", "match": {"any": ["POL"]}},
        {"key": "year", "range": {"gte": 2000}},
    ]
    assert (hits[0].score, hits[0].chunk.judgment_id, hits[0].chunk.number, hits[0].chunk.meta) == (
        0.9,
        "7",
        2,
        {"year": 2020},
    )
    s.delete_judgments([7, 8])
    assert session.requests[1][2] == {
        "filter": {"must": [{"key": "judgment_id", "match": {"any": ["7", "8"]}}]}
    }


def test_qdrant_errors_are_readable():
    s, _ = store(requests.ConnectionError("odmowa"))
    with pytest.raises(QdrantError, match="nie odpowiada"):
        s.count()


def test_embedder_orders_vectors_and_retries():
    data = {"data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]}
    session = FakeSession(
        Response(503, text="busy"), requests.ConnectionError("x"), Response(200, data)
    )
    waits = []
    embedder = Embedder("http://lm/v1/embeddings", "bge", session=session, sleep=waits.append)
    assert embedder.embed(["a", "b"]) == [[1.0], [2.0]]
    assert waits == [10, 20]
    assert session.requests[0][2]["input"] == ["a", "b"]
    with pytest.raises(EmbeddingError, match="zwrócił 2"):
        Embedder("u", "m", session=FakeSession(Response(200, data))).embed(["a"])
    with pytest.raises(EmbeddingError, match="nie odpowiada"):
        Embedder("u", "m", retries=0, session=FakeSession(Response(500))).embed(["a"])
