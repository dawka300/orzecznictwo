"""Embedding packages (judgments_core/packages.py) and the colleagues' script, end to end."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest
from test_etpc_embed import FakeEmbedder, FakeVectors, prepared
from test_etpc_tables import rows

from etpc.embed import EtpcEmbedJob
from judgments_core import packages
from judgments_core.db import MariaDbStore
from judgments_core.embed import read_progress

SCRIPT = Path(__file__).parent.parent / "docker" / "licz" / "licz_paczke.py"


def worker():
    spec = importlib.util.spec_from_file_location("licz_paczke", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Http:
        """LM Studio replaced by the fake model (the same vectors as the controls)."""

        def __init__(self, url, model, batch):
            pass

        def licz(self, texts):
            return np.asarray(FakeEmbedder().embed(texts), dtype=np.float32)

    module.Http = Http
    return module


def job(conn, vectors):
    return EtpcEmbedJob(conn, FakeEmbedder(), vectors, batch_size=4)


def test_package_computed_elsewhere_comes_back(conn, tmp_path):
    ruling_id = prepared(conn)
    vectors = FakeVectors()
    done = packages.export(job(conn, vectors), FakeEmbedder(), tmp_path, "etpc-0001", 1000)
    assert (done.name, done.judgments) == ("etpc-0001", 1) and done.chunks > 1
    texts_path, meta_path, vectors_path = packages.paths(tmp_path, "etpc-0001")
    assert texts_path.is_file() and meta_path.is_file()
    store = MariaDbStore(conn)
    assert store.packages_open_ids() == {ruling_id}
    # wektory tutaj pomija orzeczenie z otwartej paczki
    assert job(conn, vectors).run().judgments == 0
    assert packages.export(job(conn, vectors), FakeEmbedder(), tmp_path, "etpc-0002", 1000) is None

    # u kolegi: test szybkości bez wyniku, potem cała paczka (wznawialna, w częściach)
    script = worker()
    assert script.main([str(texts_path), "--silnik", "http", "--limit", "2"]) == 0
    assert not vectors_path.exists()
    assert script.main([str(texts_path), "--silnik", "http"]) == 0
    computed = np.load(vectors_path)
    assert computed.shape == (done.chunks, 3) and computed.dtype == np.float16

    saved, skipped = packages.import_package(job(conn, vectors), tmp_path, "etpc-0001")
    assert (saved, skipped) == (1, 0)
    chunks = vectors.points[str(ruling_id)]
    assert len(chunks) == done.chunks
    assert chunks[0].meta["itemid"] == "001-252650" and chunks[0].text_length > 0
    assert rows(conn, "SELECT ruling_id, model, chunks FROM ruling_vectors") == (
        (ruling_id, "fake-model", done.chunks),
    )
    assert store.packages_open_ids() == set()
    assert store.packages_list()[0][6] is not None  # zaimportowana
    # licznik na stronach: policzone po imporcie, status "paczki"
    with conn.cursor() as cur:
        progress = read_progress(cur, job(conn, vectors).progress_key)
    assert (progress["status"], progress["done"], progress["chunks"]) == (
        "paczki",
        1,
        done.chunks,
    )
    assert progress["total"] >= 1 and progress["eta_s"] is None


def test_wrong_vectors_or_changed_text(conn, tmp_path):
    ruling_id = prepared(conn)
    vectors = FakeVectors()
    done = packages.export(job(conn, vectors), FakeEmbedder(), tmp_path, "etpc-0001", 1000)
    _, _, vectors_path = packages.paths(tmp_path, "etpc-0001")

    np.save(vectors_path, np.zeros((done.chunks - 1, 3), dtype=np.float16))
    with pytest.raises(packages.PackageError, match="niedokończona"):
        packages.import_package(job(conn, vectors), tmp_path, "etpc-0001")
    other = np.tile(np.asarray([0.0, 1.0, 0.0], dtype=np.float16), (done.chunks, 1))
    np.save(vectors_path, other)
    with pytest.raises(packages.PackageError, match="inny model"):
        packages.import_package(job(conn, vectors), tmp_path, "etpc-0001")

    good = np.asarray(FakeEmbedder().embed(["x"] * done.chunks), dtype=np.float16)
    texts = [
        line for line in packages.read_meta(packages.paths(tmp_path, "etpc-0001")[1])[0]["controls"]
    ]
    good[: len(texts)] = np.asarray(texts, dtype=np.float16)
    np.save(vectors_path, good)
    rows(conn, f"UPDATE ruling_documents SET body_sha256 = 'nowy' WHERE ruling_id = {ruling_id}")
    assert packages.import_package(job(conn, vectors), tmp_path, "etpc-0001") == (0, 1)
    assert vectors.points == {}
    # zwolnione: wektory policzy je tutaj
    assert job(conn, vectors).run().judgments == 1
