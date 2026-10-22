"""Document analysis on the pages ("Analiza dokumentu"): an uploaded judgment or brief
-> the closest judgments in the base, grouped by their outcome.

The common part for every project:
1. the text: .docx (standard library: the XML inside the zip), .pdf (pypdf; a scan
   without a text layer needs OCR - refused with a message), .txt, or pasted;
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

from judgments_core.embeddings import make_embedder
from judgments_core.web import Response, e, number, print_button, shorten

log = logging.getLogger(__name__)

MAX_UPLOAD = 20_000_000  # bajtów
MAX_JOBS = 20  # tyle ostatnich raportów w pamięci
RESULTS = 20  # orzeczeń w raporcie (domyślnie)
MAX_RESULTS = 50
PASSAGE_CHARS = 700  # tyle tekstu pasującego fragmentu w raporcie
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_JOB_PATH = re.compile(r"^/analiza/([0-9a-f]{12})$")
GROUPS = ("uwzglednione", "nieuwzglednione", "inne")
SIDES = {
    "skarzacy": "skarżącego (skarga, skarga kasacyjna, skarga do ETPC)",
    "organ": "organu albo państwa (odpowiedź na skargę)",
}


class DocumentError(ValueError):
    """The document can't be read; the message is for the user."""


# --- tekst dokumentu -----------------------------------------------------------


def extract_text(filename, data):
    """Plain text of an uploaded .docx, .pdf or .txt; DocumentError otherwise."""
    name = (filename or "").lower()
    if name.endswith(".docx"):
        text = docx_text(data)
    elif name.endswith(".pdf"):
        text = pdf_text(data)
    elif name.endswith((".txt", ".md")):
        text = decode(data)
    elif name.endswith(".doc"):
        raise DocumentError("stary format .doc - zapisz w Wordzie jako .docx albo PDF")
    else:
        raise DocumentError("obsługiwane pliki: .docx, .pdf, .txt")
    return clean(text)


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


def pdf_text(data):
    try:
        from pypdf import PdfReader  # obraz www (docker/www.Dockerfile)
    except ImportError as error:
        raise DocumentError("brak biblioteki pypdf: docker compose build www") from error
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            reader.decrypt("")
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as error:
        raise DocumentError(f"nie można odczytać PDF ({type(error).__name__})") from error
    if len(text.strip()) < 50:
        raise DocumentError(
            "PDF nie ma warstwy tekstu (skan) - potrzebny OCR; skopiuj tekst i wklej go w polu niżej"
        )
    return text


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


def parse_form(request, limit=MAX_UPLOAD):
    """Fields of a POST form: {name: text} and, for files, {name: (filename, bytes)}."""
    content_type = request.environ.get("CONTENT_TYPE", "")
    body = request.body(limit)
    if "multipart/form-data" not in content_type:
        return {k: v[-1] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}
    message = BytesParser(policy=HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
    )
    fields = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        fields[name] = (filename, payload) if filename is not None else decode(payload)
    return fields


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
    error: str = ""
    embedder: str = ""
    seconds: float = 0.0
    result: object = None  # judgments_core.rag.Retrieval
    groups: dict = field(default_factory=dict)  # judgment id -> grupa


class DocumentAnalysis:
    """Jobs of one project's pages: start, progress, report (handle() = the pages)."""

    def __init__(self, settings, retriever, chunker, connect, semantic_embedder):
        self.settings = settings
        self.retriever = retriever
        self.chunker = chunker
        self.connect = connect
        self.semantic_embedder = semantic_embedder
        self.jobs = {}
        self.lock = threading.Lock()

    def start(self, filename, text, side, count):
        job = Job(uuid.uuid4().hex[:12], filename, side, count, len(text))
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
            job.step = "sprawdzam LM Studio"
            embedder, how = choose_embedder(self.settings, self.semantic_embedder)
            job.embedder = how
            job.step = f"liczę wektory dokumentu ({how}) i szukam w bazie"
            with contextlib.closing(self.connect()) as conn, conn.cursor() as cur:
                job.result = self.retriever.like_text(
                    cur, text, self.chunker, job.count, embedder=embedder
                )
            job.groups = {
                s.judgment_id: self.retriever.outcome_group(s.result) for s in job.result.sources
            }
            job.status = "gotowe"
        except Exception as error:
            log.exception("analiza dokumentu %s", job.id)
            job.status, job.error = "blad", f"{type(error).__name__}: {error}"
        finally:
            job.seconds = time.monotonic() - started
            job.step = ""

    # --- strony ---

    def handle(self, request, layout):
        """/analiza (form, POST = start) and /analiza/<id> (progress, report)."""
        match = _JOB_PATH.match(request.path)
        if match:
            job = self.get(match.group(1))
            if job is None:
                body = "<p class=error>Nie ma takiej analizy (serwer był restartowany?).</p>"
                return Response(layout("Analiza dokumentu", body + _form()), "404 Not Found")
            return Response(layout("Analiza dokumentu", _job_page(job, self.retriever)))
        if request.method != "POST":
            return Response(layout("Analiza dokumentu", _form()))
        try:
            fields = parse_form(request)
            filename, data = fields.get("plik") or ("", b"")
            pasted = (fields.get("tekst") or "").strip()
            if data:
                text = extract_text(filename, data)
            elif pasted:
                filename, text = "tekst wklejony", clean(pasted)
            else:
                raise DocumentError("wybierz plik albo wklej tekst")
            if len(text) < 200:
                raise DocumentError("za mało tekstu do analizy (najmniej ~200 znaków)")
        except (DocumentError, ValueError) as error:
            return Response(
                layout("Analiza dokumentu", f"<p class=error>{e(error)}</p>" + _form()),
                "400 Bad Request",
            )
        side = fields.get("strona") if fields.get("strona") in SIDES else "skarzacy"
        count = min(max(_int(fields.get("ile"), RESULTS), 5), MAX_RESULTS)
        job = self.start(filename, text, side, count)
        return Response("", "303 See Other", headers=[("Location", f"/analiza/{job.id}")])


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _form():
    sides = "".join(
        f"<label><input type=radio name=strona value={key}{' checked' if key == 'skarzacy' else ''}>"
        f" {e(text)}</label>"
        for key, text in SIDES.items()
    )
    return f"""<h1>Analiza dokumentu</h1>
<p class=hint>Wgraj orzeczenie albo pismo (.docx, .pdf, .txt) albo wklej tekst. Dokument jest
cięty na fragmenty, każdy szuka w bazie najbliższych orzeczeń po znaczeniu, a raport dzieli je
według wyniku sprawy. Dokument zostaje na tym komputerze i nie jest zapisywany.</p>
<form class=analysis method=post action="/analiza" enctype="multipart/form-data">
<p><label>Plik <input type=file name=plik accept=".docx,.pdf,.txt"></label></p>
<p><label>albo tekst<br><textarea name=tekst rows=8 cols=80></textarea></label></p>
<fieldset><legend>Na poparcie stanowiska</legend>{sides}</fieldset>
<p><label>Ile orzeczeń <input type=number name=ile value={RESULTS} min=5 max={MAX_RESULTS}></label></p>
<p><button type=submit>Analizuj</button></p>
</form>"""


def _job_page(job, retriever):
    if job.status == "liczy":
        waited = int(time.time() - job.created)
        return f"""<meta http-equiv=refresh content=3>
<h1>Analiza dokumentu</h1>
<p><b>{e(job.filename)}</b> · {number(job.chars)} znaków</p>
<p class=progress-line>Trwa: {e(job.step)} ({waited} s). Strona odświeża się sama.</p>"""
    if job.status == "blad":
        return (
            f"<h1>Analiza dokumentu</h1><p class=error>Nie udało się: {e(job.error)}</p>"
            '<p><a href="/analiza">Nowa analiza</a></p>'
        )
    return _report(job, retriever)


def _report(job, retriever):
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
        f"<details><summary>{e(legend[0])}</summary><pre class=legend>{e(legend[1])}</pre></details>"
        if len(legend) == 2
        else ""
    )
    html = [
        f"""<h1>Analiza dokumentu</h1>
<p class=doc-tools translate=no>{print_button()} <a href="/analiza">Nowa analiza</a></p>
<p><b>{e(job.filename)}</b> · {number(job.chars)} znaków · strona: {e(SIDES[job.side])}
· wektory: {e(job.embedder)} · {job.seconds:.0f} s</p>
<p>{len(result.sources)} najbliższych orzeczeń - {summary}</p>
<p class=hint>Podział według <b>wyniku sprawy</b>, nie argumentacji: orzeczenie „na poparcie”
mogło wygrać z innego powodu, a „przeciwne” może zawierać przydatny pogląd. W sprawach
kasacyjnych sprawdź, kto wniósł skargę kasacyjną.</p>{own}"""
    ]
    for title, group, note in sections:
        sources = [s for s in result.sources if job.groups.get(s.judgment_id) == group]
        html.append(f"<h2>{e(title)} ({len(sources)})</h2><p class=hint>{e(note)}</p>")
        if not sources:
            html.append("<p class=empty>Brak.</p>")
        for s in sources:
            html.append(_source(s))
    return "\n".join(html)


def _source(s):
    fragments = "".join(
        f"<p class=hint>{e(f.heading)}</p><blockquote>{e(shorten(f.text, PASSAGE_CHARS))}</blockquote>"
        for f in s.fragments
    )
    return f"""<section class=hit><h3>[{s.number}] <a href="/orzeczenie/{s.judgment_id}">{e(s.title)}</a></h3>
<p class=hint>{e(s.details)} · <a href="{e(s.url)}" target=_blank rel="noopener noreferrer">źródło</a></p>
{fragments}</section>"""
