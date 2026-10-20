"""ruling_weights: how much a ruling matters, a tier A-D with the reasons shown next to it.

Simple thresholds anyone can check, no model:
- A kluczowe: HUDOC importance 1 (the Court's own editors), a Grand Chamber
  judgment, or a judgment cited by at least KEY_CITATIONS rulings;
- B ważne: importance 2, a Grand Chamber decision, or cited by at least
  IMPORTANT_CITATIONS;
- C zwykłe: importance 3 or cited by at least NOTED_CITATIONS;
- D rutynowe: the rest (mostly repetitive committee cases, rarely cited decisions).

Citations alone lift a decision to B at most: some routine decisions are cited
thousands of times in standard formulas (Josipović v. Serbia, strike-out and
non-exhaustion wording). Rebuilt after ruling_ranks, whose cited_by it reads.
"""

from __future__ import annotations

import logging
import time

from etpc.ranks import replace_rows

log = logging.getLogger(__name__)

TIERS = {"A": "kluczowe", "B": "ważne", "C": "zwykłe", "D": "rutynowe"}
GRAND_CHAMBER = ("GRANDCHAMBER", "DECGRANDCHAMBER")
KEY_CITATIONS = 100
IMPORTANT_CITATIONS = 20
NOTED_CITATIONS = 5
IMPORTANCE_REASONS = {
    1: "kluczowe w HUDOC",
    2: "wysoka ważność w HUDOC",
    3: "średnia ważność w HUDOC",
}


def weigh(kind, formation, importance, cited_by):
    """(tier, reasons) of one ruling."""
    judgment = kind == "judgment"
    grand = formation in GRAND_CHAMBER
    reasons = [IMPORTANCE_REASONS[importance]] if importance in IMPORTANCE_REASONS else []
    if grand:
        reasons.append("Wielka Izba")
    if cited_by:
        reasons.append(f"cytowania: {cited_by}")
    if importance == 1 or (judgment and (grand or cited_by >= KEY_CITATIONS)):
        tier = "A"
    elif importance == 2 or grand or cited_by >= IMPORTANT_CITATIONS:
        tier = "B"
    elif importance == 3 or cited_by >= NOTED_CITATIONS:
        tier = "C"
    else:
        tier = "D"
    return tier, "; ".join(reasons) or "bez wyróżnień"


def refresh_weights(cur):
    """Rebuild ruling_weights from rulings and ruling_ranks; the caller commits."""
    started = time.monotonic()
    cur.execute(
        """SELECT r.id, r.kind, r.formation, r.importance, COALESCE(k.cited_by, 0)
        FROM rulings r LEFT JOIN ruling_ranks k ON k.ruling_id = r.id"""
    )
    rows = [
        (i, *weigh(kind, formation, importance, cited))
        for i, kind, formation, importance, cited in cur.fetchall()
    ]
    replace_rows(
        cur,
        "ruling_weights",
        "INSERT INTO ruling_weights (ruling_id, tier, reasons) VALUES (%s, %s, %s)",
        rows,
    )
    counts = {t: sum(1 for _, tier, _ in rows if tier == t) for t in TIERS}
    log.info(
        "waga orzeczeń: %s (%.0fs)",
        ", ".join(f"{t} {n}" for t, n in counts.items()),
        time.monotonic() - started,
    )
