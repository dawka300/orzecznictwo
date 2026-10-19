"""Search over the ETPC tables: FULLTEXT in the texts or titles plus filters.

A query is typed as words and "phrases"; every word must occur, -word
excludes, word* matches a prefix. MariaDB FULLTEXT indexes words of 3+
letters minus a short English stopword list, so those are left out.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import pymysql

PAGE_SIZE = 20
# Zapytanie dłuższe niż tyle sekund jest przerywane (bardzo ogólne słowa
# przy zimnej pamięci podręcznej bazy) - lepiej poprosić o zawężenie.
MAX_STATEMENT_S = 20
MIN_WORD = 3  # innodb_ft_min_token_size
# INFORMATION_SCHEMA.INNODB_FT_DEFAULT_STOPWORD
_STOPWORDS = (
    "a about an are as at be by com de en for from how i in is it la of on or"
    " that the this to was what when where who will with und www"
)
STOPWORDS = frozenset(_STOPWORDS.split())
SNIPPET_BEFORE = 160
SNIPPET_LENGTH = 480
MAX_STATEMENT_TIME_ERROR = 1969

_TOKEN = re.compile(r'(-?)"([^"]*)"|(\S+)')
_WORD_PARTS = re.compile(r"[^\W_]+")


class QueryError(ValueError):
    """The query can't be searched (no usable word); the message is for the user."""


@dataclass
class Terms:
    boolean: str  # dla MATCH ... AGAINST (... IN BOOLEAN MODE)
    highlight: list = field(default_factory=list)  # [(tekst, prefiks?)] do zaznaczania
    # najdłuższe szukane słowo: wokół niego wycinek tekstu (LOCATE); fraza
    # się nie nadaje - w tekście bywa z łącznikiem albo złamana wierszem
    locate: str = ""


def parse_query(text):
    """User text -> Terms; raises QueryError when nothing searchable is left."""
    parts, highlight = [], []
    for match in _TOKEN.finditer(text):
        exclude, phrase, word = match.groups()
        if phrase is not None:
            words = _WORD_PARTS.findall(phrase)
            prefix = False
        else:
            exclude = word.startswith("-")
            prefix = word.endswith("*")
            words = _WORD_PARTS.findall(word)
        if not words:
            continue
        if len(words) > 1:
            # Fraza (także słowo z łącznikiem: pre-trial = "pre trial"); musi
            # mieć choć jedno słowo z indeksu, inaczej FULLTEXT nic nie znajdzie.
            if not any(_indexed(w) for w in words):
                continue
            term = " ".join(words)
            parts.append(f'{"-" if exclude else "+"}"{term}"')
        else:
            term = words[0]
            if not _indexed(term) and not prefix:
                continue
            if prefix and len(term) < MIN_WORD:
                continue
            parts.append(f"{'-' if exclude else '+'}{term}{'*' if prefix else ''}")
        if not exclude:
            highlight.append((term, prefix))
    if not highlight:
        raise QueryError(
            "Podaj co najmniej jedno słowo do wyszukania (min. 3 litery, bez słów typu the, of, to)."
        )
    words = [w for text, _ in highlight for w in text.split()]
    return Terms(" ".join(parts), highlight, max(words, key=len))


def _indexed(word):
    return len(word) >= MIN_WORD and word.lower() not in STOPWORDS


def highlight_pattern(terms):
    """Regex marking the query's words and phrases in a text, or None."""
    if not terms:
        return None
    alternatives = []
    for text, prefix in sorted(terms, key=lambda t: -len(t[0])):
        body = r"\W+".join(re.escape(w) for w in text.split())
        end = r"\w*" if prefix else r"(?!\w)"
        alternatives.append(r"(?<!\w)" + body + end)
    return re.compile("|".join(alternatives), re.IGNORECASE)


@dataclass
class SearchQuery:
    text: str = ""
    field: str = "tresc"  # tresc (pełne teksty) / tytul (nazwa sprawy)
    kind: str = ""
    formation: str = ""
    language: str = ""
    respondent: str = ""
    article: str = ""
    relation: str = ""  # "" / violation / no_violation / concerned
    importance: int = 0  # 1-4: ważność co najmniej taka (1 = tylko kluczowe)
    year_from: int = 0
    year_to: int = 0
    appno: str = ""
    cites: str = ""  # orzeczenia cytujące skargę o tym numerze
    sort: str = ""  # "" = trafność przy tekście, data przy jego braku; data / najstarsze
    page: int = 1


@dataclass
class Hit:
    id: int
    ecli: str
    kind: str
    formation: str
    ruling_date: object
    title: str
    importance: int
    conclusion: str
    states: list
    appnos: list
    appno_count: int
    languages: list
    violations: list
    score: float = 0.0
    itemid: str = ""  # dokument z wycinkiem
    snippet_language: str = ""
    snippet: str = ""


@dataclass
class SearchResult:
    total: int
    hits: list
    seconds: float
    terms: Terms | None = None


def search(cur, q):
    """Run the query; raises QueryError for unsearchable or too slow queries."""
    started = time.monotonic()
    terms = parse_query(q.text) if q.text.strip() else None
    sql, args = _ids_query(q, terms)
    try:
        cur.execute(f"SET STATEMENT max_statement_time={MAX_STATEMENT_S} FOR {sql}", args)
    except pymysql.err.OperationalError as e:
        if e.args[0] == MAX_STATEMENT_TIME_ERROR:
            raise QueryError(
                f"Wyszukiwanie trwało ponad {MAX_STATEMENT_S} s - zawęź zapytanie"
                " (więcej słów, filtr lat, państwa lub języka)."
            ) from e
        raise
    ranked = cur.fetchall()
    cur.execute("SELECT FOUND_ROWS()")
    total = cur.fetchone()[0]
    hits = _hits(cur, [row[0] for row in ranked])
    for hit, (_, score) in zip(hits, ranked):
        hit.score = float(score or 0)
    if terms is not None and q.field == "tresc":
        _add_snippets(cur, hits, terms, q.language)
    return SearchResult(total, hits, time.monotonic() - started, terms)


def _ids_query(q, terms):
    """SQL for one page of ruling ids with their score, counting all hits."""
    where, args = [], []
    if terms is not None and q.field == "tresc":
        language = "AND d.language = %s" if q.language else ""
        source = f"""(SELECT d.ruling_id, MAX(MATCH(d.body) AGAINST (%s IN BOOLEAN MODE)) score
            FROM ruling_documents d
            WHERE MATCH(d.body) AGAINST (%s IN BOOLEAN MODE) {language}
            GROUP BY d.ruling_id) m JOIN rulings r ON r.id = m.ruling_id"""
        args += [terms.boolean, terms.boolean] + ([q.language] if q.language else [])
        score = "m.score"
    else:
        source = "rulings r"
        if terms is not None:
            score = "MATCH(r.title) AGAINST (%s IN BOOLEAN MODE)"
            where.append("MATCH(r.title) AGAINST (%s IN BOOLEAN MODE)")
            args += [terms.boolean, terms.boolean]
        else:
            score = "NULL"
        if q.language:
            where.append(
                "EXISTS (SELECT 1 FROM ruling_documents d"
                " WHERE d.ruling_id = r.id AND d.language = %s)"
            )
            args.append(q.language)
    for column, value in (("r.kind", q.kind), ("r.formation", q.formation)):
        if value:
            where.append(f"{column} = %s")
            args.append(value)
    if q.importance:
        where.append("r.importance <= %s")
        args.append(q.importance)
    if q.year_from:
        where.append("r.ruling_date >= %s")
        args.append(f"{q.year_from:04d}-01-01")
    if q.year_to:
        where.append("r.ruling_date < %s")
        args.append(f"{q.year_to + 1:04d}-01-01")
    if q.respondent:
        where.append(
            "EXISTS (SELECT 1 FROM ruling_respondents s WHERE s.ruling_id = r.id AND s.state = %s)"
        )
        args.append(q.respondent)
    if q.article:
        relation = "AND a.relation = %s" if q.relation else ""
        where.append(
            "EXISTS (SELECT 1 FROM ruling_articles a WHERE a.ruling_id = r.id"
            f" AND (a.article = %s OR a.article LIKE %s) {relation})"
        )
        args += [q.article, _like_prefix(q.article + "-")] + ([q.relation] if q.relation else [])
    if q.appno:
        where.append(
            "EXISTS (SELECT 1 FROM ruling_applications p WHERE p.ruling_id = r.id AND p.appno = %s)"
        )
        args.append(q.appno)
    if q.cites:
        where.append(
            "EXISTS (SELECT 1 FROM ruling_citations c"
            " WHERE c.ruling_id = r.id AND c.cited_appno = %s)"
        )
        args.append(q.cites)
    order = {
        "data": "r.ruling_date DESC, r.id DESC",
        "najstarsze": "r.ruling_date, r.id",
    }.get(q.sort, "score DESC, r.ruling_date DESC" if terms else "r.ruling_date DESC, r.id DESC")
    sql = f"""SELECT SQL_CALC_FOUND_ROWS r.id, {score} score FROM {source}
        {"WHERE " + " AND ".join(where) if where else ""}
        ORDER BY {order} LIMIT %s OFFSET %s"""
    page = max(q.page, 1)
    return sql, args + [PAGE_SIZE, (page - 1) * PAGE_SIZE]


def _like_prefix(text):
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _hits(cur, ids):
    """Rulings with their states, applications, languages and violations, in ids order."""
    if not ids:
        return []
    marks = ", ".join(["%s"] * len(ids))
    cur.execute(
        f"""SELECT r.id, r.ecli, r.kind, r.formation, r.ruling_date, r.title, r.importance,
            r.conclusion,
            (SELECT GROUP_CONCAT(state ORDER BY state)
             FROM ruling_respondents WHERE ruling_id = r.id),
            (SELECT SUBSTRING_INDEX(GROUP_CONCAT(appno ORDER BY position), ',', 3)
             FROM ruling_applications WHERE ruling_id = r.id),
            (SELECT COUNT(*) FROM ruling_applications WHERE ruling_id = r.id),
            (SELECT GROUP_CONCAT(DISTINCT language ORDER BY language)
             FROM ruling_documents WHERE ruling_id = r.id),
            (SELECT GROUP_CONCAT(article ORDER BY article)
             FROM ruling_articles WHERE ruling_id = r.id AND relation = 'violation')
        FROM rulings r WHERE r.id IN ({marks})""",
        ids,
    )
    by_id = {}
    for row in cur.fetchall():
        *head, states, appnos, appno_count, languages, violations = row
        by_id[row[0]] = Hit(
            *head,
            states=_split(states),
            appnos=_split(appnos),
            appno_count=appno_count,
            languages=_split(languages),
            violations=_split(violations),
        )
    return [by_id[i] for i in ids if i in by_id]


def _split(value):
    return value.split(",") if value else []


def _add_snippets(cur, hits, terms, language):
    """Each hit gets a piece of text around the first term, from its best document."""
    if not hits:
        return
    marks = ", ".join(["%s"] * len(hits))
    cur.execute(
        f"""SELECT ruling_id, itemid, language, LOCATE(%s, body) pos,
            SUBSTRING(body, GREATEST(1, LOCATE(%s, body) - %s), %s)
        FROM ruling_documents WHERE ruling_id IN ({marks})""",
        [terms.locate, terms.locate, SNIPPET_BEFORE, SNIPPET_LENGTH] + [h.id for h in hits],
    )
    best = {}
    for ruling_id, itemid, lang, pos, snippet in cur.fetchall():
        rank = (lang != language if language else True, pos == 0, _language_rank(lang))
        if ruling_id not in best or rank < best[ruling_id][0]:
            best[ruling_id] = (rank, itemid, lang, pos, snippet)
    for hit in hits:
        if hit.id in best:
            _, hit.itemid, hit.snippet_language, pos, snippet = best[hit.id]
            hit.snippet = _trim(snippet, pos > SNIPPET_BEFORE)


def _language_rank(language):
    return {"ENG": 0, "FRE": 1}.get(language, 2)


def _trim(snippet, cut_start):
    """Whole words only, one line."""
    text = " ".join(snippet.split())
    if cut_start and " " in text:
        text = "… " + text.split(" ", 1)[1]
    if " " in text:
        text = text.rsplit(" ", 1)[0] + " …"
    return text


@dataclass
class RulingPage:
    ruling: dict
    states: list
    applications: list
    articles: dict  # relation -> [article]
    keywords: list
    documents: list  # [dict] bez treści
    document: dict | None  # wybrany dokument z treścią
    cites: list  # numery skarg przywołanych
    cited_by: list  # [(id, title, date)]
    cited_by_count: int


def ruling_page(cur, ruling_id, language=""):
    """Everything about one ruling, with the text of one language version; None if missing."""
    cur.execute(
        """SELECT id, group_key, ecli, kind, formation, ruling_date, title, importance,
            separate_opinion, conclusion, represented_by, judges, domestic_law,
            strasbourg_case_law, source_updated_at
        FROM rulings WHERE id = %s""",
        (ruling_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    ruling = dict(zip([c[0] for c in cur.description], row))
    states = _column(cur, "SELECT state FROM ruling_respondents WHERE ruling_id = %s", ruling_id)
    applications = _column(
        cur,
        "SELECT appno FROM ruling_applications WHERE ruling_id = %s ORDER BY position",
        ruling_id,
    )
    cur.execute(
        "SELECT relation, article FROM ruling_articles WHERE ruling_id = %s ORDER BY article",
        (ruling_id,),
    )
    articles = {}
    for relation, article in cur.fetchall():
        articles.setdefault(relation, []).append(article)
    keywords = _column(
        cur, "SELECT term_id FROM ruling_keywords WHERE ruling_id = %s ORDER BY term_id", ruling_id
    )
    cur.execute(
        """SELECT itemid, language, doctype, is_translation, title, body_length
        FROM ruling_documents WHERE ruling_id = %s""",
        (ruling_id,),
    )
    names = [c[0] for c in cur.description]
    documents = sorted(
        (dict(zip(names, r)) for r in cur.fetchall()),
        key=lambda d: (_language_rank(d["language"]), d["language"], d["itemid"]),
    )
    chosen = next((d for d in documents if d["language"] == language), None)
    chosen = chosen or (documents[0] if documents else None)
    document = None
    if chosen is not None:
        cur.execute(
            "SELECT body, conclusion FROM ruling_documents WHERE itemid = %s", (chosen["itemid"],)
        )
        body, conclusion = cur.fetchone()
        document = {**chosen, "body": body, "conclusion": conclusion}
    cites = _column(
        cur,
        "SELECT cited_appno FROM ruling_citations WHERE ruling_id = %s ORDER BY cited_appno",
        ruling_id,
    )
    cur.execute(
        """SELECT SQL_CALC_FOUND_ROWS r.id, r.title, r.ruling_date
        FROM (SELECT DISTINCT c.ruling_id
              FROM ruling_applications a
              JOIN ruling_citations c ON c.cited_appno = a.appno
              WHERE a.ruling_id = %s) x
        JOIN rulings r ON r.id = x.ruling_id
        WHERE r.id <> %s
        ORDER BY r.ruling_date DESC LIMIT 30""",
        (ruling_id, ruling_id),
    )
    cited_by = list(cur.fetchall())
    cur.execute("SELECT FOUND_ROWS()")
    cited_by_count = cur.fetchone()[0]
    return RulingPage(
        ruling,
        states,
        applications,
        articles,
        keywords,
        documents,
        document,
        cites,
        cited_by,
        cited_by_count,
    )


def _column(cur, sql, *args):
    cur.execute(sql, args)
    return [r[0] for r in cur.fetchall()]
