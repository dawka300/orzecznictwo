"""The JuDDGES/pl-nsa dataset (Hugging Face) as a Source: CBOSA up to 2025-03.

https://huggingface.co/datasets/JuDDGES/pl-nsa - judgments of the NSA and all
WSA scraped from orzeczenia.nsa.gov.pl by the JuDDGES project, 46 parquet
files (~31 GB). Each row becomes one RawRecord (source_id = the CBOSA id) as
JSON, without full_text: it only repeats sentence + reasons + ... (the parser
uses the parts). Files are downloaded once into DATA_DIR and read in batches;
the cursor is {"file": n, "row": k}.

Needs pyarrow (and tzdata on Windows): only where the import runs, not on the
hosting, which gets the finished database.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

import requests

from judgments_core.source import Page, RawRecord, Source

log = logging.getLogger(__name__)

DATASET = "JuDDGES/pl-nsa"
FILES_URL = f"https://huggingface.co/api/datasets/{DATASET}/tree/main/data"
FILE_URL = f"https://huggingface.co/datasets/{DATASET}/resolve/main/data/{{name}}"
DATA_DIR = Path(__file__).resolve().parent / "database" / "juddges"
CBOSA_URL = "https://orzeczenia.nsa.gov.pl/doc/{id}"
BATCH_ROWS = 500
SKIPPED_COLUMNS = ("full_text",)


class JuddgesSource(Source):
    """Local parquet files of the dataset - the NSA child of Source (no HTTP client needed)."""

    name = "juddges"

    def __init__(self, settings=None, client=None, data_dir=DATA_DIR, files=None):
        super().__init__(settings, client)
        self.data_dir = Path(data_dir)
        self._files = files  # nazwy plików; None = lista z Hugging Face

    def files(self):
        """data_0.parquet, data_1.parquet, ... in numeric order."""
        if self._files is None:
            response = requests.get(FILES_URL, timeout=60)
            response.raise_for_status()
            names = [
                Path(f["path"]).name for f in response.json() if f["path"].endswith(".parquet")
            ]
            self._files = sorted(
                names, key=lambda n: int("".join(c for c in n if c.isdigit()) or 0)
            )
        return self._files

    def iter_pages(self, cursor):
        state = json.loads(cursor) if cursor else {"file": 0, "row": 0}
        files = self.files()
        for number in range(state["file"], len(files)):
            path = self._local(files[number])
            skip = state["row"] if number == state["file"] else 0
            for row, records in iter_rows(path, skip):
                yield Page(records, json.dumps({"file": number, "row": row}))
            # Następny plik od początku (kursor "za" ostatnim wierszem tego).
            yield Page([], json.dumps({"file": number + 1, "row": 0}))

    def _local(self, name):
        path = self.data_dir / name
        if not path.exists():
            download(FILE_URL.format(name=name), path)
        return path


def iter_rows(path, skip=0):
    """(rows read so far, [RawRecord]) per batch of a parquet file, after `skip` rows."""
    import pyarrow.parquet as pq  # tylko tam, gdzie działa import

    parquet = pq.ParquetFile(path)
    columns = [c for c in parquet.schema_arrow.names if c not in SKIPPED_COLUMNS]
    position = 0
    for batch in parquet.iter_batches(batch_size=BATCH_ROWS, columns=columns):
        rows = batch.to_pylist()
        start, position = position, position + len(rows)
        if position <= skip:
            continue
        yield position, [to_record(r) for r in rows[max(0, skip - start) :]]


def to_record(row):
    cbosa_id = (row.get("judgment_id") or "").rsplit("/", 1)[-1]
    body = json.dumps(row, ensure_ascii=False, default=_json_value, sort_keys=True)
    return RawRecord(cbosa_id, body, url=CBOSA_URL.format(id=cbosa_id))


def _json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"{type(value).__name__} nie do JSON")


def download(url, path):
    """A large file to path (through a .part file, so a broken download is not used)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + ".part")
    log.info("pobieram %s", url)
    with requests.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        with open(part, "wb") as f:
            f.writelines(response.iter_content(chunk_size=1 << 20))
    part.replace(path)
    log.info("zapisano %s (%d MB)", path.name, path.stat().st_size // 1_000_000)
