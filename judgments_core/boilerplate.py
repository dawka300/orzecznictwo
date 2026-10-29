"""Formulas (boilerplate): sentences a court repeats word for word in many judgments
("Skarga nie zasługuje na uwzględnienie.", "Zgodnie z art. 1 § 1 i 2 p.u.s.a. sądy
administracyjne sprawują kontrolę ..."). In the comparison of a brief with a judgment
they are not an answer to anything: a formula close in meaning to every argument would
make it "answered", a brief's standard sentence would make it "passed over".

The list is counted once from a sample of the base (command formulki): a sentence
(normalised: lower case, numbers as 0, one kind of quotes and dashes, single spaces)
found in at least min_docs judgments of the sample is a formula. Saved as text,
"<w ilu orzeczeniach>\\t<zdanie>", one per line, most frequent first: readable and
editable by hand (a line removed = a sentence no longer filtered). Most formulas are
said in many words, so the project also gives patterns of whole sentences (a court's,
a party's) and a court's sentence close in meaning to one of the list counts too
(Formulas). The pages load it (load) and drop those sentences (strip); the report lists
what was dropped and why, nothing disappears silently.

A project's child of FormulaCorpus says which texts of its base are counted
(sample_texts); the counting, the file and the matching are common.
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

MIN_WORDS = 3  # słów >= 3 liter; krótsze "zdania" (nagłówki, sygnatury) nie są formułkami
MIN_DOCS = 50
SAMPLE = 50000
PROGRESS_EVERY_S = 30
# "Podobne": cosinus bge-m3 zdania sądu z najbliższym zdaniem listy (kalibracja: NOTEPAD,
# "Filtr formułek, krok 3": >= 0.88 przytoczenia i wzorce, niżej już uzasadnienie sprawy);
# zdanie dłuższe niż tyle znaków to już wywód, nie formułka.
NEAR = 0.88
SIMILAR_MAX_CHARS = 400
EMBED_BATCH = 16

# Koniec zdania: . ! ? ; : i spacja przed wielką literą, cyfrą albo cudzysłowem; nie po skrócie.
_END = re.compile(r"([.!?;:])\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ0-9„\"(])")
_LAST_WORD = re.compile(r"(\w+)\W*$")
ABBREVIATIONS = frozenset(
    "art ust par pkt lit nr poz dz sygn por zob np tj tzw tzn ww jw ok r zm ze ws pt św"  # noqa: SIM905
    " dr prof str s orz wyr post cyt dot wg ul godz zw pn al tys mln zł gr ds".split()
)
_QUOTES = str.maketrans({c: '"' for c in "„”“«»‟″"} | {c: "'" for c in "‘’‚`´"})
_DASHES = re.compile(r"[‐‑‒–—―−]")
_DIGITS = re.compile(r"\d+")
_BRACKETS = re.compile(r"\([^()]*\)")
_SPACES = re.compile(r"\s+")
_WORD = re.compile(r"[^\W\d_]{3,}")


def sentence_spans(line):
    """(start, end) of the sentences of one line; "art. 145 § 1", "Dz. U.", "k.p.a."
    don't end one."""
    spans, start = [], 0
    for match in _END.finditer(line):
        word = _LAST_WORD.search(line[start : match.start(1)])
        last = word.group(1).lstrip("0123456789") if word else ""  # "2002r." jak "r."
        if (
            match.group(1) == "."
            and last.isalpha()
            and (len(last) == 1 or last.lower() in ABBREVIATIONS)
        ):
            continue
        spans.append((start, match.start(1) + 1))
        start = match.end()
    if line[start:].strip():
        spans.append((start, len(line)))
    return spans


def sentences(text):
    return [
        line[a:b].strip()
        for line in text.split("\n")
        for a, b in sentence_spans(line)
        if line[a:b].strip()
    ]


def normalise(sentence):
    """The key of a sentence: lower case, without brackets (the journal "(Dz. U. z 2023 r.
    poz. 1634 ze zm.)", "(t.j. ...)", a case cited - they vary in the same formula), numbers
    as 0, one kind of quotes and dashes, single spaces, no trailing punctuation; "" for a
    too short one."""
    text = _BRACKETS.sub(" ", _BRACKETS.sub(" ", sentence))  # nawias w nawiasie: dwa razy
    text = _DASHES.sub("-", text.translate(_QUOTES).lower())
    text = _SPACES.sub(" ", _DIGITS.sub("0", text)).strip(" .;:,")
    return text if len(_WORD.findall(text)) >= MIN_WORDS else ""


@dataclass
class Dropped:
    """A sentence left out and why: "lista" (in `count` judgments of the sample),
    "wzorzec" (the pattern `label`), "podobne" (cosine `score` to the sentence `label`
    of the list)."""

    sentence: str
    how: str
    count: int = 0
    label: str = ""
    score: float = 0.0


class Formulas:
    """Formulas of one project in memory, three ways to recognise one:
    1. the list (counts: normalised sentence -> in how many judgments of the sample);
    2. patterns [(label, regex)] a whole sentence matches: families of formulas said in
       many words ("Skarga okazała się bezzasadna."), separately for a court's text
       (court_patterns) and for a party's brief or an authority's decision
       (brief_patterns: headings, the petitum, the signature, the instruction on appeal);
    3. similar (a court's text only, with an embedder): a sentence whose vector is within
       `near` cosine of a sentence of the list - the same formula in other words. Not in
       a brief: a party's argument close to a court's formula ("the court is not bound by
       the charges ...") is still an argument."""

    near = NEAR

    def __init__(self, counts=None, sample=0, path=None, court_patterns=(), brief_patterns=()):
        self.counts = counts or {}
        self.sample = sample
        self.path = path
        # zdania listy w pierwotnej postaci (wzorce dla "podobne"); bez pliku: klucze
        self.examples = list(self.counts)
        self.patterns = {
            "court": [(label, re.compile(rx, re.IGNORECASE)) for label, rx in court_patterns],
            "brief": [(label, re.compile(rx, re.IGNORECASE)) for label, rx in brief_patterns],
        }
        self._vectors = {}  # klucz modelu -> (macierz zdań listy, zdania)
        self._lock = threading.Lock()

    def __len__(self):
        return len(self.counts)

    def __bool__(self):
        return bool(self.counts or self.patterns["court"] or self.patterns["brief"])

    @classmethod
    def load(cls, path, court_patterns=(), brief_patterns=()):
        """The list from its file (none = an empty list) and the project's patterns."""
        path = Path(path)
        counts, examples, sample = {}, [], 0
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("#"):
                    found = re.search(r"próbka:\s*(\d+)", line)
                    sample = int(found.group(1)) if found else sample
                    continue
                count, _, sentence = line.partition("\t")
                key = normalise(sentence)
                if key and count.strip().isdigit():
                    if key not in counts:
                        examples.append(sentence.strip())
                    counts[key] = max(counts.get(key, 0), int(count))
        formulas = cls(counts, sample, path, court_patterns, brief_patterns)
        formulas.examples = examples
        return formulas

    def count(self, sentence):
        """In how many judgments of the sample (0 = not on the list)."""
        key = normalise(sentence)
        return self.counts.get(key, 0) if key else 0

    def pattern(self, sentence, kind):
        """The label of the first pattern of `kind` the whole sentence matches, or ""."""
        text = _SPACES.sub(" ", sentence).strip()
        for label, regex in self.patterns.get(kind, ()):
            if regex.fullmatch(text):
                return label
        return ""

    def strip(self, text, kind="court", embedder=None):
        """(the text without formulas, [Dropped]). kind: "court" (a court's text) or
        "brief" (a party, an authority). Lines stay lines (a line of formulas only goes),
        so the paragraphs of the comparison stay as they were."""
        if not self or not text:
            return text, []
        rows, dropped = [], []
        for line in text.split("\n"):
            kept = []
            label = self.pattern(
                line, kind
            )  # cały wiersz: "Skarżący: Jan Kowalski", "Sygn. akt: ..."
            if label and line.strip():
                dropped.append(Dropped(line.strip(), "wzorzec", label=label))
                rows.append((kept, True))
                continue
            for a, b in sentence_spans(line):
                sentence = line[a:b].strip()
                count = self.count(sentence)
                label = "" if count else self.pattern(sentence, kind)
                if count:
                    dropped.append(Dropped(sentence, "lista", count=count))
                elif label:
                    dropped.append(Dropped(sentence, "wzorzec", label=label))
                elif sentence:
                    kept.append(sentence)
            rows.append((kept, bool(line.strip())))
        if kind == "court" and embedder is not None and self.examples:
            similar = self.similar([s for kept, _ in rows for s in kept], embedder)
            dropped += similar.values()
            rows = [([s for s in kept if s not in similar], full) for kept, full in rows]
        return "\n".join(" ".join(kept) for kept, full in rows if kept or not full), dropped

    def similar(self, candidates, embedder):
        """{sentence: Dropped} of the candidates within `near` of a sentence of the list.
        Needs numpy (with the model in the process it is there); without it nothing - the
        list and the patterns still work."""
        try:
            import numpy as np
        except ImportError:
            return {}
        texts = list(
            dict.fromkeys(s for s in candidates if normalise(s) and len(s) <= SIMILAR_MAX_CHARS)
        )
        if not texts:
            return {}
        reference, examples = self.reference(embedder, np)
        found = {}
        for start in range(0, len(texts), EMBED_BATCH):
            batch = texts[start : start + EMBED_BATCH]
            scores = _unit(np, embedder.embed(batch)) @ reference.T
            for sentence, row in zip(batch, scores):
                best = int(row.argmax())
                if row[best] >= self.near:
                    found[sentence] = Dropped(
                        sentence, "podobne", label=examples[best], score=float(row[best])
                    )
        return found

    def prepare_in_background(self, embedder):
        """The list's vectors computed at start, in a thread (~1 min on a CPU), so the
        first comparison doesn't wait for them; an error only goes to the log."""

        def run():
            try:
                import numpy as np

                self.reference(embedder, np)
            except Exception as error:  # noqa: BLE001 - porównanie policzy je samo
                log.warning("formułki: wektory listy nie policzone z góry: %s", error)

        if self.examples:
            threading.Thread(target=run, name="formulki-wektory", daemon=True).start()

    def reference(self, embedder, np):
        """The list's sentences as unit vectors, computed once per model."""
        key = (type(embedder).__name__, getattr(embedder, "model", ""))
        with self._lock:
            if key not in self._vectors:
                vectors = []
                for start in range(0, len(self.examples), EMBED_BATCH):
                    vectors += embedder.embed(self.examples[start : start + EMBED_BATCH])
                self._vectors[key] = (_unit(np, vectors), self.examples)
            return self._vectors[key]


def _unit(np, vectors):
    matrix = np.asarray(vectors, dtype=np.float32)
    return matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-12)


def count_formulas(texts, min_docs=MIN_DOCS):
    """[(count, sentence)] of the sentences found in at least min_docs of the texts (each
    text counts a sentence once), most frequent first, and the number of texts. The
    sentence shown is its first form seen; counting keeps only hashes (millions of
    sentences), the text is remembered from the second occurrence on."""
    counts, examples, done, last = {}, {}, 0, time.monotonic()
    for text in texts:
        done += 1
        keys = {}
        for sentence in sentences(text):
            key = normalise(sentence)
            if key:
                keys.setdefault(hash(key), sentence)
        for h, sentence in keys.items():
            seen = counts.get(h, 0) + 1
            counts[h] = seen
            if seen == 2:
                examples[h] = sentence
        if time.monotonic() - last > PROGRESS_EVERY_S:
            last = time.monotonic()
            log.info("formułki: %d tekstów, %d różnych zdań", done, len(counts))
            print(f"  {done} tekstów, {len(counts)} różnych zdań", flush=True)
    found = sorted(
        ((n, examples[h]) for h, n in counts.items() if n >= min_docs),
        key=lambda item: (-item[0], item[1]),
    )
    return found, done


def save(path, found, sample, source):
    path = Path(path)
    header = [
        "# Formułki: zdania powtarzane dosłownie (po normalizacji: małe litery, liczby jako 0)",
        f"# w wielu orzeczeniach - {source}; próbka: {sample} orzeczeń.",
        "# Format: <w ilu orzeczeniach próbki>\\t<zdanie>. Usunięty wiersz = zdanie nie jest",
        "# już pomijane w Porównaniu dokumentów i Analizie sprawy. Od nowa: python -m <projekt> formulki",
    ]
    lines = header + [f"{n}\t{_SPACES.sub(' ', s)}" for n, s in found]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class FormulaCorpus:
    """Parent of the projects' formula sources (command formulki): which texts of the
    base are counted. A child gives `file` (where the list lives, read by the pages),
    `source` (a line about what was counted) and sample_texts()."""

    file: Path
    source = "orzeczenia z bazy"

    def __init__(self, conn):
        self.conn = conn

    def sample_texts(self, size, rng):
        """Texts of `size` judgments picked at random (rng: random.Random)."""
        raise NotImplementedError

    def build(self, size=SAMPLE, min_docs=MIN_DOCS, seed=1):
        found, done = count_formulas(self.sample_texts(size, random.Random(seed)), min_docs)
        save(self.file, found, done, self.source)
        return found, done
