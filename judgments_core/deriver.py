"""Project tables from raw_records, batch by batch, resumable like the Importer.

A project gives a function derive(cursor, records) that writes its tables
from raw records (judgments_core.db.StoredRecord). The Deriver feeds it the
records changed since the last pass, oldest first, and saves how far it got
in sync_state together with each batch. After a parser change, a pass with
from_start re-derives everything without downloading anything again.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime

from judgments_core.source import utc_now

log = logging.getLogger(__name__)

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass
class DeriveResult:
    batches: int = 0
    records: int = 0
    finished: bool = False  # nie ma więcej zmienionych rekordów
    stopped_by: str = ""  # "limit" / "czas" przy wcześniejszym końcu


class Deriver:
    def __init__(
        self,
        store,
        source_name,
        derive,
        *,
        from_start=False,
        limit=None,
        batch_size=100,
        time_budget_s=0,
        clock=time.monotonic,
        now=utc_now,
    ):
        self.store = store
        self.source_name = source_name
        self.derive = derive
        self.state_key = f"{source_name}:derived"
        self.from_start = from_start
        self.limit = limit
        self.batch_size = batch_size
        self.time_budget_s = time_budget_s
        self.clock = clock
        self.now = now

    def run(self):
        started = self.clock()
        result = DeriveResult()
        after = None if self.from_start else _load(self.store.get_state(self.state_key))
        # Tylko rekordy sprzed bieżącej sekundy (utc_now ucina mikrosekundy):
        # zapisane później mają changed_at >= tej granicy, więc nie mogą
        # wpaść przed zapisany znacznik i zostać pominięte.
        before = self.now()
        while True:
            size = self.batch_size
            if self.limit:
                size = min(size, self.limit - result.records)
            records = self.store.raw_records_after(self.source_name, after, before, size)
            if not records:
                result.finished = True
                return result
            last = records[-1]
            after = (last.changed_at, last.source_id)
            self.store.derive_batch(self.derive, records, self.state_key, _dump(after))
            result.batches += 1
            result.records += len(records)
            log.info(
                "porcja %d: %d rekordów (łącznie %d), do %s %s",
                result.batches,
                len(records),
                result.records,
                *after,
            )
            if self.limit and result.records >= self.limit:
                result.stopped_by = "limit"
                return result
            if self.time_budget_s and self.clock() - started >= self.time_budget_s:
                result.stopped_by = "czas"
                return result


def _dump(after):
    changed_at, source_id = after
    return json.dumps({"changed_at": changed_at.strftime(TIME_FORMAT), "source_id": source_id})


def _load(state):
    if not state:
        return None
    data = json.loads(state)
    return datetime.strptime(data["changed_at"], TIME_FORMAT), data["source_id"]
