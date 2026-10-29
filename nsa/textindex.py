"""NSA word index in Elasticsearch: one document per judgment (children of the core textindex).

Texts (thesis, sentence, reasons, dissenting opinion) analysed by Morfologik
(lemmas, so inflected forms match without the stem trick of FULLTEXT), not
stored; the filter fields are those of the vectors' payload plus thesis and
finality. The version = text sha256 + the indexed metadata.
"""

from __future__ import annotations

from typing import ClassVar

from judgments_core.textindex import POLISH_ANALYSIS, VERSION_FIELD, IndexJob, WordSearch
from nsa.semantic import NsaSemantic
from nsa.stats import NSA_COURT

TEXT_FIELDS = ["thesis", "sentence", "reasons", "dissenting_opinion"]
KEYWORD = {"type": "keyword"}

DEFINITION = {
    "settings": {
        "number_of_shards": 2,
        "number_of_replicas": 0,
        "refresh_interval": "30s",
        "analysis": POLISH_ANALYSIS,
    },
    "mappings": {
        "dynamic": "strict",
        "_source": {"excludes": TEXT_FIELDS},
        "properties": {
            **{name: {"type": "text", "analyzer": "pl"} for name in TEXT_FIELDS},
            "kind": KEYWORD,
            "court": KEYWORD,
            "is_nsa": {"type": "boolean"},
            "is_final": {"type": "boolean"},
            "has_thesis": {"type": "boolean"},
            "date": {"type": "date"},
            "year": {"type": "short"},
            "symbols": KEYWORD,
            "keywords": KEYWORD,
            "sha": VERSION_FIELD,
        },
    },
}

VERSIONS_SQL = """SELECT j.id, CONCAT_WS('|', j.text_sha256, j.kind, j.court, j.judgment_date,
    j.is_final, j.thesis IS NOT NULL,
    (SELECT GROUP_CONCAT(code ORDER BY code) FROM judgment_symbols WHERE judgment_id = j.id),
    (SELECT GROUP_CONCAT(keyword ORDER BY keyword SEPARATOR '|')
     FROM judgment_keywords WHERE judgment_id = j.id))
FROM judgments j WHERE j.id > %s ORDER BY j.id LIMIT %s"""


class NsaIndexJob(IndexJob):
    definition = DEFINITION

    def versions(self, cur, after, size):
        cur.execute(VERSIONS_SQL, (after, size))
        return cur.fetchall()

    def documents(self, cur, keys):
        cur.execute(
            "SELECT id, kind, court, judgment_date, is_final, thesis, sentence, reasons,"
            " dissenting_opinion FROM judgments WHERE id IN %s",
            (keys,),
        )
        documents = {}
        for key, kind, court, when, final, thesis, sentence, reasons, dissent in cur.fetchall():
            documents[key] = {
                "thesis": thesis,
                "sentence": sentence,
                "reasons": reasons,
                "dissenting_opinion": dissent,
                "kind": kind,
                "court": court,
                "is_nsa": court == NSA_COURT,
                "is_final": None if final is None else bool(final),
                "has_thesis": thesis is not None,
                "date": when.isoformat() if when else None,
                "year": when.year if when else None,
                "symbols": [],
                "keywords": [],
            }
        for table, column, field in (
            ("judgment_symbols", "code", "symbols"),
            ("judgment_keywords", "keyword", "keywords"),
        ):
            cur.execute(
                f"SELECT judgment_id, {column} FROM {table} WHERE judgment_id IN %s", (keys,)
            )
            for key, value in cur.fetchall():
                if key in documents:
                    documents[key][field].append(value)
        return documents


class NsaWords(WordSearch):
    # teza mówi najwięcej o sprawie
    fields: ClassVar[list] = ["thesis^2", "sentence", "reasons", "dissenting_opinion"]

    def filters(self, q):
        filters = NsaSemantic.payload_filters(self, q)  # te same co w wektorach
        if q.final in ("0", "1"):
            filters["is_final"] = q.final == "1"
        if q.with_thesis:
            filters["has_thesis"] = True
        return filters
