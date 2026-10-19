"""ruling_stats: counts of rulings per language, formation, state, year ...

Rebuilt from scratch after each przetworz pass (a second or two on the full
set); readers see the old rows until the commit.
"""

from __future__ import annotations

# (dimension, value expression, FROM ... with alias r for rulings)
_DIMENSIONS = [
    ("formation", "r.formation", "rulings r"),
    ("respondent", "s.state", "ruling_respondents s JOIN rulings r ON r.id = s.ruling_id"),
    ("year", "YEAR(r.ruling_date)", "rulings r"),
    ("importance", "COALESCE(r.importance, 0)", "rulings r"),
    (
        "violation",
        "a.article",
        "ruling_articles a JOIN rulings r ON r.id = a.ruling_id AND a.relation = 'violation'",
    ),
]

_KINDS = """COUNT(DISTINCT r.id),
    COUNT(DISTINCT CASE WHEN r.kind = 'judgment' THEN r.id END),
    COUNT(DISTINCT CASE WHEN r.kind = 'decision' THEN r.id END)"""


def refresh_stats(cur):
    """Replace ruling_stats with fresh counts; the caller commits."""
    cur.execute("DELETE FROM ruling_stats")
    cur.execute(
        f"""INSERT INTO ruling_stats
            (dimension, value, rulings, judgments, decisions, documents, updated_at)
        SELECT 'total', 'all', {_KINDS},
            (SELECT COUNT(*) FROM ruling_documents), UTC_TIMESTAMP()
        FROM rulings r"""
    )
    cur.execute(
        f"""INSERT INTO ruling_stats
            (dimension, value, rulings, judgments, decisions, documents, updated_at)
        SELECT 'language', d.language, {_KINDS}, COUNT(*), UTC_TIMESTAMP()
        FROM ruling_documents d JOIN rulings r ON r.id = d.ruling_id
        GROUP BY d.language"""
    )
    for dimension, value, source in _DIMENSIONS:
        cur.execute(
            f"""INSERT INTO ruling_stats
                (dimension, value, rulings, judgments, decisions, documents, updated_at)
            SELECT %s, {value}, {_KINDS}, NULL, UTC_TIMESTAMP()
            FROM {source}
            GROUP BY {value}""",
            (dimension,),
        )


def read_stats(cur):
    """{dimension: [(value, rulings, judgments, decisions, documents, updated_at)]}, biggest first."""
    cur.execute(
        """SELECT dimension, value, rulings, judgments, decisions, documents, updated_at
        FROM ruling_stats ORDER BY dimension, rulings DESC, value"""
    )
    result = {}
    for dimension, *row in cur.fetchall():
        result.setdefault(dimension, []).append(tuple(row))
    return result
