"""NSA chunker: a judgment's text (judgment_body) -> chunks, the NSA child of Chunker.

Pure, no database. Polish administrative judgments have no numbered headings
like the ECHR ones: the parts are the thesis, the operative part, the
reasoning and a dissenting opinion (headings from nsa.parser.PART_HEADINGS);
inside them paragraphs are packed by the common Chunker.
"""

from __future__ import annotations

import re

from judgments_core.chunker import Block, Chunker, lines_with_offsets
from judgments_core.vectors import Chunk
from nsa.parser import PART_HEADINGS

# nagłówek w tekście -> część fragmentu
PARTS = {
    "TEZA": "teza",
    "SENTENCJA": "sentencja",
    "UZASADNIENIE": "uzasadnienie",
    "ZDANIE ODRĘBNE": "zdanie_odrebne",
}
assert set(PARTS) == set(PART_HEADINGS.values())
# Początek właściwych rozważań sądu w uzasadnieniu.
REASONING_RE = re.compile(r"zważył(?:[a-ząęłńóśźż]*)?,?\s+co następuje\s*:?", re.IGNORECASE)
# Skrót orzeczenia (poziom 1: jeden wektor na orzeczenie), w znakach.
SUMMARY_CHARS = 2000
SUMMARY_THESIS_CHARS = 500
SUMMARY_SENTENCE_CHARS = 400  # koniec sentencji: tam jest rozstrzygnięcie


class NsaChunker(Chunker):
    # Polskie słowa są dłuższe niż angielskie (~3,3 znaku na token bge-m3):
    # te same ~500 tokenów to mniej znaków.
    target_chars = 1700
    max_chars = 2700
    min_chars = 400

    def blocks(self, body):
        part, section = "tekst", ""
        for start, line in lines_with_offsets(body):
            if line in PARTS:
                part, section = PARTS[line], line
                yield Block(part, section, (), start, line, True)
                continue
            yield Block(part, section, (), start, line, False)

    def summary(self, judgment_id, body, parts, title, meta=None):
        """One chunk for the whole judgment (level 1): the thesis, the end of the operative
        part (the decision) and the start of the court's reasoning ("zważył, co następuje"),
        about SUMMARY_CHARS together. The vector store keeps no text, only start/length in
        body, so those point to the shown passage - a real, continuous piece of the text
        (the reasoning start, else the operative part); the embedded text is the summary.
        parts = (thesis, sentence, reasons, dissenting_opinion) as in judgment_body."""
        thesis, sentence, reasons, _ = (p.strip() if p else "" for p in parts)
        shown, part = _reasoning(reasons), "uzasadnienie"
        if not shown:
            shown, part = sentence, "sentencja"
        if not shown:
            shown, part = thesis, "teza"
        if not shown:
            return []
        pieces = []
        if thesis:
            pieces.append(thesis[:SUMMARY_THESIS_CHARS])
        if sentence and part != "sentencja":
            pieces.append(sentence[-SUMMARY_SENTENCE_CHARS:])
        room = max(SUMMARY_CHARS - sum(len(p) + 2 for p in pieces), SUMMARY_CHARS // 2)
        # Sentencja zaczyna się składem sądu, rozstrzygnięcie jest na końcu.
        shown = shown[-room:] if part == "sentencja" else shown[:room]
        text = "\n\n".join([*pieces, shown])
        start = body.find(shown)
        return [
            Chunk(
                judgment_id=str(judgment_id),
                number=0,
                text=text,
                start=max(start, 0),
                part=part,
                meta={**(meta or {}), "context": f"{title[: self.title_max_chars]} | skrót"},
                length=len(shown),
            )
        ]


def _reasoning(reasons):
    """The reasoning from "zważył, co następuje" on, else the whole reasons text."""
    if not reasons:
        return ""
    match = REASONING_RE.search(reasons)
    return reasons[match.end() :].strip() if match else reasons
