"""Qdrant over REST (requests only, no client library): a VectorStore.

One collection per project and model (the default name contains the model),
so vectors of different models never meet. Vectors are stored on disk with an
int8 copy in RAM; searches rescore with the originals.
"""

from __future__ import annotations

import re
import uuid

import requests

from judgments_core.vectors import Chunk, Hit

# Stała przestrzeń nazw identyfikatorów punktów: uuid5("<orzeczenie>:<numer>").
POINT_NAMESPACE = uuid.UUID("6f1c9d4e-2b1a-4c7e-9a55-0c3e5b7d2f10")

UPSERT_POINTS = 256


class QdrantError(RuntimeError):
    pass


class CollectionMissing(QdrantError):
    """The collection doesn't exist yet (nothing embedded with this model)."""


def collection_name(project, model):
    return f"{project}_{re.sub(r'[^A-Za-z0-9_-]+', '-', model)}"


def point_id(judgment_id, number):
    return str(uuid.uuid5(POINT_NAMESPACE, f"{judgment_id}:{number}"))


class QdrantStore:
    def __init__(self, url, collection, model_name, *, timeout_s=120.0, session=None):
        self.url = url.rstrip("/")
        self.collection = collection
        self.model_name = model_name
        self.timeout_s = timeout_s
        self.session = session or requests.Session()

    def _call(self, method, path, body=None, missing_ok=False):
        url = f"{self.url}/collections/{self.collection}{path}"
        try:
            response = self.session.request(method, url, json=body, timeout=self.timeout_s)
        except requests.RequestException as e:
            raise QdrantError(f"Qdrant {self.url} nie odpowiada ({type(e).__name__}: {e})") from e
        if response.status_code == 404 and missing_ok:
            return None
        if response.status_code == 404 and "doesn't exist" in response.text:
            raise CollectionMissing(f"kolekcji {self.collection} jeszcze nie ma")
        if response.status_code != 200:
            raise QdrantError(
                f"Qdrant {method} {path or '/'}: HTTP {response.status_code}: {response.text[:300]}"
            )
        return response.json()["result"]

    def ensure_collection(self, dimension, indexes=None):
        """Create the collection if missing; indexes = {payload field: integer/keyword/datetime ...}."""
        info = self._call("GET", "", missing_ok=True)
        if info is None:
            self._call(
                "PUT",
                "",
                {
                    "vectors": {"size": dimension, "distance": "Cosine", "on_disk": True},
                    "quantization_config": {
                        "scalar": {"type": "int8", "quantile": 0.99, "always_ram": True}
                    },
                    "on_disk_payload": True,
                },
            )
            existing = {}
        else:
            size = info["config"]["params"]["vectors"]["size"]
            if size != dimension:
                raise QdrantError(
                    f"kolekcja {self.collection} ma wektory {size}, model daje {dimension}"
                )
            existing = info.get("payload_schema") or {}
        for field, schema in (indexes or {}).items():
            if field not in existing:
                self._call("PUT", "/index?wait=true", {"field_name": field, "field_schema": schema})

    def upsert(self, chunks):
        if not chunks:
            return
        points = [
            {
                "id": point_id(c.judgment_id, c.number),
                "vector": c.vector,
                "payload": {
                    **c.meta,
                    "judgment_id": c.judgment_id,
                    "number": c.number,
                    "part": c.part,
                    "start": c.start,
                    "length": len(c.text),
                },
            }
            for c in chunks
        ]
        # Qdrant przyjmuje najwyżej 32 MB JSON-u; wektor 1024 to ~20 KB.
        for i in range(0, len(points), UPSERT_POINTS):
            self._call("PUT", "/points?wait=true", {"points": points[i : i + UPSERT_POINTS]})

    def delete_judgment(self, judgment_id):
        self.delete_judgments([judgment_id])

    def delete_judgments(self, judgment_ids):
        if judgment_ids:
            ids = [str(i) for i in judgment_ids]
            self._call(
                "POST",
                "/points/delete?wait=true",
                {"filter": {"must": [_condition("judgment_id", ids)]}},
            )

    def count(self):
        info = self._call("GET", "", missing_ok=True)
        return 0 if info is None else self._call("POST", "/points/count", {"exact": True})["count"]

    def search(self, vector, k=10, filters=None, exclude=None):
        """Best chunks. filters = {field: value | [values] | {"gte": .., "lte": ..}};
        exclude = the same for conditions that must not hold. Text is not stored
        (start/length point into the judgment's text)."""
        result = self._call("POST", "/points/query", self._query(vector, k, filters, exclude))
        return [_hit(p) for p in result["points"]]

    def search_judgments(self, vector, k=10, filters=None, exclude=None, per_judgment=1):
        """Best chunks (per_judgment of them) of each of the k best judgments, judgment
        after judgment (Qdrant groups by judgment_id)."""
        body = {
            **self._query(vector, k, filters, exclude),
            "group_by": "judgment_id",
            "group_size": per_judgment,
        }
        result = self._call("POST", "/points/query/groups", body)
        return [_hit(hit) for group in result["groups"] for hit in group["hits"]]

    def vectors_of(self, judgment_id, limit=2000):
        """[(part, vector)] of one judgment's chunks."""
        result = self._call(
            "POST",
            "/points/scroll",
            {
                "filter": {"must": [_condition("judgment_id", str(judgment_id))]},
                "limit": limit,
                "with_payload": ["part"],
                "with_vector": True,
            },
        )
        return [(p["payload"].get("part", ""), p["vector"]) for p in result["points"]]

    @staticmethod
    def _query(vector, k, filters, exclude):
        body = {
            "query": vector,
            "limit": k,
            "with_payload": True,
            "params": {"quantization": {"rescore": True}},
        }
        conditions = {}
        if filters:
            conditions["must"] = [_condition(f, v) for f, v in filters.items()]
        if exclude:
            conditions["must_not"] = [_condition(f, v) for f, v in exclude.items()]
        if conditions:
            body["filter"] = conditions
        return body


def _hit(point):
    payload = dict(point["payload"])
    chunk = Chunk(
        judgment_id=payload.pop("judgment_id"),
        number=payload.pop("number"),
        text="",
        start=payload.pop("start"),
        part=payload.pop("part", ""),
        meta=payload,
    )
    return Hit(chunk, point["score"])


def _condition(field, value):
    if isinstance(value, dict):
        return {"key": field, "range": value}
    if isinstance(value, (list, tuple, set)):
        return {"key": field, "match": {"any": list(value)}}
    return {"key": field, "match": {"value": value}}
