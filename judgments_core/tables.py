"""Parent of the project table writers: raw records -> project tables.

The Deriver hands a batch of raw records to a TableWriter (it is the
`derive(cursor, records)` callable). The common part is here: one SAVEPOINT
per record, so a failing record is rolled back alone and reported as a
problem (blad_parsera) instead of stopping the batch. A project's child says
what to write for one record (write) and what to do after the batch
(finish, e.g. rebuild the rulings the batch touched).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from judgments_core.source import utc_now


class TableWriter(ABC):
    parser_class = None  # klasa potomna judgments_core.parser.Parser

    def __init__(self, parser=None):
        self.parser = parser or self.parser_class()
        self.now = None

    def __call__(self, cur, records):
        return self.derive(cur, records)

    def derive(self, cur, records):
        """Write a batch; returns problems [(source_id, problem, details)]."""
        self.now = utc_now().replace(tzinfo=None)
        self.start(cur)
        problems = []
        for record in records:
            cur.execute("SAVEPOINT rekord")
            try:
                problems += self.write(cur, record, self.parser.load(record.body)) or []
            except Exception as e:  # noqa: BLE001 - każdy błąd rekordu jako problem
                cur.execute("ROLLBACK TO SAVEPOINT rekord")
                problems.append((record.source_id, "blad_parsera", f"{type(e).__name__}: {e}"))
        problems += self.finish(cur) or []
        return problems

    def start(self, cur):
        """Before a batch (reset what finish() needs)."""

    @abstractmethod
    def write(self, cur, record, data):
        """One record into the tables; returns its problems (or None)."""

    def finish(self, cur):
        """After the batch; returns problems (or None)."""

    @staticmethod
    def insert_rows(cur, table_columns, rows):
        """INSERT INTO table (columns) VALUES ... for each row; nothing for no rows."""
        if rows:
            marks = ", ".join(["%s"] * len(rows[0]))
            cur.executemany(f"INSERT INTO {table_columns} VALUES ({marks})", rows)
