"""Text cleanup shared by sources that deliver HTML (from the SAOS project)."""

from __future__ import annotations

import html
import re

TAG_RE = re.compile(r"<[^>]+>")
BLOCK_RE = re.compile(r"</?(p|div|br|h\d|li|tr|table)[^>]*>", re.IGNORECASE)
SPACES_RE = re.compile(r"[ \t ]+")
LINE_EDGE_SPACES_RE = re.compile(r" *\n *")
NEWLINES_RE = re.compile(r"\n{3,}")
LETTERS = "a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ"
# Słowo przeniesione do następnej linii: "wprowa-\ndzającą", też przez numer
# strony: "wys-\n2\ntąpił". Druga część musi zaczynać się małą literą.
SPLIT_WORD_RE = re.compile(rf"(?<=[{LETTERS}])-\n+(?:(?:- )?\d+(?: -)?\n+)?(?=[a-ząćęłńóśźż])")


def clean_html(txt):
    """HTML -> plain text: block tags become line breaks, spaces are squeezed."""
    if not txt:
        return None
    txt = BLOCK_RE.sub("\n", txt)
    txt = TAG_RE.sub("", txt)
    txt = html.unescape(txt)
    txt = SPACES_RE.sub(" ", txt)
    txt = LINE_EDGE_SPACES_RE.sub("\n", txt)
    txt = SPLIT_WORD_RE.sub("", txt)
    txt = NEWLINES_RE.sub("\n\n", txt)
    return txt.strip()
