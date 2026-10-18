"""Project tables of ETPC from raw records: derive(cursor, records) for the Deriver.

Each changed document is upserted into ruling_documents under its ruling
(ECLI). Then every touched ruling is rebuilt from its best language version:
the rulings row and its side tables. The best version's raw record is read
back from raw_records, so the result doesn't depend on the order in which
the language versions arrived.
"""

from __future__ import annotations

import json

from etpc.parser import is_wanted, language_priority, parse_document, parse_ruling
from etpc.source import HudocSource
from judgments_core.source import utc_now

SIDE_TABLES = {
    "ruling_applications": ("appno", "position"),
    "ruling_respondents": ("state",),
    "ruling_articles": ("relation", "article"),
    "ruling_keywords": ("term_id",),
    "ruling_citations": ("cited_appno",),
}


def derive(cur, records):
    """Returns problems [(source_id, problem, details)]; a failing record is skipped."""
    now = utc_now().replace(tzinfo=None)
    touched, left, problems = set(), set(), []
    for record in records:
        # SAVEPOINT: błąd jednego dokumentu cofa tylko jego zapisy.
        cur.execute("SAVEPOINT document")
        try:
            columns = json.loads(record.body)
            if not is_wanted(columns):
                continue
            document = parse_document(columns)
            ruling_id = _ensure_ruling(cur, columns, now)
            previous = _upsert_document(cur, ruling_id, document, now)
        except Exception as e:  # noqa: BLE001 - zapisujemy każdy błąd jako problem
            cur.execute("ROLLBACK TO SAVEPOINT document")
            problems.append((record.source_id, "blad_parsera", f"{type(e).__name__}: {e}"))
            continue
        touched.add(ruling_id)
        if previous not in (None, ruling_id):
            left.add(previous)  # dokument zmienił ECLI
        if not document.body:
            problems.append((record.source_id, "pusty_tekst", "brak contentbody w HUDOC"))
    for ruling_id in touched | left:
        cur.execute("SAVEPOINT ruling")
        try:
            _rebuild_ruling(cur, ruling_id, now)
        except Exception as e:  # noqa: BLE001
            cur.execute("ROLLBACK TO SAVEPOINT ruling")
            best = _best_itemid(cur, ruling_id) or f"ruling:{ruling_id}"
            problems.append((best, "blad_parsera", f"orzeczenie: {type(e).__name__}: {e}"))
    return problems


def _ensure_ruling(cur, columns, now):
    """Id of the ruling for this document's ECLI; a placeholder row if it is new."""
    ruling = parse_ruling(columns)
    cur.execute(
        "INSERT INTO rulings (group_key, ecli, kind, formation, ruling_date, title, parsed_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)"
        # LAST_INSERT_ID(id): lastrowid zwraca id także istniejącego wiersza.
        " ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)",
        (
            ruling.group_key,
            ruling.ecli,
            ruling.kind,
            ruling.formation,
            ruling.ruling_date,
            ruling.title,
            now,
        ),
    )
    return cur.lastrowid


def _upsert_document(cur, ruling_id, d, now):
    """Insert or update by itemid; returns the ruling_id it had before (or None)."""
    cur.execute("SELECT ruling_id FROM ruling_documents WHERE itemid = %s", (d.itemid,))
    row = cur.fetchone()
    cur.execute(
        "INSERT INTO ruling_documents"
        " (itemid, ruling_id, language, doctype, is_translation, title, conclusion,"
        "  body, body_length, body_sha256, source_created_at, source_updated_at, parsed_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON DUPLICATE KEY UPDATE"
        "  ruling_id = VALUES(ruling_id), language = VALUES(language),"
        "  doctype = VALUES(doctype), is_translation = VALUES(is_translation),"
        "  title = VALUES(title), conclusion = VALUES(conclusion), body = VALUES(body),"
        "  body_length = VALUES(body_length), body_sha256 = VALUES(body_sha256),"
        "  source_created_at = VALUES(source_created_at),"
        "  source_updated_at = VALUES(source_updated_at), parsed_at = VALUES(parsed_at)",
        (
            d.itemid,
            ruling_id,
            d.language,
            d.doctype,
            d.is_translation,
            d.title,
            d.conclusion,
            d.body,
            len(d.body),
            d.body_sha256,
            d.source_created_at,
            d.source_updated_at,
            now,
        ),
    )
    return row[0] if row else None


def _documents(cur, ruling_id):
    cur.execute(
        "SELECT itemid, language, source_updated_at FROM ruling_documents WHERE ruling_id = %s",
        (ruling_id,),
    )
    return cur.fetchall()


def _best_itemid(cur, ruling_id):
    """The language version describing the ruling: ENG, then FRE, then any."""
    documents = _documents(cur, ruling_id)
    if not documents:
        return None
    return min(documents, key=lambda d: (language_priority(d[1]), d[0]))[0]


def _rebuild_ruling(cur, ruling_id, now):
    documents = _documents(cur, ruling_id)
    if not documents:
        cur.execute("DELETE FROM rulings WHERE id = %s", (ruling_id,))
        return
    best = _best_itemid(cur, ruling_id)
    cur.execute(
        "SELECT body FROM raw_records WHERE source = %s AND source_id = %s",
        (HudocSource.name, best),
    )
    r = parse_ruling(json.loads(cur.fetchone()[0]))
    updated = [d[2] for d in documents if d[2] is not None]
    cur.execute(
        "UPDATE rulings SET ecli = %s, kind = %s, formation = %s, ruling_date = %s,"
        " title = %s, importance = %s, separate_opinion = %s, originating_body = %s,"
        " type_code = %s, conclusion = %s, represented_by = %s, judges = %s,"
        " domestic_law = %s, strasbourg_case_law = %s, source_updated_at = %s,"
        " parsed_at = %s WHERE id = %s",
        (
            r.ecli,
            r.kind,
            r.formation,
            r.ruling_date,
            r.title,
            r.importance,
            r.separate_opinion,
            r.originating_body,
            r.type_code,
            r.conclusion,
            r.represented_by,
            r.judges,
            r.domestic_law,
            r.strasbourg_case_law,
            max(updated) if updated else None,
            now,
            ruling_id,
        ),
    )
    rows = {
        "ruling_applications": [(a, i) for i, a in enumerate(r.applications)],
        "ruling_respondents": [(s,) for s in r.respondents],
        "ruling_articles": list(r.articles),
        "ruling_keywords": [(k,) for k in r.keywords],
        "ruling_citations": [(c,) for c in r.citations],
    }
    for table, columns in SIDE_TABLES.items():
        cur.execute(f"DELETE FROM {table} WHERE ruling_id = %s", (ruling_id,))
        if rows[table]:
            placeholders = ", ".join(["%s"] * (len(columns) + 1))
            cur.executemany(
                f"INSERT INTO {table} (ruling_id, {', '.join(columns)}) VALUES ({placeholders})",
                [(ruling_id, *row) for row in rows[table]],
            )
