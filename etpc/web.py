"""ETPC search pages: python -m etpc www [--port 8080].

/                   wyszukiwanie (tekst + filtry), bez zapytania: najnowsze orzeczenia
/orzeczenie/<id>    orzeczenie z metadanymi i pełnym tekstem wybranej wersji językowej
/statystyki         liczby orzeczeń z ruling_stats
"""

from __future__ import annotations

import logging
import re

from etpc import labels, search
from etpc.chunker import EtpcChunker
from etpc.embed import PROGRESS_KEY
from etpc.labels import label
from etpc.rag import EtpcRetriever
from etpc.semantic import EtpcSemantic
from etpc.stats import read_stats
from etpc.weights import TIERS
from judgments_core.analysis import DocumentAnalysis
from judgments_core.embed import read_progress
from judgments_core.embeddings import make_embedder
from judgments_core.qdrant import QdrantStore, collection_name
from judgments_core.semantic import SemanticUnavailable
from judgments_core.web import (
    Response,
    WebApp,
    e,
    layout,
    print_button,
    url,
    vectors_card,
    vectors_line,
)
from judgments_core.web import bar as _bar
from judgments_core.web import date_pl as _date
from judgments_core.web import details as _details
from judgments_core.web import highlight as _highlight
from judgments_core.web import number as _n
from judgments_core.web import shorten as _shorten
from judgments_core.web import text_html as _body

log = logging.getLogger(__name__)

_RULING_PATH = re.compile(r"^/orzeczenie/(\d+)$")
_FRAGMENT = re.compile(r"^(\d+)-(\d+)$")
# Zapytanie z przeglądarki nie czeka długo na LM Studio/Qdranta (gdy model
# zapytań w procesie nie działa).
SEMANTIC_TIMEOUT_S = 20
SIMILAR = 10


class EtpcWebApp(WebApp):
    """Search, ruling and statistics pages of ETPC (child of WebApp).

    semantic: etpc.semantic.EtpcSemantic; by default the settings' query embedders + Qdrant.
    """

    description = (
        "Wyroki i decyzje Europejskiego Trybunału Praw Człowieka z bazy HUDOC: teksty"
        " po angielsku i francusku (część po polsku), artykuły Konwencji, naruszenia, cytowania."
    )

    def facts(self, cur):
        stats = read_stats(cur)
        if not stats:
            return []
        total = stats["total"][0]
        return [(_n(total[1]), "orzeczeń"), (_n(total[2]), "wyroków"), (_n(total[3]), "decyzji")]

    def __init__(self, settings, semantic=None):
        super().__init__(settings)
        if semantic is None:
            semantic = EtpcSemantic(
                make_embedder(settings, timeout_s=SEMANTIC_TIMEOUT_S, retries=0),
                QdrantStore(
                    settings.qdrant_url,
                    settings.qdrant_collection or collection_name("etpc", settings.embed_model),
                    settings.embed_model,
                    timeout_s=SEMANTIC_TIMEOUT_S,
                ),
            )
            semantic.prepare_in_background()
        self.semantic = semantic
        self.analysis = DocumentAnalysis(
            settings,
            EtpcRetriever(semantic),
            EtpcChunker(),
            self.connect,
            getattr(semantic, "embedder", None),
        )

    def route(self, request):
        if request.path == "/analiza" or request.path.startswith("/analiza/"):
            return self.analysis.handle(request, _layout)
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
        weight=_weight_param(request.get("waga")),
        citing=request.get_int("cytujace"),
        cited_by=request.get_int("cytowane_przez"),
        translate=not request.get("bez_tlumaczenia"),
        sort=request.get("sort"),
        page=request.get_int("strona", 1),
        mode=_PAGE_MODES.get(request.get("tryb"), DEFAULT_MODE),
    )


# Tryb na stronie: domyślnie samo znaczenie - hybryda z FULLTEXT-em bywa bardzo wolna
# (polskie zwroty -> grupy częstych fraz, 1-4 min) przy tej samej trafności na pytaniach
# wzorcowych (notepad.md, „Czas pytania RAG”). W adresie: tryb=hybrydowe / pelnotekstowe.
DEFAULT_MODE = "znaczeniowe"
_PAGE_MODES = {
    "": DEFAULT_MODE,
    "znaczeniowe": "znaczeniowe",
    "hybrydowe": "",  # "" = hybryda w etpc.search
    "pelnotekstowe": "pelnotekstowe",
}


def _mode_param(mode):
    """The page's tryb parameter for a search mode ("" when it is the default)."""
    return {DEFAULT_MODE: "", "": "hybrydowe"}.get(mode, mode)


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
        "waga": q.weight,
        "cytujace": q.citing,
        "cytowane_przez": q.cited_by,
        "bez_tlumaczenia": "" if q.translate else "1",
        "sort": q.sort,
        "strona": q.page if q.page > 1 else 0,
        "tryb": _mode_param(q.mode),
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
    body = vectors_line(read_progress(cur, PROGRESS_KEY)) + body
    return Response(_layout(title, f"<div class=search>{form}<section>{body}</section></div>"))


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
{_select("tryb", _plain_options([("", "znaczenie (szybko)"), ("hybrydowe", "słowa + znaczenie (wolniej)"), ("pelnotekstowe", "tylko słowa")], _mode_param(q.mode)), "Tryb wyszukiwania")}
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
{_select("waga", _plain_options([("", "każda"), ("A", "A - kluczowe"), ("B", "co najmniej B - ważne"), ("C", "co najmniej C - zwykłe")], q.weight), "Waga")}
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
    if h.tier:
        badges += _weight_badge(h.tier, h.reasons)
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
        code = labels.LANG_CODES.get(h.snippet_language, "")
        text = (
            f"<p class=snip{f' lang={code}' if code else ''}><span class=lang translate=no>"
            f"{e(h.snippet_language)}</span> {where}{_highlight(h.snippet, pattern)}</p>"
        )
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
            row("Waga", _weight_badge(*page.weight) + e(page.weight[1]) if page.weight else ""),
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
    if len(page.documents) == 1 and doc and doc["language"] != "POL":
        # Wyroki Izby wychodzą w jednym języku urzędowym; inne wersje (jeśli w ogóle) później.
        tabs += (
            "<span class=hint>jedyna wersja w HUDOC - po polsku: prawy przycisk"
            " → Przetłumacz na polski (Pobierz PDF zapisze tłumaczenie)</span>"
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
    # Język strony = język wersji w tekście: przeglądarka proponuje tłumaczenie
    # (np. z angielskiego), polski interfejs obok jest wyłączony z tłumaczenia.
    lang = labels.LANG_CODES.get(doc["language"], "pl") if doc else "pl"
    official = ""
    if doc:
        pdf = url(HUDOC_PDF, library="ECHR", id=doc["itemid"], filename=f"{doc['itemid']}.pdf")
        version = label(labels.LANGUAGES, doc["language"])
        official = f'<a href="{e(pdf)}" target=_blank rel="noopener noreferrer">PDF z HUDOC ({e(version)})</a>'
    tools = f"<p class=doc-tools lang=pl translate=no>{print_button()}{official}</p>"
    # na wydruku/PDF panelu bocznego nie ma: data, skład i źródło w jednej linii
    tools += (
        f"<p class=print-only lang=pl translate=no>{_date(r['ruling_date'])} · "
        f"{e(label(labels.FORMATIONS, r['formation']))} · {e(hudoc)}</p>"
    )
    body = f"""<p class=back lang=pl translate=no><a href="/" onclick="if(history.length>1){{history.back();return false}}">« wróć</a></p>
<h1>{e(r["title"])}</h1>
{tools}
<div class=ruling>
<article>
<nav class=tabs lang=pl translate=no>{tabs}</nav>
<div lang=pl translate=no>{found}</div>
<div class=text lang={lang}>{text}</div>
</article>
<aside lang=pl translate=no><dl>{meta}</dl>{side}</aside>
</div>"""
    return Response(_layout(r["title"], body, lang))


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
    # Kolejność = wypełnianie dwóch słupków (.tables): mniej więcej pół wysokości w każdym.
    tables = "".join(
        [
            table("respondent", "Państwa pozwane", labels.STATES, "panstwo"),
            (
                "<section class=card-table><h2>Lata</h2><table><tr><th></th><th>orzeczeń</th>"
                f"<th>wyroków</th><th>decyzji</th></tr>{year_rows}</table></section>"
            ),
            table("formation", "Składy", labels.FORMATIONS, "sklad"),
            table("importance", "Ważność", labels.IMPORTANCE, "waznosc", order=lambda r: int(r[0])),
            table("language", "Języki tekstów", labels.LANGUAGES, "jezyk", documents=True),
            _most_cited(cur),
            table("violation", "Najczęściej naruszone artykuły (30)", None, "", limit=30),
        ]
    )
    updated = total[5].strftime("%d.%m.%Y %H:%M") if total[5] else ""
    body = f"""<h1>Statystyki</h1>
<p class=hint>Przeliczone {e(updated)} UTC, po ostatnim przetworz. Orzeczenie = jedno ECLI ze wszystkimi wersjami językowymi.</p>
<div class=cards>{cards}</div>
{vectors_card(read_progress(cur, PROGRESS_KEY), COMMAND_EMBED, ORDER_NOTE)}
<div class=tables>{tables}</div>"""
    return Response(_layout("Statystyki", body))


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


BRAND = "Orzecznictwo ETPC"
# Oficjalny PDF wersji językowej z HUDOC (działa też dla tłumaczeń).
HUDOC_PDF = "https://hudoc.echr.coe.int/app/conversion/pdf/"
COMMAND_EMBED = "python -m etpc wektory"
ORDER_NOTE = "Najpierw wyroki (od kluczowych i najnowszych), potem decyzje."


def _weight_param(value):
    """A-D from the address, anything else = no filter."""
    value = value.upper()
    return value if value in TIERS else ""


def _weight_badge(tier, reasons):
    """Badge "A kluczowe" with the reasons as its tooltip."""
    return (
        f"<span class='badge tier {e(tier)}' title='waga: {e(reasons)}'>"
        f"{e(tier)} {e(TIERS.get(tier, ''))}</span>"
    )


def _layout(title, body, lang="pl"):
    return layout(title, body, BRAND, lang=lang)
