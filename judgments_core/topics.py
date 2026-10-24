"""Parent of the project topic models (command tematy): BERTopic over the vectors already in Qdrant.

Two levels:
- A (judgments): one vector per judgment = the mean of its chunks (parts in
  judgment_skipped_parts left out, e.g. separate opinions) -> groups of cases;
- B (chunks): every chunk of the parts in chunk_parts (e.g. THE LAW) on its
  own -> legal issues cutting across cases.

Steps: the vectors come from the store in one scroll (kept in cache_dir,
reading ~1M vectors over REST takes long; read again when the number of
points changed or with refresh); BERTopic fits (a sample of) them: UMAP ->
HDBSCAN -> c-TF-IDF keywords from the texts. Then every item, also outside
the sample and HDBSCAN's outliers, goes to the nearest topic centre (cosine,
the score is kept), so every judgment / chunk has a topic. Representatives =
members closest to the centre (one per judgment); their excerpts and the
keywords let the chat model name the topic in Polish (name_topics, resumable:
a topic is saved with an empty label first). Saved by db.MariaDbStore.topics_*.

numpy, BERTopic, UMAP and HDBSCAN are imported only when fitting: compiled
packages, local only (requirements-dev.txt, the www image), not the hosting.

A project's child gives the parts, the stop words, the texts
(judgment_texts), case titles for the listing, the naming context and the
assignment tables.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from judgments_core.chat import ChatError, without_thinking
from judgments_core.db import MariaDbStore

log = logging.getLogger(__name__)

LEVELS = ("A", "B")
LEVEL_NAMES = {"A": "całe orzeczenia", "B": "fragmenty uzasadnień prawnych"}
PROGRESS_EVERY_S = 60
KEYWORDS = 12
REPRESENTATIVES = 5
EXCERPT_CHARS = 700
FACTS_TOP = 5
# Słowa: same litery, co najmniej 3 (bez numerów skarg, paragrafów, dat).
TOKEN_PATTERN = r"(?u)\b[^\W\d_]{3,}\b"
ASSIGN_BLOCK = 50000
# Słownik słów kluczowych: z tylu tekstów, słowo (para słów) w co najmniej tylu.
VOCABULARY_SAMPLE = 20000
VOCABULARY_MIN_DF = 5
CACHE_VERSION = 1
# Grupa (np. państwo) mniejsza niż tyle: bez odejmowania średniej (byłaby to ona sama).
CENTER_MIN_GROUP = 30


@dataclass
class Items:
    """What a level groups: per item its judgment, chunk number (None for A),
    text spans [(start, length)] in the judgment's text and payload facts."""

    level: str
    judgments: list
    numbers: list
    spans: list
    facts: list
    vectors: object = None  # numpy (n, d) float32, wektory długości 1

    def __len__(self):
        return len(self.judgments)

    def key(self, i):
        number = self.numbers[i]
        return str(self.judgments[i]) if number is None else f"{self.judgments[i]}:{number}"


@dataclass
class Built:
    level: str
    items: int = 0
    sample: int = 0
    topics: int = 0
    outliers: float = (
        0.0  # udział poza tematami HDBSCAN w próbce (potem przypisane do najbliższego)
    )
    mean_score: float = 0.0


class TopicModel:
    # Części fragmentów pomijane w wektorze orzeczenia (poziom A), np. zdania odrębne.
    judgment_skipped_parts: ClassVar[tuple] = ()
    # Części, których fragmenty grupuje poziom B; ich tekst daje też słowa poziomu A.
    chunk_parts: ClassVar[tuple] = ()
    # Pola payloadu liczone w temacie: {pole: opis dla modelu nazywającego}.
    fact_fields: ClassVar[dict] = {}
    # Pola payloadu, których średnią grupy odejmujemy od wektorów przed
    # dopasowaniem (np. państwo, język): bez tego tematy dzielą się po nich.
    center_by: ClassVar[tuple] = ()
    stop_words: ClassVar[frozenset] = frozenset()
    # Czym są teksty (dla modelu nazywającego), np. "orzeczenia ETPC (EN/FR)".
    corpus = ""
    # Tabele przypisań: (tabela, kolumny) jak w db.MariaDbStore.topics_replace.
    assignment_tables: ClassVar[dict] = {}
    # Domyślne parametry poziomów (opcje polecenia je zmieniają).
    min_cluster_size: ClassVar[dict] = {"A": 40, "B": 150}
    min_samples: ClassVar[dict] = {"A": 10, "B": 15}
    sample_size: ClassVar[dict] = {"A": 0, "B": 150000}  # 0 = wszystkie
    text_chars: ClassVar[dict] = {"A": 6000, "B": 2500}

    def __init__(self, conn, vectors, cache_dir, model_name, *, refresh=False):
        self.conn = conn
        self.store = MariaDbStore(conn)
        self.vectors = vectors
        self.cache_dir = Path(cache_dir)
        self.model_name = model_name
        self.refresh = refresh
        self._levels = None

    # --- dla klasy potomnej -------------------------------------------------

    def judgment_texts(self, cur, judgment_ids):
        """{judgment id: the text the chunks' start/length point into}."""
        raise NotImplementedError

    def judgment_titles(self, cur, judgment_ids):
        """{judgment id: short description (case, date)} for the listing."""
        return {}

    # --- budowa -------------------------------------------------------------

    def build(self, level, *, min_cluster_size=None, min_samples=None, sample=None):
        """Fit the topics of a level and save them (labels empty); returns Built."""
        import numpy as np

        items = self.items(level)
        result = Built(level, items=len(items))
        if not len(items):
            log.warning("poziom %s: brak wektorów", level)
            return result
        size = self.sample_size[level] if sample is None else sample
        chosen = _sample(len(items), size)
        result.sample = len(chosen)
        log.info("poziom %s: %d elementów, dopasowanie na %d", level, len(items), len(chosen))
        texts = self.texts(items, chosen, self.text_chars[level])
        vectors = centered(items, self.center_by)
        fitted = vectors[chosen]
        labels, keywords = self.fit(
            texts,
            fitted,
            min_cluster_size or self.min_cluster_size[level],
            min_samples or self.min_samples[level],
        )
        del texts
        result.outliers = float(np.mean(labels < 0)) if len(labels) else 0.0
        topics = sorted(keywords)
        if not topics:
            log.warning("poziom %s: HDBSCAN nie znalazł tematów (mniejsze --min-temat?)", level)
            return result
        centres = np.stack([_unit(fitted[labels == t].mean(axis=0)) for t in topics])
        del fitted
        best, score = assign(vectors, centres)
        del vectors
        # Numery od największego tematu po przypisaniu wszystkich.
        sizes = np.bincount(best, minlength=len(topics))
        order = np.argsort(-sizes, kind="stable")
        renumber = np.empty_like(order)
        renumber[order] = np.arange(len(order))
        best = renumber[best]
        rows = []
        for new, old in enumerate(order):
            members = np.flatnonzero(best == new)
            reps = representatives(items, members, score[members])
            rows.append(
                {
                    "topic": new,
                    "keywords": ", ".join(keywords[topics[old]]),
                    "size": len(members),
                    "facts": json.dumps(topic_facts(items, members, self.fact_fields)),
                    "representatives": json.dumps([items.key(i) for i in reps]),
                    "reps": reps,
                    "model": self.model_name,
                }
            )
        excerpts = self.texts(items, [i for r in rows for i in r["reps"]], EXCERPT_CHARS)
        position = 0
        for row in rows:
            n = len(row.pop("reps"))
            row["excerpts"] = json.dumps(excerpts[position : position + n], ensure_ascii=False)
            position += n
        table, columns = self.assignment_tables[level]
        assignments = [
            (
                *(
                    (items.judgments[i],)
                    if level == "A"
                    else (items.judgments[i], items.numbers[i])
                ),
                int(best[i]),
                round(float(score[i]), 4),
            )
            for i in range(len(items))
        ]
        self.store.topics_replace(level, rows, table, columns, assignments)
        result.topics = len(rows)
        result.mean_score = float(score.mean())
        return result

    def fit(self, texts, vectors, min_cluster_size, min_samples):
        """BERTopic on precomputed vectors: (labels per item, -1 = outlier; {topic: keywords})."""
        import numpy as np
        from bertopic import BERTopic
        from bertopic.vectorizers import ClassTfidfTransformer
        from hdbscan import HDBSCAN
        from sklearn.feature_extraction.text import CountVectorizer
        from umap import UMAP

        model = BERTopic(
            umap_model=UMAP(
                n_neighbors=15, n_components=5, min_dist=0.0, metric="cosine", low_memory=True
            ),
            hdbscan_model=HDBSCAN(
                min_cluster_size=min_cluster_size,
                min_samples=min_samples,
                metric="euclidean",
                cluster_selection_method="eom",
                core_dist_n_jobs=-1,
            ),
            # BERTopic liczy słowa na tekstach tematów (sklejonych), więc min_df
            # odcinałby słowa jednego tematu; słownik z góry, z próbki dokumentów.
            vectorizer_model=CountVectorizer(
                vocabulary=self.vocabulary(texts),
                stop_words=sorted(self.stop_words),
                token_pattern=TOKEN_PATTERN,
                ngram_range=(1, 2),
            ),
            ctfidf_model=ClassTfidfTransformer(reduce_frequent_words=True),
            top_n_words=KEYWORDS,
            # "english" usuwa z tekstów wszystko spoza [A-Za-z0-9 ]: francuskie
            # słowa słownika nie występowałyby nigdzie (df = 0, idf nieskończone).
            language="multilingual",
            calculate_probabilities=False,
            verbose=True,
        )
        labels, _ = model.fit_transform(texts, embeddings=vectors)
        labels = np.asarray(labels)
        keywords = {
            int(t): [w for w, _ in model.get_topic(t)][:KEYWORDS]
            for t in sorted(set(labels.tolist()))
            if t >= 0
        }
        return labels, keywords

    def vocabulary(self, texts, sample=VOCABULARY_SAMPLE, min_df=VOCABULARY_MIN_DF):
        """Words and word pairs in at least min_df documents of a sample (names of
        one case drop out; the sample keeps the memory of all pairs small)."""
        from sklearn.feature_extraction.text import CountVectorizer

        chosen = [texts[i] for i in _sample(len(texts), sample)]
        vectorizer = CountVectorizer(
            stop_words=sorted(self.stop_words),
            token_pattern=TOKEN_PATTERN,
            ngram_range=(1, 2),
            min_df=min(min_df, len(chosen)),
            dtype="int32",
        )
        vectorizer.fit(chosen)
        log.info(
            "słownik: %d słów i par słów z %d tekstów", len(vectorizer.vocabulary_), len(chosen)
        )
        return sorted(vectorizer.vocabulary_)

    # --- wektory ------------------------------------------------------------

    def items(self, level):
        if self._levels is None:
            self._levels = self._cached() or self._scan()
        return self._levels[level]

    def _scan(self):
        """Both levels from one scroll over the store (A: mean per judgment, B: chunks)."""
        import numpy as np

        extra = [*self.fact_fields, *(f for f in self.center_by if f not in self.fact_fields)]
        fields = ["judgment_id", "number", "part", "start", "length", *extra]
        exclude = (
            {"part": list(self.judgment_skipped_parts)} if self.judgment_skipped_parts else None
        )
        sums, chunks, facts = {}, {}, {}
        b_vectors = []
        b = Items("B", [], [], [], [])
        # Liczba wszystkich punktów (też pominiętych części) = klucz pamięci podręcznej.
        points = self.vectors.count()
        started = last = time.monotonic()
        seen = 0
        for payload, vector in self.vectors.scroll(fields, exclude=exclude):
            judgment = int(payload["judgment_id"])
            v = np.asarray(vector, dtype=np.float32)
            if judgment in sums:
                sums[judgment] += v
            else:
                sums[judgment] = v.copy()
                facts[judgment] = {f: payload.get(f) for f in extra}
            text_part = payload.get("part") in self.chunk_parts
            span = (int(payload["start"]), int(payload["length"]))
            chunks.setdefault(judgment, []).append((not text_part, payload["number"], span))
            if text_part:
                b.judgments.append(judgment)
                b.numbers.append(int(payload["number"]))
                b.spans.append([span])
                b.facts.append(facts[judgment])
                b_vectors.append(v)
            seen += 1
            if time.monotonic() - last >= PROGRESS_EVERY_S:
                last = time.monotonic()
                log.info("wektory z Qdranta: %d (%.0f/s)", seen, seen / (last - started))
        log.info("wektory z Qdranta: %d punktów, %d orzeczeń", seen, len(sums))
        judgments = sorted(sums)
        a = Items(
            "A",
            judgments,
            [None] * len(judgments),
            [_text_spans(chunks[j], self.text_chars["A"]) for j in judgments],
            [facts[j] for j in judgments],
        )
        a.vectors = np.stack([_unit(sums[j]) for j in judgments]).astype(np.float32)
        del sums
        b.vectors = _units(np.stack(b_vectors)) if b_vectors else np.zeros((0, a.vectors.shape[1]))
        levels = {"A": a, "B": b}
        self._save_cache(levels, points)
        return levels

    def _cache_key(self):
        return {
            "version": CACHE_VERSION,
            "collection": getattr(self.vectors, "collection", ""),
            "skipped": list(self.judgment_skipped_parts),
            "chunk_parts": list(self.chunk_parts),
            "facts": [*self.fact_fields, *self.center_by],
        }

    def _cached(self):
        import numpy as np

        meta_path = self.cache_dir / "meta.json"
        if self.refresh or not meta_path.is_file():
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        points = self.vectors.count()
        if meta.get("key") != self._cache_key() or meta.get("points") != points:
            log.info(
                "wektory w Qdrancie zmieniły się (%s -> %d): czytam od nowa",
                meta.get("points"),
                points,
            )
            return None
        levels = {}
        for level in LEVELS:
            m = meta[level]
            items = Items(level, m["judgments"], m["numbers"], m["spans"], m["facts"])
            items.vectors = np.load(self.cache_dir / f"{level}.npy")
            levels[level] = items
        log.info("wektory z pamięci podręcznej %s (%d punktów)", self.cache_dir, points)
        return levels

    def _save_cache(self, levels, points):
        import numpy as np

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        meta = {"key": self._cache_key(), "points": points}
        for level, items in levels.items():
            np.save(self.cache_dir / f"{level}.npy", items.vectors)
            meta[level] = {
                "judgments": items.judgments,
                "numbers": items.numbers,
                "spans": items.spans,
                "facts": items.facts,
            }
        (self.cache_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    # --- teksty -------------------------------------------------------------

    def texts(self, items, indices, cap, batch=200):
        """Text of each item in indices (its spans of the judgment's text, at most cap chars)."""
        by_judgment = {}
        for position, i in enumerate(indices):
            by_judgment.setdefault(items.judgments[i], []).append((position, i))
        out = [""] * len(indices)
        judgments = list(by_judgment)
        for start in range(0, len(judgments), batch):
            part = judgments[start : start + batch]
            with self.conn.cursor() as cur:
                bodies = self.judgment_texts(cur, part)
            self.conn.commit()
            for judgment in part:
                body = bodies.get(judgment, "")
                for position, i in by_judgment[judgment]:
                    out[position] = _cut(body, items.spans[i], cap)
        return out

    # --- nazwy --------------------------------------------------------------

    def name_topics(self, chat, level, *, suffix="", only_missing=True, limit=None):
        """Ask the chat model for each topic's Polish name (at most limit topics, the
        largest first); each saved at once. Returns (named, left); stops at the first
        ChatError (model not loaded, spending limit ...)."""
        named = 0
        topics = [t for t in self.store.topics_list(level) if not (only_missing and t["label"])]
        topics = topics[:limit] if limit else topics
        for position, topic in enumerate(topics):
            try:
                reply = "".join(
                    without_thinking(chat.stream(self.naming_messages(level, topic, suffix)))
                )
            except ChatError as e:
                log.warning("nazwy tematów przerwane: %s", e)
                return named, len(topics) - position
            label, description = parse_label(reply)
            if not label:
                log.warning(
                    "temat %s%d: model nie podał nazwy (%r)", level, topic["topic"], reply[:200]
                )
                continue
            self.store.topics_set_label(level, topic["topic"], label, description)
            named += 1
            log.info("temat %s%d: %s", level, topic["topic"], label)
        return named, 0

    def naming_messages(self, level, topic, suffix=""):
        facts = _facts_text(json.loads(topic["facts"] or "{}"), self.fact_fields)
        excerpts = json.loads(topic["excerpts"] or "[]")
        lines = [
            f"Poziom: {LEVEL_NAMES[level]}; liczba w temacie: {topic['size']}.",
            f"Słowa kluczowe (c-TF-IDF, od najważniejszego): {topic['keywords']}",
        ]
        if facts:
            lines.append(f"Najczęstsze: {facts}")
        lines.append("Fragmenty najbliższe środkowi tematu:")
        lines += [f"[{n}] {' '.join(e.split())}" for n, e in enumerate(excerpts, 1) if e.strip()]
        lines.append(
            "Podaj nazwę tego tematu po polsku (3-8 słów, jak hasło indeksu rzeczowego,"
            " konkretne zagadnienie prawne, bez słowa „temat”) i jedno zdanie opisu."
            " Odpowiedz dokładnie w dwóch wierszach:\nNAZWA: ...\nOPIS: ..."
        )
        if suffix:
            lines.append(suffix)
        return [
            {
                "role": "system",
                "content": f"Nazywasz grupy tematyczne w zbiorze: {self.corpus}."
                " Piszesz po polsku, zwięźle, polską terminologią prawniczą.",
            },
            {"role": "user", "content": "\n".join(lines)},
        ]

    # --- lista --------------------------------------------------------------

    def listing(self, level, examples=3):
        """Lines describing the saved topics of a level (for checking them by hand)."""
        topics = self.store.topics_list(level)
        if not topics:
            return [f"Poziom {level}: brak tematów (python -m <projekt> tematy --poziom {level})."]
        reps = {
            t["topic"]: [r.split(":")[0] for r in json.loads(t["representatives"] or "[]")][
                :examples
            ]
            for t in topics
        }
        ids = sorted({int(j) for r in reps.values() for j in r})
        with self.conn.cursor() as cur:
            titles = self.judgment_titles(cur, ids) if ids else {}
        self.conn.commit()
        total = sum(t["size"] for t in topics)
        lines = [
            (
                f"Poziom {level} ({LEVEL_NAMES[level]}): {len(topics)} tematów, {total} elementów,"
                f" policzone {topics[0]['built_at']:%Y-%m-%d %H:%M}"
            )
        ]
        for t in topics:
            facts = _facts_text(json.loads(t["facts"] or "{}"), self.fact_fields, top=3)
            lines.append(f"\n{level}{t['topic']}  [{t['size']}]  {t['label'] or '(bez nazwy)'}")
            if t["description"]:
                lines.append(f"    {t['description']}")
            lines.append(f"    słowa: {t['keywords']}")
            if facts:
                lines.append(f"    {facts}")
            for j in reps[t["topic"]]:
                lines.append(f"    - {titles.get(int(j), j)}")
        return lines


def centered(items, fields, min_group=CENTER_MIN_GROUP):
    """Vectors minus the mean of their group, field after field (the first value of a
    list), again of length 1; groups smaller than min_group stay as they are."""
    import numpy as np

    if not fields:
        return items.vectors
    vectors = items.vectors.copy()
    for f in fields:
        codes, keys = np.empty(len(items), dtype=np.int64), {}
        for i, facts in enumerate(items.facts):
            value = facts.get(f)
            if isinstance(value, (list, tuple)):
                value = value[0] if value else None
            codes[i] = keys.setdefault(value, len(keys))
        counts = np.bincount(codes, minlength=len(keys))
        sums = np.zeros((len(keys), vectors.shape[1]), dtype=np.float64)
        np.add.at(sums, codes, vectors)
        means = (sums / np.maximum(counts, 1)[:, None]).astype(np.float32)
        means[counts < min_group] = 0
        for start in range(0, len(vectors), ASSIGN_BLOCK):
            block = slice(start, start + ASSIGN_BLOCK)
            vectors[block] -= means[codes[block]]
        log.info("odjęta średnia grup %s: %d grup", f, int((counts >= min_group).sum()))
    return _units(vectors)


def assign(vectors, centres, block=ASSIGN_BLOCK):
    """(nearest centre per row, its cosine similarity); rows and centres of length 1."""
    import numpy as np

    best = np.empty(len(vectors), dtype=np.int64)
    score = np.empty(len(vectors), dtype=np.float32)
    for start in range(0, len(vectors), block):
        sims = vectors[start : start + block] @ centres.T
        best[start : start + block] = sims.argmax(axis=1)
        score[start : start + block] = sims.max(axis=1)
    return best, score


def representatives(items, members, scores, count=REPRESENTATIVES):
    """Members closest to the centre, at most one per judgment."""
    import numpy as np

    chosen, judgments = [], set()
    for k in np.argsort(-scores, kind="stable"):
        i = int(members[k])
        if items.judgments[i] in judgments:
            continue
        chosen.append(i)
        judgments.add(items.judgments[i])
        if len(chosen) == count:
            break
    return chosen


def topic_facts(items, members, fields, top=FACTS_TOP):
    """{field: [[value, share of members], ...]} of the most common payload values."""
    out = {}
    for f in fields:
        counts = Counter()
        for i in members:
            value = items.facts[int(i)].get(f)
            values = set(value) if isinstance(value, (list, tuple)) else {value}
            counts.update(v for v in values if v not in (None, ""))
        if counts:
            out[f] = [[v, round(c / len(members), 3)] for v, c in counts.most_common(top)]
    return out


def parse_label(reply):
    """(name, description) from the model's "NAZWA: ... / OPIS: ..." reply."""

    def field(name):
        m = re.search(rf"^[\s*#>-]*{name}[\s*]*:[\s*]*(.+)$", reply, re.IGNORECASE | re.MULTILINE)
        return m.group(1) if m else ""

    label = field("NAZWA")
    if not label:
        lines = [x for x in reply.strip().splitlines() if x.strip()]
        label = lines[0] if lines else ""
    # Nazwa bez cudzysłowów i kropki na końcu; opis to zdanie, kropka zostaje.
    return _clean(label)[:255], field("OPIS").strip(" \t*") or None


def _clean(text):
    return text.strip(" \t*\"'„”“`.")


def _facts_text(facts, labels, top=FACTS_TOP):
    parts = []
    for f, values in facts.items():
        shown = ", ".join(f"{v} ({share:.0%})" for v, share in values[:top])
        parts.append(f"{labels.get(f, f)}: {shown}")
    return "; ".join(parts)


def _text_spans(chunks, cap):
    """Spans for a judgment's text: chunks of the text parts first (in order), then
    the rest, until cap characters."""
    spans, total = [], 0
    for _, _, span in sorted(chunks):
        if total >= cap:
            break
        spans.append(list(span))
        total += span[1]
    return spans


def _cut(body, spans, cap):
    pieces, total = [], 0
    for start, length in spans:
        piece = body[start : start + min(length, cap - total)]
        pieces.append(piece)
        total += len(piece)
        if total >= cap:
            break
    return "\n".join(pieces)


def _sample(n, size, seed=42):
    if not size or size >= n:
        return list(range(n))
    return sorted(random.Random(seed).sample(range(n), size))


def _unit(v):
    import numpy as np

    norm = np.linalg.norm(v)
    return v / norm if norm else v


def _units(m):
    import numpy as np

    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return (m / norms).astype(np.float32)
