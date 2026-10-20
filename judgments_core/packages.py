"""Embedding packages: chunks computed on other computers (colleagues' GPUs, in docker).

Embedding a whole project on one card takes weeks; chunks are independent, so
the work splits into packages:

1. export (python -m <projekt> paczki): the next judgments the embedding job
   would take (or the last ones, --od-konca) are chunked exactly as wektory
   would chunk them; two files:
   - <name>.jsonl.gz - for the person who computes: a header line and one
     {"t": text} line per chunk (the text the model embeds); no database needed,
   - <name>.meta.jsonl.gz - stays here: per judgment its id, document, sha256
     and chunks (number, start, part, length, payload), plus reference vectors
     of the first CONTROLS chunks computed here (the query model).
   The judgments are recorded as an open package (vector_packages,
   vector_package_items), so wektory here skips them.
2. compute elsewhere: docker/licz/licz_paczke.py (sentence-transformers on
   CUDA in docker, or LM Studio) -> <name>.wektory.npy (float16, one row per
   chunk, in file order).
3. import (python -m <projekt> paczka-import <name>): the first CONTROLS
   vectors must match the reference ones (cosine >= MIN_COSINE: the same model,
   not another one or another quantization); judgments whose text changed since
   the export are skipped (wektory computes them again); the rest goes to the
   vector store and the tracking table exactly as wektory saves it.

numpy is needed only for the import (a local tool, not the hosting).
"""

from __future__ import annotations

import gzip
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from judgments_core.embed import Candidate
from judgments_core.vectors import Chunk

log = logging.getLogger(__name__)

FORMAT = 1
CONTROLS = 32  # tyle pierwszych fragmentów paczki liczymy też tutaj, do porównania
MIN_COSINE = 0.99  # LM Studio GGUF F16 vs sentence-transformers: ~0.9996


class PackageError(RuntimeError):
    """A package can't be imported; the message is for the user."""


@dataclass
class Exported:
    name: str
    judgments: int
    chunks: int
    chars: int
    path: Path


def paths(directory, name):
    """(texts for the computer, local metadata, vectors coming back)."""
    directory = Path(directory)
    return (
        directory / f"{name}.jsonl.gz",
        directory / f"{name}.meta.jsonl.gz",
        directory / f"{name}.wektory.npy",
    )


def next_name(store, project):
    return f"{project}-{len(store.packages_list()) + 1:04d}"


def export(job, controls_embedder, directory, name, size_chunks, from_end=False):
    """One package of about size_chunks chunks; None when nothing is left to embed."""
    model = job.embedder.model
    with job.conn.cursor() as cur:
        todo = [Candidate(*row) for row in job.candidates(cur, model)]
    job.conn.commit()
    elsewhere = job.store.packages_open_ids()
    todo = [c for c in todo if c.judgment_id not in elsewhere]
    if from_end:
        todo.reverse()
    loaded, size, position = [], 0, 0
    while position < len(todo) and size < size_chunks:
        group, position = job._load_group(todo, position)
        loaded += [item for item in group if item[3]]  # bez fragmentów (pusty tekst) pomijamy
        size = sum(len(chunks) for *_, chunks in loaded)
    if not loaded:
        return None
    texts = [job.chunker.embedding_input(c) for *_, chunks in loaded for c in chunks]
    controls = controls_embedder.embed(texts[:CONTROLS])
    texts_path, meta_path, _ = paths(directory, name)
    texts_path.parent.mkdir(parents=True, exist_ok=True)
    header = {"format": FORMAT, "name": name, "model": model, "count": len(texts)}
    with gzip.open(texts_path, "wt", encoding="utf-8") as f:
        f.write(json.dumps(header) + "\n")
        for text in texts:
            f.write(json.dumps({"t": text}, ensure_ascii=False) + "\n")
    with gzip.open(meta_path, "wt", encoding="utf-8") as f:
        controls = [[round(float(x), 6) for x in v] for v in controls]
        f.write(json.dumps({**header, "controls": controls}) + "\n")
        for candidate, document, sha, chunks in loaded:
            item = {
                "j": candidate.judgment_id,
                "d": document,
                "s": sha,
                "c": [[c.number, c.start, c.part, len(c.text), c.meta] for c in chunks],
            }
            f.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
    chars = sum(len(t) for t in texts)
    job.store.packages_add(name, model, [c.judgment_id for c, *_ in loaded], len(texts), chars)
    return Exported(name, len(loaded), len(texts), chars, texts_path)


def read_meta(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        header = json.loads(next(f))
        return header, [json.loads(line) for line in f]


def check_controls(vectors, controls):
    """The lowest cosine between the returned and the reference control vectors."""
    import numpy as np  # tylko lokalnie (requirements-dev)

    got = np.asarray(vectors[: len(controls)], dtype=np.float32)
    ref = np.asarray(controls, dtype=np.float32)
    cos = (got * ref).sum(1) / (np.linalg.norm(got, axis=1) * np.linalg.norm(ref, axis=1))
    return float(cos.min())


def import_package(job, directory, name):
    """Vectors of a package computed elsewhere -> vector store + tracking table.

    Returns (judgments saved, judgments skipped because their text changed).
    """
    import numpy as np  # tylko lokalnie (requirements-dev)

    _, meta_path, vectors_path = paths(directory, name)
    for path in (meta_path, vectors_path):
        if not path.is_file():
            raise PackageError(f"brak pliku {path}")
    header, judgments = read_meta(meta_path)
    if header.get("format") != FORMAT:
        raise PackageError(f"nieznany format paczki: {header.get('format')}")
    model = job.embedder.model
    if header["model"] != model:
        raise PackageError(f"paczka jest dla modelu {header['model']}, wektory liczy {model}")
    vectors = np.load(vectors_path)
    if vectors.ndim != 2 or len(vectors) != header["count"]:
        raise PackageError(
            f"wektorów {vectors.shape}, a paczka ma {header['count']} fragmentów"
            " (niedokończona albo nie ta paczka)"
        )
    cosine = check_controls(vectors, header["controls"])
    if cosine < MIN_COSINE:
        raise PackageError(
            f"wektory kontrolne się nie zgadzają (cosinus {cosine:.4f} < {MIN_COSINE}):"
            " inny model albo inna kwantyzacja"
        )
    log.info("kontrola: najniższy cosinus %.5f na %d fragmentach", cosine, len(header["controls"]))
    with job.conn.cursor() as cur:
        current = {row[0]: row[2] for row in job.candidates(cur, model)}
    job.conn.commit()
    job.vectors.ensure_collection(vectors.shape[1], job.payload_indexes)
    saved, skipped, row = 0, 0, 0
    group, group_chunks = [], []
    for item in judgments:
        count = len(item["c"])
        if current.get(item["j"]) != item["s"]:
            skipped += 1  # tekst zmieniony albo już policzone: zajmie się nim wektory
            row += count
            continue
        chunks = [
            Chunk(
                str(item["j"]),
                number,
                "",
                start,
                part,
                vector=[float(x) for x in vectors[row + k]],
                meta=meta,
                length=length,
            )
            for k, (number, start, part, length, meta) in enumerate(item["c"])
        ]
        row += count
        group.append((Candidate(item["j"], item["d"], item["s"], 0), item["d"], item["s"], chunks))
        group_chunks += chunks
        if len(group_chunks) >= job.group_chunks:
            job._save(group, group_chunks)
            saved += len(group)
            group, group_chunks = [], []
    if group:
        job._save(group, group_chunks)
        saved += len(group)
    job.store.packages_close(name, imported=True)
    return saved, skipped
