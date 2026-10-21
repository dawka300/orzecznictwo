"""NSA search pages: python -m nsa www [--port 8082] (child of WebApp).

/                   wyszukiwanie (tekst + filtry), bez zapytania: najnowsze orzeczenia
/orzeczenie/<id>    orzeczenie: metadane, powiązane, przepisy, pełny tekst, podobne
/statystyki         liczby orzeczeń (judgment_stats) i postęp wektorów
"""

from __future__ import annotations

import logging
import re

from judgments_core.embed import read_progress
from judgments_core.embeddings import make_embedder
from judgments_core.qdrant import QdrantStore, collection_name
from judgments_core.semantic import SemanticUnavailable
from judgments_core.web import (
    Response,
    WebApp,
    bar,
    date_pl,
    e,
    highlight,
    layout,
    number,
    text_html,
    url,
    vectors_card,
    vectors_line,
)
from nsa import search
from nsa.embed import PROGRESS_KEY
from nsa.semantic import NsaSemantic
from nsa.stats import NSA_COURT, read_stats, symbol_names

log = logging.getLogger(__name__)

BRAND = "Orzecznictwo sądów administracyjnych"
# Stan CBOSA w zbiorze JuDDGES/pl-nsa (nsa/juddges.py): pobrane do tego dnia.
DATA_AS_OF = "5.03.2025"
COMMAND_EMBED = "python -m nsa wektory"
ORDER_NOTE = "Najpierw uchwały, orzeczenia z tezą lub publikacją, wyroki NSA, wyroki WSA, potem postanowienia."
SEMANTIC_TIMEOUT_S = 20
SIMILAR = 10
KINDS = {"wyrok": "wyrok", "postanowienie": "postanowienie", "uchwala": "uchwała", "inne": "inne"}
_JUDGMENT_PATH = re.compile(r"^/orzeczenie/(\d+)$")
_FRAGMENT = re.compile(r"^(\d+)-(\d+)$")


class NsaWebApp(WebApp):
    description = (
        "Orzeczenia Naczelnego Sądu Administracyjnego i wojewódzkich sądów administracyjnych"
        " z bazy CBOSA: wyroki, postanowienia, uchwały; teza, sentencja, uzasadnienie."
    )

    def facts(self, cur):
        stats = read_stats(cur)
        if not stats:
            return []
        total = stats["total"][0]
        return [
            (number(total[1]), "orzeczeń"),
            (number(total[2]), "wyroków"),
            (number(total[4]), "NSA"),
        ]

    def __init__(self, settings, semantic=None):
        super().__init__(settings)
        if semantic is None:
            semantic = NsaSemantic(
                make_embedder(settings, timeout_s=SEMANTIC_TIMEOUT_S, retries=0),
                QdrantStore(
                    settings.qdrant_url,
                    settings.qdrant_collection or collection_name("nsa", settings.embed_model),
                    settings.embed_model,
                    timeout_s=SEMANTIC_TIMEOUT_S,
                ),
            )
            semantic.prepare_in_background()
        self.semantic = semantic

    def route(self, request):
        match = _JUDGMENT_PATH.match(request.path)
        if request.path not in ("/", "/statystyki") and not match:
            return Response(
                _layout("Nie ma takiej strony", "<p>Nie ma takiej strony.</p>"), "404 Not Found"
            )
        with self.connect() as conn, conn.cursor() as cur:
            if match:
                return _judgment(cur, request, int(match.group(1)), self.semantic)
            if request.path == "/statystyki":
                return _stats(cur)
            return _search(cur, request, self.semantic)

    def error_page(self):
        return Response(
            _layout("Błąd", "<p class=error>Błąd serwera - szczegóły w logu.</p>"),
            "500 Internal Server Error",
        )


def _layout(title, body):
    return layout(title, body, BRAND)


# --- wyszukiwanie ---------------------------------------------------------

PARAMS = {  # pole SearchQuery -> parametr adresu
    "text": "q",
    "mode": "tryb",
    "kind": "rodzaj",
    "instance": "instancja",
    "court": "sad",
    "year_from": "od",
    "year_to": "do",
    "symbol": "symbol",
    "keyword": "haslo",
    "judge": "sedzia",
    "authority": "organ",
    "result": "wynik",
    "final": "prawomocne",
    "sort": "sort",
}


def _query(request):
    q = search.SearchQuery(
        year_from=request.get_int("od"),
        year_to=request.get_int("do"),
        inflect=not request.get("bez_odmiany"),
        with_thesis=bool(request.get("z_teza")),
        page=request.get_int("strona", 1),
    )
    for name, param in PARAMS.items():
        if name not in ("year_from", "year_to"):
            setattr(q, name, request.get(param))
    if q.mode not in search.MODES:
        q.mode = ""
    return q


def _params(q, **changes):
    params = {param: getattr(q, name) for name, param in PARAMS.items()}
    params.update(
        bez_odmiany="" if q.inflect else "1",
        z_teza="1" if q.with_thesis else "",
        strona=q.page if q.page > 1 else 0,
    )
    params.update(changes)
    return params


def _search(cur, request, semantic):
    q = _query(request)
    stats = read_stats(cur)
    error, result = "", None
    try:
        result = search.search(cur, q, semantic)
    except search.QueryError as err:
        error = str(err)
    body = f"<p class=error>{e(error)}</p>" if error else _results(q, result)
    body = vectors_line(read_progress(cur, PROGRESS_KEY)) + body
    title = f"{q.text} - wyszukiwanie" if q.text else "Wyszukiwanie"
    return Response(
        _layout(title, f"<div class=search>{_form(cur, q, stats)}<section>{body}</section></div>")
    )


def _options(pairs, selected):
    return "".join(
        f'<option value="{e(v)}"{" selected" if str(v) == str(selected) else ""}>{e(t)}</option>'
        for v, t in pairs
    )


def _select(name, options, caption):
    return f'<label>{e(caption)}<select name="{name}">{options}</select></label>'


def _input(name, value, caption, placeholder=""):
    return f'<label>{e(caption)}<input name="{name}" value="{e(value or "")}" placeholder="{e(placeholder)}"></label>'


def _form(cur, q, stats):
    courts = [("", "wszystkie")] + [
        (c, f"{c.replace('Wojewódzki Sąd Administracyjny', 'WSA')} - {number(n)}")
        for c, n, *_ in sorted(stats.get("court", []), key=lambda r: (r[0] != NSA_COURT, r[0]))
    ]
    kinds = [("", "wszystkie")] + [
        (k, f"{KINDS.get(k, k)} - {number(n)}") for k, n, *_ in stats.get("kind", [])
    ]
    symbols = stats.get("symbol", [])[:300]
    names = symbol_names(cur, [s[0] for s in symbols])
    datalist = "".join(
        f'<option value="{e(c)}">{e(names.get(c, ""))} ({number(n)})</option>'
        for c, n, *_ in symbols
    )
    return f"""<form class=filters method=get action="/">
<div class=query>
  <input type=search name=q value="{e(q.text)}" placeholder="np. przymus adwokacko-radcowski, &quot;podatek od nieruchomości&quot;" autofocus>
  <button type=submit>Szukaj</button>
</div>
<label class=check><input type=checkbox name=bez_odmiany value=1{"" if q.inflect else " checked"}> bez odmiany słów (dokładnie jak wpisane)</label>
<p class=hint>Hybrydowo: orzeczenia ze słowami i o podobnym znaczeniu (można pytać zwyczajnie). Słowa: wszystkie muszą wystąpić, „fraza”, -słowo wyklucza, słowo* = początek; dłuższe słowa szukane z odmianą (po rdzeniu).</p>
<div class=grid>
{_select("tryb", _options([("", "hybrydowo: słowa + znaczenie"), ("znaczeniowe", "tylko znaczenie"), ("pelnotekstowe", "tylko słowa")], q.mode), "Tryb wyszukiwania")}
{_select("instancja", _options([("", "NSA i WSA"), ("nsa", "tylko NSA"), ("wsa", "tylko WSA")], q.instance), "Instancja")}
{_select("sad", _options(courts, q.court), "Sąd")}
{_select("rodzaj", _options(kinds, q.kind), "Rodzaj")}
{_input("od", q.year_from or "", "Rok od", "2004")}
{_input("do", q.year_to or "", "Rok do", "2025")}
<label>Symbol sprawy<input name=symbol value="{e(q.symbol)}" list=symbole placeholder="np. 6110"><datalist id=symbole>{datalist}</datalist></label>
{_input("haslo", q.keyword, "Hasło tematyczne", "np. Podatek od towarów i usług")}
{_input("sedzia", q.judge, "Sędzia (imię i nazwisko)")}
{_input("organ", q.authority, "Skarżony organ (początek)", "np. Dyrektor Izby")}
{_input("wynik", q.result, "Wynik (dokładnie)", "np. Oddalono skargę kasacyjną")}
{_select("prawomocne", _options([("", "dowolnie"), ("1", "prawomocne"), ("0", "nieprawomocne")], q.final), "Prawomocność")}
{_select("sort", _options([("", "trafność / najnowsze"), ("data", "najnowsze"), ("najstarsze", "najstarsze")], q.sort), "Kolejność")}
<label class=check><input type=checkbox name=z_teza value=1{" checked" if q.with_thesis else ""}> tylko z tezą</label>
</div>
<div class=actions><button type=submit>Szukaj</button> <a href="/">wyczyść</a></div>
</form>"""


def _results(q, result):
    notice = f"<p class=notice>{e(result.notice)}</p>" if result.notice else ""
    if result.total == 0:
        return (
            notice
            + "<p class=empty>Nic nie znaleziono. Spróbuj innych słów albo mniej filtrów.</p>"
        )
    first = (max(q.page, 1) - 1) * search.PAGE_SIZE + 1
    last = first + len(result.hits) - 1
    how = {
        "hybrydowe": f"najlepszych wg słów i znaczenia (do {search.CANDIDATES} z każdego)",
        "znaczeniowe": f"najbliższych znaczeniowo (do {search.CANDIDATES})",
    }.get(result.mode, "")
    head = (
        f"<p class=count>Orzeczenia {number(first)}-{number(last)} z <b>{number(result.total)}</b>"
        f"{' ' + how if how else ''} <span>({result.seconds:.2f} s)</span></p>"
    )
    pattern = search.highlight_pattern(result.terms.highlight) if result.terms else None
    items = "".join(_hit(q, h, pattern) for h in result.hits)
    return notice + head + f"<ol class=hits start={first}>{items}</ol>" + _pages(q, result.total)


def _hit(q, h, pattern):
    link = url(
        f"/orzeczenie/{h.id}",
        q=q.text,
        bez_odmiany="" if q.inflect else "1",
        fragment=f"{h.passage_start}-{h.passage_length}" if h.passage_start >= 0 else "",
    )
    if h.passage_start >= 0:
        link += "#fragment"
    badges = f'<span class="badge {e("judgment" if h.kind == "wyrok" else "decision")}">{e(KINDS.get(h.kind, h.kind))}</span>'
    if h.court == NSA_COURT:
        badges += "<span class='badge key'>NSA</span>"
    if h.has_thesis:
        badges += "<span class='badge cited'>teza</span>"
    final = {1: "prawomocne", 0: "nieprawomocne"}.get(h.is_final, "")
    meta = " · ".join(
        x
        for x in (date_pl(h.judgment_date), e(h.court), e(final), e(h.challenged_authority or ""))
        if x
    )
    symbols = ", ".join(
        f'<a href="{e(url("/", **_params(q, symbol=s, strona=0)))}">{e(s)}</a>' for s in h.symbols
    )
    result_line = f"<p class=viol>{e(h.result.replace(chr(10), '; '))}</p>" if h.result else ""
    text = ""
    if h.snippet:
        where = f"<span class=passage-path>{e(h.passage)}</span>" if h.passage else ""
        text = f"<p class=snip><span class=lang>{e(h.snippet_part)}</span> {where}{highlight(h.snippet, pattern)}</p>"
    return f"""<li><a class=title href="{e(link)}">{e(h.docket_number or "(bez sygnatury)")} - {e(h.judgment_type)}</a>
<p class=meta>{badges} {meta}</p>{result_line}{text}<p class=langs>{symbols} {e(", ".join(h.keywords))}</p></li>"""


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
        out.append(
            f"<b>{p}</b>"
            if p == page
            else f'<a href="{e(url("/", **_params(q, strona=p if p > 1 else 0)))}">{p}</a>'
        )
        previous = p
    if page < count:
        out.append(f'<a href="{e(url("/", **_params(q, strona=page + 1)))}">następna »</a>')
    return f"<nav class=pages>{''.join(out)}</nav>"


# --- orzeczenie -----------------------------------------------------------


def _judgment(cur, request, judgment_id, semantic):
    page = search.judgment_page(cur, judgment_id)
    if page is None:
        return Response(
            _layout("Nie ma takiego orzeczenia", "<p>Nie ma takiego orzeczenia.</p>"),
            "404 Not Found",
        )
    j, query = page.judgment, request.get("q")
    pattern = None
    if query:
        try:
            pattern = search.highlight_pattern(
                search.parse_query(query, not request.get("bez_odmiany")).highlight
            )
        except search.QueryError:
            pattern = None
    fragment = _FRAGMENT.match(request.get("fragment"))
    passage = (
        (int(fragment.group(1)), int(fragment.group(1)) + int(fragment.group(2)))
        if fragment
        else None
    )
    text, marks = text_html(page.body, pattern, passage)

    def row(caption, value):
        return f"<dt>{e(caption)}</dt><dd>{value}</dd>" if value else ""

    judges = ", ".join(
        e(name) + (" (przewodniczący)" if p else "") + (" (sprawozdawca)" if r else "")
        for name, p, r in page.judges
    )
    symbols = "<br>".join(
        f'<a href="{e(url("/", symbol=c))}">{e(c)}</a> {e(d)}' for c, d in page.symbols
    )
    keywords = ", ".join(f'<a href="{e(url("/", haslo=k))}">{e(k)}</a>' for k in page.keywords)
    bases = "".join(
        f"<li>{e(b['journal'] or '')} {e(b['article'] or '')}"
        + (f' <a href="{e(b["link"])}" rel=noreferrer>ISAP</a>' if b["link"] else "")
        + (f"<br><span>{e(b['law'])}</span>" if b["law"] else "")
        + "</li>"
        for b in page.legal_bases
    )
    related = "".join(
        "<li>"
        + (
            f'<a href="/orzeczenie/{r["id"]}">{e(r["docket_number"])}</a>'
            if r["id"]
            else e(r["docket_number"])
        )
        + f" - {e(r['judgment_type'] or '')} <span>{date_pl(r['judgment_date'])}</span></li>"
        for r in page.related
    )
    publications = "<br>".join(e(t) for _, t in page.publications)
    cbosa = f"https://orzeczenia.nsa.gov.pl/doc/{j['cbosa_id']}"
    meta = "".join(
        [
            row("Data", date_pl(j["judgment_date"])),
            row("Data wpływu", date_pl(j["submission_date"])),
            row("Sąd", e(j["court"])),
            row("Prawomocność", {1: "prawomocne", 0: "nieprawomocne"}.get(j["is_final"], "")),
            row("Sędziowie", judges),
            row("Symbol", symbols),
            row("Hasła", keywords),
            row("Skarżony organ", e(j["challenged_authority"])),
            row("Wynik", "<br>".join(e(r) for r in page.results)),
            row("Publikacje i glosy", publications),
            row(
                "CBOSA",
                f'<a href="{e(cbosa)}" target=_blank rel="noopener noreferrer">{e(j["cbosa_id"])}</a>',
            ),
        ]
    )
    side = ""
    if related:
        side += f"<h2>Powiązane ({len(page.related)})</h2><ul class=cites>{related}</ul>"
    if bases:
        side += f"<h2>Powołane przepisy ({len(page.legal_bases)})</h2><ul class=cites>{bases}</ul>"
    side = _similar(cur, semantic, judgment_id) + side
    found = ""
    if pattern is not None:
        found = (
            f'<p class=found>Trafień w tekście: {marks}. <a href="#t1">Przejdź do pierwszego</a></p>'
            if marks
            else "<p class=found>Szukane słowa nie występują w tekście.</p>"
        )
    missing = ""
    if j["kind"] in ("wyrok", "postanowienie", "uchwala") and not (j["reasons"] or "").strip():
        # Zbiór JuDDGES to CBOSA z jednego dnia: uzasadnienie sporządzone później
        # (zwykle tygodnie po wyroku) albo nigdy nieopublikowane go nie ma.
        missing = (
            f"<p class=notice>Uzasadnienia nie ma w zbiorze (CBOSA w stanie z {DATA_AS_OF})"
            " - sąd mógł je sporządzić albo opublikować później."
            f' <a href="{e(cbosa)}" target=_blank rel="noopener noreferrer">Sprawdź w CBOSA</a>.</p>'
        )
    title = f"{j['docket_number'] or '(bez sygnatury)'} - {j['judgment_type']}"
    body = f"""<p class=back><a href="/" onclick="if(history.length>1){{history.back();return false}}">« wróć</a></p>
<h1>{e(title)}</h1>
<div class=ruling>
<article>{found}{missing}<div class=text>{text}</div></article>
<aside><dl>{meta}</dl>{side}</aside>
</div>"""
    return Response(_layout(title, body))


def _similar(cur, semantic, judgment_id):
    if semantic is None:
        return ""
    try:
        passages = semantic.similar(judgment_id, SIMILAR)
    except SemanticUnavailable:
        return ""
    if not passages:
        return ""
    ids = [p.judgment_id for p in passages]
    cur.execute(
        f"SELECT id, docket_number, judgment_type, judgment_date FROM judgments WHERE id IN ({', '.join(['%s'] * len(ids))})",
        ids,
    )
    found = {row[0]: row for row in cur.fetchall()}
    items = "".join(
        f'<li><a href="/orzeczenie/{i}">{e(found[i][1])}</a> {e(found[i][2])} <span>{date_pl(found[i][3])}</span></li>'
        for i in ids
        if i in found
    )
    return f"<h2>Podobne orzeczenia</h2><ul class=cites>{items}</ul>"


# --- statystyki -----------------------------------------------------------


def _stats(cur):
    stats = read_stats(cur)
    progress = vectors_card(read_progress(cur, PROGRESS_KEY), COMMAND_EMBED, ORDER_NOTE)
    if not stats:
        body = "<h1>Statystyki</h1><p class=empty>Jeszcze nie policzone - uruchom: python -m nsa przetworz</p>"
        return Response(_layout("Statystyki", body + progress))
    total = stats["total"][0]
    cards = "".join(
        f"<div class=card><b>{number(v)}</b><span>{t}</span></div>"
        for v, t in (
            (total[1], "orzeczeń"),
            (total[2], "wyroków"),
            (total[3], "postanowień"),
            (total[4], "NSA"),
        )
    )

    def table(caption, rows, link, label=lambda v: v):
        top = max((r[1] for r in rows), default=1) or 1
        body = "".join(
            f"<tr><td><a href='{e(url('/', **link(value)))}'>{e(label(value))}</a></td>"
            f"<td class=num{bar(n, top)}>{number(n)}</td><td class=num>{number(w)}</td>"
            f"<td class=num>{number(p)}</td></tr>"
            for value, n, w, p, *_ in rows
        )
        head = "<tr><th></th><th>orzeczeń</th><th>wyroków</th><th>postanowień</th></tr>"
        return (
            f"<section class=card-table><h2>{e(caption)}</h2><table>{head}{body}</table></section>"
        )

    symbols = stats.get("symbol", [])[:50]
    names = symbol_names(cur, [s[0] for s in symbols])
    years = sorted(stats.get("year", []), key=lambda r: int(r[0]), reverse=True)
    tables = "".join(
        [
            table("Sądy", stats.get("court", []), lambda v: {"sad": v}),
            table(
                "Rodzaje", stats.get("kind", []), lambda v: {"rodzaj": v}, lambda v: KINDS.get(v, v)
            ),
            table(
                "Najczęstsze symbole spraw (50)",
                symbols,
                lambda v: {"symbol": v},
                lambda v: f"{v} {names.get(v, '')}",
            ),
            table("Lata", years, lambda v: {"od": v, "do": v}),
        ]
    )
    updated = total[5].strftime("%d.%m.%Y %H:%M") if total[5] else ""
    body = f"""<h1>Statystyki</h1>
<p class=hint>Przeliczone {e(updated)} UTC, po ostatnim przetworz. Źródło: zbiór JuDDGES/pl-nsa (CBOSA do 03.2025).</p>
<div class=cards>{cards}</div>{progress}<div class=tables>{tables}</div>"""
    return Response(_layout("Statystyki", body))
