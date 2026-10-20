"""Project tables of NSA from raw records: NsaTables, the NSA child of TableWriter.

Each record is one judgment: its judgments row is upserted by CBOSA id and
its side tables replaced. Records without any text are skipped (problem
pusty_tekst); a failing record doesn't stop the batch (SAVEPOINT in TableWriter).
"""

from __future__ import annotations

from judgments_core.tables import TableWriter
from nsa.parser import JuddgesParser

SIDE_TABLES = (
    "judgment_judges",
    "judgment_symbols",
    "judgment_keywords",
    "judgment_results",
    "judgment_legal_bases",
    "judgment_related",
    "judgment_publications",
)


class NsaTables(TableWriter):
    """One judgment per record into judgments + side tables (NSA child of TableWriter)."""

    parser_class = JuddgesParser
    source = "juddges"  # kolumna judgments.source

    def write(self, cur, record, row):
        j = self.parser.parse(row)
        if not j.text:
            _delete(cur, j.cbosa_id)
            return [(record.source_id, "pusty_tekst", "orzeczenie bez tekstu")]
        judgment_id = _upsert(cur, j, self.source, self.now)
        _replace_side_tables(cur, judgment_id, j)
        if not j.docket_number:
            return [(record.source_id, "brak_sygnatury", "zapisane bez sygnatury")]
        return None


def _upsert(cur, j, source, now):
    text = j.text
    cur.execute(
        "INSERT INTO judgments (cbosa_id, docket_number, judgment_type, kind, court,"
        " judgment_date, submission_date, is_final, challenged_authority, result, thesis,"
        " sentence, reasons, dissenting_opinion, text_length, text_sha256, source, parsed_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id),"
        "  docket_number = VALUES(docket_number), judgment_type = VALUES(judgment_type),"
        "  kind = VALUES(kind), court = VALUES(court), judgment_date = VALUES(judgment_date),"
        "  submission_date = VALUES(submission_date), is_final = VALUES(is_final),"
        "  challenged_authority = VALUES(challenged_authority), result = VALUES(result),"
        "  thesis = VALUES(thesis), sentence = VALUES(sentence), reasons = VALUES(reasons),"
        "  dissenting_opinion = VALUES(dissenting_opinion), text_length = VALUES(text_length),"
        "  text_sha256 = VALUES(text_sha256), source = VALUES(source), parsed_at = VALUES(parsed_at)",
        (
            j.cbosa_id,
            j.docket_number,
            j.judgment_type[:100],
            j.kind,
            j.court[:100],
            j.judgment_date,
            j.submission_date,
            j.is_final,
            (j.challenged_authority or "")[:191] or None,
            "\n".join(j.results) or None,
            j.thesis,
            j.sentence,
            j.reasons,
            j.dissenting_opinion,
            len(text),
            j.text_sha256,
            source,
            now,
        ),
    )
    return cur.lastrowid


def _replace_side_tables(cur, judgment_id, j):
    for table in SIDE_TABLES:
        cur.execute(f"DELETE FROM {table} WHERE judgment_id = %s", (judgment_id,))
    TableWriter.insert_rows(
        cur,
        "judgment_judges (judgment_id, position, name, is_presiding, is_rapporteur)",
        [(judgment_id, i, name[:100], p, r) for i, (name, p, r) in enumerate(j.judges)],
    )
    TableWriter.insert_rows(
        cur,
        "judgment_symbols (judgment_id, code, description)",
        [(judgment_id, code, description[:255]) for code, description in j.symbols],
    )
    TableWriter.insert_rows(
        cur,
        "judgment_keywords (judgment_id, keyword)",
        _distinct([(judgment_id, k[:100]) for k in j.keywords]),
    )
    TableWriter.insert_rows(
        cur,
        "judgment_results (judgment_id, result)",
        _distinct([(judgment_id, r[:191]) for r in j.results]),
    )
    TableWriter.insert_rows(
        cur,
        "judgment_legal_bases (judgment_id, position, journal, article, law, link)",
        [
            (
                judgment_id,
                i,
                (b.journal or "")[:191] or None,
                b.article,
                b.law,
                (b.link or "")[:255] or None,
            )
            for i, b in enumerate(j.legal_bases)
        ],
    )
    TableWriter.insert_rows(
        cur,
        "judgment_related (judgment_id, position, related_cbosa_id, docket_number, judgment_date, judgment_type)",
        [
            (
                judgment_id,
                i,
                r.cbosa_id,
                r.docket_number[:50],
                r.judgment_date,
                (r.judgment_type or "")[:100] or None,
            )
            for i, r in enumerate(j.related)
        ],
    )
    TableWriter.insert_rows(
        cur,
        "judgment_publications (judgment_id, kind, position, text)",
        [(judgment_id, kind, i, text[:500]) for i, (kind, text) in enumerate(j.publications)],
    )


def _distinct(rows):
    """Rows unique after truncation (prefix-equal values collide in the primary key)."""
    seen, out = set(), []
    for row in rows:
        key = tuple(str(v).lower() for v in row)  # collation utf8mb4_unicode_ci
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


def _delete(cur, cbosa_id):
    cur.execute("DELETE FROM judgments WHERE cbosa_id = %s", (cbosa_id,))
