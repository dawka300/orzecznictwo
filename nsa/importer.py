"""NSA import: judgments of the administrative courts into raw_records of the nsa database.

Child of the core Importer (the loop, resuming, limit and time budget are
common). Override here what NSA needs done differently while importing.
"""

from __future__ import annotations

from judgments_core.importer import Importer


class NsaImporter(Importer):
    """JuDDGES dataset (JuddgesSource; later CBOSA) -> nsa.raw_records."""
