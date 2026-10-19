"""etpc/embed.py on the scratch MariaDB, with a fake embedder and vector store."""

from test_etpc_tables import derive, english, polish, rows, save, setup

from etpc.embed import EmbedJob


class FakeEmbedder:
    model = "fake-model"
    dimension = 3

    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [[1.0, 0.0, float(len(t))] for t in texts]


class FakeVectors:
    def __init__(self):
        self.points = {}  # judgment_id -> chunks
        self.collection = None

    def ensure_collection(self, dimension, indexes=None):
        self.collection = (dimension, sorted(indexes))

    def upsert(self, chunks):
        for c in chunks:
            self.points.setdefault(c.judgment_id, []).append(c)

    def delete_judgments(self, judgment_ids):
        for i in judgment_ids:
            self.points.pop(str(i), None)


def prepared(conn):
    store = setup(conn)
    save(store, english(), polish())
    derive(store)
    return rows(conn, "SELECT id FROM rulings")[0][0]


def run(conn, embedder=None, vectors=None, **options):
    embedder = embedder or FakeEmbedder()
    vectors = vectors if vectors is not None else FakeVectors()
    result = EmbedJob(conn, embedder, vectors, batch_size=4, **options).run()
    return result, embedder, vectors


def test_embeds_english_version_once(conn):
    ruling_id = prepared(conn)
    result, embedder, vectors = run(conn)
    assert (result.rulings, result.finished) == (1, True)
    chunks = vectors.points[str(ruling_id)]
    assert len(chunks) == result.chunks > 1
    assert all(len(batch) <= 4 for batch in embedder.calls)
    first = chunks[0]
    assert first.vector[:2] == [1.0, 0.0]
    assert first.meta["itemid"] == "001-252650" and first.meta["language"] == "ENG"
    assert first.meta["respondents"] == ["POL"] and first.meta["kind"] == "judgment"
    assert first.meta["context"].startswith("CASE OF ANDRZEJ RUCIŃSKI v. POLAND")
    assert vectors.collection[0] == 3
    assert rows(conn, "SELECT ruling_id, itemid, model, chunks FROM ruling_vectors") == (
        (ruling_id, "001-252650", "fake-model", len(chunks)),
    )
    again, embedder, _ = run(conn, vectors=vectors)
    assert (again.rulings, embedder.calls) == (0, [])


def test_changed_text_or_model_embeds_again(conn):
    prepared(conn)
    _, _, vectors = run(conn)
    rows(conn, "UPDATE ruling_vectors SET body_sha256 = 'stary'")
    assert run(conn, vectors=vectors)[0].rulings == 1
    other = FakeEmbedder()
    other.model = "inny-model"
    assert run(conn, embedder=other, vectors=vectors)[0].rulings == 1
    assert rows(conn, "SELECT model FROM ruling_vectors") == (("inny-model",),)


def test_deleted_ruling_loses_vectors(conn):
    ruling_id = prepared(conn)
    _, _, vectors = run(conn)
    rows(conn, "DELETE FROM rulings")
    result, _, _ = run(conn, vectors=vectors)
    assert result.removed == 1
    assert str(ruling_id) not in vectors.points
    assert rows(conn, "SELECT COUNT(*) FROM ruling_vectors") == ((0,),)


def test_time_budget_stops_before_work(conn):
    prepared(conn)
    ticks = iter(range(0, 1000, 100))
    result, embedder, _ = run(conn, time_budget_s=50, clock=lambda: next(ticks))
    assert (result.rulings, result.stopped_by, result.remaining, embedder.calls) == (
        0,
        "czas",
        1,
        [],
    )


def test_progress_saved(conn):
    from etpc.embed import read_progress

    prepared(conn)
    run(conn)
    with conn.cursor() as cur:
        progress = read_progress(cur)
    assert (
        progress["status"],
        progress["total"],
        progress["done"],
        progress["remaining_chars"],
    ) == (
        "gotowe",
        1,
        1,
        0,
    )
    assert progress["chunks"] > 1 and progress["model"] == "fake-model"


def test_progress_of_killed_pass(conn):
    from datetime import timedelta

    from etpc.embed import read_progress
    from judgments_core.source import utc_now

    prepared(conn)
    run(conn)
    rows(conn, "UPDATE sync_state SET value = REPLACE(value, '\"gotowe\"', '\"liczy\"')")
    with conn.cursor() as cur:
        assert read_progress(cur)["status"] == "liczy"
        assert read_progress(cur, now=utc_now() + timedelta(minutes=11))["status"] == "przerwano"
