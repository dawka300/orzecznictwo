"""NSA chunker: a judgment's text (judgment_body) -> chunks, the NSA child of Chunker.

Pure, no database. Polish administrative judgments have no numbered headings
like the ECHR ones: the parts are the thesis, the operative part, the
reasoning and a dissenting opinion (headings from nsa.parser.PART_HEADINGS);
inside them paragraphs are packed by the common Chunker.
"""

from __future__ import annotations

from judgments_core.chunker import Block, Chunker, lines_with_offsets
from nsa.parser import PART_HEADINGS

# nagłówek w tekście -> część fragmentu
PARTS = {
    "TEZA": "teza",
    "SENTENCJA": "sentencja",
    "UZASADNIENIE": "uzasadnienie",
    "ZDANIE ODRĘBNE": "zdanie_odrebne",
}
assert set(PARTS) == set(PART_HEADINGS.values())


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
