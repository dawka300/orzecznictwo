"""Counts of NSA judgments (judgment_stats) for the statistics page and the search form."""

from __future__ import annotations

from judgments_core.source import utc_now

NSA_COURT = "Naczelny Sąd Administracyjny"
DIMENSIONS = {
    "total": "'all'",
    "court": "court",
    "kind": "kind",
    "year": "YEAR(judgment_date)",
}


def refresh_stats(cur):
    """Rebuild judgment_stats from judgments (a few seconds for ~2M rows)."""
    now = utc_now().replace(tzinfo=None)
    cur.execute("DELETE FROM judgment_stats")
    for dimension, expression in DIMENSIONS.items():
        cur.execute(
            "INSERT INTO judgment_stats (dimension, value, judgments, wyroki, postanowienia, nsa, updated_at)"
            f" SELECT %s, {expression}, COUNT(*), SUM(kind = 'wyrok'), SUM(kind = 'postanowienie'),"
            f" SUM(court = %s), %s FROM judgments GROUP BY {expression}",
            (dimension, NSA_COURT, now),
        )
    # Symbole spraw: kod + najczęstszy opis
    cur.execute(
        "INSERT INTO judgment_stats (dimension, value, judgments, wyroki, postanowienia, nsa, updated_at)"
        " SELECT 'symbol', s.code, COUNT(*), SUM(j.kind = 'wyrok'), SUM(j.kind = 'postanowienie'),"
        " SUM(j.court = %s), %s FROM judgment_symbols s JOIN judgments j ON j.id = s.judgment_id"
        " GROUP BY s.code",
        (NSA_COURT, now),
    )


def read_stats(cur):
    """{dimension: [(value, judgments, wyroki, postanowienia, nsa, updated_at)]}, biggest first."""
    cur.execute(
        "SELECT dimension, value, judgments, wyroki, postanowienia, nsa, updated_at"
        " FROM judgment_stats ORDER BY dimension, judgments DESC"
    )
    stats = {}
    for dimension, *row in cur.fetchall():
        stats.setdefault(dimension, []).append(tuple(row))
    return stats


def symbol_names(cur, codes):
    """{code: description} - the most common description of each symbol code."""
    if not codes:
        return {}
    cur.execute(
        "SELECT code, description FROM judgment_symbols WHERE code IN %s AND description <> ''"
        " GROUP BY code, description",
        (list(codes),),
    )
    names = {}
    for code, description in cur.fetchall():
        names.setdefault(code, description)
    return names
