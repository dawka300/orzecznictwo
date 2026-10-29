"""Search over the NSA tables: FULLTEXT in the texts plus filters, hybrid with vectors.

A query is typed as words and "phrases"; every word must occur, -word
excludes, word* matches a prefix. MariaDB FULLTEXT has no Polish stemming,
so with `inflect` (default) longer words are searched by their stem
(kasacyjna -> kasacyj*). With the word index (Elasticsearch, nsa/textindex.py)
words go there instead, whole (Morfologik lemmas match the inflected forms);
filters it doesn't have are applied by SQL to its best WORD_CANDIDATES.
Hybrid mode (default when vectors are available,
nsa/semantic.py): the best judgments by words and by meaning merged by RRF
(judgments_core.semantic.rrf); filters Qdrant doesn't know are applied by SQL.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import pymysql

from judgments_core.semantic import SemanticUnavailable, rrf
from judgments_core.textindex import TextIndexError
from nsa.parser import judgment_body
from nsa.stats import NSA_COURT

log = logging.getLogger(__name__)

PAGE_SIZE = 20
CANDIDATES = 200
# Wyszukiwanie po samych słowach z indeksu: tyle najlepszych (filtry spoza indeksu w SQL).
WORD_CANDIDATES = 1000
MAX_STATEMENT_S = 20
HYBRID_STATEMENT_S = 10
MAX_STATEMENT_TIME_ERROR = 1969
# Przerwane w środku przeszukiwania FULLTEXT (InnoDB, pod obciążeniem) - też limit czasu.
FULLTEXT_ABORTED_ERROR = 188
MIN_WORD = 3  # innodb_ft_min_token_size
STEM_FROM = 5  # od tylu liter słowo szukane po rdzeniu
SNIPPET_BEFORE = 160
SNIPPET_LENGTH = 480
MODES = ("", "pelnotekstowe", "znaczeniowe")  # "" = hybrydowe
# Polskie słowa bez znaczenia dla wyszukiwania (krótsze niż 3 litery i tak nie są w indeksie).
STOPWORDS = frozenset(
    [
        "oraz",
        "się",
        "jest",
        "dla",
        "lub",
        "czy",
        "przez",
        "albo",
        "jako",
        "także",
        "tym",
        "tej",
        "ten",
        "które",
        "który",
        "która",
    ]
)
FULLTEXT = (
    "MATCH(j.thesis, j.sentence, j.reasons, j.dissenting_opinion) AGAINST (%s IN BOOLEAN MODE)"
)

_TOKEN = re.compile(r'(-?)"([^"]*)"|(\S+)')
_WORD = re.compile(r"[^\W_]+")


class QueryError(ValueError):
    """The query can't be searched; the message is for the user."""


@dataclass
class Terms:
    boolean: str
    highlight: list = field(default_factory=list)  # [(tekst, prefiks?)]
    locate: str = ""  # czego szukać w uzasadnieniu dla wycinka


def parse_query(text, inflect=True):
    """User text -> Terms; raises QueryError when nothing searchable is left."""
    parts, highlight = [], []
    for match in _TOKEN.finditer(text):
        exclude, phrase, word = match.groups()
        if phrase is not None:
            words = _WORD.findall(phrase)
            if not any(len(w) >= MIN_WORD for w in words):
                continue
            parts.append(f'{"-" if exclude else "+"}"{" ".join(words)}"')
            if not exclude:
                highlight.append((" ".join(words), False))
            continue
        exclude, prefix = word.startswith("-"), word.endswith("*")
        for w in _WORD.findall(word):
            if len(w) < MIN_WORD or (w.lower() in STOPWORDS and not exclude):
                continue
            stem = prefix or (inflect and not exclude and len(w) >= STEM_FROM)
            term = w[: max(4, len(w) - 2)] if stem and not prefix else w
            parts.append(f"{'-' if exclude else '+'}{term}{'*' if stem else ''}")
            if not exclude:
                highlight.append((term, stem))
    if not highlight:
        raise QueryError("Podaj co najmniej jedno słowo do wyszukania (min. 3 litery).")
    longest = max((t for t, _ in highlight), key=len)
    return Terms(" ".join(parts), highlight, longest)


def highlight_pattern(terms):
    """Regex marking the query's words and stems in a text, or None."""
    if not terms:
        return None
    alternatives = []
    for text, prefix in sorted(terms, key=lambda t: -len(t[0])):
        body = r"\W+".join(re.escape(w) for w in text.split())
        alternatives.append(r"(?<!\w)" + body + (r"\w*" if prefix else r"(?!\w)"))
    return re.compile("|".join(alternatives), re.IGNORECASE)


@dataclass
class SearchQuery:
    text: str = ""
    mode: str = ""  # "" hybrydowe / pelnotekstowe / znaczeniowe
    inflect: bool = True  # słowa po rdzeniu (odmiana)
    kind: str = ""  # wyrok / postanowienie / uchwala / inne
    instance: str = ""  # nsa / wsa
    court: str = ""
    year_from: int = 0
    year_to: int = 0
    symbol: str = ""  # kod symbolu sprawy, np. 6110
    keyword: str = ""  # hasło tematyczne
    judge: str = ""
    authority: str = ""  # skarżony organ (początek nazwy)
    result: str = ""  # treść wyniku (dokładnie)
    final: str = ""  # "1" prawomocne / "0" nieprawomocne
    with_thesis: bool = False
    sort: str = ""  # "" trafność/najnowsze, data, najstarsze
    page: int = 1


@dataclass
class Hit:
    id: int
    cbosa_id: str
    docket_number: str
    judgment_type: str
    kind: str
    court: str
    judgment_date: object
    is_final: object
    challenged_authority: str
    result: str
    has_thesis: bool
    symbols: list = field(default_factory=list)
    keywords: list = field(default_factory=list)
    score: float = 0.0
    snippet: str = ""
    snippet_part: str = ""  # teza / uzasadnienie / fragment
    passage: str = ""  # część znaleziona znaczeniowo
    passage_start: int = -1
    passage_length: int = 0


@dataclass
class SearchResult:
    total: int
    hits: list
    seconds: float
    terms: Terms | None = None
    mode: str = "pelnotekstowe"
    notice: str = ""


def search(cur, q, semantic=None, words=None):
    """Run the query; raises QueryError for unsearchable or too slow queries.

    words (nsa.textindex.NsaWords): words through Elasticsearch; None, or the index
    down, = MariaDB FULLTEXT."""
    if semantic is not None and q.text.strip() and q.mode != "pelnotekstowe":
        try:
            return _hybrid(cur, q, semantic, words)
        except SemanticUnavailable as e:
            result = _fulltext(cur, q, words)
            result.notice = " ".join(filter(None, (str(e), result.notice)))
            return result
    return _fulltext(cur, q, words)


def _fulltext(cur, q, words=None):
    started = time.monotonic()
    terms = parse_query(q.text, q.inflect) if q.text.strip() else None
    notice = ""
    if terms is not None and words is not None:
        try:
            return _from_index(cur, q, terms, words, started)
        except TextIndexError as e:
            notice = _fallback(e)
    ranked = _run(cur, *_ids_query(q, terms))
    cur.execute("SELECT FOUND_ROWS()")
    total = cur.fetchone()[0]
    hits = _hits(cur, [row[0] for row in ranked])
    for hit, (_, score) in zip(hits, ranked):
        hit.score = float(score or 0)
    if terms is not None:
        _add_snippets(cur, hits, terms)
    return SearchResult(total, hits, time.monotonic() - started, terms, notice=notice)


def _from_index(cur, q, terms, words, started):
    """Words through the index: its ranking (or date order), judgments with reasons first."""
    sort = {"data": "desc", "najstarsze": "asc"}.get(q.sort, "")
    ids, total = _word_ids(cur, q, words, WORD_CANDIDATES, sort)
    if not sort and ids:
        ids = _reasons_first(cur, ids)
    page = max(q.page, 1)
    hits = _hits(cur, ids[(page - 1) * PAGE_SIZE : page * PAGE_SIZE])
    _add_snippets(cur, hits, terms)
    return SearchResult(total, hits, time.monotonic() - started, terms)


def _word_ids(cur, q, words, top, sort=""):
    """([id] best first, total) from the word index; filters it lacks are applied by SQL."""
    boolean = parse_query(q.text, inflect=False).boolean  # całe słowa: lematy w indeksie
    ids, total = words.ranked(boolean, q, top, sort)
    if not ids:
        return [], 0
    allowed = {row[0] for row in _run(cur, *_ids_query(q, None, only_ids=ids, top=len(ids)))}
    kept = [i for i in ids if i in allowed]
    return kept, total if len(kept) == len(ids) else len(kept)


def _fallback(error):
    log.warning("indeks słów: %s", error)
    return f"Indeks słów niedostępny ({error}) - słowa z MariaDB."


def _hybrid(cur, q, semantic, words=None):
    started = time.monotonic()
    passages = {p.judgment_id: p for p in semantic.judgments(q.text, q, k=CANDIDATES)}
    terms, by_words, notice, fallback = None, [], "", ""
    if q.mode != "znaczeniowe":
        try:
            terms = parse_query(q.text, q.inflect)
        except QueryError:
            terms = None
        if terms is not None and words is not None:
            try:
                by_words = _word_ids(cur, q, words, CANDIDATES)[0]
            except TextIndexError as e:
                fallback, words = _fallback(e), None
        if terms is not None and words is None:
            try:
                by_words = [
                    row[0]
                    for row in _run(cur, *_ids_query(q, terms, top=CANDIDATES), HYBRID_STATEMENT_S)
                ]
            except QueryError:
                notice = (
                    f"Wyszukiwanie słów trwało ponad {HYBRID_STATEMENT_S} s (bardzo ogólne słowa)"
                    " - pokazuję wyniki znaczeniowe."
                )
    allowed = set()
    if passages:
        allowed = {
            row[0]
            for row in _run(cur, *_ids_query(q, None, only_ids=list(passages), top=len(passages)))
        }
    by_meaning = [i for i in passages if i in allowed]
    scores = rrf((by_meaning, by_words))
    ids = sorted(scores, key=lambda i: -scores[i])
    if not q.sort and ids:
        ids = _reasons_first(cur, ids)
    if q.sort and ids:
        ids = [row[0] for row in _run(cur, *_ids_query(q, None, only_ids=ids, top=len(ids)))]
    page = max(q.page, 1)
    hits = _hits(cur, ids[(page - 1) * PAGE_SIZE : page * PAGE_SIZE])
    for hit in hits:
        hit.score = scores[hit.id]
    found_by_meaning = set(by_meaning)
    _add_passages(cur, [h for h in hits if h.id in found_by_meaning], passages)
    if terms is not None:
        _add_snippets(cur, [h for h in hits if h.id not in found_by_meaning], terms)
    mode = "znaczeniowe" if q.mode == "znaczeniowe" or terms is None or notice else "hybrydowe"
    notice = " ".join(filter(None, (fallback, notice)))
    return SearchResult(len(ids), hits, time.monotonic() - started, terms, mode, notice)


def _run(cur, sql, args, limit_s=MAX_STATEMENT_S):
    try:
        cur.execute(f"SET STATEMENT max_statement_time={limit_s} FOR {sql}", args)
    except pymysql.err.MySQLError as e:
        if e.args and e.args[0] in (MAX_STATEMENT_TIME_ERROR, FULLTEXT_ABORTED_ERROR):
            raise QueryError(
                f"Wyszukiwanie trwało ponad {limit_s} s - zawęź zapytanie (więcej słów, lata, sąd, symbol)."
            ) from e
        raise
    return cur.fetchall()


def _reasons_first(cur, ids):
    """ids with judgments that have reasons first, each group in the given order."""
    marks = ", ".join(["%s"] * len(ids))
    cur.execute(f"SELECT id FROM judgments WHERE id IN ({marks}) AND reasons IS NULL", ids)
    without = {row[0] for row in cur.fetchall()}
    return [i for i in ids if i not in without] + [i for i in ids if i in without]


def _ids_query(q, terms, only_ids=None, top=None):
    """SQL for one page (or the top N) of judgment ids with their score, counting all hits."""
    where, args = [], []
    if terms is not None:
        score = FULLTEXT
        where.append(FULLTEXT)
        args += [terms.boolean, terms.boolean]
    else:
        score = "NULL"
    for column, value in (("j.kind", q.kind), ("j.court", q.court)):
        if value:
            where.append(f"{column} = %s")
            args.append(value)
    if q.instance == "nsa":
        where.append("j.court = %s")
        args.append(NSA_COURT)
    elif q.instance == "wsa":
        where.append("j.court <> %s")
        args.append(NSA_COURT)
    if q.year_from:
        where.append("j.judgment_date >= %s")
        args.append(f"{q.year_from:04d}-01-01")
    if q.year_to:
        where.append("j.judgment_date < %s")
        args.append(f"{q.year_to + 1:04d}-01-01")
    if q.final in ("0", "1"):
        where.append("j.is_final = %s")
        args.append(int(q.final))
    if q.with_thesis:
        where.append("j.thesis IS NOT NULL")
    if q.authority:
        where.append("j.challenged_authority LIKE %s")
        args.append(_like_prefix(q.authority))
    for table, column, value in (
        ("judgment_symbols", "code", q.symbol),
        ("judgment_keywords", "keyword", q.keyword),
        ("judgment_judges", "name", q.judge),
        ("judgment_results", "result", q.result),
    ):
        if value:
            where.append(
                f"EXISTS (SELECT 1 FROM {table} x WHERE x.judgment_id = j.id AND x.{column} = %s)"
            )
            args.append(value)
    if only_ids is not None:
        where.append(f"j.id IN ({', '.join(['%s'] * len(only_ids))})")
        args += only_ids
    order = {"data": "j.judgment_date DESC, j.id DESC", "najstarsze": "j.judgment_date, j.id"}.get(
        q.sort,
        # przy trafności najpierw orzeczenia z uzasadnieniem (sama sentencja mało mówi)
        "(j.reasons IS NULL), score DESC, j.judgment_date DESC"
        if terms
        else "j.judgment_date DESC, j.id DESC",
    )
    sql = f"""SELECT SQL_CALC_FOUND_ROWS j.id, {score} score FROM judgments j
        {"WHERE " + " AND ".join(where) if where else ""}
        ORDER BY {order} LIMIT %s OFFSET %s"""
    if top is not None:
        return sql, args + [top, 0]
    return sql, args + [PAGE_SIZE, (max(q.page, 1) - 1) * PAGE_SIZE]


def _like_prefix(text):
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _hits(cur, ids):
    if not ids:
        return []
    marks = ", ".join(["%s"] * len(ids))
    cur.execute(
        f"""SELECT j.id, j.cbosa_id, j.docket_number, j.judgment_type, j.kind, j.court,
            j.judgment_date, j.is_final, j.challenged_authority, j.result, j.thesis IS NOT NULL,
            (SELECT GROUP_CONCAT(code ORDER BY code SEPARATOR '|') FROM judgment_symbols WHERE judgment_id = j.id),
            (SELECT GROUP_CONCAT(keyword ORDER BY keyword SEPARATOR '|') FROM judgment_keywords WHERE judgment_id = j.id)
        FROM judgments j WHERE j.id IN ({marks})""",
        ids,
    )
    by_id = {}
    for row in cur.fetchall():
        *head, symbols, keywords = row
        by_id[row[0]] = Hit(*head, symbols=_split(symbols), keywords=_split(keywords))
    return [by_id[i] for i in ids if i in by_id]


def _split(value):
    return value.split("|") if value else []


def _add_snippets(cur, hits, terms):
    """Thesis when there is one, else a piece of the reasoning around the longest query word."""
    if not hits:
        return
    marks = ", ".join(["%s"] * len(hits))
    cur.execute(
        f"""SELECT id, thesis, LOCATE(%s, reasons) pos,
            SUBSTRING(reasons, GREATEST(1, LOCATE(%s, reasons) - %s), %s), LEFT(sentence, %s)
        FROM judgments WHERE id IN ({marks})""",
        [terms.locate, terms.locate, SNIPPET_BEFORE, SNIPPET_LENGTH, SNIPPET_LENGTH]
        + [h.id for h in hits],
    )
    rows = {row[0]: row[1:] for row in cur.fetchall()}
    for hit in hits:
        thesis, pos, piece, sentence = rows.get(hit.id, (None, 0, "", ""))
        if thesis:
            hit.snippet, hit.snippet_part = _trim(thesis[:SNIPPET_LENGTH], False), "teza"
        elif pos:
            hit.snippet, hit.snippet_part = _trim(piece, pos > SNIPPET_BEFORE), "uzasadnienie"
        elif sentence:
            hit.snippet, hit.snippet_part = _trim(sentence, False), "sentencja"


def _add_passages(cur, hits, passages):
    """Hits found by meaning show the start of their best passage (offsets in judgment_body)."""
    if not hits:
        return
    marks = ", ".join(["%s"] * len(hits))
    cur.execute(
        f"SELECT id, thesis, sentence, reasons, dissenting_opinion FROM judgments WHERE id IN ({marks})",
        [h.id for h in hits],
    )
    bodies = {row[0]: judgment_body(*row[1:]) for row in cur.fetchall()}
    for hit in hits:
        p = passages[hit.id]
        body = bodies.get(hit.id, "")
        hit.snippet = _trim(body[p.start : p.start + SNIPPET_LENGTH], False)
        hit.snippet_part = p.part
        hit.passage = p.context.split(" | ", 1)[1] if " | " in p.context else ""
        hit.passage_start, hit.passage_length = p.start, p.length


def _trim(snippet, cut_start):
    """Whole words only, one line."""
    text = " ".join((snippet or "").split())
    if cut_start and " " in text:
        text = "… " + text.split(" ", 1)[1]
    if len(text) >= SNIPPET_LENGTH - 20 and " " in text:
        text = text.rsplit(" ", 1)[0] + " …"
    return text


@dataclass
class JudgmentPage:
    judgment: dict
    body: str
    judges: list  # [(nazwisko, przewodniczący, sprawozdawca)]
    symbols: list  # [(kod, opis)]
    keywords: list
    results: list
    legal_bases: list  # [dict]
    related: list  # [dict: docket_number, judgment_date, judgment_type, related_cbosa_id, id]
    publications: list  # [(rodzaj, tekst)]


def judgment_page(cur, judgment_id):
    cur.execute("SELECT * FROM judgments WHERE id = %s", (judgment_id,))
    row = cur.fetchone()
    if row is None:
        return None
    judgment = dict(zip([d[0] for d in cur.description], row))
    body = judgment_body(
        judgment["thesis"],
        judgment["sentence"],
        judgment["reasons"],
        judgment["dissenting_opinion"],
    )

    def rows(sql):
        cur.execute(sql, (judgment_id,))
        return cur.fetchall()

    def dicts(sql):
        cur.execute(sql, (judgment_id,))
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    return JudgmentPage(
        judgment=judgment,
        body=body,
        judges=rows(
            "SELECT name, is_presiding, is_rapporteur FROM judgment_judges WHERE judgment_id = %s ORDER BY position"
        ),
        symbols=rows(
            "SELECT code, description FROM judgment_symbols WHERE judgment_id = %s ORDER BY code"
        ),
        keywords=[
            r[0]
            for r in rows(
                "SELECT keyword FROM judgment_keywords WHERE judgment_id = %s ORDER BY keyword"
            )
        ],
        results=[r[0] for r in rows("SELECT result FROM judgment_results WHERE judgment_id = %s")],
        legal_bases=dicts(
            "SELECT journal, article, law, link FROM judgment_legal_bases WHERE judgment_id = %s ORDER BY position"
        ),
        related=dicts(
            "SELECT r.docket_number, r.judgment_date, r.judgment_type, r.related_cbosa_id, j.id"
            " FROM judgment_related r LEFT JOIN judgments j ON j.cbosa_id = r.related_cbosa_id"
            " WHERE r.judgment_id = %s ORDER BY r.judgment_date"
        ),
        publications=rows(
            "SELECT kind, text FROM judgment_publications WHERE judgment_id = %s ORDER BY kind, position"
        ),
    )
