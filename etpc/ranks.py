"""ruling_ranks and ruling_links: the citation graph between rulings, ranked.

HUDOC lists the application numbers a ruling cites (extractedappno and
sclappnos -> ruling_citations). A citation counts for the Grand Chamber
judgment with that application number dated no later than the citing ruling
(the Chamber judgment in the same case is cited as "[GC]" once the Grand
Chamber has ruled), else for the earliest such judgment (the merits judgment
rather than its later just-satisfaction one), else for the earliest decision.

The position follows the number of citing rulings. PageRank (a citation from
a much-cited ruling weighs more) only breaks ties: on its own it pushes 1999
rulings up, because citations point back in time and old rulings collect the
weight of whole chains.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict

log = logging.getLogger(__name__)

GRAND_CHAMBER = "GRANDCHAMBER"
DAMPING = 0.85
ITERATIONS = 40
BATCH = 5000


def citation_edges(rulings, applications, citations):
    """Distinct (citing id, cited id) pairs.

    rulings: {id: (kind, date, formation)}; applications: [(appno, ruling id)];
    citations: [(citing ruling id, cited appno)].
    """
    by_appno = defaultdict(list)
    for appno, ruling_id in applications:
        kind, date, formation = rulings[ruling_id]
        # wyroki przed decyzjami, w każdej grupie najwcześniejsze najpierw
        grand = kind == "judgment" and formation == GRAND_CHAMBER
        by_appno[appno].append((kind != "judgment", date, ruling_id, grand))
    for candidates in by_appno.values():
        candidates.sort()
    edges = set()
    for citing, appno in citations:
        citing_date = rulings[citing][1]
        known = [
            (cited, grand)
            for _, date, cited, grand in by_appno.get(appno, ())
            if cited != citing and date <= citing_date
        ]
        if known:
            cited = next((c for c, grand in known if grand), known[0][0])
            edges.add((citing, cited))
    return edges


def pagerank(nodes, edges, damping=DAMPING, iterations=ITERATIONS):
    """{node: score}, scores averaging 1.0 (sum = number of nodes)."""
    index = {node: i for i, node in enumerate(nodes)}
    n = len(index)
    if n == 0:
        return {}
    out_degree = [0] * n
    links = [(index[s], index[d]) for s, d in edges]
    for s, _ in links:
        out_degree[s] += 1
    rank = [1.0] * n
    for _ in range(iterations):
        # ranga orzeczeń, które nic nie cytują, rozkłada się na wszystkie
        dangling = sum(r for r, k in zip(rank, out_degree) if k == 0)
        base = (1 - damping) + damping * dangling / n
        new = [base] * n
        for s, d in links:
            new[d] += damping * rank[s] / out_degree[s]
        rank = new
    return {node: rank[i] for node, i in index.items()}


def refresh_ranks(cur):
    """Rebuild ruling_ranks from the citation tables; the caller commits."""
    started = time.monotonic()
    cur.execute("SELECT id, kind, ruling_date, formation FROM rulings")
    rulings = {i: (kind, date, formation) for i, kind, date, formation in cur.fetchall()}
    cur.execute("SELECT appno, ruling_id FROM ruling_applications")
    applications = cur.fetchall()
    cur.execute("SELECT ruling_id, cited_appno FROM ruling_citations")
    citations = cur.fetchall()
    edges = citation_edges(rulings, applications, citations)
    cited_by = defaultdict(int)
    for _, cited in edges:
        cited_by[cited] += 1
    scores = pagerank(list(rulings), edges)
    order = sorted(scores, key=lambda i: (-cited_by[i], -scores[i], i))
    rows = [(i, cited_by[i], scores[i], position) for position, i in enumerate(order, 1)]
    _replace(
        cur,
        "ruling_ranks",
        "INSERT INTO ruling_ranks (ruling_id, cited_by, pagerank, position) VALUES (%s, %s, %s, %s)",
        rows,
    )
    _replace(
        cur,
        "ruling_links",
        "INSERT INTO ruling_links (citing_id, cited_id) VALUES (%s, %s)",
        sorted(edges),
    )
    log.info(
        "ranking cytowań: %d orzeczeń, %d powiązań (%.0fs)",
        len(rows),
        len(edges),
        time.monotonic() - started,
    )


def _replace(cur, table, insert, rows):
    cur.execute(f"DELETE FROM {table}")
    for start in range(0, len(rows), BATCH):
        cur.executemany(insert, rows[start : start + BATCH])
