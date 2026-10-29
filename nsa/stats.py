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


# CBOSA nie podaje opisu tych symboli (pusty także na stronach orzeczeń); ustalone
# z orzeczeń: 658/659 mają w 89% sygnatury SAB (skargi na bezczynność i przewlekłość),
# 6560 występuje razem z VAT, PIT i CIT, 6561 z podatkami lokalnymi.
INFERRED_SYMBOLS = {
    "658": "Skargi na bezczynność organu (opis ustalony z orzeczeń)",
    "659": "Skargi na przewlekłe prowadzenie postępowania (opis ustalony z orzeczeń)",
    "6560": "Interpretacje przepisów prawa podatkowego (opis ustalony z orzeczeń)",
    "6561": "Interpretacje w sprawach podatków i opłat lokalnych (opis ustalony z orzeczeń)",
}
# Opisy symboli się nie zmieniają, a zapytanie grupuje judgment_symbols (sekundy):
# raz na proces strony.
_names = {}


def symbol_names(cur, codes):
    """{code: description} - the most common description of each symbol code; codes
    CBOSA leaves without one get INFERRED_SYMBOLS, "Inne o symbolu podstawowym ..." (x9,
    as CBOSA writes it) or the description of their basic symbol (first 3 digits)."""
    codes = list(codes)
    bases = {c[:3] for c in codes if len(c) == 4}
    missing = sorted({*codes, *bases} - _names.keys())
    if missing:
        cur.execute(
            "SELECT code, description FROM judgment_symbols WHERE code IN %s AND description <> ''"
            " GROUP BY code, description",
            (missing,),
        )
        found = {}
        for code, description in cur.fetchall():
            found.setdefault(code, description)
        _names.update({c: found.get(c, "") for c in missing})
    names = {}
    for code in codes:
        base = code[:3]
        name = _names.get(code) or INFERRED_SYMBOLS.get(code, "")
        if not name and len(code) == 4 and code[0] == "6":
            if code.endswith("9"):
                name = f"Inne o symbolu podstawowym {base}"
            elif _names.get(base) or INFERRED_SYMBOLS.get(base):
                name = f"{_names.get(base) or INFERRED_SYMBOLS[base]} (symbol podstawowy {base})"
        if name:
            names[code] = name
    return names
