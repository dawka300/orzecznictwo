"""NSA processing: raw_records (source juddges) -> the nsa tables.

Child of the core Deriver (batches, resuming and problems are common).
"""

from __future__ import annotations

from judgments_core.deriver import Deriver
from nsa.juddges import JuddgesSource
from nsa.tables import NsaTables


class NsaDeriver(Deriver):
    source_name = JuddgesSource.name
    writer_class = NsaTables
