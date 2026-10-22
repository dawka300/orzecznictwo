"""Parent of the project retrievers: the best passages for a question, packed into a limit.

RAG for models that can't search by themselves (local models with a small
context, one call): the program picks what the model reads. The same
passages go to Claude as the MCP tool fragmenty.

The common part:
1. the project's search ranks judgments (hybrid words + meaning, with the
   page's filters; FULLTEXT alone when vectors are down),
2. for the top `candidates` judgments the vector store gives up to
   `per_judgment` passages closest to the question; a judgment without
   vectors gets the place of the first query word in its text (or its start),
3. each passage is widened by `widen_chars` on both sides (to line ends), so
   the model sees the reasoning around it; overlapping ones merge,
4. with a reranker (judgments_core/rerank.py, setting <PREFIKS>_RERANKER):
   a cross-encoder scores every candidate passage against the question;
   judgments are reordered by their best passage, passages within a judgment
   by score,
5. packing: best passage of each judgment in rank order, then the second
   best ..., until `budget_chars`.

A project's child runs its search (ranking), reads texts (bodies), describes a
judgment for citing (describe) and maps the keys of the evaluation questions
to judgments (expected).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from judgments_core.rerank import RerankError, make_reranker
from judgments_core.semantic import SemanticUnavailable, rrf

log = logging.getLogger(__name__)

CANDIDATES = 20  # orzeczeń z wyszukiwarki, z których bierzemy fragmenty
PER_JUDGMENT = 3  # najwięcej fragmentów jednego orzeczenia
BUDGET_CHARS = 16_000  # ~4-5 tys. tokenów: mieści się w małym modelu lokalnym
WIDEN_CHARS = 400  # tyle tekstu dookoła fragmentu (z każdej strony)
TEXT_WINDOW = 1_500  # okno wokół słowa zapytania, gdy orzeczenie nie ma wektorów
# podobne-do: najwięcej fragmentów własnego tekstu, kandydatów na fragment, dopasowań na orzeczenie
OWN_CHUNKS = 40
PER_OWN_CHUNK = 30
MATCHES_SHOWN = 3
SNAP_CHARS = 200  # granica okna przesuwana do końca wiersza, jeśli jest tak blisko


@dataclass
class Ranking:
    """What the project's search found: hits in order (each has .id)."""

    hits: list
    pattern: object = None  # regex słów zapytania (miejsce w tekście bez wektorów)
    mode: str = ""
    notice: str = ""


@dataclass
class Fragment:
    start: int
    end: int
    heading: str  # ścieżka nagłówków / część orzeczenia
    text: str = ""


@dataclass
class Reference:
    """One judgment in the context: [number] and its passages in text order."""

    number: int
    judgment_id: int
    title: str  # nazwa sprawy / sygnatura
    details: str  # rodzaj, data, sąd, skargi ...
    url: str
    language: str
    fragments: list = field(default_factory=list)
    result: str = ""  # wynik sprawy z metadanych (do sprawdzania odpowiedzi)


@dataclass
class Retrieval:
    sources: list  # Reference w kolejności rankingu
    ranked: list  # identyfikatory orzeczeń z wyszukiwarki, w kolejności
    mode: str = ""
    notice: str = ""

    @property
    def chars(self):
        return sum(len(f.text) for s in self.sources for f in s.fragments)


@dataclass
class _Candidate:
    """A passage before packing; order = how good it is within its judgment."""

    start: int
    end: int
    heading: str
    core: tuple = ()  # (start, end) samego fragmentu bez poszerzenia: to ocenia reranker
    score: float = 0.0  # ocena rerankera
    part: str = ""  # część orzeczenia (last_parts)


class Retriever(ABC):
    candidates = CANDIDATES
    per_judgment = PER_JUDGMENT
    budget_chars = BUDGET_CHARS
    widen_chars = WIDEN_CHARS
    query_class = None  # SearchQuery projektu (filtry jak na stronie)
    # Tryb wyszukiwarki dla fragmentów: "" hybrydowe / znaczeniowe / pelnotekstowe;
    # zapytanie z własnym trybem (filtr mode) wygrywa.
    mode = ""
    semantic_class = None  # SemanticSearch projektu (from_settings)
    # Części orzeczenia, których fragmenty idą na koniec kolejki w orzeczeniu (np. zdania
    # odrębne: nie mogą wypierać uzasadnienia sądu); zostają, gdy innych nie ma.
    last_parts = ()

    def __init__(self, semantic=None, reranker=None):
        self.semantic = semantic
        self.reranker = reranker

    @classmethod
    def from_settings(cls, settings, project, semantic=None):
        """With the project's semantic search (query model, Qdrant) and the reranker
        from the settings; both models start loading in the background."""
        if semantic is None and cls.semantic_class is not None:
            semantic = cls.semantic_class.from_settings(settings, project)
        reranker = make_reranker(settings)
        if reranker is not None:
            reranker.prepare_in_background()
        return cls(semantic, reranker)

    @abstractmethod
    def ranking(self, cur, query):
        """Run the project's search for query; a Ranking (raises its QueryError)."""

    @abstractmethod
    def bodies(self, cur, wanted):
        """wanted = {judgment id: document of its passages or ""} ->
        {judgment id: (document, language, text the offsets point into)}."""

    @abstractmethod
    def describe(self, hit, document):
        """(title, details, url) for citing one hit of the ranking."""

    @abstractmethod
    def expected(self, cur, keys):
        """{key: {judgment ids}} for the keys of evaluation questions (appno, docket ...)."""

    def result(self, hit):
        """The case's outcome from the metadata ("" = unknown); checked against answers."""
        return ""

    def query(self, text, **filters):
        filters = {k: v for k, v in filters.items() if k != "mode" or v}
        return self.query_class(text=text, **{"mode": self.mode, **filters})

    def retrieve(self, cur, query, budget_chars=None):
        """The best passages for query.text with the query's filters, packed."""
        budget = budget_chars or self.budget_chars
        ranking = self.ranking(cur, query)
        hits = ranking.hits[: self.candidates]
        notice = ranking.notice
        passages = {}
        if self.semantic is not None and hits and query.text.strip():
            try:
                passages = self.semantic.passages_of(
                    query.text, [h.id for h in hits], self.per_judgment
                )
            except SemanticUnavailable as e:
                notice = notice or str(e)
        wanted = {h.id: passages[h.id][0].document if passages.get(h.id) else "" for h in hits}
        texts = self.bodies(cur, wanted)
        found = {}
        for hit in hits:
            if hit.id not in texts:
                continue
            document, language, body = texts[hit.id]
            found[hit.id] = (hit, document, language, body)
        candidates = {
            i: self._candidates(body, passages.get(i, []), ranking.pattern)
            for i, (_, _, _, body) in found.items()
        }
        order = list(found)
        ranked = [h.id for h in ranking.hits]
        if self.reranker is not None and candidates:
            try:
                order = self._rerank(query.text, found, candidates)
                ranked = order + [i for i in ranked if i not in found]
            except RerankError as e:
                notice = notice or f"Reranker niedostępny: {e}"
        chosen = pack([(i, [c.end - c.start for c in candidates[i]]) for i in order], budget)
        sources = []
        for judgment_id, picks in chosen:
            hit, document, language, body = found[judgment_id]
            title, details, url = self.describe(hit, document)
            spans = [candidates[judgment_id][k] for k in picks]
            fragments = [
                Fragment(c.start, c.end, c.heading, body[c.start : c.end].strip())
                for c in merge(spans)
            ]
            sources.append(
                Reference(
                    len(sources) + 1,
                    judgment_id,
                    title,
                    details,
                    url,
                    language,
                    fragments,
                    self.result(hit),
                )
            )
        return Retrieval(sources, ranked, ranking.mode, notice)

    def hits(self, cur, ids):
        """The project's search hits (for describe and result) of these judgments, in order."""
        raise NotImplementedError("projekt nie umie wczytać orzeczeń po identyfikatorach")

    def outcome_group(self, result):
        """The outcome of a judgment as "uwzglednione" (the complaint won: violation,
        quashed ...), "nieuwzglednione" (dismissed, no violation, inadmissible) or "inne";
        a project's child reads its result texts. Used by the document analysis."""
        return "inne"

    def like_text(self, cur, text, chunker, k=10, result_contains="", embedder=None):
        """Judgments closest to a whole text (e.g. one's own judgment), by meaning.

        The text is cut by the project's chunker exactly like the judgments in the base
        (at most OWN_CHUNKS chunks); each chunk searches the vector store on its own, the
        rankings are fused (rrf): a judgment close to many parts of the text comes first.
        Each source shows its passages that matched and which own chunk they matched
        ("Twój fragment n"). Opposite rulings on the same issue are close in meaning too:
        result_contains keeps only judgments whose outcome contains that text. embedder:
        the model for the own chunks (default: the semantic search's query model)."""
        if self.semantic is None:
            raise SemanticUnavailable("Ten projekt nie ma wyszukiwania znaczeniowego.")
        own = chunker.chunk(0, text, "", {})[:OWN_CHUNKS]
        if not own:
            return Retrieval([], [])
        rankings, matches = [], {}
        try:
            embedder = embedder or self.semantic.embedder  # analiza dokumentu: wybrany model
            vectors = embedder.embed([chunker.embedding_input(c) for c in own])
            for number, vector in enumerate(vectors):
                ranking = []
                for hit in self.semantic.vectors.search_judgments(vector, PER_OWN_CHUNK):
                    passage = self.semantic.passage(hit)
                    ranking.append(passage.judgment_id)
                    matches.setdefault(passage.judgment_id, []).append(
                        (passage.score, number, passage)
                    )
                rankings.append(ranking)
        except RuntimeError as e:
            raise SemanticUnavailable(f"Wyszukiwanie znaczeniowe niedostępne: {e}") from e
        scores = rrf(rankings)
        order = sorted(scores, key=scores.get, reverse=True)
        chosen = []
        for start in range(0, len(order), 100):
            for hit in self.hits(cur, order[start : start + 100]):
                if result_contains.lower() in self.result(hit).lower():
                    chosen.append(hit)
            if len(chosen) >= k:
                break
        chosen = chosen[:k]
        best = {h.id: max(matches[h.id], key=lambda m: m[0]) for h in chosen}
        texts = self.bodies(cur, {i: m[2].document for i, m in best.items()})
        sources = []
        for hit in chosen:
            if hit.id not in texts:
                continue
            document, language, body = texts[hit.id]
            title, details, url = self.describe(hit, document)
            # Każdy fragment orzeczenia raz, z numerami Twoich fragmentów, które do niego pasują.
            by_passage = {}
            for score, number, p in sorted(matches[hit.id], key=lambda m: m[0], reverse=True):
                by_passage.setdefault(p.start, (p, []))[1].append(number + 1)
            fragments = []
            for p, numbers in list(by_passage.values())[:MATCHES_SHOWN]:
                numbers = sorted(set(numbers))
                label = "Twój fragment" if len(numbers) == 1 else "Twoje fragmenty"
                heading = f"{label} {', '.join(map(str, numbers))} ~ {_heading(p.context, p.part)}"
                end = p.start + p.length
                fragments.append(Fragment(p.start, end, heading, body[p.start : end].strip()))
            sources.append(
                Reference(
                    len(sources) + 1,
                    hit.id,
                    title,
                    details,
                    url,
                    language,
                    fragments,
                    self.result(hit),
                )
            )
        legend = "\n".join(
            f"  {n}. {' '.join(c.text.split())[:100]}…" for n, c in enumerate(own, 1)
        )
        return Retrieval(
            sources, order, "znaczeniowe", f"Twój tekst: {len(own)} fragmentów:\n{legend}"
        )

    def _rerank(self, question, found, candidates):
        """Score every candidate passage; candidates sorted in place, judgments by their
        best passage (a tie keeps the search order)."""
        pairs = [(i, c, found[i][3][c.core[0] : c.core[1]]) for i in found for c in candidates[i]]
        scores = self.reranker.scores(question, [text for _, _, text in pairs])
        best = {}
        for (i, c, _), score in zip(pairs, scores):
            c.score = score
            best[i] = max(best.get(i, score), score)
        for i in found:
            candidates[i].sort(key=lambda c: -c.score)
        position = {i: n for n, i in enumerate(found)}
        return sorted(found, key=lambda i: (-best[i], position[i]))

    def _candidates(self, body, passages, pattern):
        """Widened passages of one judgment, best first; without vectors one window
        at the first query word (or at the start of the text)."""
        if passages:
            spans = [
                _Candidate(
                    *widen(body, p.start, p.start + p.length, self.widen_chars),
                    heading=_heading(p.context, p.part),
                    core=(p.start, p.start + p.length),
                    part=p.part,
                )
                for p in passages
            ]
            if self.last_parts:  # sort stabilny: kolejność trafności w grupach zostaje
                spans.sort(key=lambda c: c.part in self.last_parts)
            return spans[: self.per_judgment]
        match = pattern.search(body) if pattern is not None else None
        at = match.start() if match else 0
        start = max(at - TEXT_WINDOW // 3, 0)
        start, end = widen(body, start, start + TEXT_WINDOW, 0)
        return [_Candidate(start, end, heading="", core=(start, end))]


def widen(body, start, end, by):
    """[start - by, end + by] inside the text, moved to line ends when one is near,
    else to whole words (never starts or ends in the middle of a word)."""
    start, end = max(start - by, 0), min(end + by, len(body))
    if start > 0:
        line = body.rfind("\n", max(start - SNAP_CHARS, 0), start)
        if line >= 0:
            start = line + 1
        elif not body[start - 1].isspace():
            space = body.find(" ", start, end)
            start = space + 1 if space >= 0 else start
    if end < len(body):
        line = body.find("\n", end, end + SNAP_CHARS)
        if line >= 0:
            end = line
        elif not body[end].isspace():
            space = body.rfind(" ", start, end)
            end = space if space > start else end
    return start, end


def merge(spans):
    """Spans in text order, overlapping or touching ones joined (the first heading stays)."""
    merged = []
    for span in sorted(spans, key=lambda s: s.start):
        if merged and span.start <= merged[-1].end + 1:
            last = merged[-1]
            last.end = max(last.end, span.end)
        else:
            merged.append(_Candidate(span.start, span.end, span.heading, span.core))
    return merged


def pack(judgments, budget):
    """judgments = [(id, [lengths of its passages, best first])] in rank order ->
    [(id, [indexes of chosen passages])]: rounds of the best not yet taken passage
    of each judgment while it fits; the very first passage always goes (the
    model has something to read even with a tiny limit)."""
    chosen = {}
    used = 0
    depth = max((len(lengths) for _, lengths in judgments), default=0)
    for round_ in range(depth):
        for judgment_id, lengths in judgments:
            if round_ >= len(lengths):
                continue
            if used + lengths[round_] > budget and used:
                continue
            chosen.setdefault(judgment_id, []).append(round_)
            used += lengths[round_]
    return [(i, chosen[i]) for i, _ in judgments if i in chosen]


def render(retrieval, empty="Nic nie znaleziono.", ids=True):
    """The sources as text for a model: [n] title, details, link, then passages.

    ids=False: without the database ids (a local model copies them into its
    answer; Claude needs them for the tool orzeczenie)."""
    if not retrieval.sources:
        return empty
    parts = []
    for s in retrieval.sources:
        head = f"[{s.number}] {s.title} (id {s.judgment_id})" if ids else f"[{s.number}] {s.title}"
        lines = [head, s.details, s.url]
        for f in s.fragments:
            where = f"{f.heading}, " if f.heading else ""
            language = f" ({s.language})" if s.language else ""
            lines.append(f"--- {where}znaki {f.start}-{f.end}{language}")
            lines.append(f.text)
        parts.append("\n".join(x for x in lines if x))
    return "\n\n".join(parts)


def _heading(context, part):
    """Heading path from the chunk's context line ("title | path"), else its part."""
    return context.split(" | ", 1)[1] if " | " in context else part
