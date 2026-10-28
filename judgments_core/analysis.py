"""Document analysis on the pages ("Analiza dokumentu"): an uploaded judgment or brief
-> the closest judgments in the base, grouped by their outcome.

The common part for every project:
1. the text: .docx (standard library: the XML inside the zip), .pdf (pypdf; pages
   without a text layer - a scan - through OCR, judgments_core/ocr.py), a photo of a page
   (OCR), .txt, or pasted;
2. the model for the document's vectors: LM Studio when it answers and has the
   embedding model loaded (fast, on the graphics card), else the query model already
   loaded in this process (sentence-transformers, CPU) - the vectors are the same model;
3. the search: Retriever.like_text (the document cut like the base, each chunk
   searches the vectors, rankings fused);
4. the report: the judgments split by Retriever.outcome_group into "na poparcie" /
   "przeciwne" / "inne" from the side the user chose (complainant or authority). It is
   the outcome of the case, not the reasoning: telling which arguments a judgment
   supports needs a language model (later).

The work runs in a background thread (a minute or more); the page shows its progress
and refreshes itself. The document stays in memory only (not written to disk) and is
dropped when the analysis ends; the last MAX_JOBS reports stay in memory.
"""

from __future__ import annotations

import contextlib
import io
import logging
import re
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from email.parser import BytesParser
from email.policy import HTTP
from urllib.parse import parse_qs
from xml.etree import ElementTree

import requests

from judgments_core import claude_report, ocr
from judgments_core import markers as text_markers
from judgments_core.embeddings import make_embedder
from judgments_core.web import Response, e, file_field, number, shorten

log = logging.getLogger(__name__)

MAX_UPLOAD = 20_000_000  # bajtów
FILE_TYPES = (
    ".docx,.pdf,.txt,.jpg,.jpeg,.png,.tif,.tiff"  # accept= pól plików (OCR: skany, zdjęcia)
)
MAX_JOBS = 20  # tyle ostatnich raportów w pamięci
RESULTS = 20  # orzeczeń w raporcie (domyślnie)
MAX_RESULTS = 50
PASSAGE_CHARS = 700  # tyle tekstu pasującego fragmentu w raporcie
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_JOB_PATH = re.compile(r"^/analiza/([0-9a-f]{12})(\.pdf)?$")
# href="/..." w raporcie -> pełny adres portalu w PDF (plik otwierany poza przeglądarką).
_ROOT_HREF = re.compile(r"""(href=["']?)/(?!/)""")
GROUPS = ("uwzglednione", "nieuwzglednione", "inne")
SIDES = {
    "skarzacy": "skarżącego (skarga, skarga kasacyjna, skarga do ETPC)",
    "organ": "organu albo państwa (odpowiedź na skargę)",
}


class DocumentError(ValueError):
    """The document can't be read; the message is for the user."""


# --- tekst dokumentu -----------------------------------------------------------


def extract_text(filename, data):
    """Plain text of an uploaded .docx, .pdf, photo or .txt; DocumentError otherwise."""
    return read_document(filename, data)[0]


def read_document(filename, data):
    """(plain text, note): the note says when OCR read the text ("OCR: 3 z 5 stron"),
    else ""; DocumentError when the file can't be read."""
    name = (filename or "").lower()
    note = ""
    if name.endswith(".docx"):
        text = docx_text(data)
    elif name.endswith(".pdf"):
        text, note = pdf_text(data, with_note=True)
    elif name.endswith(ocr.IMAGE_TYPES):
        try:
            text, note = ocr.image_text(data, "." + name.rsplit(".", 1)[-1]), "OCR"
        except ocr.OcrError as error:
            raise DocumentError(str(error)) from error
        if len(text.strip()) < 20:
            raise DocumentError("OCR nie znalazł tekstu na zdjęciu (ostrość, oświetlenie, kadr)")
    elif name.endswith((".txt", ".md")):
        text = decode(data)
    elif name.endswith(".doc"):
        raise DocumentError("stary format .doc - zapisz w Wordzie jako .docx albo PDF")
    else:
        raise DocumentError("obsługiwane pliki: .docx, .pdf, zdjęcie (.jpg, .png, .tif), .txt")
    return clean(text), note


def docx_text(data):
    """Paragraphs of a Word .docx (word/document.xml), one per line."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as error:
        raise DocumentError("to nie jest plik Word .docx") from error
    paragraphs = []
    for paragraph in ElementTree.fromstring(xml).iter(f"{_W}p"):
        parts = []
        for node in paragraph.iter():
            if node.tag == f"{_W}t":
                parts.append(node.text or "")
            elif node.tag == f"{_W}tab":
                parts.append(" ")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


PAGE_WITHOUT_TEXT = 30  # znaków: mniej = strona to skan, idzie do OCR


def pdf_text(data, with_note=False):
    """Text of a PDF page by page; pages without a text layer (a scan, a mixed PDF) are
    read by OCR when it is installed. with_note: (text, note) as in read_document."""
    try:
        from pypdf import PdfReader  # obraz www (docker/www.Dockerfile)
    except ImportError as error:
        raise DocumentError("brak biblioteki pypdf: docker compose build www") from error
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            reader.decrypt("")
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as error:
        raise DocumentError(f"nie można odczytać PDF ({type(error).__name__})") from error
    scanned = [n for n, page in enumerate(pages, 1) if len(page.strip()) < PAGE_WITHOUT_TEXT]
    note = ""
    if scanned and ocr.available():
        try:
            read = ocr.pdf_pages_text(data, scanned)
        except ocr.OcrError as error:
            raise DocumentError(str(error)) from error
        for n, text in read.items():
            pages[n - 1] = text
        note = f"OCR: {len(read)} z {len(pages)} stron"
        if len(read) < len(scanned):
            note += f", pominięto {len(scanned) - len(read)} (limit {ocr.MAX_PAGES})"
    elif scanned:
        note = f"{len(scanned)} z {len(pages)} stron bez tekstu pominięto (brak OCR)"
    text = "\n".join(pages)
    if len(text.strip()) < 50:
        raise DocumentError(
            "PDF nie ma warstwy tekstu (skan), a OCR nie jest dostępny albo nic nie odczytał;"
            " skopiuj tekst i wklej go w polu niżej"
        )
    return (text, note) if with_note else text


def decode(data):
    for encoding in ("utf-8-sig", "cp1250"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def clean(text):
    """Lines stripped, spaces collapsed, page numbers and empty runs dropped."""
    lines = []
    for raw in text.replace("\r", "\n").split("\n"):
        line = " ".join(raw.split())
        if re.fullmatch(r"(strona\s*)?\d{1,4}(\s*(/|z)\s*\d{1,4})?", line, re.IGNORECASE):
            continue  # numer strony z PDF
        if line or (lines and lines[-1]):
            lines.append(line)
    return "\n".join(lines).strip()


def parse_form(request, limit=MAX_UPLOAD, lists=()):
    """Fields of a POST form: {name: text} and, for files, {name: (filename, bytes)}.
    Names in lists (several files in one field, ticked checkboxes) give a list of all
    their values instead of the last one."""
    content_type = request.environ.get("CONTENT_TYPE", "")
    body = request.body(limit)
    fields = {name: [] for name in lists}
    if "multipart/form-data" not in content_type:
        pairs = [
            (k, v)
            for k, values in parse_qs(body.decode("utf-8", "replace")).items()
            for v in values
        ]
    else:
        message = BytesParser(policy=HTTP).parsebytes(
            b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
        )
        pairs = []
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            payload = part.get_payload(decode=True) or b""
            filename = part.get_filename()
            pairs.append((name, (filename, payload) if filename is not None else decode(payload)))
    for name, value in pairs:
        if name in lists:
            fields[name].append(value)
        else:
            fields[name] = value
    return fields


def form_document(fields, file_field, text_field, what, library=None, pasted_name=""):
    """(name, text, stored id) of one document of a form: a file (read here, OCR when needed), a
    pasted text, or a document of the case folder (field <file_field>_teczka: its text
    is stored, no reading again; stored id = its number, else 0); DocumentError for the
    user otherwise."""
    stored = str(fields.get(f"{file_field}_teczka") or "").strip()
    filename, data = fields.get(file_field) or ("", b"")
    pasted = (fields.get(text_field) or "").strip()
    stored_id = 0
    if data:
        text, note = read_document(filename, data)
        if note:
            filename = f"{filename} ({note})"
    elif pasted:
        filename, text = pasted_name or f"{what} (tekst wklejony)", clean(pasted)
    elif stored.isdigit() and library is not None:
        stored_id = int(stored)
        filename, text = library.read(stored_id)
    else:
        raise DocumentError(
            f"brak dokumentu: {what} - wybierz plik, wklej tekst albo wybierz z teczki"
        )
    if len(text) < 200:
        raise DocumentError(f"za mało tekstu: {what} (najmniej ~200 znaków)")
    return filename, text, stored_id


# --- model wektorów ------------------------------------------------------------


def lm_studio_has_model(settings, timeout_s=3):
    """True when LM Studio answers and lists the embedding model (/v1/models)."""
    models_url = settings.embed_url.rsplit("/", 1)[0] + "/models"
    try:
        response = requests.get(models_url, timeout=timeout_s)
        response.raise_for_status()
        ids = {m.get("id") for m in response.json().get("data", [])}
    except (requests.RequestException, ValueError):
        return False
    return settings.embed_model in ids


def choose_embedder(settings, fallback):
    """(embedder, description): LM Studio with the model loaded, else fallback (the query
    model of this process); LM Studio failing in the middle falls back too."""
    if lm_studio_has_model(settings):
        return make_embedder(settings, "http", timeout_s=120, retries=1), "LM Studio"
    return fallback, "model w tym procesie (Python, CPU) - LM Studio niedostępne albo bez modelu"


# --- analiza w tle -------------------------------------------------------------


@dataclass
class Job:
    id: str
    filename: str
    side: str
    count: int
    chars: int
    created: float = field(default_factory=time.time)
    status: str = "liczy"  # liczy / gotowe / blad
    step: str = "czeka na start"
    phase: str = ""  # wektory / szukanie (pasek postępu)
    done: int = 0
    total: int = 0
    error: str = ""
    embedder: str = ""
    seconds: float = 0.0
    result: object = None  # judgments_core.rag.Retrieval
    groups: dict = field(default_factory=dict)  # judgment id -> grupa
    markers: object = None  # judgments_core.markers.Markers (warstwa 2)
    claude: bool = False  # użytkownik zgodził się wysłać znaczniki do Claude
    stored_id: int = 0  # dokument z teczki: wektory z bazy (library.py)
    claude_report: str = ""
    claude_error: str = ""
    claude_cost: str = ""
    claude_saved: str = ""  # katalog w badania/analiza/


class DocumentAnalysis:
    """Jobs of one project's pages: start, progress, report (handle() = the pages)."""

    library = None  # library.Library: dokumenty z teczki (ustawia strona projektu)

    def __init__(self, settings, retriever, chunker, connect, semantic_embedder):
        self.settings = settings
        self.retriever = retriever
        self.chunker = chunker
        self.connect = connect
        self.semantic_embedder = semantic_embedder
        self.jobs = {}
        self.lock = threading.Lock()

    def start(self, filename, text, side, count, claude=False, stored_id=0):
        job = Job(uuid.uuid4().hex[:12], filename, side, count, len(text))
        job.claude, job.stored_id = claude, stored_id
        with self.lock:
            self.jobs[job.id] = job
            while len(self.jobs) > MAX_JOBS:
                del self.jobs[next(iter(self.jobs))]
        threading.Thread(
            target=self._run, args=(job, text), name=f"analiza-{job.id}", daemon=True
        ).start()
        return job

    def get(self, job_id):
        with self.lock:
            return self.jobs.get(job_id)

    def _run(self, job, text):
        started = time.monotonic()
        try:
            job.markers = text_markers.analyse(text)  # warstwa 2: lokalnie, chwila
            job.step = "sprawdzam LM Studio"
            embedder, how = choose_embedder(self.settings, self.semantic_embedder)
            if job.stored_id and self.library is not None:  # dokument z teczki: wektory z bazy
                embedder = self.library.embedder_for(embedder, owner=job.stored_id)
                how += ", wektory zapisane w teczce"
            job.embedder = how
            job.step = f"liczę wektory dokumentu ({how}) i szukam w bazie"
            with contextlib.closing(self.connect()) as conn, conn.cursor() as cur:
                job.result = self.retriever.like_text(
                    cur,
                    text,
                    self.chunker,
                    job.count,
                    embedder=embedder,
                    progress=lambda phase, done, total: _advance(job, phase, done, total),
                )
            job.groups = {
                s.judgment_id: self.retriever.outcome_group(s.result) for s in job.result.sources
            }
            if job.claude:
                job.step = "Claude pisze raport ze znaczników (do kilku minut)"
                self._claude(job)
            job.status = "gotowe"
        except Exception as error:
            log.exception("analiza dokumentu %s", job.id)
            job.status, job.error = "blad", f"{type(error).__name__}: {error}"
        finally:
            job.seconds = time.monotonic() - started
            job.step = ""

    def _claude(self, job):
        """Claude's report on the markers, saved with what was sent (badania/analiza/)."""
        prompt = claude_report.markers_prompt(job.markers, job.filename)
        try:
            job.claude_report, usage = claude_report.ask(
                self.settings, claude_report.SYSTEM, prompt
            )
        except claude_report.ClaudeError as error:
            job.claude_error = str(error)
            return
        job.claude_cost = claude_report.cost(self.settings, usage)
        files = {
            "claude.md": job.claude_report + "\n",
            "wyslane.md": claude_report.SYSTEM + "\n\n---\n\n" + prompt + "\n",
            "znaczniki.csv": claude_report.markers_csv(job.markers),
        }
        meta = {
            "analiza": job.id,
            "plik": job.filename,
            "model": self.settings.claude_model,
            "tokeny": usage,
            "koszt": job.claude_cost,
            "slow": job.markers.words,
            "jezyk": job.markers.lang,
        }
        try:
            job.claude_saved = str(claude_report.save("analiza", job.id, files, meta))
        except OSError as error:
            job.claude_saved = f"nie zapisano ({error})"

    # --- strony ---

    def handle(self, request, layout):
        """/analiza (form, POST = start), /analiza/<id> (progress, report) and
        /analiza/<id>.pdf (the report as a PDF file with links)."""
        match = _JOB_PATH.match(request.path)
        if match:
            job = self.get(match.group(1))
            if job is None:
                body = "<p class=error>Nie ma takiej analizy (serwer był restartowany?).</p>"
                return Response(
                    layout("Analiza dokumentu", body + _form(self.settings, self.library)),
                    "404 Not Found",
                )
            if match.group(2) and job.status == "gotowe":
                return _pdf_response(job, self.retriever, layout, _base_url(request))
            return Response(layout("Analiza dokumentu", _job_page(job, self.retriever)))
        if request.method != "POST":
            page = _form(self.settings, self.library, request.get_int("teczka"))
            return Response(layout("Analiza dokumentu", page))
        try:
            fields = parse_form(request)
            filename, text, stored_id = form_document(
                fields, "plik", "tekst", "dokument", self.library, "tekst wklejony"
            )
        except (DocumentError, ValueError) as error:
            return Response(
                layout(
                    "Analiza dokumentu",
                    f"<p class=error>{e(error)}</p>" + _form(self.settings, self.library),
                ),
                "400 Bad Request",
            )
        side = fields.get("strona") if fields.get("strona") in SIDES else "skarzacy"
        count = min(max(_int(fields.get("ile"), RESULTS), 5), MAX_RESULTS)
        claude = bool(fields.get("claude")) and claude_report.available(self.settings)[0]
        job = self.start(filename, text, side, count, claude, stored_id)
        return Response("", "303 See Other", headers=[("Location", f"/analiza/{job.id}")])


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _form(settings, library=None, selected=0):
    ready, why = claude_report.available(settings)
    claude = (
        "<label><input type=checkbox name=claude value=1> wyślij znaczniki językowe do Claude"
        " i pokaż jego raport (wychodzą liczby i do 5 zdań na znacznik, nie cały dokument)</label>"
        if ready
        else f"<label><input type=checkbox disabled> raport Claude niedostępny: {e(why)}</label>"
    )
    sides = "".join(
        f"<label><input type=radio name=strona value={key}{' checked' if key == 'skarzacy' else ''}>"
        f" {e(text)}</label>"
        for key, text in SIDES.items()
    )
    return f"""<h1>Analiza dokumentu</h1>
<p class=hint>Wgraj orzeczenie albo pismo (Word, PDF - także skan, zdjęcie strony, tekst) albo wklej tekst. Dokument jest
cięty na fragmenty, każdy szuka w bazie najbliższych orzeczeń po znaczeniu, a raport dzieli je
według wyniku sprawy. Dokument zostaje na tym komputerze i nie jest zapisywany.</p>
<form class=analysis method=post action="/analiza" enctype="multipart/form-data">
{file_field("plik", FILE_TYPES)}
{library.choices_html("plik_teczka", selected) if library else ""}
<p><label>albo tekst<br><textarea name=tekst rows=8 cols=80></textarea></label></p>
<fieldset><legend>Na poparcie stanowiska</legend>{sides}</fieldset>
<p><label>Ile orzeczeń <input type=number name=ile value={RESULTS} min=5 max={MAX_RESULTS}></label></p>
<fieldset><legend>Warstwa językowa</legend><p class=hint>Znaczniki (pewność, przymus,
kwantyfikatory, strona bierna ...) liczone są zawsze, lokalnie.</p>{claude}</fieldset>
<p><button type=submit>Analizuj</button></p>
</form>"""


def _job_page(job, retriever):
    if job.status == "liczy":
        waited = int(time.time() - job.created)
        return f"""<meta http-equiv=refresh content=2>
<h1>Analiza dokumentu</h1>
<p><b>{e(job.filename)}</b> · {number(job.chars)} znaków</p>
{_progress_bar(job)}
<p class=progress-line>Trwa: {e(job.step)} ({waited} s). Strona odświeża się sama.</p>"""
    if job.status == "blad":
        return (
            f"<h1>Analiza dokumentu</h1><p class=error>Nie udało się: {e(job.error)}</p>"
            '<p><a href="/analiza">Nowa analiza</a></p>'
        )
    return _report(job, retriever)


def _report(job, retriever, pdf=False):
    result = job.result
    favourable = "uwzglednione" if job.side == "skarzacy" else "nieuwzglednione"
    against = "nieuwzglednione" if job.side == "skarzacy" else "uwzglednione"
    sections = (
        ("Na poparcie", favourable, "wynik korzystny dla tej strony"),
        ("Przeciwne", against, "wynik niekorzystny dla tej strony"),
        ("Inne", "inne", "umorzenia, zawieszenia, skreślenia, rozstrzygnięcia mieszane"),
    )
    counts = {g: sum(1 for v in job.groups.values() if v == g) for g in GROUPS}
    summary = " · ".join(f"{title.lower()}: <b>{counts[group]}</b>" for title, group, _ in sections)
    legend = result.notice.split("\n", 1)
    own = (
        f"<details{' open' if pdf else ''}><summary>{e(legend[0])}</summary>"
        f"<pre class=legend>{e(legend[1])}</pre></details>"
        if len(legend) == 2
        else ""
    )
    tools = (
        "<p class=doc-tools translate=no>"
        f'<a class=button href="/analiza/{job.id}.pdf">Pobierz PDF (z linkami)</a>'
        ' <a href="/analiza">Nowa analiza</a></p>'
    )
    html = [
        f"""<h1>Analiza dokumentu</h1>
{"" if pdf else tools}
<p><b>{e(job.filename)}</b> · {number(job.chars)} znaków · strona: {e(SIDES[job.side])}
· wektory: {e(job.embedder)} · {job.seconds:.0f} s</p>
<p>{len(result.sources)} najbliższych orzeczeń - {summary}</p>
<p class=hint>Podział według <b>wyniku sprawy</b>, nie argumentacji: orzeczenie „na poparcie”
mogło wygrać z innego powodu, a „przeciwne” może zawierać przydatny pogląd. W sprawach
kasacyjnych sprawdź, kto wniósł skargę kasacyjną.</p>{own}
<h2 class=layer>Warstwa 1: orzeczenia z bazy</h2>"""
    ]
    for title, group, note in sections:
        sources = [s for s in result.sources if job.groups.get(s.judgment_id) == group]
        html.append(f"<h2>{e(title)} ({len(sources)})</h2><p class=hint>{e(note)}</p>")
        if not sources:
            html.append("<p class=empty>Brak.</p>")
        for s in sources:
            html.append(_source(s, pdf))
    html.append(_language_layer(job, pdf))
    return "\n".join(html)


def _source(s, pdf=False):
    fragments = "".join(
        f"<p class=hint>{e(f.heading)}</p><blockquote>{e(shorten(f.text, PASSAGE_CHARS))}</blockquote>"
        for f in s.fragments
    )
    return f"""<section class=hit><h3>[{s.number}] <a href="/orzeczenie/{s.judgment_id}">{e(s.title)}</a></h3>
<p class=hint>{e(s.details)} · <a href="{e(s.url)}" target=_blank rel="noopener noreferrer">{e(s.url) if pdf else "źródło"}</a></p>
{fragments}</section>"""


def _advance(job, phase, done, total):
    job.phase, job.done, job.total = phase, done, total
    what = "liczę wektory fragmentów dokumentu" if phase == "wektory" else "szukam w bazie"
    job.step = f"{what}: {done} z {total} ({job.embedder})"


def _progress_bar(job):
    """Vectors = the first half of the bar, the search = the second."""
    percent = 0.0
    if job.total:
        percent = 50 * job.done / job.total + (50 if job.phase == "szukanie" else 0)
    return f'<div class=progress><div style="width:{percent:.1f}%"></div></div>'


def _base_url(request):
    """http://host:port/<project> of this portal, for absolute links in the PDF."""
    env = request.environ
    host = (
        env.get("HTTP_HOST")
        or f"{env.get('SERVER_NAME', 'localhost')}:{env.get('SERVER_PORT', '80')}"
    )
    return f"{env.get('wsgi.url_scheme', 'http')}://{host}{env.get('SCRIPT_NAME', '').rstrip('/')}"


def _pdf_response(job, retriever, layout, base_url):
    """The report as a PDF file: links to the judgments' pages absolute (this portal),
    to HUDOC/CBOSA as they are and written out."""
    html = layout("Analiza dokumentu", _report(job, retriever, pdf=True))
    html = _ROOT_HREF.sub(lambda m: m.group(1) + base_url + "/", html)
    try:
        data = html_to_pdf(html)
    except RuntimeError as error:
        return Response(
            layout("Analiza dokumentu", f"<p class=error>PDF niedostępny: {e(error)}</p>"),
            "503 Service Unavailable",
        )
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", job.filename.rsplit(".", 1)[0])[:60] or "dokument"
    return Response(
        data,
        content_type="application/pdf",
        headers=[("Content-Disposition", f'attachment; filename="analiza-{name}.pdf"')],
    )


def html_to_pdf(html):
    """A4 PDF of an HTML page (its print styles) with clickable links: Chromium through
    Playwright (in the www image)."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError("brak Playwrighta (obraz www)") from error
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content(html, wait_until="load")
                return page.pdf(
                    format="A4",
                    margin={"top": "15mm", "bottom": "15mm", "left": "15mm", "right": "15mm"},
                )
            finally:
                browser.close()
    except PlaywrightError as error:
        raise RuntimeError(f"przeglądarka: {error}") from error


def _language_layer(job, pdf=False):
    """Layer 2: the language markers (always) and Claude's report (when asked for)."""
    m = job.markers
    if m is None:
        return ""
    rows = "".join(
        f"<tr><td>{e(k.title)}<br><span class=hint>{e(k.meaning)}</span></td>"
        f"<td class=num>{k.per_1000}</td><td class=num>{k.count}</td>"
        f"<td>{e(_found(k))}</td></tr>"
        for k in m.markers
    )
    examples = "".join(
        f"<details{' open' if pdf else ''}><summary>{e(k.title)} - zdania ({len(k.sentences)})</summary>"
        + "".join(f"<blockquote>{e(shorten(s, 500))}</blockquote>" for s in k.sentences)
        + "</details>"
        for k in m.markers
        if k.sentences
    )
    language = "polski" if m.lang == "pl" else "angielski"
    parts = [
        f"""<h2 class=layer>Warstwa 2: język i ukryte założenia</h2>
<p class=hint>Znaczniki liczone słownikiem ({language}; {number(m.words)} słów,
{number(m.sentences)} zdań): sygnały do przejrzenia, nie diagnoza. Wysokie „pewność” i
„absolutne” przy niskich „wyjątkach” to częsty znak tez podanych bez dowodu; „strona bierna” -
ukrytego sprawcy.</p>
<table><tr><th></th><th>na 1000 słów</th><th>razem</th><th>słowa</th></tr>{rows}</table>
{examples}"""
    ]
    if job.claude_report:
        parts.append(
            "<h2 class=layer>Raport Claude</h2><p class=hint>Na podstawie znaczników i zdań wyżej;"
            " to interpretacja modelu - sprawdź ją z pełnym tekstem."
            f" {e(job.claude_cost)} · zapisano: {e(job.claude_saved or '-')}</p>"
            f"<section class=claude>{claude_report.to_html(job.claude_report)}</section>"
        )
    elif job.claude_error:
        parts.append(f"<p class=error>Raport Claude niedostępny: {e(job.claude_error)}</p>")
    return "\n".join(parts)


def _found(marker, limit=8):
    """'oczywiście ×3, niewątpliwie ×1' - the most frequent words of a marker."""
    pairs = sorted(marker.found.items(), key=lambda x: -x[1])[:limit]
    return ", ".join(f"{w} ×{n}" for w, n in pairs)
