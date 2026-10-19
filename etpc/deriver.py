"""ETPC processing: raw_records (source hudoc) -> the etpc tables, then statistics and ranks.

Child of the core Deriver (batches, resuming and problems are common).
"""

from __future__ import annotations

from etpc.ranks import refresh_ranks
from etpc.source import HudocSource
from etpc.stats import refresh_stats
from etpc.tables import EtpcTables
from judgments_core.deriver import Deriver


class EtpcDeriver(Deriver):
    source_name = HudocSource.name
    writer_class = EtpcTables

    def after(self, cur):
        """Statistics and the citation ranking (a few seconds)."""
        refresh_stats(cur)
        refresh_ranks(cur)
