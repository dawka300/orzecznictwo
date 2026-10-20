"""Project tables from raw_records, batch by batch, resumable like the Importer.

Deriver is the parent; a project's child names its source (source_name),
its table writer (writer_class, a judgments_core.tables.TableWriter child)
and may add work after a pass (after(), e.g. statistics). The writer is a
callable derive(cursor, records) returning problems [(source_id, problem,
details)]; they land in record_problems instead of stopping the pass. The
Deriver feeds it the records changed since the last pass, oldest first, and
saves how far it got in sync_state together with each batch. After a parser
change, a pass with from_start re-derives everything without downloading
anything again.

With a reader (judgments_core.db.RawReader: several connections) the next
batch is read in the background while the current one is written; reading
is the slow part of a big pass (random disk reads of the raw texts).
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime

from judgments_core.source import utc_now

log = logging.getLogger(__name__)

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass
class DeriveResult:
    batches: int = 0
    records: int = 0
    problems: int = 0
    finished: bool = False  # nie ma więcej zmienionych rekordów
    stopped_by: str = ""  # "limit" / "czas" przy wcześniejszym końcu


class Deriver:
    source_name = ""  # klasa potomna: np. "hudoc"
    writer_class = None  # klasa potomna: TableWriter projektu

    def __init__(
        self,
        store,
        source_name=None,
        derive=None,
        *,
        from_start=False,
        limit=None,
        batch_size=100,
        time_budget_s=0,
        clock=time.monotonic,
        now=utc_now,
        reader=None,
    ):
        self.store = store
        # RawReader: porcje czytane w tle kilkoma połączeniami; None = store, po kolei
        self.reader = reader
        self.source_name = source_name or self.source_name
        # derive(cursor, records); domyślnie TableWriter projektu
        self.derive = derive or self.writer_class()
        self.state_key = f"{self.source_name}:derived"
        self.from_start = from_start
        self.limit = limit
        self.batch_size = batch_size
        self.time_budget_s = time_budget_s
        self.clock = clock
        self.now = now

    def run(self):
        started = self.clock()
        result = self.result = DeriveResult()
        after = None if self.from_start else _load(self.store.get_state(self.state_key))
        # Tylko rekordy sprzed bieżącej sekundy (utc_now ucina mikrosekundy):
        # zapisane później mają changed_at >= tej granicy, więc nie mogą
        # wpaść przed zapisany znacznik i zostać pominięte.
        before = self.now()
        ahead = ThreadPoolExecutor(1) if self.reader is not None else None
        pending = None
        try:
            while True:
                if pending is None:
                    records = self._read(after, before, result.records)
                else:
                    records, pending = pending.result(), None
                if not records:
                    result.finished = True
                    return result
                last = records[-1]
                after = (last.changed_at, last.source_id)
                done = result.records + len(records)
                if ahead is not None and not (self.limit and done >= self.limit):
                    # następna porcja czyta się, gdy ta jest zapisywana
                    pending = ahead.submit(self._read, after, before, done)
                problems = self.store.derive_batch(
                    self.source_name, self.derive, records, self.state_key, _dump(after)
                )
                result.batches += 1
                result.records += len(records)
                result.problems += len(problems)
                for source_id, problem, details in problems:
                    log.warning("problem %s: %s (%s)", source_id, problem, details)
                log.info(
                    "porcja %d: %d rekordów (łącznie %d, problemów %d), do %s %s",
                    result.batches,
                    len(records),
                    result.records,
                    result.problems,
                    *after,
                )
                if self.limit and result.records >= self.limit:
                    result.stopped_by = "limit"
                    return result
                if self.time_budget_s and self.clock() - started >= self.time_budget_s:
                    result.stopped_by = "czas"
                    return result
        finally:
            if ahead is not None:
                ahead.shutdown(wait=True)  # porcja w drodze: dokończ odczyt, nie zapisuj

    def _read(self, after, before, done):
        size = self.batch_size
        if self.limit:
            size = min(size, self.limit - done)
        source = self.reader or self.store
        return source.raw_records_after(self.source_name, after, before, size)

    def after(self, cur):
        """After a pass that processed records (statistics, rankings ...); nothing by default."""


def _dump(after):
    changed_at, source_id = after
    return json.dumps({"changed_at": changed_at.strftime(TIME_FORMAT), "source_id": source_id})


def _load(state):
    if not state:
        return None
    data = json.loads(state)
    return datetime.strptime(data["changed_at"], TIME_FORMAT), data["source_id"]
