"""ETPC import: HUDOC records into raw_records of the etpc database.

Child of the core Importer (the loop, resuming, limit and time budget are
common). Override here what ETPC needs done differently while importing.
"""

from __future__ import annotations

from judgments_core.importer import Importer


class EtpcImporter(Importer):
    """HUDOC (HudocSource) -> etpc.raw_records, source "hudoc"."""
