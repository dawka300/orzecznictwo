"""The download loop: pages from a Source into a store, resumable at any page."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    pages: int = 0
    saved: int = 0
    rejected: int = 0
    new: int = 0  # nowe w bazie
    changed: int = 0  # były, ale treść się zmieniła
    cursor_start: str | None = None
    cursor: str | None = None  # ostatni zapisany kursor
    finished: bool = False  # źródło nie ma więcej stron
    stopped_by: str = ""  # "limit" / "czas" przy wcześniejszym końcu


class Importer:
    """Runs one pass. Without a store (dry run) nothing is saved or resumed.

    After every page the records and the cursor are saved in one transaction,
    so a pass killed at any moment (hosting limits, network) resumes from the
    last saved page and loses nothing.
    """

    def __init__(
        self,
        source,
        store=None,
        *,
        state_key=None,
        limit=None,
        time_budget_s=0,
        resume=True,
        accept=None,
        outputs=(),
        clock=time.monotonic,
    ):
        self.source = source
        self.store = store
        self.state_key = state_key or f"{source.name}:cursor"
        self.limit = limit
        self.time_budget_s = time_budget_s
        self.resume = resume
        self.accept = accept  # record -> bool; odrzucone nie są zapisywane
        self.outputs = list(outputs)
        self.clock = clock

    def run(self):
        started = self.clock()
        # self.result: liczby także wtedy, gdy przebieg skończy się wyjątkiem
        result = self.result = RunResult()
        cursor = None
        if self.store is not None and self.resume:
            cursor = self.store.get_state(self.state_key)
            if cursor:
                log.info("wznawiam od kursora %s", cursor)
        result.cursor_start = result.cursor = cursor

        for page in self.source.iter_pages(cursor):
            records, page_cursor = page.records, page.cursor
            if self.limit and len(records) > self.limit - result.saved:
                records = records[: self.limit - result.saved]
                # Kursor wskazuje koniec całej strony - po przycięciu zostaje
                # stary, a następny przebieg pobierze tę stronę jeszcze raz.
                page_cursor = None
            accepted = [r for r in records if self.accept is None or self.accept(r)]
            result.rejected += len(records) - len(accepted)
            for record in accepted:
                for output in self.outputs:
                    output(record)
            if self.store is not None:
                stats = self.store.save_page(
                    self.source.name, accepted, self.state_key, page_cursor
                )
                if stats is not None:
                    result.new += stats.new
                    result.changed += stats.changed
            if page_cursor is not None:
                result.cursor = page_cursor
            result.pages += 1
            result.saved += len(accepted)
            log.info(
                "strona %d: %d rekordów (łącznie %d, odrzucono %d), kursor %s",
                result.pages,
                len(page.records),
                result.saved,
                result.rejected,
                page_cursor,
            )
            if self.limit and result.saved >= self.limit:
                result.stopped_by = "limit"
                return result
            if self.time_budget_s and self.clock() - started >= self.time_budget_s:
                result.stopped_by = "czas"
                return result
        result.finished = True
        return result
