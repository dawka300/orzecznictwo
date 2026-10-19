"""ETPC search pages: python -m etpc www [--port 8080].

/                   wyszukiwanie (tekst + filtry), bez zapytania: najnowsze orzeczenia
/orzeczenie/<id>    orzeczenie z metadanymi i pełnym tekstem wybranej wersji językowej
/statystyki         liczby orzeczeń z ruling_stats
"""

from __future__ import annotations

import logging
import re

from etpc import labels, search
from etpc.embed import PROGRESS_KEY
from etpc.labels import label
from etpc.semantic import EtpcSemantic
from etpc.stats import read_stats
from judgments_core.embed import read_progress
from judgments_core.embeddings import Embedder
from judgments_core.qdrant import QdrantStore, collection_name
from judgments_core.semantic import SemanticUnavailable
from judgments_core.web import Response, WebApp, e, url

log = logging.getLogger(__name__)

_RULING_PATH = re.compile(r"^/orzeczenie/(\d+)$")
_FRAGMENT = re.compile(r"^(\d+)-(\d+)$")
# Zapytanie z przeglądarki nie czeka długo na LM Studio/Qdranta.
SEMANTIC_TIMEOUT_S = 20
SIMILAR = 10


class EtpcWebApp(WebApp):
    """Search, ruling and statistics pages of ETPC (child of WebApp).

    semantic: etpc.semantic.EtpcSemantic; by default LM Studio + Qdrant from settings.
    """

    def __init__(self, settings, semantic=None):
        super().__init__(settings)
        self.semantic = semantic or EtpcSemantic(
            Embedder(
                settings.embed_url, settings.embed_model, timeout_s=SEMANTIC_TIMEOUT_S, retries=0
            ),
            QdrantStore(
                settings.qdrant_url,
                settings.qdrant_collection or collection_name("etpc", settings.embed_model),
                settings.embed_model,
                timeout_s=SEMANTIC_TIMEOUT_S,
            ),
        )

    def route(self, request):
        match = _RULING_PATH.match(request.path)
        if request.path not in ("/", "/statystyki") and not match:
            return Response(
                _layout("Nie ma takiej strony", "<p>Nie ma takiej strony.</p>"), "404 Not Found"
            )
        with self.connect() as conn, conn.cursor() as cur:
            if match:
                return _ruling(cur, request, int(match.group(1)), self.semantic)
            if request.path == "/statystyki":
                return _stats(cur)
            return _search(cur, request, self.semantic)

    def error_page(self):
        return Response(
            _layout("Błąd", "<p class=error>Błąd serwera - szczegóły w logu.</p>"),
            "500 Internal Server Error",
        )


# --- wyszukiwanie ---------------------------------------------------------


def _query(request):
    return search.SearchQuery(
        text=request.get("q"),
        field="tytul" if request.get("pole") == "tytul" else "tresc",
        kind=request.get("rodzaj"),
        formation=request.get("sklad"),
        language=request.get("jezyk"),
        respondent=request.get("panstwo"),
        article=request.get("artykul").replace("art.", "").strip(),
        relation=request.get("relacja"),
        importance=request.get_int("waznosc"),
        year_from=request.get_int("od"),
        year_to=request.get_int("do"),
        appno=request.get("skarga"),
        cites=request.get("cytuje"),
        min_cited=request.get_int("cytowan"),
        citing=request.get_int("cytujace"),
        cited_by=request.get_int("cytowane_przez"),
        translate=not request.get("bez_tlumaczenia"),
        sort=request.get("sort"),
        page=request.get_int("strona", 1),
        mode=request.get("tryb") if request.get("tryb") in search.MODES else "",
    )


def _params(q, **changes):
    """URL parameters of a query (for pagination and links)."""
    params = {
        "q": q.text,
        "pole": "tytul" if q.field == "tytul" else "",
        "rodzaj": q.kind,
        "sklad": q.formation,
        "jezyk": q.language,
        "panstwo": q.respondent,
        "artykul": q.article,
        "relacja": q.relation,
        "waznosc": q.importance,
        "od": q.year_from,
        "do": q.year_to,
        "skarga": q.appno,
        "cytuje": q.cites,
        "cytowan": q.min_cited,
        "cytujace": q.citing,
        "cytowane_przez": q.cited_by,
        "bez_tlumaczenia": "" if q.translate else "1",
        "sort": q.sort,
        "strona": q.page if q.page > 1 else 0,
        "tryb": q.mode,
    }
    params.update(changes)
    return params


def _search(cur, request, semantic=None):
    q = _query(request)
    stats = read_stats(cur)
    error, result = "", None
    try:
        result = search.search(cur, q, semantic)
    except search.QueryError as err:
        error = str(err)
    form = _search_form(q, stats)
    if error:
        body = f"<p class=error>{e(error)}</p>"
    else:
        body = _results(q, result)
    title = f"{q.text} - wyszukiwanie" if q.text else "Wyszukiwanie"
    body = _vectors_line(read_progress(cur, PROGRESS_KEY)) + body
    return Response(_layout(title, f"<div class=search>{form}<section>{body}</section></div>"))


def _vectors_line(progress):
    """One line about the embedding while it is not finished."""
    if not progress or progress["done"] >= progress["total"]:
        return ""
    percent = 100 * progress["done"] / progress["total"] if progress["total"] else 0
    return (
        f"<p class=progress-line>Wyszukiwanie znaczeniowe: policzono {_n(progress['done'])}"
        f" z {_n(progress['total'])} orzeczeń ({percent:.1f}%)"
        f"{' - trwa' if progress['status'] == 'liczy' else ' - wstrzymane'}."
        ' <a href="/statystyki#wektory">szczegóły</a></p>'
    )


def _options(rows, names, selected, all_label):
    """<option>s from ruling_stats rows (code, count, ...); without stats from the labels."""
    rows = rows or [(code, None) for code in names]
    out = [f'<option value="">{e(all_label)}</option>']
    for row in rows:
        code, count = str(row[0]), row[1]
        text = label(names, code) if names.get(code) else code
        if names is labels.LANGUAGES or names is labels.STATES:
            text = f"{text} ({code})" if names.get(code) else code
        if count is not None:
            text += f" - {_n(count)}"
        sel = " selected" if code == selected else ""
        out.append(f'<option value="{e(code)}"{sel}>{e(text)}</option>')
    return "".join(out)


def _select(name, options_html, caption):
    return f'<label>{e(caption)}<select name="{name}">{options_html}</select></label>'


def _plain_options(pairs, selected):
    return "".join(
        f'<option value="{e(v)}"{" selected" if str(v) == str(selected) else ""}>{e(t)}</option>'
        for v, t in pairs
    )


def _search_form(q, stats):
    field_radio = "".join(
        f'<label class=radio><input type=radio name=pole value="{v}"'
        f"{' checked' if q.field == v else ''}> {t}</label>"
        for v, t in (("tresc", "w treści"), ("tytul", "w nazwie sprawy"))
    )
    order = list(labels.FORMATIONS)
    formations = sorted(
        stats.get("formation", []), key=lambda r: order.index(r[0]) if r[0] in order else 99
    )
    return f"""<form class=filters method=get action="/">
<div class=query>
  <input type=search name=q value="{e(q.text)}" placeholder="np. pre-trial detention, &quot;freedom of expression&quot;, -turkey" autofocus>
  <button type=submit>Szukaj</button>
</div>
<div class=field>{field_radio}</div>
{_select("tryb", _plain_options([("", "hybrydowo: słowa + znaczenie"), ("znaczeniowe", "tylko znaczenie"), ("pelnotekstowe", "tylko słowa")], q.mode), "Tryb wyszukiwania")}
<label class=check><input type=checkbox name=bez_tlumaczenia value=1{"" if q.translate else " checked"}> bez tłumaczenia polskich zwrotów</label>
<p class=hint>Hybrydowo: orzeczenia z szukanymi słowami i orzeczenia o podobnym znaczeniu (pytanie można zadać zwyczajnie, po polsku), razem w jednej kolejności. Słowa: „fraza w cudzysłowie”, -słowo wyklucza, słowo* = początek słowa; znane polskie zwroty (prawo do sądu, przewlekłość …) są tłumaczone.</p>
<div class=grid>
{_select("rodzaj", _plain_options([("", "wyroki i decyzje"), ("judgment", "wyroki"), ("decision", "decyzje")], q.kind), "Rodzaj")}
{_select("sklad", _options(formations, labels.FORMATIONS, q.formation, "wszystkie"), "Skład")}
{_select("jezyk", _options(stats.get("language"), labels.LANGUAGES, q.language, "wszystkie"), "Język tekstu")}
{_select("panstwo", _options(stats.get("respondent"), labels.STATES, q.respondent, "wszystkie"), "Państwo pozwane")}
<label>Artykuł<input name=artykul value="{e(q.article)}" placeholder="np. 6, 6-1, P1-1"></label>
{_select("relacja", _plain_options([("", "dowolnie"), ("violation", "naruszenie"), ("no_violation", "brak naruszenia"), ("concerned", "dotyczy")], q.relation), "Artykuł - jak")}
{_select("waznosc", _plain_options([(0, "każda"), (1, "tylko kluczowe"), (2, "co najmniej wysoka"), (3, "co najmniej średnia")], q.importance), "Ważność")}
<label>Rok od<input name=od value="{e(q.year_from or "")}" inputmode=numeric placeholder="1959"></label>
<label>Rok do<input name=do value="{e(q.year_to or "")}" inputmode=numeric placeholder="2026"></label>
<label>Nr skargi<input name=skarga value="{e(q.appno)}" placeholder="np. 22716/12"></label>
<label>Cytuje skargę<input name=cytuje value="{e(q.cites)}" placeholder="np. 30210/96"></label>
{_select("cytowan", _plain_options([(0, "dowolnie"), (10, "co najmniej 10 razy"), (50, "co najmniej 50 razy"), (100, "co najmniej 100 razy"), (500, "co najmniej 500 razy")], q.min_cited), "Cytowane")}
{_select("sort", _plain_options([("", "trafność / najnowsze"), ("cytowane", "najczęściej cytowane"), ("data", "najnowsze"), ("najstarsze", "najstarsze")], q.sort), "Kolejność")}
{_hidden(q)}
</div>
<div class=actions><button type=submit>Szukaj</button> <a href="/">wyczyść</a></div>
</form>"""


def _results(q, result):
    notice = f"<p class=notice>{e(result.notice)}</p>" if result.notice else ""
    if result.total == 0:
        return (
            notice
            + _translations(q, result)
            + "<p class=empty>Nic nie znaleziono. Spróbuj innych słów albo mniej filtrów."
            " Teksty są głównie po angielsku i francusku - polskie słowa spoza słownika"
            " znajdą się tylko w tłumaczeniach.</p>"
        )
    first = (max(q.page, 1) - 1) * search.PAGE_SIZE + 1
    last = first + len(result.hits) - 1
    how = {
        "hybrydowe": f"najlepszych wg słów i znaczenia (do {search.CANDIDATES} z każdego)",
        "znaczeniowe": f"najbliższych znaczeniowo (do {search.CANDIDATES})",
    }.get(result.mode, "")
    head = (
        f"<p class=count>Orzeczenia {_n(first)}-{_n(last)} z <b>{_n(result.total)}</b>"
        f"{' ' + how if how else ''} <span>({result.seconds:.2f} s)</span></p>"
    )
    pattern = search.highlight_pattern(result.terms.highlight) if result.terms else None
    items = "".join(_hit(q, h, pattern) for h in result.hits)
    return (
        notice
        + _translations(q, result)
        + head
        + f"<ol class=hits start={first}>{items}</ol>"
        + _pages(q, result.total)
    )


def _translations(q, result):
    if result is None or result.terms is None or not result.terms.translations:
        return ""
    items = "; ".join(
        f"<b>{e(polish)}</b> → {e(' | '.join(alternatives))}"
        for polish, alternatives in result.terms.translations
    )
    off = url("/", **_params(q, bez_tlumaczenia="1", strona=0))
    return (
        f"<p class=translated>Przetłumaczone: {items}."
        f' <a href="{e(off)}">Szukaj bez tłumaczenia</a></p>'
    )


def _hidden(q):
    """Filters from links (cited by / citing) kept when the form is sent again."""
    return "".join(
        f'<input type=hidden name="{name}" value="{value}">'
        for name, value in (("cytujace", q.citing), ("cytowane_przez", q.cited_by))
        if value
    )


def _hit(q, h, pattern):
    link = url(
        f"/orzeczenie/{h.id}",
        jezyk=h.snippet_language,
        q=q.text if q.field == "tresc" else "",
        bez_tlumaczenia="" if q.translate else "1",
        fragment=f"{h.passage_start}-{h.passage_length}" if h.passage_start >= 0 else "",
    )
    if h.passage_start >= 0:
        link += "#fragment"
    states = ", ".join(label(labels.STATES, s) for s in h.states)
    appnos = ", ".join(h.appnos)
    if h.appno_count > len(h.appnos):
        appnos += f" (+{_n(h.appno_count - len(h.appnos))})"
    badges = f'<span class="badge {e(h.kind)}">{e(label(labels.KINDS, h.kind))}</span>'
    if h.importance == 1:
        badges += "<span class='badge key'>kluczowe</span>"
    if h.cited_by:
        badges += (
            f"<span class='badge cited' title='miejsce {_n(h.rank)} w rankingu cytowań'>"
            f"cytowane {_n(h.cited_by)}×</span>"
        )
    meta = " · ".join(
        x
        for x in (
            _date(h.ruling_date),
            e(label(labels.FORMATIONS, h.formation)),
            e(states),
            e(appnos),
        )
        if x
    )
    langs = " ".join(
        f"<span title='{e(label(labels.LANGUAGES, x))}'>{e(x)}</span>" for x in h.languages
    )
    violations = (
        f"<p class=viol>Naruszenie: {e(', '.join(h.violations))}</p>" if h.violations else ""
    )
    if h.snippet:
        where = f"<span class=passage-path>{e(h.passage)}</span>" if h.passage else ""
        text = f"<p class=snip><span class=lang>{e(h.snippet_language)}</span> {where}{_highlight(h.snippet, pattern)}</p>"
    elif h.conclusion:
        text = f"<p class=snip>{e(_shorten(h.conclusion, 300))}</p>"
    else:
        text = ""
    return f"""<li><a class=title href="{e(link)}">{e(h.title)}</a>
<p class=meta>{badges} {meta}</p>{violations}{text}<p class=langs>{langs}</p></li>"""


def _pages(q, total):
    count = (total + search.PAGE_SIZE - 1) // search.PAGE_SIZE
    if count <= 1:
        return ""
    page = max(q.page, 1)
    shown = sorted({1, count, *range(max(1, page - 2), min(count, page + 2) + 1)})
    out, previous = [], 0
    if page > 1:
        out.append(f'<a href="{e(url("/", **_params(q, strona=page - 1)))}">« poprzednia</a>')
    for p in shown:
        if p - previous > 1:
            out.append("<span>…</span>")
        if p == page:
            out.append(f"<b>{p}</b>")
        else:
            out.append(f'<a href="{e(url("/", **_params(q, strona=p if p > 1 else 0)))}">{p}</a>')
        previous = p
    if page < count:
        out.append(f'<a href="{e(url("/", **_params(q, strona=page + 1)))}">następna »</a>')
    return f"<nav class=pages>{''.join(out)}</nav>"


# --- orzeczenie -----------------------------------------------------------


def _ruling(cur, request, ruling_id, semantic=None):
    page = search.ruling_page(cur, ruling_id, request.get("jezyk"))
    if page is None:
        return Response(
            _layout("Nie ma takiego orzeczenia", "<p>Nie ma takiego orzeczenia.</p>"),
            "404 Not Found",
        )
    r, query = page.ruling, request.get("q")
    pattern = None
    if query:
        try:
            terms = search.parse_query(query, translate=not request.get("bez_tlumaczenia"))
            pattern = search.highlight_pattern(terms.highlight)
        except search.QueryError:
            pattern = None

    def row(caption, value):
        return f"<dt>{e(caption)}</dt><dd>{value}</dd>" if value else ""

    def article_links(relation):
        return ", ".join(
            f'<a href="{e(url("/", artykul=a, relacja=relation))}">{e(a)}</a>'
            for a in page.articles.get(relation, [])
        )

    apps = ", ".join(
        f'<a href="{e(url("/", skarga=a))}">{e(a)}</a>' for a in page.applications[:50]
    )
    if len(page.applications) > 50:
        apps += f" … (razem {_n(len(page.applications))})"
    states = ", ".join(
        f'<a href="{e(url("/", panstwo=s))}">{e(label(labels.STATES, s))}</a>' for s in page.states
    )
    doc = page.document
    hudoc = f"https://hudoc.echr.coe.int/eng?i={doc['itemid']}" if doc else ""
    meta = "".join(
        [
            row("Data", _date(r["ruling_date"])),
            row("Rodzaj", e(label(labels.KINDS, r["kind"]))),
            row("Skład", e(label(labels.FORMATIONS, r["formation"]))),
            row("Ważność", e(label(labels.IMPORTANCE, r["importance"] or 0))),
            row(
                "Cytowania",
                f"w {_n(page.cited_by_count)} orzeczeniach, miejsce {_n(page.rank)} w rankingu"
                if page.cited_by_count
                else "",
            ),
            row("Państwo", states),
            row("Skargi", apps),
            row("Naruszenie", article_links("violation")),
            row("Brak naruszenia", article_links("no_violation")),
            row("Dotyczy art.", article_links("concerned")),
            row("Zdanie odrębne", "tak" if r["separate_opinion"] else ""),
            row("ECLI", e(r["ecli"])),
            row("Sentencja", e(r["conclusion"])),
            row("Pełnomocnik", e(r["represented_by"])),
            row("Sędziowie", e(r["judges"])),
            row("Prawo krajowe", _details(r["domestic_law"])),
            row("Orzecznictwo ETPC", _details(r["strasbourg_case_law"])),
            row("Słowa kluczowe (id)", e(", ".join(str(k) for k in page.keywords))),
            row(
                "HUDOC",
                f'<a href="{e(hudoc)}" rel=noreferrer>{e(doc["itemid"])}</a>' if doc else "",
            ),
        ]
    )
    tabs = "".join(
        (
            f"<b>{e(label(labels.LANGUAGES, d['language']))}"
            f"{' (tłum.)' if d['is_translation'] else ''}</b>"
            if doc and d["itemid"] == doc["itemid"]
            else f'<a href="{e(url(f"/orzeczenie/{ruling_id}", jezyk=d["language"], q=query, bez_tlumaczenia=request.get("bez_tlumaczenia")))}">'
            f"{e(label(labels.LANGUAGES, d['language']))}{' (tłum.)' if d['is_translation'] else ''}</a>"
        )
        for d in page.documents
    )
    fragment = _FRAGMENT.match(request.get("fragment"))
    passage = None
    if fragment:
        passage = (int(fragment.group(1)), int(fragment.group(1)) + int(fragment.group(2)))
    text, marks = _body(doc["body"], pattern, passage) if doc else ("", 0)
    if doc and not doc["body"].strip():
        text = (
            "<p class=empty>HUDOC nie ma tekstu tego dokumentu (zapewne skan PDF) -"
            f' <a href="{e(hudoc)}" rel=noreferrer>otwórz w HUDOC</a>.</p>'
        )
    found = ""
    if pattern is not None and doc:
        found = (
            f'<p class=found>Trafień w tekście: {marks}. <a href="#t1">Przejdź do pierwszego</a></p>'
            if marks
            else "<p class=found>Szukane słowa nie występują w tej wersji językowej.</p>"
        )
    side = _similar(cur, semantic, ruling_id) + _citations(page)
    body = f"""<p class=back><a href="/" onclick="if(history.length>1){{history.back();return false}}">« wróć</a></p>
<h1>{e(r["title"])}</h1>
<div class=ruling>
<article>
<nav class=tabs>{tabs}</nav>
{found}
<div class=text>{text}</div>
</article>
<aside><dl>{meta}</dl>{side}</aside>
</div>"""
    return Response(_layout(r["title"], body))


def _citations(page):
    out = ""
    if page.cited_by:
        out += _linked_list(
            "Cytowane w", page.cited_by, page.cited_by_count, cytujace=page.ruling["id"]
        )
    if page.cites_rulings:
        out += _linked_list(
            "Cytuje", page.cites_rulings, page.cites_count, cytowane_przez=page.ruling["id"]
        )
    if page.cites:
        links = ", ".join(f'<a href="{e(url("/", skarga=a))}">{e(a)}</a>' for a in page.cites)
        out += (
            f"<details class=cited><summary>Przywołane numery skarg ({_n(len(page.cites))})"
            f"</summary>{links}</details>"
        )
    return out


def _linked_list(caption, rows, count, **search_params):
    items = "".join(
        f'<li><a href="/orzeczenie/{i}">{e(t)}</a> <span>{_date(d)}</span></li>' for i, t, d in rows
    )
    more = ""
    if count > len(rows):
        link = url("/", sort="cytowane", **search_params)
        more = f'<p><a href="{e(link)}">wszystkie ({_n(count)}), od najczęściej cytowanych</a></p>'
    return f"<h2>{caption} ({_n(count)})</h2><ul class=cites>{items}</ul>{more}"


def _body(text, pattern, passage=None):
    """Plain text -> paragraphs, query words marked; returns (html, marks).

    passage = (start, end) in the text: its paragraphs are marked as the
    passage found by meaning, the first one with id=fragment.
    """
    counter = [0]
    out = []
    position, anchored = 0, False
    for raw in text.split("\n"):
        start, position = position, position + len(raw) + 1
        line = raw.strip()
        if not line:
            continue
        classes = ["caps"] if line.isupper() and len(line) < 120 else []
        anchor = ""
        if passage and start < passage[1] and position > passage[0]:
            classes.append("passage")
            if not anchored:
                anchor, anchored = " id=fragment", True
        css = f" class='{' '.join(classes)}'" if classes else ""
        out.append(f"<p{css}{anchor}>{_highlight(line, pattern, counter)}</p>")
    return "\n".join(out), counter[0]


def _similar(cur, semantic, ruling_id):
    """Rulings closest in meaning (mean vector of this one's reasoning)."""
    if semantic is None:
        return ""
    try:
        passages = semantic.similar(ruling_id, SIMILAR)
    except SemanticUnavailable:
        return ""
    if not passages:
        return ""
    ids = [p.judgment_id for p in passages]
    cur.execute(
        f"SELECT id, title, ruling_date FROM rulings WHERE id IN ({', '.join(['%s'] * len(ids))})",
        ids,
    )
    found = {row[0]: row for row in cur.fetchall()}
    items = "".join(
        f'<li><a href="/orzeczenie/{i}">{e(found[i][1])}</a> <span>{_date(found[i][2])}</span></li>'
        for i in ids
        if i in found
    )
    return f"<h2>Podobne orzeczenia</h2><ul class=cites>{items}</ul>"


# --- statystyki -----------------------------------------------------------


def _stats(cur):
    stats = read_stats(cur)
    if not stats:
        return Response(
            _layout(
                "Statystyki",
                "<p class=empty>Statystyki jeszcze nie policzone - uruchom: python -m etpc przetworz</p>",
            )
        )
    total = stats["total"][0]
    cards = "".join(
        f"<div class=card><b>{_n(v)}</b><span>{t}</span></div>"
        for v, t in (
            (total[1], "orzeczeń"),
            (total[2], "wyroków"),
            (total[3], "decyzji"),
            (total[4], "dokumentów (wersji językowych)"),
        )
    )

    def table(dimension, caption, names, param, documents=False, limit=None, order=None):
        rows = stats.get(dimension, [])
        if order:
            rows = sorted(rows, key=order)
        rows = rows[:limit] if limit else rows
        top = max((r[1] for r in rows), default=1) or 1
        head = "<tr><th></th><th>orzeczeń</th><th>wyroków</th><th>decyzji</th>"
        head += "<th>dokumentów</th></tr>" if documents else "</tr>"
        body = ""
        for value, rulings, judgments, decisions, docs, _ in rows:
            name = (
                label(names, int(value) if names is labels.IMPORTANCE else value)
                if names
                else value
            )
            if names in (labels.LANGUAGES, labels.STATES) and names.get(value):
                name = f"{name} ({value})"
            link = url("/", **{param: value}) if param else ""
            cell = f'<a href="{e(link)}">{e(name)}</a>' if link else e(name)
            body += (
                f"<tr><td>{cell}</td><td class=num{_bar(rulings, top)}>{_n(rulings)}</td>"
                f"<td class=num>{_n(judgments)}</td><td class=num>{_n(decisions)}</td>"
            )
            body += f"<td class=num>{_n(docs)}</td></tr>" if documents else "</tr>"
        return f"<section class=card-table><h2>{caption}</h2><table>{head}{body}</table></section>"

    def years_link(value):
        return url("/", od=value, do=value)

    years = stats.get("year", [])
    top = max((r[1] for r in years), default=1)
    year_rows = "".join(
        f"<tr><td><a href='{e(years_link(v))}'>{e(v)}</a></td><td class=num{_bar(n, top)}>{_n(n)}</td>"
        f"<td class=num>{_n(j)}</td><td class=num>{_n(d)}</td></tr>"
        for v, n, j, d, _, _ in sorted(years, key=lambda r: int(r[0]), reverse=True)
    )
    tables = "".join(
        [
            table("language", "Języki tekstów", labels.LANGUAGES, "jezyk", documents=True),
            table("respondent", "Państwa pozwane", labels.STATES, "panstwo"),
            table("formation", "Składy", labels.FORMATIONS, "sklad"),
            table("importance", "Ważność", labels.IMPORTANCE, "waznosc", order=lambda r: int(r[0])),
            table("violation", "Najczęściej naruszone artykuły (30)", None, "", limit=30),
            _most_cited(cur),
            (
                "<section class=card-table><h2>Lata</h2><table><tr><th></th><th>orzeczeń</th>"
                f"<th>wyroków</th><th>decyzji</th></tr>{year_rows}</table></section>"
            ),
        ]
    )
    updated = total[5].strftime("%d.%m.%Y %H:%M") if total[5] else ""
    body = f"""<h1>Statystyki</h1>
<p class=hint>Przeliczone {e(updated)} UTC, po ostatnim przetworz. Orzeczenie = jedno ECLI ze wszystkimi wersjami językowymi.</p>
<div class=cards>{cards}</div>
{_vectors_card(read_progress(cur, PROGRESS_KEY))}
<div class=tables>{tables}</div>"""
    return Response(_layout("Statystyki", body))


VECTOR_STATUS = {
    "liczy": "trwa liczenie",
    "przerwano": "wstrzymane - dokończy następne: python -m etpc wektory",
    "gotowe": "wszystko policzone",
}


def _vectors_card(progress):
    """Embedding progress (python -m etpc wektory), from sync_state."""
    if not progress:
        return ""
    total, done = progress["total"], progress["done"]
    percent = 100 * done / total if total else 100
    status = VECTOR_STATUS.get(progress["status"], progress["status"])
    if progress["status"] == "liczy" and progress.get("eta_s"):
        status += f", zostało ok. {_duration(progress['eta_s'])}"
    pace = progress.get("chars_per_s") or 0
    facts = [
        f"<b>{_n(done)}</b> z <b>{_n(total)}</b> orzeczeń ({percent:.1f}%)",
        f"{_n(progress['chunks'])} fragmentów w bazie wektorów",
        f"tempo ~{_n(round(pace / 4))} tokenów/s" if pace else "",
        f"model {e(progress['model'])}",
        f"ostatni zapis {e(progress['updated'])} UTC",
    ]
    return f"""<section id=wektory class=vectors><h2>Wyszukiwanie znaczeniowe (wektory)</h2>
<div class=progress><div style="width:{percent:.2f}%"></div></div>
<p><b>{e(status)}</b></p><p class=hint>{" · ".join(f for f in facts if f)}</p>
<p class=hint>Najpierw wyroki (od kluczowych i najnowszych), potem decyzje. Odśwież stronę, żeby zobaczyć postęp.</p></section>"""


def _duration(seconds):
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min"


def _most_cited(cur, limit=50):
    cur.execute(
        """SELECT r.id, r.title, r.ruling_date, k.cited_by
        FROM ruling_ranks k JOIN rulings r ON r.id = k.ruling_id
        ORDER BY k.position LIMIT %s""",
        (limit,),
    )
    rows = cur.fetchall()
    if not rows:
        return ""
    top = rows[0][3] or 1
    body = "".join(
        f'<tr><td><a href="/orzeczenie/{i}">{e(t)}</a></td><td class=num>{_date(d)}</td>'
        f"<td class=num{_bar(n, top)}>{_n(n)}</td></tr>"
        for i, t, d, n in rows
    )
    more = url("/", sort="cytowane")
    return (
        f"<section class=card-table><h2>Najczęściej cytowane ({limit})"
        f' <a href="{e(more)}">wszystkie</a></h2>'
        f"<table><tr><th></th><th>data</th><th>cytowań</th></tr>{body}</table></section>"
    )


# --- wspólne ----------------------------------------------------------------


def _highlight(text, pattern, counter=None):
    if pattern is None:
        return e(text)
    out, last = [], 0
    for m in pattern.finditer(text):
        out.append(e(text[last : m.start()]))
        mark_id = ""
        if counter is not None:
            counter[0] += 1
            if counter[0] == 1:
                mark_id = " id=t1"
        out.append(f"<mark{mark_id}>{e(m.group(0))}</mark>")
        last = m.end()
    out.append(e(text[last:]))
    return "".join(out)


def _bar(value, top):
    """Background bar in a number cell, proportional to value."""
    share = 100 * value / (top or 1)
    return f" style='background:linear-gradient(to left,var(--bar) {share:.1f}%,transparent {share:.1f}%)'"


def _details(text):
    if not text:
        return ""
    if len(text) < 200:
        return e(text)
    return f"<details><summary>{e(_shorten(text, 80))}</summary>{e(text)}</details>"


def _shorten(text, length):
    text = " ".join(text.split())
    return text if len(text) <= length else text[:length].rsplit(" ", 1)[0] + " …"


def _n(value):
    return "" if value is None else f"{value:,}".replace(",", " ")


def _date(value):
    return value.strftime("%d.%m.%Y") if value else ""


def _layout(title, body):
    return f"""<!doctype html>
<html lang=pl><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>{e(title)} - Orzecznictwo ETPC</title>
<style>{CSS}</style></head>
<body><header><a class=brand href="/">Orzecznictwo ETPC</a>
<nav><a href="/">Szukaj</a><a href="/statystyki">Statystyki</a></nav></header>
<main>{body}</main></body></html>"""


CSS = """
.notice{padding:8px 12px;border-radius:7px;background:var(--key);color:var(--key-t)}
.passage-path{display:block;color:var(--muted);font-size:12px;margin:2px 0}
.text p.passage{background:var(--accent-soft);border-left:3px solid var(--accent);padding-left:8px;margin-left:-11px}
.vectors{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:4px 16px 8px;margin:16px 0}
.progress{height:12px;border-radius:6px;background:var(--line);overflow:hidden}
.progress div{height:100%;background:var(--accent)}
.progress-line{margin:0 0 10px;padding:6px 10px;border-radius:7px;background:var(--accent-soft);font-size:13px}
:root{--bg:#f6f7f9;--panel:#fff;--text:#1d2330;--muted:#5d6678;--line:#dfe3ea;
--accent:#1f4fa3;--accent-soft:#e8eefb;--mark:#ffe58a;--judgment:#e6f3ea;--judgment-t:#1e6b3a;
--decision:#f3ede4;--decision-t:#7a5216;--key:#fbe4e4;--key-t:#9b2121;--bar:#d9e3f7}
@media (prefers-color-scheme:dark){:root{--bg:#14171d;--panel:#1c2028;--text:#e3e7ee;--muted:#9aa3b5;
--line:#2d333f;--accent:#8fb3ff;--accent-soft:#233050;--mark:#6b5a12;--judgment:#1e3527;--judgment-t:#9bd8ae;
--decision:#3a3020;--decision-t:#e7c48c;--key:#4a2323;--key-t:#f2a5a5;--bar:#2a3a5c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
header{display:flex;align-items:center;gap:24px;padding:12px 24px;background:var(--panel);border-bottom:1px solid var(--line)}
.brand{font-weight:700;font-size:17px;color:var(--text)}header nav{display:flex;gap:16px}
main{max-width:1280px;margin:0 auto;padding:20px 24px 60px}
h1{font-size:22px;line-height:1.3;margin:4px 0 16px}h2{font-size:15px;margin:18px 0 8px}
.search{display:grid;grid-template-columns:320px 1fr;gap:24px;align-items:start}
.filters{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;position:sticky;top:12px}
.query{display:flex;gap:8px}.query input{flex:1;min-width:0}
input,select{width:100%;padding:7px 9px;border:1px solid var(--line);border-radius:7px;background:var(--bg);color:var(--text);font:inherit}
input[type=radio]{width:auto}
button{padding:7px 14px;border:0;border-radius:7px;background:var(--accent);color:var(--panel);font:inherit;font-weight:600;cursor:pointer}
.field{display:flex;gap:14px;margin:10px 0 4px;color:var(--muted)}.radio{display:flex;align-items:center;gap:4px}
.hint{color:var(--muted);font-size:13px;margin:6px 0 12px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.grid label{display:flex;flex-direction:column;gap:3px;font-size:13px;color:var(--muted)}
.actions{margin-top:14px;display:flex;align-items:center;gap:12px}
.count{color:var(--muted);margin:2px 0 12px}.count b{color:var(--text)}
.hits{list-style:none;padding:0;margin:0}
.hits li{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:10px}
.hits .title{font-weight:600;font-size:16px}
.meta{color:var(--muted);font-size:13px;margin:4px 0}
.badge{display:inline-block;padding:0 7px;border-radius:9px;font-size:12px;font-weight:600;margin-right:6px}
.badge.judgment{background:var(--judgment);color:var(--judgment-t)}.badge.decision{background:var(--decision);color:var(--decision-t)}
.badge.key{background:var(--key);color:var(--key-t)}
.badge.cited{background:var(--accent-soft);color:var(--accent)}
.check{display:flex;align-items:center;gap:6px;color:var(--muted);font-size:13px;margin:2px 0}
.check input{width:auto}
.translated{background:var(--accent-soft);border-radius:8px;padding:8px 12px;font-size:13px;margin:0 0 12px}
.viol{font-size:13px;margin:2px 0}.snip{margin:6px 0 0;font-size:14px}
.lang,.langs span{font-size:11px;font-weight:600;color:var(--muted);border:1px solid var(--line);border-radius:4px;padding:0 4px;margin-right:3px}
.langs{margin:6px 0 0}
mark{background:var(--mark);color:inherit;border-radius:2px;padding:0 1px}
.pages{display:flex;flex-wrap:wrap;gap:6px;margin-top:16px}
.pages a,.pages b,.pages span{padding:4px 10px;border-radius:7px;background:var(--panel);border:1px solid var(--line)}
.pages b{background:var(--accent);color:var(--panel);border-color:var(--accent)}
.error{background:var(--key);color:var(--key-t);padding:12px 14px;border-radius:8px}
.empty{color:var(--muted)}
.back{margin:0 0 6px}
.ruling{display:grid;grid-template-columns:1fr 360px;gap:24px;align-items:start}
.ruling article{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:8px 28px 28px;min-width:0}
.tabs{display:flex;flex-wrap:wrap;gap:6px;padding:10px 0;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--panel)}
.tabs a,.tabs b{padding:3px 10px;border-radius:7px;font-size:13px}.tabs b{background:var(--accent-soft)}
.found{color:var(--muted);font-size:13px}
.text{font-family:Georgia,"Times New Roman",serif;font-size:16px;line-height:1.65;overflow-wrap:anywhere}
.text p{margin:0 0 .8em}.text .caps{font-family:system-ui,sans-serif;font-weight:700;font-size:14px;letter-spacing:.02em;margin-top:1.4em}
aside{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;font-size:14px;position:sticky;top:12px;max-height:calc(100vh - 24px);overflow:auto}
dl{margin:0}dt{color:var(--muted);font-size:12px;margin-top:8px}dd{margin:0;overflow-wrap:anywhere}
details summary{cursor:pointer;color:var(--accent)}
.cites{padding-left:18px;margin:0}.cites span{color:var(--muted);font-size:12px}.cited{overflow-wrap:anywhere}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:18px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card b{display:block;font-size:24px}.card span{color:var(--muted)}
.tables{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}
.card-table{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:4px 16px 12px;overflow:auto}
table{border-collapse:collapse;width:100%;font-size:14px}
th{color:var(--muted);font-weight:500;text-align:right;font-size:12px;padding:4px 6px}
td{padding:3px 6px;border-top:1px solid var(--line)}
td.num{text-align:right;white-space:nowrap}
@media (max-width:900px){.search,.ruling,.tables{grid-template-columns:1fr}.filters,aside{position:static;max-height:none}
.cards{grid-template-columns:1fr 1fr}main{padding:16px}.ruling article{padding:8px 16px 20px}}
"""
