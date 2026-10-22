"""A CBOSA judgment page (/doc/<ID>, HTML) -> the same row dict as the JuDDGES dataset.

The JuDDGES rows were scraped from these very pages, so reading the HTML into
their shape lets parse_judgment (nsa/parser.py) and the tables stay one code
path for both sources. Pure functions, no network or database.

Page layout (orzeczenia.nsa.gov.pl, 2026-10): header <span class="war_header">
"I OSK 1228/15 - Wyrok NSA"; then table rows <tr class="niezaznaczona">, each
with a label <td class="lista-label">Sąd</td> and a value <td class="info-list-value">
(lists separated by <br/>); text parts (Tezy, Sentencja, Uzasadnienie, Zdanie
odrębne) as <div class="lista-label"> + <span class="info-list-value-uzasadnienie">
with <P> paragraphs.
"""

from __future__ import annotations

import html
import re

from nsa.parser import JuddgesParser

DOC_URL = "https://orzeczenia.nsa.gov.pl/doc/{id}"
# Identyfikator orzeczenia: 10 znaków szesnastkowych, np. 846B7C2709.
ID_RE = re.compile(r"^[0-9A-F]{10}$")
DOC_LINK_RE = re.compile(r"/doc/([0-9A-Fa-f]{10})")
HEADER_RE = re.compile(r'class="war_header">\s*([^<]*?)\s*</span>')
ROW_SPLIT_RE = re.compile(r'<tr class="niezaznaczona">')
LABEL_RE = re.compile(r'class="lista-label">\s*([^<]*?)\s*<')
VALUE_RE = re.compile(r'class="info-list-value">(.*)', re.DOTALL)
TEXT_RE = re.compile(r'class="info-list-value-uzasadnienie">(.*)</span>', re.DOTALL)
END_RE = re.compile(r"<!-- Stopka|</table>\s*</div>\s*<!--")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
ITALIC_RE = re.compile(r"font-style:\s*italic;?\">\s*([^<]*?)\s*</td>")
BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
PARAGRAPH_RE = re.compile(r"</?p\b[^>]*>", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
# "Irena Kamińska /przewodniczący sprawozdawca/"
ROLE_RE = re.compile(r"^(.*?)\s*/([^/]*)/\s*$")
# "II SAB/Kr 414/14 - Wyrok WSA w Krakowie z 2015-01-20"
RELATED_RE = re.compile(r"^(.*?) - (.*?)(?: z (\d{4}-\d{2}-\d{2}))?$")
# <a href="http://isap...">Dz.U. 2012 poz 270</a> art. 50<br/><span class='nakt'>Ustawa ...</span>
LEGAL_BASIS_RE = re.compile(
    r'<a href="([^"]*)"[^>]*>(.*?)</a>(.*?)(?:<br\s*/?>\s*)?<span class=\'nakt\'>(.*?)</span>',
    re.DOTALL | re.IGNORECASE,
)
TEXT_FIELDS = {
    "Tezy": "thesis",
    "Sentencja": "sentence",
    "Uzasadnienie": "reasons_for_judgment",
    "Zdanie odrębne": "dissenting_opinion",
}
LIST_FIELDS = {
    "Symbol z opisem": "case_type_description",
    "Hasła tematyczne": "keywords",
    "Treść wyniku": "decision",
    "Publikacja w u.z.o.": "official_collection",
    "Info. o glosach": "glosa_information",
}


def is_judgment_page(body):
    """A real judgment page (the server sometimes answers 200 with an empty body)."""
    return bool(body) and HEADER_RE.search(body) is not None


def page_to_row(body, cbosa_id):
    """Judgment page HTML -> a dict shaped like a JuDDGES row; ValueError when it isn't one."""
    header = HEADER_RE.search(body)
    if header is None:
        raise ValueError("to nie jest strona orzeczenia CBOSA (brak nagłówka)")
    docket, _, judgment_type = _clean(header.group(1)).partition(" - ")
    row = {
        "judgment_id": f"/doc/{cbosa_id}",
        "docket_number": docket or None,
        "judgment_type": judgment_type or None,
    }
    for label, value in _rows(body):
        if label in TEXT_FIELDS:
            row[TEXT_FIELDS[label]] = paragraphs(value)
        elif label in LIST_FIELDS:
            row[LIST_FIELDS[label]] = _lines(value) or None
        elif label == "Data orzeczenia":
            when = DATE_RE.search(value)
            finality = ITALIC_RE.search(value)
            row["judgment_date"] = when.group(0) if when else None
            row["finality"] = _clean(finality.group(1)) or None if finality else None
        elif label == "Data wpływu":
            when = DATE_RE.search(value)
            row["submission_date"] = when.group(0) if when else None
        elif label == "Sąd":
            row["court_name"] = _clean(_strip(value)) or None
        elif label == "Skarżony organ":
            row["challenged_authority"] = _clean(_strip(value)) or None
        elif label == "Sędziowie":
            row.update(_judges(_lines(value)))
        elif label == "Sygn. powiązane":
            row["related_docket_numbers"] = _related(value) or None
        elif label == "Powołane przepisy":
            row["extracted_legal_bases"] = _legal_bases(value) or None
    return row


def paragraphs(fragment):
    """<P>...</P><P>...</P> -> paragraphs joined by a blank line (as in JuDDGES)."""
    fragment = BR_RE.sub("\n", fragment)
    parts = (_strip(p) for p in PARAGRAPH_RE.split(fragment))
    lines = ("\n".join(_clean(line) for line in p.split("\n")).strip() for p in parts)
    return "\n\n".join(p for p in lines if p) or None


def ids_in(text):
    """CBOSA ids in any text: /doc/<ID> links (search results, Postman exports) or bare ids,
    one per line or separated by spaces/commas; upper case, duplicates removed, order kept."""
    found = DOC_LINK_RE.findall(text) or re.split(r"[\s,;]+", text)
    out, seen = [], set()
    for value in found:
        value = value.strip().upper()
        if ID_RE.match(value) and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _rows(body):
    """(label, value html) of each table row of the page, in order."""
    end = END_RE.search(body)
    body = body[: end.start()] if end else body
    for chunk in ROW_SPLIT_RE.split(body)[1:]:
        label = LABEL_RE.search(chunk)
        if label is None:
            continue
        text = TEXT_RE.search(chunk)
        value = text or VALUE_RE.search(chunk)
        if value is not None:
            yield _clean(label.group(1)), value.group(1)


def _judges(names):
    """['Irena Kamińska /przewodniczący sprawozdawca/', ...] -> judges, presiding, rapporteur."""
    judges, presiding, rapporteur = [], None, None
    for value in names:
        match = ROLE_RE.match(value)
        name, roles = (match.group(1), match.group(2)) if match else (value, "")
        judges.append(name)
        if "przewodnicz" in roles and presiding is None:
            presiding = name
        if "sprawozdawca" in roles and rapporteur is None:
            rapporteur = name
    return {"judges": judges or None, "presiding_judge": presiding, "judge_rapporteur": rapporteur}


def _related(value):
    out = []
    for link in re.finditer(r'<a href="/doc/([0-9A-Fa-f]{10})"[^>]*>(.*?)</a>', value, re.DOTALL):
        match = RELATED_RE.match(_clean(_strip(link.group(2))))
        if match:
            out.append(
                {
                    "judgment_id": f"/doc/{link.group(1).upper()}",
                    "docket_number": match.group(1),
                    "judgment_type": match.group(2) or None,
                    "judgment_date": match.group(3),
                }
            )
    return out


def _legal_bases(value):
    return [
        {
            "link": html.unescape(link) or None,
            "journal": _clean(_strip(journal)) or None,
            "article": _clean(_strip(article)) or None,
            "law": _clean(_strip(law)) or None,
        }
        for link, journal, article, law in LEGAL_BASIS_RE.findall(value)
    ]


def _lines(value):
    """A value cell with <br/>-separated entries -> non-empty stripped lines."""
    return [line for line in (_clean(_strip(p)) for p in BR_RE.split(value)) if line]


def _strip(fragment):
    """Tags removed, entities decoded."""
    return html.unescape(TAG_RE.sub("", fragment))


def _clean(text):
    """Runs of spaces/tabs/newlines -> one space (no-break spaces kept, as in JuDDGES)."""
    return re.sub(r"[ \t\r\n]+", " ", text).strip()


class CbosaParser(JuddgesParser):
    """A CBOSA judgment page: load() keeps the HTML; nsa/tables.py CbosaTables turns it
    into a JuDDGES-shaped row (page_to_row needs the record's id), parse() as JuDDGES."""

    def load(self, body):
        return body
