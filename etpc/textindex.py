"""ETPC word index in Elasticsearch: one document per ruling document (language version).

The text goes to the field of its language (English and French analysers with
stemming, Polish by Morfologik, the rest standard), not stored; a search
collapses the versions to their ruling (group_field). Filter fields as in the
vectors' payload, the language of every version (not only ENG/FRE).
The version = text sha256 + the indexed metadata of the ruling.
"""

from __future__ import annotations

from etpc.semantic import EtpcSemantic
from judgments_core.textindex import POLISH_ANALYSIS, VERSION_FIELD, IndexJob, WordSearch

# język dokumentu -> pole tekstu z jego analizatorem
LANGUAGE_FIELDS = {"ENG": "body_en", "FRE": "body_fr", "POL": "body_pl"}
OTHER_FIELD = "body"
TEXT_FIELDS = [*LANGUAGE_FIELDS.values(), OTHER_FIELD]
KEYWORD = {"type": "keyword"}

DEFINITION = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "refresh_interval": "30s",
        "analysis": POLISH_ANALYSIS,
    },
    "mappings": {
        "dynamic": "strict",
        "_source": {"excludes": TEXT_FIELDS},
        "properties": {
            "body_en": {"type": "text", "analyzer": "english"},
            "body_fr": {"type": "text", "analyzer": "french"},
            "body_pl": {"type": "text", "analyzer": "pl"},
            "body": {"type": "text", "analyzer": "standard"},
            "ruling_id": {"type": "long"},
            "language": KEYWORD,
            "kind": KEYWORD,
            "formation": KEYWORD,
            "respondents": KEYWORD,
            "importance": {"type": "byte"},
            "date": {"type": "date"},
            "year": {"type": "short"},
            "sha": VERSION_FIELD,
        },
    },
}

VERSIONS_SQL = """SELECT d.id, CONCAT_WS('|', d.body_sha256, d.language, d.ruling_id, r.kind,
    r.formation, r.importance, r.ruling_date,
    (SELECT GROUP_CONCAT(state ORDER BY state) FROM ruling_respondents WHERE ruling_id = r.id))
FROM ruling_documents d JOIN rulings r ON r.id = d.ruling_id
WHERE d.id > %s ORDER BY d.id LIMIT %s"""


class EtpcIndexJob(IndexJob):
    definition = DEFINITION
    batch = 200  # teksty ETPC są długie (średnio ~22 tys. znaków)

    def versions(self, cur, after, size):
        cur.execute(VERSIONS_SQL, (after, size))
        return cur.fetchall()

    def documents(self, cur, keys):
        cur.execute(
            "SELECT d.id, d.ruling_id, d.language, d.body, r.kind, r.formation, r.importance,"
            " r.ruling_date FROM ruling_documents d JOIN rulings r ON r.id = d.ruling_id"
            " WHERE d.id IN %s",
            (keys,),
        )
        documents, rulings = {}, {}
        for key, ruling_id, language, body, kind, formation, importance, when in cur.fetchall():
            documents[key] = {
                LANGUAGE_FIELDS.get(language, OTHER_FIELD): body,
                "ruling_id": ruling_id,
                "language": language,
                "kind": kind,
                "formation": formation,
                "importance": importance,
                "date": when.isoformat() if when else None,
                "year": when.year if when else None,
                "respondents": [],
            }
            rulings.setdefault(ruling_id, []).append(key)
        if rulings:
            cur.execute(
                "SELECT ruling_id, state FROM ruling_respondents WHERE ruling_id IN %s",
                (list(rulings),),
            )
            for ruling_id, state in cur.fetchall():
                for key in rulings[ruling_id]:
                    documents[key]["respondents"].append(state)
        return documents


class EtpcWords(WordSearch):
    fields = TEXT_FIELDS
    group_field = "ruling_id"  # wersje językowe jednego orzeczenia

    def filters(self, q):
        filters = EtpcSemantic.payload_filters(self, q)  # te same co w wektorach
        if q.language:
            filters["language"] = q.language  # w indeksie są wszystkie języki
        return filters
