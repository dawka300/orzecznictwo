"""Ruling text (ruling_documents.body) -> chunks for embeddings.

Pure functions, no database. The text is split along the structure of an
ECHR ruling: top-level sections (THE FACTS, THE LAW, FOR THESE REASONS,
separate opinions ... and their French forms) and, inside them, numbered
headings (I., A., 1., (a), (i), (α)). Lines (paragraphs) are packed into
chunks of about TARGET_CHARS without crossing a section; a new chunk starts
at a heading once the current one is long enough. Each chunk carries a
context line (case title, section, heading path) that goes in front of its
text when embedded, so a chunk from deep inside THE LAW still says what it
is about.
"""

from __future__ import annotations

import re

from judgments_core.vectors import Chunk

# Wielkość fragmentu w znakach (~4 znaki na token bge-m3 w tekstach EN/FR):
# ~500 tokenów, najwyżej ~800; krótszy niż MIN_CHARS dołącza do poprzedniego.
TARGET_CHARS = 2000
MAX_CHARS = 3200
MIN_CHARS = 500
# Nagłówek (skład, sędziowie) krótszy niż to pomijamy; dłuższy to zwykle
# stara decyzja Komisji bez nagłówków, gdzie cały stan faktyczny jest na początku.
SHORT_HEADER_CHARS = 2500
# Wiersz kontekstu przed fragmentem: tytuły spraw zbiorowych mają do 1000 znaków.
TITLE_MAX_CHARS = 150
CONTEXT_MAX_CHARS = 400
# Części bez treści merytorycznej: nie liczymy dla nich wektorów.
SKIPPED_PARTS = ("naglowek", "zalacznik")

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
SENTENCE_END_RE = re.compile(r"(?<=[.;:?!])\s+(?=[(\"“‘«A-ZÀ-Ý0-9])")


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


def split_long(text, limit=MAX_CHARS):
    """A paragraph longer than limit -> pieces cut at sentence ends, else at spaces."""
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
        if current and len(current) + 1 + len(sentence) > TARGET_CHARS:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}" if current else sentence
    if current:
        pieces.append(current)
    return pieces


def lines_with_offsets(body):
    """(start, line) for each non-empty stripped line of the body."""
    position = 0
    for raw in body.split("\n"):
        line = raw.strip()
        if line:
            yield position + raw.index(line[0]), line
        position += len(raw) + 1


def blocks(body):
    """Body -> blocks (part, section heading, heading path, start, text), one per line."""
    part, section, path = "naglowek", "", []
    lines = [x for x in lines_with_offsets(body) if not is_noise(x[1])]
    for i, (start, line) in enumerate(lines):
        new_part = section_of(line)
        if new_part:
            part, section, path = new_part, line, []
            yield part, section, (), start, line, True
            continue
        next_line = lines[i + 1][1] if i + 1 < len(lines) else ""
        level = heading_level(line, next_line) if part != "naglowek" else None
        if level:
            path = [p for p in path if p[0] < level] + [(level, line)]
        yield part, section, tuple(h for _, h in path), start, line, bool(level)


def chunk_document(ruling_id, body, title, meta=None, skip=SKIPPED_PARTS):
    """One language version of a ruling -> Chunks (number from 0, start = offset in body)."""
    groups = []  # [part, section, path, start, [lines]]
    for part, section, path, start, line, is_heading in blocks(body):
        last = groups[-1] if groups else None
        size = sum(len(x) + 1 for x in last[4]) if last else 0
        same_section = last is not None and last[0] == part and last[1] == section
        if (
            same_section
            and not (is_heading and size >= MIN_CHARS)
            and size + len(line) <= TARGET_CHARS
        ):
            last[4].append(line)
            if path[: len(last[2])] == last[2]:
                last[2] = path  # głębszy nagłówek w tym samym fragmencie: dokładniejsza ścieżka
            continue
        if same_section and not is_heading and len(line) > MAX_CHARS and size < MIN_CHARS:
            # Krótki początek przed bardzo długim akapitem: dokleić do pierwszego kawałka.
            pieces = split_long(line)
            last[4].append(pieces[0])
            groups.extend([part, section, path, start, [p]] for p in pieces[1:])
            continue
        groups.extend([part, section, path, start, [p]] for p in split_long(line))
    groups = merge_short(groups)

    header_size = sum(len(" ".join(g[4])) for g in groups if g[0] == "naglowek")
    has_sections = any(g[0] != "naglowek" for g in groups)
    chunks = []
    for part, section, path, start, lines in groups:
        if part == "naglowek" and has_sections and header_size >= SHORT_HEADER_CHARS:
            part = "poczatek"
        elif part == "naglowek" and not has_sections:
            part = "tekst"
        if part in skip or lines == [section]:
            continue
        context = " | ".join(x for x in (title[:TITLE_MAX_CHARS], section, " > ".join(path)) if x)
        context = context[:CONTEXT_MAX_CHARS]
        chunks.append(
            Chunk(
                judgment_id=str(ruling_id),
                number=len(chunks),
                text="\n".join(lines),
                start=start,
                part=part,
                meta={**(meta or {}), "context": context},
            )
        )
    return chunks


def merge_short(groups):
    """A chunk shorter than MIN_CHARS joins the previous one of the same section if it fits."""
    merged = []
    for group in groups:
        prev = merged[-1] if merged else None
        size = len("\n".join(group[4]))
        if (
            prev
            and size < MIN_CHARS
            and prev[0] == group[0]
            and prev[1] == group[1]
            and len("\n".join(prev[4])) + size <= MAX_CHARS
        ):
            prev[4].extend(group[4])
        else:
            merged.append(group)
    return merged


def embedding_input(chunk):
    """Text sent to the embedding model: context line + chunk text."""
    context = chunk.meta.get("context")
    return f"{context}\n\n{chunk.text}" if context else chunk.text
