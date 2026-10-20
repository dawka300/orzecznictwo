"""ETPC processing: raw_records (source hudoc) -> the etpc tables, then statistics, ranks and weights.

Child of the core Deriver (batches, resuming and problems are common).
"""

from __future__ import annotations

from etpc.ranks import refresh_ranks
from etpc.source import HudocSource
from etpc.stats import refresh_stats
from etpc.tables import EtpcTables
from etpc.weights import refresh_weights
from judgments_core.deriver import Deriver


class EtpcDeriver(Deriver):
    source_name = HudocSource.name
    writer_class = EtpcTables

    def after(self, cur):
        """Statistics, the citation ranking and the weights (they read the ranking)."""
        refresh_stats(cur)
        refresh_ranks(cur)
        refresh_weights(cur)
