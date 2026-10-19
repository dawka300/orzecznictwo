"""Raw records of judgments (JSON row of the JuDDGES dataset) -> DTOs of the project tables.

Pure functions, no database: nsa/tables.py writes the DTOs. JuddgesParser is
the NSA child of the core Parser.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date

from judgments_core.parser import Parser

KINDS = {"wyrok": "wyrok", "postanowienie": "postanowienie", "uchwała": "uchwala"}
FINALITY = {"orzeczenie prawomocne": True, "orzeczenie nieprawomocne": False}
# "6110 Podatek od towarów i usług", "658", "inne"
SYMBOL_RE = re.compile(r"^(\d{3,4})\s*(.*)$")
TEXT_PARTS = ("thesis", "sentence", "reasons", "dissenting_opinion")


@dataclass
class LegalBasis:
    journal: str | None
    article: str | None
    law: str | None
    link: str | None


@dataclass
class Related:
    cbosa_id: str | None
    docket_number: str
    judgment_date: date | None
    judgment_type: str | None


@dataclass
class Judgment:
    cbosa_id: str
    docket_number: str
    judgment_type: str
    kind: str
    court: str
    judgment_date: date
    submission_date: date | None
    is_final: bool | None
    challenged_authority: str | None
    results: list
    thesis: str | None
    sentence: str | None
    reasons: str | None
    dissenting_opinion: str | None
    # [(nazwisko, przewodniczący?, sprawozdawca?)]
    judges: list = field(default_factory=list)
    symbols: list = field(default_factory=list)  # [(kod, opis)]
    keywords: list = field(default_factory=list)
    legal_bases: list = field(default_factory=list)
    related: list = field(default_factory=list)
    publications: list = field(default_factory=list)  # [(zbior/glosa, tekst)]

    @property
    def text(self):
        return "\n\n".join(getattr(self, p) for p in TEXT_PARTS if getattr(self, p))

    @property
    def text_sha256(self):
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def cbosa_id(value):
    """'/doc/6447E5BA57' -> '6447E5BA57'."""
    return (value or "").rsplit("/", 1)[-1] or None


def parse_judgment(row):
    """A JuDDGES row (dict from JSON) -> Judgment; raises ValueError without id or date.

    A missing docket number (a few rows of the dataset) gives "" - the
    judgment is kept, nsa/tables.py reports it.
    """
    ident = cbosa_id(row.get("judgment_id"))
    docket = _text(row.get("docket_number")) or ""
    when = _date(row.get("judgment_date"))
    if not ident or when is None:
        raise ValueError("brak identyfikatora albo daty orzeczenia")
    judgment_type = _text(row.get("judgment_type")) or ""
    presiding, rapporteur = _text(row.get("presiding_judge")), _text(row.get("judge_rapporteur"))
    judges = _unique(row.get("judges"))
    for name in (presiding, rapporteur):
        if name and name not in judges:
            judges.append(name)
    return Judgment(
        cbosa_id=ident,
        docket_number=docket,
        judgment_type=judgment_type,
        kind=kind_of(judgment_type),
        court=_text(row.get("court_name")) or "",
        judgment_date=when,
        submission_date=_date(row.get("submission_date")),
        is_final=FINALITY.get(row.get("finality")),
        challenged_authority=_text(row.get("challenged_authority")),
        results=_unique(row.get("decision")),
        thesis=_text(row.get("thesis")),
        sentence=_text(row.get("sentence")),
        reasons=_text(row.get("reasons_for_judgment")),
        dissenting_opinion=_text(row.get("dissenting_opinion")),
        judges=[(name, name == presiding, name == rapporteur) for name in judges],
        symbols=_symbols(row.get("case_type_description")),
        keywords=_unique(row.get("keywords")),
        legal_bases=[
            LegalBasis(
                _text(b.get("journal")),
                _text(b.get("article")),
                _text(b.get("law")),
                _text(b.get("link")),
            )
            for b in row.get("extracted_legal_bases") or []
        ],
        related=[
            Related(
                cbosa_id(r.get("judgment_id")),
                _text(r.get("docket_number")) or "",
                _date(r.get("judgment_date")),
                _text(r.get("judgment_type")),
            )
            for r in row.get("related_docket_numbers") or []
            if _text(r.get("docket_number"))
        ],
        publications=[("zbior", p) for p in _unique(row.get("official_collection"))]
        + [("glosa", p) for p in _unique(row.get("glosa_information"))],
    )


def kind_of(judgment_type):
    """'Wyrok WSA w Opolu' -> 'wyrok'; uchwała -> 'uchwala'; the rest -> 'inne'."""
    first = judgment_type.split(" ", 1)[0].lower() if judgment_type else ""
    return KINDS.get(first, "inne")


def _symbols(values):
    out, seen = [], set()
    for value in values or []:
        value = " ".join(value.split())
        match = SYMBOL_RE.match(value)
        code, description = (match.group(1), match.group(2)) if match else (value[:10], value)
        if code and code not in seen:
            seen.add(code)
            out.append((code, description))
    return out


def _unique(values):
    """Non-empty stripped strings, duplicates removed, order kept."""
    out = []
    for value in values or []:
        value = _text(value)
        if value and value not in out:
            out.append(value)
    return out


def _text(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _date(value):
    """'2005-10-13T00:00:00+02:00' -> date(2005, 10, 13) (dates are Warsaw time)."""
    if not value:
        return None
    return date.fromisoformat(str(value)[:10])


class JuddgesParser(Parser):
    """A JuDDGES dataset row (JSON) -> Judgment."""

    def parse(self, row):
        return parse_judgment(row)
