"""HUDOC raw records (a document's `columns` JSON) -> DTOs of the project tables.

Pure functions, no database: etpc/tables.py writes the DTOs. HudocParser is
the ETPC child of the core Parser. A ruling (one
ECLI) takes its metadata from its best language version (English, then
French, then any), each version is a Document with its own text.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from judgments_core.parser import Parser
from judgments_core.text import clean_html

KINDS = {"JUDGMENTS": "judgment", "DECISIONS": "decision"}
# Teksty urzędowe ETPC; reszta to tłumaczenia (rządów, ministerstw, organizacji).
OFFICIAL_LANGUAGES = ("ENG", "FRE")
EMPTY_VALUES = ("", "N/A", "NULL")
NEWLINES_RE = re.compile(r"\n{3,}")
TRAILING_SPACES_RE = re.compile(r"[ \t]+\n")


@dataclass
class Document:
    """One language version (HUDOC itemid) -> ruling_documents."""

    itemid: str
    group_key: str
    language: str
    doctype: str
    is_translation: bool
    title: str
    conclusion: str | None
    body: str
    source_created_at: datetime | None
    source_updated_at: datetime | None

    @property
    def body_sha256(self):
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()

    @property
    def priority(self):
        """Lower = better source of the ruling's metadata."""
        return language_priority(self.language)


@dataclass
class Ruling:
    """One ECLI -> rulings plus its side tables."""

    group_key: str
    ecli: str | None
    kind: str
    formation: str
    ruling_date: date
    title: str
    importance: int | None = None
    separate_opinion: bool = False
    originating_body: int | None = None
    type_code: int | None = None
    conclusion: str | None = None
    represented_by: str | None = None
    judges: str | None = None
    domestic_law: str | None = None
    strasbourg_case_law: str | None = None
    applications: list = field(default_factory=list)
    respondents: list = field(default_factory=list)
    articles: list = field(default_factory=list)  # (relation, article)
    keywords: list = field(default_factory=list)
    citations: list = field(default_factory=list)


def language_priority(language):
    return OFFICIAL_LANGUAGES.index(language) if language in OFFICIAL_LANGUAGES else 9


def kind_of(columns):
    """judgment / decision, None for collections we don't keep."""
    trunk = columns.get("doctypetrunk") or ""
    if not trunk:
        parts = (columns.get("documentcollectionid2") or "").split(";")
        trunk = parts[1] if len(parts) > 1 else ""
    return KINDS.get(trunk)


def is_wanted(columns):
    return kind_of(columns) is not None and not _bool(columns.get("isplaceholder"))


def group_key(columns):
    ecli = _text(columns.get("ecli"))
    return ecli or f"itemid:{columns['itemid']}"


def parse_document(columns):
    language = _text(columns.get("languageisocode")) or "???"
    return Document(
        itemid=columns["itemid"],
        group_key=group_key(columns),
        language=language,
        doctype=_text(columns.get("doctype")) or "",
        is_translation=language not in OFFICIAL_LANGUAGES,
        title=_title(columns),
        conclusion=_text(columns.get("conclusion")),
        body=clean_body(columns.get("contentbody")),
        source_created_at=parse_timestamp(columns.get("createddate")),
        source_updated_at=parse_timestamp(columns.get("updateddate")),
    )


def parse_ruling(columns):
    """Ruling-level data from the document chosen as the ruling's best version."""
    applications = split_list(columns.get("appno"))
    return Ruling(
        group_key=group_key(columns),
        ecli=_text(columns.get("ecli")),
        kind=kind_of(columns),
        formation=_text(columns.get("doctypebranch")) or "",
        ruling_date=ruling_date(columns),
        title=_title(columns),
        importance=_int(columns.get("importance")),
        separate_opinion=_bool(columns.get("separateopinion")),
        originating_body=_int(columns.get("originatingbody")),
        type_code=_int(columns.get("typedescription")),
        conclusion=_text(columns.get("conclusion")),
        represented_by=_joined(columns.get("representedby")),
        judges=_joined(columns.get("judges")),
        domestic_law=_joined(clean_html(columns.get("issue"))),
        strasbourg_case_law=_joined(columns.get("scl")),
        applications=[a for a in applications if len(a) <= 32],
        respondents=split_list(columns.get("respondent")),
        articles=[
            (relation, article)
            for relation, key in (
                ("concerned", "article"),
                ("violation", "violation"),
                ("no_violation", "nonviolation"),
            )
            for article in split_list(columns.get(key))
            if len(article) <= 50
        ],
        keywords=[int(k) for k in split_list(columns.get("kpthesaurus")) if k.isdigit()],
        citations=[
            a
            for a in split_list(columns.get("extractedappno"))
            if a not in applications and len(a) <= 32
        ],
    )


def ruling_date(columns):
    """kpdate (ISO), else judgementdate/decisiondate (dd/mm/yyyy hh:mm:ss)."""
    kpdate = _text(columns.get("kpdate"))
    if kpdate:
        return date.fromisoformat(kpdate[:10])
    for key in ("judgementdate", "decisiondate"):
        value = _text(columns.get(key))
        if value:
            return datetime.strptime(value[:10], "%d/%m/%Y").date()
    raise ValueError(f"{columns.get('itemid')}: brak daty orzeczenia")


def parse_timestamp(value):
    """'2026-10-08T07:51:10.947Z' -> naive UTC datetime (seconds), None if empty."""
    value = _text(value)
    if not value:
        return None
    return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")


def clean_body(text):
    """contentbody: CRLF -> LF, no trailing spaces, at most one empty line."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = TRAILING_SPACES_RE.sub("\n", text)
    return NEWLINES_RE.sub("\n\n", text).strip()


def split_list(value):
    """'6;6-1;6' -> ['6', '6-1'] (order kept, duplicates and blanks dropped)."""
    items = []
    for item in (value or "").split(";"):
        item = item.strip()
        if item and item not in EMPTY_VALUES and item not in items:
            items.append(item)
    return items


def _title(columns):
    return (_text(columns.get("docname")) or columns["itemid"])[:1000]


def _joined(value):
    """'A ; B;C' -> 'A; B; C', None when empty."""
    return "; ".join(split_list(value)) or None


def _text(value):
    if value is None:
        return None
    value = str(value).strip()
    return None if value in EMPTY_VALUES else value


def _int(value):
    value = _text(value)
    return int(value) if value and value.isdigit() else None


def _bool(value):
    return (_text(value) or "").lower() == "true"


class HudocParser(Parser):
    """HUDOC `columns` JSON -> Document (one language version) and Ruling."""

    def parse(self, columns):
        return parse_document(columns)

    def parse_ruling(self, columns):
        return parse_ruling(columns)

    def is_wanted(self, columns):
        """Judgments and decisions only, no placeholders."""
        return is_wanted(columns)
