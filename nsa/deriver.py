"""NSA processing: raw_records -> the nsa tables, then statistics.

Two sources write the same tables, keyed by the CBOSA id: the JuDDGES dataset
(JSON rows, CBOSA as of 2025-03-05) and judgment pages fetched from CBOSA later
(HTML). One pass derives JuDDGES first, then CBOSA, so for a judgment in both
the CBOSA page (newer, maybe with the reasons added since) wins.

Children of the core Deriver (batches, resuming and problems are common).
"""

from __future__ import annotations

from judgments_core.deriver import Deriver, DeriveResult
from nsa.juddges import JuddgesSource
from nsa.source import CbosaSource
from nsa.stats import refresh_stats
from nsa.tables import CbosaTables, NsaTables


class CbosaDeriver(Deriver):
    source_name = CbosaSource.name
    writer_class = CbosaTables


class NsaDeriver(Deriver):
    source_name = JuddgesSource.name
    writer_class = NsaTables

    def run(self):
        """JuDDGES records, then CBOSA pages, sharing the limit and the time budget."""
        started = self.clock()
        first = super().run()
        if first.stopped_by:
            return first
        budget = 0
        if self.time_budget_s:
            budget = max(1, self.time_budget_s - int(self.clock() - started))
        cbosa = CbosaDeriver(
            self.store,
            from_start=self.from_start,
            limit=self.limit - first.records if self.limit else None,
            batch_size=self.batch_size,
            time_budget_s=budget,
            clock=self.clock,
            now=self.now,
            reader=self.reader,
        )
        try:
            cbosa.run()
        finally:
            done = cbosa.result
            self.result = DeriveResult(
                first.batches + done.batches,
                first.records + done.records,
                first.problems + done.problems,
                done.finished,
                done.stopped_by,
            )
        return self.result

    def after(self, cur):
        """Counts for the statistics page and the search form."""
        refresh_stats(cur)
