"""Word index in Elasticsearch over REST (requests only): search by words next to the vectors.

MariaDB stays the source of truth; the index is derived like the vectors
(command indeksuj): documents keyed by the project's integer id, each with a
version hash ("sha": text sha256 + indexed metadata), so a pass rewrites only
what changed. A pass may be killed anytime (Ctrl+C, docker stop, --budzet): the
last written key is kept in sync_state and the next pass continues from it;
documents of judgments deleted from the database leave the index at the end of
a pass that started from the first key. Texts are
analysed (Polish by Morfologik lemmas, English/French by the built-in
analyzers) but not stored: hits come back as ids, texts are read from MariaDB.

A query is the project's MariaDB boolean string (parse_query in search.py:
+word, -word, word*, "phrase", +(any of)), turned into an Elasticsearch bool
query (boolean_query), so the pages keep one syntax with or without the index.
Without elastic_url, or when Elasticsearch is down or the index not built yet
(TextIndexUnavailable), searches fall back to MariaDB FULLTEXT.

A project's children: an IndexJob (definition = settings and mappings,
versions and documents read from MariaDB) and a WordSearch (fields to search,
the same filters as the vectors - payload_filters of its SemanticSearch).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

import requests

from judgments_core.db import MariaDbStore

log = logging.getLogger(__name__)

PROGRESS_EVERY_S = 60
SCROLL_PAGE = 10000
DELETE_BATCH = 5000
# Polski: lematy Morfologika (wtyczka elasticsearch-analysis-morfologik, docker/elastic.Dockerfile);
# kilka lematów jednego słowa na tej samej pozycji, więc frazy działają, duplikaty precz.
POLISH_ANALYSIS = {
    "filter": {"pl_unique": {"type": "unique", "only_on_same_position": True}},
    "analyzer": {
        "pl": {
            "type": "custom",
            "tokenizer": "standard",
            "filter": ["lowercase", "morfologik_stem", "lowercase", "pl_unique"],
        }
    },
}
# Pole wersji dokumentu (nie do wyszukiwania, tylko w _source).
VERSION_FIELD = {"type": "keyword", "index": False, "doc_values": False}

_TOKEN = re.compile(r'([+-]?)(\([^()]*\)|"[^"]*"|[^\s()"]+)')


class TextIndexError(RuntimeError):
    pass


class TextIndexUnavailable(TextIndexError):
    """Elasticsearch is not running or the index is missing; the message is for the user."""


class TextIndex:
    """One Elasticsearch index; document ids are the project's integer keys."""

    def __init__(self, url, index, *, timeout_s=30.0, session=None):
        self.url = url.rstrip("/")
        self.index = index
        self.timeout_s = timeout_s
        self.session = session or requests.Session()

    def _call(self, method, path, body=None, *, ndjson=None, missing_ok=False):
        headers, data = {}, None
        if ndjson is not None:
            headers["Content-Type"] = "application/x-ndjson"
            data = ndjson.encode("utf-8")
        try:
            response = self.session.request(
                method,
                f"{self.url}/{path}",
                json=body,
                data=data,
                headers=headers,
                timeout=self.timeout_s,
            )
        except requests.RequestException as e:
            raise TextIndexUnavailable(
                f"Elasticsearch {self.url} nie odpowiada ({type(e).__name__})"
            ) from e
        if response.status_code == 404 and missing_ok:
            return None
        if response.status_code == 404 and "index_not_found" in response.text:
            raise TextIndexUnavailable(
                f"indeksu słów {self.index} jeszcze nie ma (polecenie indeksuj)"
            )
        if response.status_code >= 300:
            raise TextIndexError(
                f"Elasticsearch {method} {path}: HTTP {response.status_code}: {response.text[:300]}"
            )
        return response.json() if response.content else {}

    def exists(self):
        return self._call("GET", self.index, missing_ok=True) is not None

    def ensure(self, definition):
        """Create the index (settings, mappings) if it doesn't exist."""
        if not self.exists():
            self._call("PUT", self.index, definition)
            log.info("utworzono indeks słów %s", self.index)

    def drop(self):
        self._call("DELETE", self.index, missing_ok=True)

    def versions(self, keys):
        """{key: version} of the keys already in the index."""
        if not keys:
            return {}
        result = self._call(
            "POST",
            f"{self.index}/_mget?_source_includes=sha",
            {"ids": [str(k) for k in keys]},
        )
        return {
            int(doc["_id"]): doc["_source"].get("sha") for doc in result["docs"] if doc.get("found")
        }

    def bulk(self, documents=None, deletes=()):
        """Write documents ({key: source}) and delete keys in one _bulk call."""
        lines = []
        for key, source in (documents or {}).items():
            lines.append(json.dumps({"index": {"_index": self.index, "_id": str(key)}}))
            lines.append(json.dumps(source, ensure_ascii=False, default=str))
        for key in deletes:
            lines.append(json.dumps({"delete": {"_index": self.index, "_id": str(key)}}))
        if not lines:
            return
        result = self._call("POST", "_bulk", ndjson="\n".join(lines) + "\n")
        if result.get("errors"):
            failed = [
                item
                for entry in result["items"]
                for item in entry.values()
                if item.get("error") and not (item.get("status") == 404 and "delete" in entry)
            ]
            if failed:
                raise TextIndexError(
                    f"Elasticsearch _bulk: {len(failed)} błędów, pierwszy: {failed[0]['error']}"
                )

    def all_keys(self):
        """All keys in the index (scroll)."""
        result = self._call(
            "POST",
            f"{self.index}/_search?scroll=2m",
            {"size": SCROLL_PAGE, "_source": False, "sort": ["_doc"]},
        )
        scroll_id = result.get("_scroll_id")
        try:
            while result["hits"]["hits"]:
                for hit in result["hits"]["hits"]:
                    yield int(hit["_id"])
                result = self._call(
                    "POST", "_search/scroll", {"scroll": "2m", "scroll_id": scroll_id}
                )
                scroll_id = result.get("_scroll_id", scroll_id)
        finally:
            if scroll_id:
                self._call("DELETE", "_search/scroll", {"scroll_id": scroll_id}, missing_ok=True)

    def refresh(self):
        self._call("POST", f"{self.index}/_refresh")

    def count(self):
        result = self._call("GET", f"{self.index}/_count", missing_ok=True)
        return result["count"] if result else 0

    def search(self, body):
        return self._call("POST", f"{self.index}/_search", body)


def boolean_query(boolean, fields):
    """MariaDB boolean-mode text (+word -word word* "phrase" +(a "b c")) -> Elasticsearch bool.

    + = must, - = must_not, no operator = should (raises the score); without any
    must at least one should has to match, as in MariaDB.
    """
    must, should, must_not = [], [], []
    for operator, term in _TOKEN.findall(boolean):
        if term.startswith("("):
            options = [_clause(t, fields) for _, t in _TOKEN.findall(term[1:-1])]
            clause = {"bool": {"should": options, "minimum_should_match": 1}}
        else:
            clause = _clause(term, fields)
        {"+": must, "-": must_not}.get(operator, should).append(clause)
    query = {}
    if must:
        query["must"] = must
    if should:
        query["should"] = should
        if not must:
            query["minimum_should_match"] = 1
    if must_not:
        query["must_not"] = must_not
    return {"bool": query}


def _clause(term, fields):
    if term.startswith('"'):
        return {"multi_match": {"query": term.strip('"'), "fields": fields, "type": "phrase"}}
    if term.endswith("*"):
        return {"simple_query_string": {"query": term, "fields": fields, "analyze_wildcard": True}}
    return {"multi_match": {"query": term, "fields": fields}}


def filter_clauses(filters):
    """Filters in the vectors' form ({field: value | [values] | {gte, lte}}) -> bool filter."""
    clauses = []
    for name, value in filters.items():
        if isinstance(value, dict):
            clauses.append({"range": {name: value}})
        elif isinstance(value, (list, tuple)):
            clauses.append({"terms": {name: list(value)}})
        else:
            clauses.append({"term": {name: value}})
    return clauses


class WordSearch(ABC):
    """Search by words in the project's index: ranked ids of judgments with filters."""

    fields: ClassVar[list] = []  # pola tekstu, np. ["thesis^2", "reasons"]
    group_field = ""  # dokumenty jednego orzeczenia (ETPC: wersje językowe) -> to pole
    date_field = "date"  # sortowanie po dacie

    def __init__(self, index):
        self.index = index

    @classmethod
    def from_settings(cls, settings, project, timeout_s=10.0):
        """None when no elastic_url is set (words through MariaDB FULLTEXT)."""
        if not settings.elastic_url:
            return None
        return cls(
            TextIndex(settings.elastic_url, settings.elastic_index or project, timeout_s=timeout_s)
        )

    @abstractmethod
    def filters(self, q):
        """The part of the project's SearchQuery the index filters on (as payload_filters)."""

    def query(self, boolean, q):
        """The Elasticsearch query: the words and q's filters. A child may change the
        scoring (e.g. boost important judgments) around it."""
        return {
            "bool": {
                "must": [boolean_query(boolean, self.fields)],
                "filter": filter_clauses(self.filters(q)),
            }
        }

    def ranked(self, boolean, q, size, sort=""):
        """([judgment id] best first, total matching) for a boolean query and q's filters;
        sort "desc"/"asc" orders by date instead of score."""
        body = {
            "query": self.query(boolean, q),
            "size": size,
            "_source": False,
            "track_total_hits": True,
        }
        if sort:
            body["sort"] = [{self.date_field: sort}, "_score"]
        if self.group_field:
            body["collapse"] = {"field": self.group_field}
            body["aggs"] = {"groups": {"cardinality": {"field": self.group_field}}}
        result = self.index.search(body)
        hits = result["hits"]["hits"]
        if self.group_field:
            ids = [int(h["fields"][self.group_field][0]) for h in hits]
            total = int(result["aggregations"]["groups"]["value"])
        else:
            ids = [int(h["_id"]) for h in hits]
            total = int(result["hits"]["total"]["value"])
        return ids, max(total, len(ids))


@dataclass
class IndexResult:
    indexed: int = 0  # zapisane (nowe albo zmienione)
    unchanged: int = 0
    removed: int = 0  # usunięte z bazy -> usunięte z indeksu
    finished: bool = False
    stopped_by: str = ""  # "limit" / "czas"


class IndexJob(ABC):
    """MariaDB -> the word index, by key order; only new or changed documents are written."""

    definition: ClassVar[dict] = {}  # settings + mappings indeksu (pole sha: VERSION_FIELD)
    batch = 500  # tyle kluczy naraz (wersje, teksty, jedno _bulk)

    def __init__(
        self,
        conn,
        index,
        *,
        limit=None,
        time_budget_s=0,
        from_start=False,
        clock=time.monotonic,
    ):
        self.conn = conn
        self.index = index
        self.limit = limit
        self.time_budget_s = time_budget_s
        self.from_start = from_start
        self.clock = clock
        self.store = MariaDbStore(conn)  # kursor przerwanego przebiegu w sync_state
        self.result = IndexResult()

    @abstractmethod
    def versions(self, cur, after, size):
        """[(key, version text)] of the next size keys above after, by key; the text changes
        whenever anything indexed changes (text sha256 + metadata)."""

    @abstractmethod
    def documents(self, cur, keys):
        """{key: document fields} for these keys."""

    def run(self):
        started = last_report = self.clock()
        state_key = f"indeksuj:{self.index.index}"
        if self.from_start:
            self.index.drop()
            self.store.delete_state(state_key)
        self.index.ensure(self.definition)
        r, seen = self.result, set()
        after = int(self.store.get_state(state_key) or 0)
        resumed = after > 0
        if resumed:
            log.info("indeks %s: wznawiam od klucza %s", self.index.index, after)
        while True:
            if self.time_budget_s and self.clock() - started >= self.time_budget_s:
                r.stopped_by = "czas"
                break
            if self.limit is not None and r.indexed >= self.limit:
                r.stopped_by = "limit"
                break
            with self.conn.cursor() as cur:
                rows = self.versions(cur, after, self.batch)
                if not rows:
                    r.finished = True
                    break
                after = rows[-1][0]
                versions = {key: _digest(version) for key, version in rows}
                seen.update(versions)
                indexed = self.index.versions(list(versions))
                changed = [k for k, v in versions.items() if indexed.get(k) != v]
                r.unchanged += len(rows) - len(changed)
                if self.limit is not None:
                    changed = changed[: self.limit - r.indexed]
                documents = self.documents(cur, changed) if changed else {}
            self.conn.commit()
            for key, document in documents.items():
                document["sha"] = versions[key]
            self.index.bulk(documents)
            self.store.set_state(state_key, str(after))
            r.indexed += len(documents)
            if self.clock() - last_report >= PROGRESS_EVERY_S:
                last_report = self.clock()
                log.info(
                    "indeks %s: zapisano %d, bez zmian %d (do klucza %s)",
                    self.index.index,
                    r.indexed,
                    r.unchanged,
                    after,
                )
        if r.finished:
            self.store.delete_state(state_key)
            if resumed:
                log.info("usunięte z bazy wyjdą z indeksu przy następnym pełnym przebiegu")
            else:
                self._remove_deleted(seen)
        self.index.refresh()
        return r

    def _remove_deleted(self, seen):
        """Documents whose key is no longer in the database leave the index."""
        stale = [key for key in self.index.all_keys() if key not in seen]
        for start in range(0, len(stale), DELETE_BATCH):
            self.index.bulk(deletes=stale[start : start + DELETE_BATCH])
        self.result.removed = len(stale)


def _digest(version):
    return hashlib.sha256(str(version).encode("utf-8")).hexdigest()
