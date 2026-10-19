"""ETPC chunker: ruling text (ruling_documents.body) -> chunks, the ETPC child of Chunker.

Pure, no database. The packing is common (judgments_core/chunker.py); here
is where an ECHR ruling's structure is: top-level sections (THE FACTS, THE
LAW, FOR THESE REASONS, separate opinions ... and their French forms) and,
inside them, numbered headings (I., A., 1., (a), (i), (α)). The context line
(case title, section, heading path) lets a chunk from deep inside THE LAW
still say what it is about.
"""

from __future__ import annotations

import re

from judgments_core.chunker import Block, Chunker, lines_with_offsets

# Nagłówek (skład, sędziowie) krótszy niż to pomijamy; dłuższy to zwykle
# stara decyzja Komisji bez nagłówków, gdzie cały stan faktyczny jest na początku.
SHORT_HEADER_CHARS = 2500

# Sekcje najwyższego poziomu: (część, wzorzec całej linii, bez rozróżniania
# wielkości liter; linia musi też być pisana wielkimi literami, poza sentencją).
SECTIONS = [
    ("procedura", r"PROCEDURE|PROCÉDURE"),
    ("wprowadzenie", r"INTRODUCTION"),
    (
        "fakty",
        r"(AS TO )?THE FACTS|FACTS( AND PROCEDURE)?|EN FAIT|(LES )?FAITS|(EN )?FAIT ET PROCÉDURE",
    ),
    (
        "prawo_krajowe",
        r"RELEVANT .*(LAW|FRAMEWORK|PRACTICE|MATERIALS?)|LE CADRE JURIDIQUE.*|LE DROIT .*PERTINENTS?.*",
    ),
    ("zarzuty", r"COMPLAINTS?|GRIEFS?"),
    ("prawo", r"(AS TO )?THE LAW|LAW|EN DROIT|DROIT"),
    ("zalacznik", r"APPENDI(X|CES)( .*)?|ANNEX(E|ES)?( .*)?"),
    (
        "opinia",
        (
            r".*\bOPINION\b.*\b(JUDGES?|JUGES?)\b.*|.*OPINION SÉPARÉE.*|(JOINT |COMMON )?(SEPARATE )?OPINIONS?"
            r"|.*\bDECLARATION OF JUDGES?\b.*|.*\bDÉCLARATION DES? JUGES?\b.*"
        ),
    ),
]
SECTION_RES = [
    (part, re.compile(rf"(?:{pattern})\s*[.:]?", re.IGNORECASE)) for part, pattern in SECTIONS
]
# Sentencja: "FOR THESE REASONS, THE COURT, UNANIMOUSLY," / "For these reasons, the Court ..."
OPERATIVE_RE = re.compile(r"(FOR THESE REASONS|PAR CES MOTIFS)\b", re.IGNORECASE)

# Nagłówki numerowane w sekcji -> poziom w ścieżce.
ROMAN = r"[IVXL]+"
# Tytuł nagłówka zaczyna się wielką literą ("(a) Pilot judgment ..."); punkty
# cytowanych przepisów zwykle małą ("11. decided to resume ...").
TITLE_START = r"[\"“‘«]?[A-ZÀ-ÝĀ-Ž]"
HEADING_RES = [
    (1, re.compile(rf"{ROMAN}\.\s+{TITLE_START}")),
    (2, re.compile(rf"[A-Z]\.\s+{TITLE_START}")),
    (3, re.compile(rf"\d{{1,2}}\.\s{TITLE_START}")),
    # (i) przed (a): pojedyncze "i" w ETPC to prawie zawsze liczba rzymska.
    (5, re.compile(rf"\(?[ivx]+\)\s+{TITLE_START}")),
    (4, re.compile(rf"\(?[a-z]\)\s+{TITLE_START}")),
    (6, re.compile(rf"\([α-ω]{{1,3}}\)\s+{TITLE_START}")),
]
# Akapit numerowany: "4.  The facts ..." (numer, dwie spacje / tabulator).
PARAGRAPH_RE = re.compile(r"\d{1,4}\.(\s{2,}|\t)")
HEADING_MAX_CHARS = 200
# Linia, po której następuje taka, nie jest nagłówkiem, tylko początkiem
# łamanego akapitu ("26. The Government objected that D. Van Oosterwijck" /
# "- had no ...").
CONTINUATION_RE = re.compile(r"[a-zà-ÿ\-–,;)]")
# Powtarzana na stronach główka: "ZAKUTNIY AND OTHERS v. UKRAINE JUDGMENT".
RUNNING_HEAD_RE = re.compile(r".+ (v\.|c\.) .+ (JUDGMENT|DECISION|ARRÊT|DÉCISION)( \(.*\))?")
VERSUS_RE = re.compile(r" (v|c)\. ")
PAGE_NUMBER_RE = re.compile(r"[-– ]*\d{1,3}[-– ]*")


def is_upper(line):
    letters = [c for c in line if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def section_of(line):
    """Top-level section of a heading line, or None."""
    if len(line) > HEADING_MAX_CHARS:
        return None
    if OPERATIVE_RE.match(line):
        return "sentencja"
    if not is_upper(line):
        return None
    for part, pattern in SECTION_RES:
        if pattern.fullmatch(line):
            return part
    return None


def heading_level(line, next_line=""):
    """Level of a numbered heading inside a section (1 = I., ...), or None."""
    if (
        len(line) > HEADING_MAX_CHARS
        or PARAGRAPH_RE.match(line)
        or line.endswith((".", ";", ",", ":"))
    ):
        return None
    if CONTINUATION_RE.match(next_line) and not any(p.match(next_line) for _, p in HEADING_RES):
        return None
    for level, pattern in HEADING_RES:
        if pattern.match(line):
            return level
    if is_upper(line) and len(line) > 3 and any(c.isalpha() for c in line):
        return 1
    return None


def is_noise(line):
    if PAGE_NUMBER_RE.fullmatch(line):
        return True
    # "v." / "c." w nazwie sprawy są małymi literami
    return bool(RUNNING_HEAD_RE.fullmatch(line) and is_upper(VERSUS_RE.sub(" ", line)))


class EtpcChunker(Chunker):
    # Części bez treści merytorycznej: nie liczymy dla nich wektorów.
    skipped_parts = ("naglowek", "zalacznik")

    def blocks(self, body):
        part, section, path = "naglowek", "", []
        lines = [x for x in lines_with_offsets(body) if not is_noise(x[1])]
        for i, (start, line) in enumerate(lines):
            new_part = section_of(line)
            if new_part:
                part, section, path = new_part, line, []
                yield Block(part, section, (), start, line, True)
                continue
            next_line = lines[i + 1][1] if i + 1 < len(lines) else ""
            level = heading_level(line, next_line) if part != "naglowek" else None
            if level:
                path = [p for p in path if p[0] < level] + [(level, line)]
            yield Block(part, section, tuple(h for _, h in path), start, line, bool(level))

    def relabel(self, groups):
        """A long header (old Commission decisions) is kept as poczatek; no sections at all = tekst."""
        header_size = sum(len(" ".join(g.lines)) for g in groups if g.part == "naglowek")
        has_sections = any(g.part != "naglowek" for g in groups)
        for g in groups:
            if g.part == "naglowek" and not has_sections:
                g.part = "tekst"
            elif g.part == "naglowek" and header_size >= SHORT_HEADER_CHARS:
                g.part = "poczatek"
        return groups
