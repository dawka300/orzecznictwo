"""Parent of the project chunkers: a judgment's text -> chunks for embeddings.

The common part is the packing: lines (paragraphs) are joined into chunks of
about target_chars without crossing a section; a new chunk starts at a
heading once the current one is long enough; a paragraph longer than
max_chars is cut at sentence ends; a short chunk joins the previous one of
the same section. Each chunk gets a context line (title | section | heading
path) that goes in front of its text when embedded.

A project's child says where its texts' sections and headings are
(blocks()) and which parts are left out; it may relabel groups (relabel()).
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from judgments_core.vectors import Chunk

SENTENCE_END_RE = re.compile(r"(?<=[.;:?!])\s+(?=[(\"“‘«A-ZÀ-ÝĀ-Ž0-9])")


@dataclass
class Block:
    """One line of the text with its place in the structure."""

    part: str  # część (np. fakty, prawo, uzasadnienie)
    section: str  # nagłówek tej części, jak w tekście
    path: tuple  # ścieżka nagłówków numerowanych w części
    start: int  # pozycja w tekście (znaki)
    text: str
    is_heading: bool = False


@dataclass
class Group:
    """Lines packed into one chunk (before it becomes a Chunk)."""

    part: str
    section: str
    path: tuple
    start: int
    lines: list

    @property
    def size(self):
        return len("\n".join(self.lines))


class Chunker(ABC):
    # Wielkość fragmentu w znakach (~4 znaki na token bge-m3): ~500 tokenów,
    # najwyżej ~800; krótszy niż min_chars dołącza do poprzedniego.
    target_chars = 2000
    max_chars = 3200
    min_chars = 500
    # Wiersz kontekstu: tytuł ucięty (sprawy zbiorowe mają bardzo długie nazwy).
    title_max_chars = 150
    context_max_chars = 400
    skipped_parts = ()  # części bez wektorów (np. nagłówek, załącznik)

    @abstractmethod
    def blocks(self, body):
        """The text -> Blocks, one per non-empty line, in order."""

    def relabel(self, groups):
        """Change the parts of packed groups (e.g. a long header becomes text); as is by default."""
        return groups

    def chunk(self, judgment_id, body, title, meta=None):
        """A judgment's text -> Chunks (number from 0, start = offset in body)."""
        groups = []
        for b in self.blocks(body):
            last = groups[-1] if groups else None
            size = sum(len(x) + 1 for x in last.lines) if last else 0
            same_section = last is not None and last.part == b.part and last.section == b.section
            if (
                same_section
                and not (b.is_heading and size >= self.min_chars)
                and size + len(b.text) <= self.target_chars
            ):
                last.lines.append(b.text)
                if b.path[: len(last.path)] == last.path:
                    last.path = (
                        b.path
                    )  # głębszy nagłówek w tym samym fragmencie: dokładniejsza ścieżka
                continue
            pieces = self.split_long(b.text)
            if (
                same_section
                and not b.is_heading
                and len(b.text) > self.max_chars
                and size < self.min_chars
            ):
                # Krótki początek przed bardzo długim akapitem: dokleić do pierwszego kawałka.
                last.lines.append(pieces.pop(0))
            groups.extend(Group(b.part, b.section, b.path, b.start, [p]) for p in pieces)
        chunks = []
        for g in self.relabel(self.merge_short(groups)):
            if g.part in self.skipped_parts or g.lines == [g.section]:
                continue
            chunks.append(
                Chunk(
                    judgment_id=str(judgment_id),
                    number=len(chunks),
                    text="\n".join(g.lines),
                    start=g.start,
                    part=g.part,
                    meta={**(meta or {}), "context": self.context(title, g)},
                )
            )
        return chunks

    def context(self, title, group):
        parts = (title[: self.title_max_chars], group.section, " > ".join(group.path))
        return " | ".join(x for x in parts if x)[: self.context_max_chars]

    def split_long(self, text):
        """A paragraph longer than max_chars -> pieces cut at sentence ends, else at spaces."""
        limit = self.max_chars
        if len(text) <= limit:
            return [text]
        pieces, current = [], ""
        for sentence in SENTENCE_END_RE.split(text):
            while len(sentence) > limit:
                cut = sentence.rfind(" ", 0, limit)
                cut = cut if cut > limit // 2 else limit
                if current:
                    pieces.append(current)
                    current = ""
                pieces.append(sentence[:cut])
                sentence = sentence[cut:].lstrip()
            if current and len(current) + 1 + len(sentence) > self.target_chars:
                pieces.append(current)
                current = sentence
            else:
                current = f"{current} {sentence}" if current else sentence
        if current:
            pieces.append(current)
        return pieces

    def merge_short(self, groups):
        """A group shorter than min_chars joins the previous one of the same section if it fits."""
        merged = []
        for g in groups:
            prev = merged[-1] if merged else None
            if (
                prev
                and g.size < self.min_chars
                and (prev.part, prev.section) == (g.part, g.section)
                and prev.size + g.size <= self.max_chars
            ):
                prev.lines.extend(g.lines)
            else:
                merged.append(g)
        return merged

    @staticmethod
    def embedding_input(chunk):
        """Text sent to the embedding model: context line + chunk text."""
        context = chunk.meta.get("context")
        return f"{context}\n\n{chunk.text}" if context else chunk.text


def lines_with_offsets(body):
    """(start, line) for each non-empty stripped line of the body."""
    position = 0
    for raw in body.split("\n"):
        line = raw.strip()
        if line:
            yield position + raw.index(line[0]), line
        position += len(raw) + 1
