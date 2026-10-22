"""NSA import: judgments of the administrative courts into raw_records of the nsa database.

Child of the core Importer (the loop, resuming, limit and time budget are
common). Override here what NSA needs done differently while importing.
"""

from __future__ import annotations

from judgments_core.importer import Importer

CHUNK = 1000


class NsaImporter(Importer):
    """CBOSA pages (CbosaSource) or the JuDDGES dataset (JuddgesSource) -> nsa.raw_records."""

    def run(self):
        # CbosaSource nie ma kursora: pomija orzeczenia już pobrane z CBOSA
        # (--od-poczatku: pobiera wszystkie z plików jeszcze raz).
        if self.store is not None and self.resume and hasattr(self.source, "fetched"):
            name = self.source.name
            self.source.fetched = lambda ids: fetched_ids(self.store.conn, name, ids)
        return super().run()


def fetched_ids(conn, source, ids):
    """The ids among `ids` that raw_records already has for `source`."""
    found = set()
    with conn.cursor() as cur:
        for start in range(0, len(ids), CHUNK):
            part = ids[start : start + CHUNK]
            cur.execute(
                "SELECT source_id FROM raw_records WHERE source = %s"
                f" AND source_id IN ({', '.join(['%s'] * len(part))})",
                (source, *part),
            )
            found.update(row[0] for row in cur.fetchall())
    conn.commit()
    return found
