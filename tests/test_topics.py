"""judgments_core/topics.py + etpc/topics.py: vectors -> topics, on the scratch MariaDB.

The fit is replaced by a rule (no UMAP/HDBSCAN) except in the last test,
which runs BERTopic itself on three made-up groups.
"""

import json

import numpy as np
import pytest
from test_etpc_tables import rows, setup

from etpc.topics import EtpcTopics
from judgments_core.chat import ChatError
from judgments_core.topics import (
    Items,
    assign,
    centered,
    parse_label,
    representatives,
    topic_facts,
)

X, Y, OTHER = [1.0, 0.1, 0.0, 0.0], [0.1, 1.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]


def point(judgment, number, part, vector, start=0, length=10, articles=("6",)):
    payload = {
        "judgment_id": str(judgment),
        "number": number,
        "part": part,
        "start": start,
        "length": length,
        "articles": list(articles),
        "respondents": ["POL"],
    }
    return payload, vector


# Orzeczenia 1-3 o X, 4-6 o Y; każde ma fakty, prawo i zdanie odrębne (inny kierunek).
POINTS = [
    p
    for j in range(1, 7)
    for p in (
        point(j, 0, "fakty", X if j <= 3 else Y, 0, 5, ("6",) if j <= 3 else ("8",)),
        point(j, 1, "prawo", X if j <= 3 else Y, 5, 5, ("6",) if j <= 3 else ("8",)),
        point(j, 2, "opinia", OTHER, 10, 5),
    )
]
BODIES = {j: f"fakt{j}law-{j}opin{j}" for j in range(1, 7)}


class FakeVectors:
    collection = "etpc_fake"

    def __init__(self, points):
        self.points = points
        self.scrolls = 0

    def count(self):
        return len(self.points)

    def scroll(self, payload, exclude=None, page=1000):
        self.scrolls += 1
        skipped = (exclude or {}).get("part", [])
        for p, v in self.points:
            if p["part"] not in skipped:
                yield {k: p[k] for k in payload if k in p}, v


class RuleTopics(EtpcTopics):
    """ETPC topics with a fit by rule: X -> 0, Y -> 1, the first item an outlier."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fits = []

    def judgment_texts(self, cur, judgment_ids):
        return {j: BODIES[j] for j in judgment_ids}

    def fit(self, texts, vectors, min_cluster_size, min_samples):
        self.fits.append(list(texts))
        labels = np.where(vectors[:, 0] > vectors[:, 1], 0, 1)
        labels[0] = -1
        return labels, {0: ["xa", "xb"], 1: ["ya", "yb"]}


class FakeChat:
    def __init__(self, replies):
        self.replies = list(replies)
        self.messages = []

    def stream(self, messages):
        self.messages.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        yield reply


def topics_of(conn, tmp_path, points=POINTS, **kwargs):
    return RuleTopics(conn, FakeVectors(points), tmp_path / "cache", "fake-model", **kwargs)


def test_level_a_groups_rulings_by_their_mean_without_opinions(conn, tmp_path):
    setup(conn)
    model = topics_of(conn, tmp_path)
    built = model.build("A")
    assert (built.items, built.sample, built.topics) == (6, 6, 2)
    assert built.outliers == pytest.approx(1 / 6)
    # Odstające (orzeczenie 1) też dostaje najbliższy temat.
    assigned = dict(rows(conn, "SELECT ruling_id, topic FROM ruling_topics"))
    assert assigned[1] == assigned[2] == assigned[3] != assigned[4] == assigned[5] == assigned[6]
    # Wektor orzeczenia bez zdania odrębnego = dokładnie X.
    score = rows(conn, "SELECT score FROM ruling_topics WHERE ruling_id = 2")[0][0]
    assert score == pytest.approx(1.0, abs=1e-3)
    # Tekst do słów: najpierw prawo, potem fakty, bez zdania odrębnego.
    assert model.fits[-1][0] == "law-1\nfakt1"
    topics = {t["topic"]: t for t in model.store.topics_list("A")}
    x = topics[assigned[1]]
    assert (x["size"], x["keywords"], x["label"]) == (3, "xa, xb", "")
    assert json.loads(x["facts"]) == {"articles": [["6", 1.0]], "respondents": [["POL", 1.0]]}
    assert sorted(json.loads(x["representatives"])) == ["1", "2", "3"]
    assert all(e.startswith("law") for e in json.loads(x["excerpts"]))


def test_level_b_groups_law_chunks_and_replaces_old_topics(conn, tmp_path):
    setup(conn)
    model = topics_of(conn, tmp_path)
    model.build("B")
    model.build("B")  # drugi raz: stare tematy i przypisania zastąpione
    assert rows(conn, "SELECT COUNT(*), MIN(number), MAX(number) FROM chunk_topics")[0] == (6, 1, 1)
    assert len(model.store.topics_list("B")) == 2
    assert model.store.topics_list("A") == []
    reps = json.loads(model.store.topics_list("B")[0]["representatives"])
    assert all(r.endswith(":1") for r in reps)
    assert model.fits[-1][0] == "law-1"


def test_vectors_are_read_once_and_cached_until_the_store_changes(conn, tmp_path):
    setup(conn)
    first = topics_of(conn, tmp_path)
    first.build("A")
    again = topics_of(conn, tmp_path)
    again.build("A")
    assert again.vectors.scrolls == 0
    changed = topics_of(conn, tmp_path, points=POINTS[:-3])
    assert changed.build("A").items == 5
    assert changed.vectors.scrolls == 1
    refreshed = topics_of(conn, tmp_path, points=POINTS[:-3], refresh=True)
    refreshed.build("A")
    assert refreshed.vectors.scrolls == 1


def test_names_topics_and_stops_when_the_model_is_missing(conn, tmp_path):
    setup(conn)
    model = topics_of(conn, tmp_path)
    model.build("A")
    chat = FakeChat(["**NAZWA:** Prawo do sądu.\nOPIS: Dostęp do sądu.", ChatError("brak modelu")])
    assert model.name_topics(chat, "A", suffix="/no_think") == (1, 1)
    prompt = chat.messages[0][1]["content"]
    assert "xa, xb" in prompt or "ya, yb" in prompt
    assert "artykuły Konwencji" in prompt and prompt.endswith("/no_think")
    labels = [t["label"] for t in model.store.topics_list("A")]
    assert sorted(labels) == ["", "Prawo do sądu"]
    # Następny przebieg nazywa tylko brakujący.
    assert model.name_topics(FakeChat(["NAZWA: Życie rodzinne"]), "A") == (1, 0)
    assert sorted(t["label"] for t in model.store.topics_list("A")) == [
        "Prawo do sądu",
        "Życie rodzinne",
    ]
    listing = "\n".join(model.listing("A"))
    assert "Prawo do sądu" in listing and "słowa:" in listing


def test_assign_and_representatives():
    vectors = np.array([X, Y, X, [0.7, 0.7, 0, 0]], dtype=np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    centres = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float32)
    best, score = assign(vectors, centres, block=2)
    assert best.tolist() == [0, 1, 0, 0]
    assert score[0] == pytest.approx(score[2])
    items = Items("B", [7, 7, 8, 9], [1, 2, 1, 1], [], [])
    # Najlepsze dwa fragmenty są z orzeczenia 7: drugi pominięty.
    assert representatives(items, np.array([0, 1, 2]), np.array([0.9, 0.8, 0.5]), 2) == [0, 2]


def test_centering_removes_the_state_and_keeps_the_issue():
    # Dwa zagadnienia (oś 0 / oś 1) w dwóch państwach (przesunięcie na osi 2 / 3).
    rng = np.random.default_rng(1)
    issue = np.repeat(np.eye(4, dtype=np.float32)[:2], 40, axis=0)
    state = np.tile(np.repeat(np.eye(4, dtype=np.float32)[2:], 20, axis=0), (2, 1))
    vectors = issue + 3 * state + 0.01 * rng.standard_normal((80, 4)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    states = [["POL"] if s[2] else ["TUR"] for s in state]
    items = Items("A", list(range(80)), [None] * 80, [], [{"respondents": s} for s in states])
    items.vectors = vectors
    out = centered(items, ("respondents",), min_group=10)
    # Przed: najbliżej siebie te z jednego państwa; po: te z jednego zagadnienia.
    assert vectors[0] @ vectors[40] > vectors[0] @ vectors[20]
    assert out[0] @ out[20] > 0.9 > out[0] @ out[40]
    assert np.allclose(np.linalg.norm(out, axis=1), 1)
    assert centered(items, ()) is vectors
    # Za małe grupy zostają bez zmian.
    assert np.allclose(centered(items, ("respondents",), min_group=50), vectors)


def test_topic_facts_count_each_value_once_per_item():
    items = Items("A", [1, 2], [None, None], [], [{"a": ["6", "6", "13"]}, {"a": ["6"]}])
    assert topic_facts(items, [0, 1], {"a": ""}) == {"a": [["6", 1.0], ["13", 0.5]]}


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (
            "NAZWA: Przewlekłość postępowania\nOPIS: Sprawy o czas.",
            ("Przewlekłość postępowania", "Sprawy o czas."),
        ),
        ("nazwa: „Ekstradycja”.", ("Ekstradycja", None)),
        ("Wolność słowa dziennikarzy", ("Wolność słowa dziennikarzy", None)),
        ("", ("", None)),
    ],
)
def test_parse_label(reply, expected):
    assert parse_label(reply) == expected


def test_bertopic_finds_made_up_groups(tmp_path):
    pytest.importorskip("bertopic")
    rng = np.random.default_rng(0)
    centres = np.eye(3, 16, dtype=np.float32)
    vectors = np.concatenate([c + 0.05 * rng.standard_normal((60, 16)) for c in centres])
    vectors = (vectors / np.linalg.norm(vectors, axis=1, keepdims=True)).astype(np.float32)
    words = ["extradition torture", "property expropriation", "détention provisoire"]
    texts = [f"{words[i // 60]} case number {i}" for i in range(180)]
    model = EtpcTopics(None, None, tmp_path, "fake")
    labels, keywords = model.fit(texts, vectors, min_cluster_size=20, min_samples=5)
    assert len(keywords) == 3
    for group in range(3):
        assert len(set(labels[group * 60 : group * 60 + 60].tolist())) == 1
    assert {k[0] for k in keywords.values()} <= set(" ".join(words).split()) | set(words)
