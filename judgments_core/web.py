"""Minimal WSGI helpers, the parent web application and a threaded local server.

Standard library only. WebApp is the parent: a WSGI callable with the
common part (favicon, a database connection per request, a 500 page and a
log entry for any error); a project's child (EtpcWebApp ...) routes its own
pages. The same code runs locally (`python -m <projekt> www`) and on the
hosting under Passenger.
"""

from __future__ import annotations

import contextlib
import logging
import re
from abc import ABC, abstractmethod
from html import escape
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlencode
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import pymysql

from judgments_core import db

log = logging.getLogger(__name__)


class Request:
    def __init__(self, environ):
        self.environ = environ
        self.path = environ.get("PATH_INFO", "/") or "/"
        self.params = {k: v[-1] for k, v in parse_qs(environ.get("QUERY_STRING", "")).items()}

    def get(self, name, default=""):
        return self.params.get(name, default).strip()

    def get_int(self, name, default=0):
        value = self.get(name)
        return int(value) if value.isdigit() else default


class Response:
    def __init__(self, body, status="200 OK", content_type="text/html; charset=utf-8"):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.content_type = content_type

    def __call__(self, start_response):
        start_response(
            self.status,
            [("Content-Type", self.content_type), ("Content-Length", str(len(self.body)))],
        )
        return [self.body]


def url(path, **params):
    """path?params without empty values."""
    query = urlencode({k: v for k, v in params.items() if v not in ("", None, 0)})
    return f"{path}?{query}" if query else path


def e(value):
    """HTML-escaped text; None -> ''."""
    return "" if value is None else escape(str(value))


class WebApp(ABC):
    """WSGI application of a project: app(environ, start_response)."""

    # opis projektu na stronie głównej portalu
    description = ""

    def __init__(self, settings):
        self.settings = settings

    def __call__(self, environ, start_response):
        request = Request(environ)
        if request.path == "/favicon.ico":
            return Response(b"", "204 No Content", "image/x-icon")(start_response)
        try:
            response = self.route(request)
        except Exception:
            log.exception("www: błąd strony %s", request.path)
            response = self.error_page()
        # Pod prefiksem (Portal: /etpc, /nsa; hosting w podkatalogu) linki stron
        # pisane od korzenia ("/orzeczenie/1") dostają ten prefiks.
        root = environ.get("SCRIPT_NAME", "").rstrip("/")
        if root and response.content_type.startswith("text/html"):
            response.body = _ROOT_LINK.sub(
                lambda m: m.group(1) + root.encode("utf-8") + b"/", response.body
            )
        return response(start_response)

    @abstractmethod
    def route(self, request):
        """The Response for a request."""

    def connect(self):
        """A database connection for one request (pages only read)."""
        return db.connect(self.settings.db_dsn, autocommit=True)

    def error_page(self):
        return Response("<p>Błąd serwera - szczegóły w logu.</p>", "500 Internal Server Error")

    def summary(self):
        """facts() for the portal's home page; [] when the database can't be read."""
        if type(self).facts is WebApp.facts:  # projekt bez liczb: bez łączenia z bazą
            return []
        try:
            with contextlib.closing(self.connect()) as conn, conn.cursor() as cur:
                return self.facts(cur)
        except pymysql.Error as error:
            log.warning("www: liczby na stronę główną niedostępne: %s", error)
            return []

    def facts(self, cur):
        """[(value, label)]: a few numbers of the project, e.g. ("82 976", "orzeczeń")."""
        return []


# href="/...", action='/...', src=/... (ale nie //host): link od korzenia serwera.
_ROOT_LINK = re.compile(rb"""((?:href|action|src)=["']?)/(?!/)""")


class Portal:
    """Several projects' WebApps on one server: /<name>/... = that project's pages
    (it sees itself at "/" with SCRIPT_NAME=/<name>), "/" = the home page with a
    card per project. Every HTML page gets a bar of tabs: home and the projects.

    apps: {name: (title, WSGI app)} in display order. default: the project that old
    addresses without a prefix (/orzeczenie/1, /?q=...) are redirected to.
    """

    def __init__(self, apps, title="Orzecznictwo", default=None):
        self.apps = dict(apps)
        self.title = title
        self.default = default

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "/") or "/"
        query = environ.get("QUERY_STRING", "")
        root = environ.get("SCRIPT_NAME", "").rstrip("/")
        if path == "/favicon.ico":
            return Response(b"", "204 No Content", "image/x-icon")(start_response)
        name, slash, rest = path[1:].partition("/")
        if name in self.apps:
            if not slash:  # /etpc -> /etpc/
                return _redirect(start_response, f"{root}/{name}/", query)
            inner = dict(environ, SCRIPT_NAME=f"{root}/{name}", PATH_INFO="/" + rest)
            return self._with_tabs(self.apps[name][1], inner, start_response, root, name)
        if path == "/" and not query:
            # linki od korzenia; przedrostek dopisuje niżej _ROOT_LINK
            page = self.home().replace("<body>", "<body>" + self.tabs("", ""), 1)
            response = Response(page)
            if root:
                response.body = _ROOT_LINK.sub(
                    lambda m: m.group(1) + root.encode("utf-8") + b"/", response.body
                )
            return response(start_response)
        if self.default:
            return _redirect(start_response, f"{root}/{self.default}{path}", query)
        return Response("<p>Nie ma takiej strony.</p>", "404 Not Found")(start_response)

    def tabs(self, root, current):
        """The bar of tabs: home page, then the projects; current = active project name."""
        links = [
            f'<a class="home{" on" if not current else ""}" href="{root}/">{e(self.title)}</a>'
        ]
        for name in self.apps:
            on = " class=on" if name == current else ""
            links.append(f'<a{on} href="{root}/{name}/">{e(name.upper())}</a>')
        return f"<nav class=portal-tabs>{''.join(links)}</nav>"

    def _with_tabs(self, app, environ, start_response, root, name):
        """Run a project's app; its HTML page gets the bar of tabs after <body>."""
        answer = {}

        def capture(status, headers, exc_info=None):
            answer.update(status=status, headers=headers)

        body = b"".join(app(environ, capture))
        headers = answer["headers"]
        kind = next((v for k, v in headers if k.lower() == "content-type"), "")
        if kind.startswith("text/html") and b"<body>" in body:
            bar = self.tabs(root, name).encode("utf-8")
            body = body.replace(b"<body>", b"<body>" + bar, 1)
            headers = [(k, v) for k, v in headers if k.lower() != "content-length"]
            headers.append(("Content-Length", str(len(body))))
        start_response(answer["status"], headers)
        return [body]

    def home(self):
        cards = "".join(self._card(name, title, app) for name, (title, app) in self.apps.items())
        body = f"""<div class=intro><h1>{e(self.title)}</h1>
<p>Wyszukiwarki orzeczeń: po słowach i po znaczeniu, z filtrami i statystykami.
Wybierz bazę albo zacznij od pytania.</p></div>
<div class=projects>{cards}</div>"""
        return layout(self.title, body, self.title, nav=(), header=False)

    def _card(self, name, title, app):
        description = getattr(app, "description", "")
        summary = getattr(app, "summary", None)
        facts = summary() if summary else []
        numbers = "".join(f"<div><b>{e(v)}</b><span>{e(t)}</span></div>" for v, t in facts)
        return f"""<section class=project>
<h2><a href="/{name}/">{e(title)}</a></h2>
{f"<p>{e(description)}</p>" if description else ""}
{f"<div class=facts>{numbers}</div>" if numbers else ""}
<form class=query action="/{name}/"><input type=search name=q placeholder="Szukaj w {e(name.upper())}"><button>Szukaj</button></form>
<p class=links><a href="/{name}/">Wyszukiwanie z filtrami</a> · <a href="/{name}/statystyki">Statystyki</a></p>
</section>"""


def _redirect(start_response, location, query=""):
    location = f"{location}?{query}" if query else location
    start_response("301 Moved Permanently", [("Location", location), ("Content-Length", "0")])
    return [b""]


class _ThreadingServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        log.info("www: %s", format % args)


def serve(app, host, port):
    """Serve the WSGI app until Ctrl+C."""
    with make_server(
        host, port, app, server_class=_ThreadingServer, handler_class=_QuietHandler
    ) as server:
        log.info("Wyszukiwarka: http://%s:%d/ (Ctrl+C kończy)", host, port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            log.info("Zatrzymano.")


# --- wspólne elementy stron -------------------------------------------------


def number(value):
    return "" if value is None else f"{value:,}".replace(",", " ")


def date_pl(value):
    return value.strftime("%d.%m.%Y") if value else ""


def shorten(text, length):
    text = " ".join(text.split())
    return text if len(text) <= length else text[:length].rsplit(" ", 1)[0] + " …"


def details(text):
    if not text:
        return ""
    if len(text) < 200:
        return e(text)
    return f"<details><summary>{e(shorten(text, 80))}</summary>{e(text)}</details>"


def bar(value, top):
    """Background bar in a number cell, proportional to value."""
    share = 100 * value / (top or 1)
    return f" style='background:linear-gradient(to left,var(--bar) {share:.1f}%,transparent {share:.1f}%)'"


def highlight(text, pattern, counter=None):
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


def text_html(text, pattern, passage=None):
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
        out.append(f"<p{css}{anchor}>{highlight(line, pattern, counter)}</p>")
    return "\n".join(out), counter[0]


def duration(seconds):
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min"


VECTOR_STATUS = {
    "liczy": "trwa liczenie",
    "przerwano": "wstrzymane - dokończy następne: {command}",
    "gotowe": "wszystko policzone",
}


def vectors_line(progress):
    """One line about the embedding while it is not finished."""
    if not progress or progress["done"] >= progress["total"]:
        return ""
    percent = 100 * progress["done"] / progress["total"] if progress["total"] else 0
    return (
        f"<p class=progress-line>Wyszukiwanie znaczeniowe: policzono {number(progress['done'])}"
        f" z {number(progress['total'])} orzeczeń ({percent:.1f}%)"
        f"{' - trwa' if progress['status'] == 'liczy' else ' - wstrzymane'}."
        ' <a href="/statystyki#wektory">szczegóły</a></p>'
    )


def vectors_card(progress, command, order_note=""):
    """Embedding progress (command = the CLI command, order_note = what comes first), from sync_state."""
    if not progress:
        return ""
    total, done = progress["total"], progress["done"]
    percent = 100 * done / total if total else 100
    status = VECTOR_STATUS.get(progress["status"], progress["status"]).format(command=command)
    if progress["status"] == "liczy" and progress.get("eta_s"):
        status += f", zostało ok. {duration(progress['eta_s'])}"
    pace = progress.get("chars_per_s") or 0
    facts = [
        f"<b>{number(done)}</b> z <b>{number(total)}</b> orzeczeń ({percent:.1f}%)",
        f"{number(progress['chunks'])} fragmentów w bazie wektorów",
        f"tempo ~{number(round(pace / 4))} tokenów/s" if pace else "",
        f"model {e(progress['model'])}",
        f"ostatni zapis {e(progress['updated'])} UTC",
    ]
    return f"""<section id=wektory class=vectors><h2>Wyszukiwanie znaczeniowe (wektory)</h2>
<div class=progress><div style="width:{percent:.2f}%"></div></div>
<p><b>{e(status)}</b></p><p class=hint>{" · ".join(f for f in facts if f)}</p>
<p class=hint>{e(order_note)} Odśwież stronę, żeby zobaczyć postęp.</p></section>"""


def layout(title, body, brand, nav=(("/", "Szukaj"), ("/statystyki", "Statystyki")), header=True):
    """A whole HTML page in the common look (light and dark theme)."""
    links = "".join(f'<a href="{e(href)}">{e(text)}</a>' for href, text in nav)
    top = f'<header><a class=brand href="/">{e(brand)}</a>\n<nav>{links}</nav></header>\n'
    return f"""<!doctype html>
<html lang=pl><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>{e(title)} - {e(brand)}</title>
<style>{CSS}</style></head>
<body>{top if header else ""}<main>{body}</main></body></html>"""


# Wspólny wygląd stron projektów (zmienne kolorów: jasny i ciemny motyw).
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
.portal-tabs{display:flex;align-items:stretch;gap:4px;padding:0 24px;background:var(--accent);overflow-x:auto}
.portal-tabs a{color:#fff;opacity:.8;padding:8px 14px;font-size:14px;font-weight:600;white-space:nowrap;border-bottom:3px solid transparent}
.portal-tabs a:hover{opacity:1;text-decoration:none}.portal-tabs a.on{opacity:1;border-bottom-color:#fff}
.portal-tabs a.home{margin-right:12px}
@media (prefers-color-scheme:dark){.portal-tabs{background:#233050}}
.intro{max-width:760px;margin:12px 0 24px}.intro h1{font-size:28px;margin:8px 0}.intro p{color:var(--muted);font-size:16px;margin:0}
.projects{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:20px}
.project{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px 20px}
.project h2{font-size:19px;margin:0 0 6px}.project p{margin:6px 0;color:var(--muted)}
.facts{display:flex;flex-wrap:wrap;gap:8px 24px;margin:14px 0}
.facts div{display:flex;flex-direction:column}.facts b{font-size:20px}.facts span{color:var(--muted);font-size:13px}
.project .query{margin:14px 0 8px}.project .links{font-size:14px}
@media (max-width:600px){.portal-tabs,header{padding-left:16px;padding-right:16px}main{padding-left:16px;padding-right:16px}
.projects{grid-template-columns:1fr}}
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
