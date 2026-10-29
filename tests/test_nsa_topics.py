"""nsa/topics.py on the scratch MariaDB: float16 judgment vectors, level B only from the
slice of chunk_filter, texts and titles from the NSA tables. The fit is a rule."""

import numpy as np
import pytest
from test_etpc_tables import rows
from test_topics import OTHER, FakeVectors, X, Y

from judgments_core import db
from judgments_core.topics import Items, _kept_spans, _matches, _text_spans, centered
from nsa.__main__ import PROJECT
from nsa.topics import NsaTopics


def point(judgment, number, part, vector, kind="wyrok", year=2020, is_nsa=False, start=0):
    payload = {
        "judgment_id": str(judgment),
        "number": number,
        "part": part,
        "start": start,
        "length": 5,
        "court": "Naczelny Sąd Administracyjny" if is_nsa else "WSA w Gliwicach",
        "kind": kind,
        "symbols": ["6014"],
        "is_nsa": is_nsa,
        "year": year,
    }
    return payload, vector


# 1-3 o X, 4-6 o Y; 4-6 to postanowienia NSA z 2021 (wycinek poziomu B), 1-3 wyroki WSA.
POINTS = [
    p
    for j in range(1, 7)
    for p in (
        point(j, 0, "sentencja", OTHER, *(("postanowienie", 2021, True) if j > 3 else ())),
        point(
            j,
            1,
            "uzasadnienie",
            X if j <= 3 else Y,
            *(("postanowienie", 2021, True) if j > 3 else ()),
            start=18,  # za sentencją w judgment_body: "SENTENCJA\noddala\n\n"
        ),
    )
]


class RuleNsaTopics(NsaTopics):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fits = []

    def fit(self, texts, vectors, min_cluster_size, min_samples):
        self.fits.append((list(texts), vectors.dtype))
        labels = np.where(vectors[:, 0] > vectors[:, 1], 0, 1)
        names = {0: ["xa"], 1: ["ya"]}
        return labels, {t: names[t] for t in set(labels.tolist())}


def setup(conn):
    db.migrate(conn, ("core", db.CORE_MIGRATIONS), ("nsa", PROJECT.migrations))
    with conn.cursor() as cur:
        for j in range(1, 7):
            cur.execute(
                "INSERT INTO judgments (id, cbosa_id, docket_number, judgment_type, kind, court,"
                " judgment_date, sentence, reasons, text_sha256, text_length, source,"
                " parsed_at)"
                " VALUES (%s, %s, %s, 'Wyrok WSA', 'wyrok', 'WSA', '2020-01-02', 'oddala',"
                " %s, '', 0, 'cbosa', '2026-10-29')",
                (j, f"ID{j}", f"II SA/Gl {j}/20", f"uzasadnienie {j}"),
            )
    conn.commit()


def test_level_a_in_float16_without_the_operative_part(conn, tmp_path):
    setup(conn)
    model = RuleNsaTopics(conn, FakeVectors(POINTS), tmp_path / "cache", "fake", work_dir=tmp_path)
    built = model.build("A")
    assert (built.items, built.topics) == (6, 2)
    assert model.fits[-1][1] == np.float32  # dopasowanie na float32 z próbki
    assigned = dict(rows(conn, "SELECT judgment_id, topic FROM judgment_topics"))
    assert assigned[1] == assigned[2] == assigned[3] != assigned[4] == assigned[5]
    # Bez sentencji wektor orzeczenia = dokładnie X (float16: z dokładnością do 1e-3).
    score = rows(conn, "SELECT score FROM judgment_topics WHERE judgment_id = 1")[0][0]
    assert score == pytest.approx(1.0, abs=1e-3)
    assert np.load(tmp_path / "cache" / "A.npy").dtype == np.float16
    assert (tmp_path / "tematy" / "cache" / "A.npy").is_file()  # A w pliku mapowanym
    assert not list((tmp_path / "tematy" / "cache").glob("suma-*"))  # sumy usunięte
    # Tekst do słów z tabeli judgments (nagłówek części jak w judgment_body).
    assert model.fits[-1][0][0] == "UZASA"  # wycinek z judgment_body, bez sentencji
    listing = "\n".join(model.listing("A"))
    assert "II SA/Gl 1/20 - Wyrok WSA z 2020-01-02" in listing


def test_level_b_only_from_the_slice(conn, tmp_path):
    setup(conn)
    model = RuleNsaTopics(conn, FakeVectors(POINTS), tmp_path / "cache", "fake", work_dir=tmp_path)
    built = model.build("B")
    assert built.items == 3  # tylko postanowienia NSA od 2019 (4-6)
    assert sorted(j for j, _ in rows(conn, "SELECT judgment_id, number FROM chunk_topics")) == [
        4,
        5,
        6,
    ]


def test_matches_values_lists_and_ranges():
    payload = {"kind": "postanowienie", "year": 2021, "symbols": ["6014", "6550"]}
    assert _matches(payload, {"kind": "postanowienie", "year": (2019, None)})
    assert _matches(payload, {"symbols": "6550"})
    assert not _matches(payload, {"year": (2022, None)})
    assert not _matches(payload, {"is_nsa": True})


def test_kept_spans_give_the_same_text_as_all_spans():
    chunks = [(n % 2 == 0, n, (n * 10, 10)) for n in range(100)]
    assert _text_spans(_kept_spans(chunks, 55), 55) == _text_spans(chunks, 55)


def test_centering_in_place_keeps_float16():
    items = Items("A", [1, 2], [None, None], [[], []], [{"court": "a"}, {"court": "a"}])
    items.vectors = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float16)
    out = centered(items, ("court",), min_group=1, inplace=True)
    assert out is items.vectors and out.dtype == np.float16
    assert np.allclose(np.linalg.norm(out.astype(np.float32), axis=1), 1, atol=1e-3)
