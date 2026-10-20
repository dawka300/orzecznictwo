"""`python -m etpc sprawdz`: HUDOC vs raw_records, year by year.

For each year the HUDOC count is compared with the documents in raw_records
(by kpdate). Where they differ, the year's itemids are listed from HUDOC and
compared one by one: missing documents become record_problems
(stage pobierz, brak_w_bazie) for `pobierz --brakujace`; documents no longer
in HUDOC are recorded as nie_ma_w_hudoc.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass

from etpc.source import FIRST_YEAR

log = logging.getLogger(__name__)


@dataclass
class YearCheck:
    year: int
    hudoc: int
    ours: int
    missing: int = 0
    extra: int = 0


def verify(store, source, years=None):
    """Returns [YearCheck] for the years checked; problems go to the database."""
    ours = _ids_by_year(store, source.name)
    last_year = source.now().year
    checks = []
    for year in years or range(FIRST_YEAR, last_year + 1):
        check = YearCheck(year, source.count_year(year), len(ours.get(year, ())))
        if check.hudoc != check.ours:
            hudoc_ids = set(source.itemids_of_year(year))
            mine = ours.get(year, set())
            missing = sorted(hudoc_ids - mine)
            extra = sorted(mine - hudoc_ids)
            check.missing, check.extra = len(missing), len(extra)
            store.add_problems(
                source.name,
                "pobierz",
                [(i, "brak_w_bazie", f"rok {year}") for i in missing]
                + [(i, "nie_ma_w_hudoc", f"rok {year}") for i in extra],
            )
        log.info(
            "rok %d: HUDOC %d, baza %d%s",
            year,
            check.hudoc,
            check.ours,
            f" - brakuje {check.missing}, nadmiarowych {check.extra}"
            if check.hudoc != check.ours
            else "",
        )
        checks.append(check)
    return checks


def _ids_by_year(store, source_name):
    """{year: {itemid}} from raw_records - one pass over the table."""
    with store.conn.cursor() as cur:
        cur.execute(
            "SELECT source_id, LEFT(JSON_VALUE(body, '$.kpdate'), 4) FROM raw_records"
            " WHERE source = %s",
            (source_name,),
        )
        rows = cur.fetchall()
    store.conn.commit()
    by_year = defaultdict(set)
    for itemid, year in rows:
        if year and year.isdigit():
            by_year[int(year)].add(itemid)
    return by_year
