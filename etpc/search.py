"""Search over the ETPC tables: FULLTEXT in the texts or titles plus filters.

A query is typed as words and "phrases"; every word must occur, -word
excludes, word* matches a prefix. MariaDB FULLTEXT indexes words of 3+
letters minus a short English stopword list, so those are left out. Known
Polish phrases are translated to English/French (etpc/glossary.py).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import pymysql

from etpc import glossary

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
LOCATE_WORDS = 8

_TOKEN = re.compile(r'(-?)"([^"]*)"|(\S+)')
_WORD_PARTS = re.compile(r"[^\W_]+")


class QueryError(ValueError):
    """The query can't be searched (no usable word); the message is for the user."""


@dataclass
class Terms:
    boolean: str  # dla MATCH ... AGAINST (... IN BOOLEAN MODE)
    highlight: list = field(default_factory=list)  # [(tekst, prefiks?)] do zaznaczania
    # czego szukać w tekście dla wycinka (LOCATE), w kolejności: najpierw całe
    # frazy, potem najdłuższe słowo każdego terminu (fraza w tekście bywa
    # z łącznikiem albo złamana wierszem)
    locate: list = field(default_factory=list)
    # [(polski zwrot, [odpowiedniki EN/FR])] przetłumaczone słownikiem
    translations: list = field(default_factory=list)


def parse_query(text, translate=True):
    """User text -> Terms; raises QueryError when nothing searchable is left.

    With translate, known Polish phrases (etpc/glossary.py) become required
    groups of their English/French wordings; the remaining plain words are
    then optional (they raise the score, but a Polish word missing from an
    English text doesn't drop it) and Polish stopwords are left out.
    """
    items = []  # ("group", polski zwrot, odpowiedniki) | ("token", wyklucz?, słowa, prefiks?)
    run = []  # kolejne zwykłe słowa - do tłumaczenia razem ("prawo do sądu")

    def flush():
        pieces = glossary.translate(run) if translate else [([w], None) for w in run]
        for words, alternatives in pieces:
            if alternatives:
                items.append(("group", " ".join(words), alternatives))
            else:
                items.append(("token", False, words, False))
        run.clear()

    for match in _TOKEN.finditer(text):
        exclude, phrase, word = match.groups()
        if phrase is not None:
            flush()
            items.append(("token", bool(exclude), _WORD_PARTS.findall(phrase), False))
            continue
        exclude, prefix = word.startswith("-"), word.endswith("*")
        words = _WORD_PARTS.findall(word)
        if not exclude and not prefix and len(words) == 1:
            run.append(words[0])
        elif translate and not exclude and not prefix and _in_glossary(run, words):
            run.extend(words)  # "adwokacko-radcowski" jako dwa słowa do słownika
        else:
            flush()
            items.append(("token", exclude, words, prefix))
    flush()

    translated = any(item[0] == "group" for item in items)
    parts, highlight, translations = [], [], []
    for item in items:
        if item[0] == "group":
            _, polish, alternatives = item
            options = []
            for alternative in alternatives:
                words = _WORD_PARTS.findall(alternative)
                if not any(_indexed(w) for w in words):
                    continue
                options.append(f'"{" ".join(words)}"' if len(words) > 1 else words[0])
                highlight.append((" ".join(words), False))
            if options:
                parts.append(f"+({' '.join(options)})")
                translations.append((polish, alternatives))
            continue
        _, exclude, words, prefix = item
        if not words:
            continue
        plain = not exclude and not prefix and len(words) == 1
        if translated and plain and glossary.normalize(words[0]) in glossary.POLISH_STOPWORDS:
            continue
        operator = "-" if exclude else ("" if translated and plain else "+")
        if len(words) > 1:
            # Fraza (także słowo z łącznikiem: pre-trial = "pre trial"); musi
            # mieć choć jedno słowo z indeksu, inaczej FULLTEXT nic nie znajdzie.
            if not any(_indexed(w) for w in words):
                continue
            term = " ".join(words)
            parts.append(f'{operator}"{term}"')
        else:
            term = words[0]
            if not _indexed(term) and not prefix:
                continue
            if prefix and len(term) < MIN_WORD:
                continue
            parts.append(f"{operator}{term}{'*' if prefix else ''}")
        if not exclude:
            highlight.append((term, prefix))
    if not highlight:
        raise QueryError(
            "Podaj co najmniej jedno słowo do wyszukania (min. 3 litery, bez słów typu the, of, to)."
        )
    locate = [term for term, _ in highlight if " " in term]
    locate += [max(term.split(), key=len) for term, _ in highlight]
    unique = list({w.lower(): w for w in reversed(locate)}.values())[::-1]
    return Terms(" ".join(parts), highlight, unique[:LOCATE_WORDS], translations)


def _in_glossary(run, words):
    """Whether a glossary phrase covers some of a hyphenated word's parts after the run."""
    position = 0
    for original, alternatives in glossary.translate(run + words):
        position += len(original)
        if alternatives and position > len(run):
            return True
    return False


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
    translate: bool = True  # polskie zwroty -> angielskie/francuskie (tylko w treści)
    cites: str = ""  # orzeczenia cytujące skargę o tym numerze
    min_cited: int = 0  # cytowane w co najmniej tylu orzeczeniach
    citing: int = 0  # orzeczenia cytujące orzeczenie o tym id (ruling_links)
    cited_by: int = 0  # orzeczenia cytowane przez orzeczenie o tym id
    # "" = trafność przy tekście, data przy jego braku; data / najstarsze / cytowane
    sort: str = ""
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
    cited_by: int
    rank: int  # miejsce w rankingu cytowań, 0 = brak
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
    terms = None
    if q.text.strip():
        terms = parse_query(q.text, translate=q.translate and q.field == "tresc")
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
    if q.min_cited or q.sort == "cytowane":
        source += " LEFT JOIN ruling_ranks k ON k.ruling_id = r.id"
    if q.min_cited:
        where.append("k.cited_by >= %s")
        args.append(q.min_cited)
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
    if q.citing:
        where.append(
            "EXISTS (SELECT 1 FROM ruling_links l WHERE l.citing_id = r.id AND l.cited_id = %s)"
        )
        args.append(q.citing)
    if q.cited_by:
        where.append(
            "EXISTS (SELECT 1 FROM ruling_links l WHERE l.cited_id = r.id AND l.citing_id = %s)"
        )
        args.append(q.cited_by)
    order = {
        "data": "r.ruling_date DESC, r.id DESC",
        "najstarsze": "r.ruling_date, r.id",
        "cytowane": "COALESCE(k.position, 4294967295), r.ruling_date DESC",
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
             FROM ruling_articles WHERE ruling_id = r.id AND relation = 'violation'),
            COALESCE(k.cited_by, 0), COALESCE(k.position, 0)
        FROM rulings r LEFT JOIN ruling_ranks k ON k.ruling_id = r.id
        WHERE r.id IN ({marks})""",
        ids,
    )
    by_id = {}
    for row in cur.fetchall():
        *head, states, appnos, appno_count, languages, violations, cited_by, rank = row
        by_id[row[0]] = Hit(
            *head,
            states=_split(states),
            appnos=_split(appnos),
            appno_count=appno_count,
            languages=_split(languages),
            violations=_split(violations),
            cited_by=cited_by,
            rank=rank,
        )
    return [by_id[i] for i in ids if i in by_id]


def _split(value):
    return value.split(",") if value else []


def _add_snippets(cur, hits, terms, language):
    """Each hit gets a piece of text around a query term, from its best document."""
    if not hits:
        return
    marks = ", ".join(["%s"] * len(hits))
    locates = ", ".join(["LOCATE(%s, body)"] * len(terms.locate))
    cur.execute(
        f"""SELECT ruling_id, itemid, language, {locates}
        FROM ruling_documents WHERE ruling_id IN ({marks})""",
        list(terms.locate) + [h.id for h in hits],
    )
    best = {}
    for ruling_id, itemid, lang, *positions in cur.fetchall():
        pos = next((p for p in positions if p), 0)  # pierwszy znaleziony w kolejności locate
        rank = (lang != language if language else True, pos == 0, _language_rank(lang))
        if ruling_id not in best or rank < best[ruling_id][0]:
            best[ruling_id] = (rank, itemid, lang, pos)
    if not best:
        return
    piece = (
        "SELECT %s, SUBSTRING(body, GREATEST(1, %s - %s), %s) FROM ruling_documents"
        " WHERE itemid = %s"
    )
    pieces = " UNION ALL ".join([piece] * len(best))
    args = []
    for _, itemid, _, pos in best.values():
        args += [itemid, pos, SNIPPET_BEFORE, SNIPPET_LENGTH, itemid]
    cur.execute(pieces, args)
    snippets = dict(cur.fetchall())
    for hit in hits:
        if hit.id in best:
            _, hit.itemid, hit.snippet_language, pos = best[hit.id]
            hit.snippet = _trim(snippets.get(hit.itemid, ""), pos > SNIPPET_BEFORE)


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
    cites_rulings: list  # [(id, title, date)] orzeczenia cytowane (ruling_links)
    cites_count: int
    cited_by: list  # [(id, title, date)] orzeczenia cytujące
    cited_by_count: int
    rank: int  # miejsce w rankingu cytowań, 0 = brak


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
    cites_rulings, cites_count = _linked(cur, "l.citing_id = %s", "l.cited_id", ruling_id)
    cited_by, cited_by_count = _linked(cur, "l.cited_id = %s", "l.citing_id", ruling_id)
    rank = _column(cur, "SELECT position FROM ruling_ranks WHERE ruling_id = %s", ruling_id)
    return RulingPage(
        ruling,
        states,
        applications,
        articles,
        keywords,
        documents,
        document,
        cites,
        cites_rulings,
        cites_count,
        cited_by,
        cited_by_count,
        rank[0] if rank else 0,
    )


def _linked(cur, condition, other, ruling_id, limit=30):
    """Newest linked rulings (ruling_links) and how many there are."""
    cur.execute(
        f"""SELECT SQL_CALC_FOUND_ROWS r.id, r.title, r.ruling_date
        FROM ruling_links l JOIN rulings r ON r.id = {other}
        WHERE {condition}
        ORDER BY r.ruling_date DESC LIMIT {int(limit)}""",
        (ruling_id,),
    )
    rows = list(cur.fetchall())
    cur.execute("SELECT FOUND_ROWS()")
    return rows, cur.fetchone()[0]


def _column(cur, sql, *args):
    cur.execute(sql, args)
    return [r[0] for r in cur.fetchall()]
