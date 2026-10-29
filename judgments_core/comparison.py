"""Comparing two documents on the pages ("Porównanie dokumentów"): which arguments of a
brief (a complaint, a cassation appeal) the court's reasoning answers and which it
passes over - the absence analysis.

The brief is cut into arguments (paragraphs; numbered points start a new one), the
judgment into passages of the court's own reasoning: from "zważył, co następuje"
(NSA/WSA), "THE LAW" / "The Court's assessment" (ECHR), else the whole text - the part
before it restates the parties' arguments and would "answer" every argument with its
own summary. Formulas (boilerplate.py: sentences repeated in many judgments of the
base, the project's patterns of a court's and a party's formulas, a court's sentences
close in meaning to a listed one) are dropped from both and listed in the report. Both are embedded by the same model (LM Studio when it has the model, else
this process'), every argument gets its closest reasoning passage by cosine similarity:
odniesiono się (>= ANSWERED), częściowo (>= PARTLY), pominięto (below). The thresholds
are a heuristic for bge-m3 - the report shows the similarity and the passage, the
reading stays with the lawyer. No database: the documents only, in memory.
"""

from __future__ import annotations

import math
import re
import threading
import time
import uuid
from dataclasses import dataclass, field

from judgments_core.analysis import (
    _ROOT_HREF,
    FILE_TYPES,
    MAX_JOBS,
    DocumentError,
    _base_url,
    choose_embedder,
    form_document,
    html_to_pdf,
    parse_form,
)
from judgments_core.web import Response, e, file_field, number, shorten

ANSWERED = 0.70  # cosinus bge-m3: zarzut omówiony (obca sprawa nie przekracza ~0.62; kalibracja I FZ 106/22)
PARTLY = 0.62  # temat się pojawia, ale nie wprost
MIN_UNIT = 200  # krótszy akapit dołącza do następnego (znaki)
MAX_UNIT = 1500  # dłuższy dzielony na zdania
MAX_UNITS = 120  # tyle argumentów pisma i fragmentów orzeczenia najwyżej
EMBED_BATCH = 8
# Początek własnych rozważań sądu (wcześniej: streszczenie stanowisk stron).
REASONING_MARKERS = re.compile(
    r"zważył[a-ząęłńóśźż]*,?\s+co następuje\s*:?"
    r"|^\s*THE LAW\s*$|^\s*The Court['’]s assessment\s*$"
    r"|^\s*EN DROIT\s*$|^\s*Appréciation de la Cour\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_POINT = re.compile(r"^\s*(\d{1,2}[.)]|[a-z][.)]|[IVX]{1,4}[.)]|-|•)\s+")
_SENTENCE_END = re.compile(r"(?<=[.;:?!])\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ0-9(„\"])")
_PDF_PATH = re.compile(r"^/porownanie/([0-9a-f]{12})(\.pdf)?$")
STATUSES = (
    ("pominieto", "Pominięte przez sąd", "brak bliskiego fragmentu w rozważaniach sądu"),
    ("czesciowo", "Częściowo", "temat się pojawia, ale nie wprost - sprawdź"),
    ("odniesiono", "Sąd się odniósł", "bliski fragment w rozważaniach sądu"),
)


def units(text):
    """Arguments / passages: paragraphs, a numbered point starts a new one, short ones
    join the next, long ones are cut at sentence ends."""
    out, current = [], ""
    for line in (line.strip() for line in text.split("\n")):
        if not line:
            if len(current) >= MIN_UNIT:
                out.append(current)
                current = ""
            continue
        if current and (_POINT.match(line) and len(current) >= MIN_UNIT // 2):
            out.append(current)
            current = ""
        current = f"{current} {line}".strip()
        if len(current) >= MIN_UNIT and line.endswith((".", ":", ";")):
            out.append(current)
            current = ""
    if current:
        # krótka końcówka dołącza do poprzedniego - chyba że to numerowany punkt (osobny zarzut)
        if out and len(current) < MIN_UNIT // 2 and not _POINT.match(current):
            out[-1] += " " + current
        else:
            out.append(current)
    return [piece for unit in out for piece in _split_long(unit)][:MAX_UNITS]


def _split_long(unit):
    if len(unit) <= MAX_UNIT:
        return [unit]
    pieces, current = [], ""
    for sentence in _SENTENCE_END.split(unit):
        if current and len(current) + len(sentence) > MAX_UNIT:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def reasoning(text):
    """(the court's own reasoning, whether a marker was found)."""
    match = REASONING_MARKERS.search(text)
    return (text[match.end() :].strip(), True) if match else (text, False)


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def status_of(score):
    if score >= ANSWERED:
        return "odniesiono"
    if score >= PARTLY:
        return "czesciowo"
    return "pominieto"


@dataclass
class Match:
    number: int
    argument: str
    score: float
    passage: str
    status: str


@dataclass
class Comparison:
    id: str
    brief_name: str
    judgment_name: str
    created: float = field(default_factory=time.time)
    status: str = "liczy"
    step: str = "czeka na start"
    done: int = 0
    total: int = 0
    error: str = ""
    embedder: str = ""
    seconds: float = 0.0
    marker_found: bool = False
    only_reasoning: bool = True
    passages: int = 0
    stored: tuple = (0, 0)  # numery w teczce (pismo, orzeczenie); 0 = plik albo tekst
    skip_formulas: bool = True
    dropped: dict = field(default_factory=dict)  # "pismo"/"orzeczenie" -> [Dropped]
    formula_sample: int = 0  # z ilu orzeczeń policzono formułki
    matches: list = field(default_factory=list)


class DocumentComparison:
    """Comparisons of one project's pages (handle() = the pages)."""

    library = None  # library.Library: dokumenty z teczki (ustawia strona projektu)
    formulas = None  # boilerplate.Formulas projektu (ustawia strona projektu); None = bez filtra

    def __init__(self, settings, semantic_embedder):
        self.settings = settings
        self.semantic_embedder = semantic_embedder
        self.jobs = {}
        self.lock = threading.Lock()

    def start(
        self,
        brief_name,
        brief,
        judgment_name,
        judgment,
        only_reasoning=True,
        stored=(0, 0),
        skip_formulas=True,
    ):
        job = Comparison(uuid.uuid4().hex[:12], brief_name, judgment_name)
        job.only_reasoning, job.stored = only_reasoning, stored
        job.skip_formulas = skip_formulas and bool(self.formulas)
        job.formula_sample = self.formulas.sample if job.skip_formulas else 0
        with self.lock:
            self.jobs[job.id] = job
            while len(self.jobs) > MAX_JOBS:
                del self.jobs[next(iter(self.jobs))]
        threading.Thread(
            target=self._run, args=(job, brief, judgment), name=f"porownanie-{job.id}", daemon=True
        ).start()
        return job

    def get(self, job_id):
        with self.lock:
            return self.jobs.get(job_id)

    def _run(self, job, brief, judgment):
        started = time.monotonic()
        try:
            job.step = "sprawdzam LM Studio"
            embedder, how = choose_embedder(self.settings, self.semantic_embedder)
            job.embedder = how
            if job.only_reasoning:
                judgment, job.marker_found = reasoning(judgment)
            if job.skip_formulas:  # po wycięciu rozważań: "zważył, co następuje" to też formułka
                job.step = "pomijam formułki"
                brief, job.dropped["pismo"] = self.formulas.strip(brief, "brief")
                judgment, job.dropped["orzeczenie"] = self.formulas.strip(
                    judgment, "court", embedder
                )
            arguments, passages = units(brief), units(judgment)
            if not arguments or not passages:
                raise DocumentError("za mało tekstu w jednym z dokumentów")
            job.passages = len(passages)
            texts = arguments + passages
            if any(job.stored) and self.library is not None:  # z teczki: wektory z bazy
                owned = {t: job.stored[0] for t in arguments if job.stored[0]}
                owned.update({t: job.stored[1] for t in passages if job.stored[1]})
                embedder = self.library.embedder_for(embedder, owned=owned)
                how += ", wektory zapisane w teczce"
                job.embedder = how
            job.total, vectors = len(texts), []
            for start in range(0, len(texts), EMBED_BATCH):
                vectors += embedder.embed(texts[start : start + EMBED_BATCH])
                job.done = len(vectors)
                job.step = f"liczę wektory: {job.done} z {job.total} fragmentów ({how})"
            own, theirs = vectors[: len(arguments)], vectors[len(arguments) :]
            for n, (argument, vector) in enumerate(zip(arguments, own), 1):
                scores = [cosine(vector, other) for other in theirs]
                best = max(range(len(scores)), key=scores.__getitem__)
                job.matches.append(
                    Match(n, argument, scores[best], passages[best], status_of(scores[best]))
                )
            job.status = "gotowe"
        except Exception as error:  # noqa: BLE001 - błąd do raportu
            job.status, job.error = "blad", f"{type(error).__name__}: {error}"
        finally:
            job.seconds = time.monotonic() - started
            job.step = ""

    # --- strony ---

    def handle(self, request, layout):
        """/porownanie (form, POST = start), /porownanie/<id>, /porownanie/<id>.pdf."""
        match = _PDF_PATH.match(request.path)
        if match:
            job = self.get(match.group(1))
            if job is None:
                body = "<p class=error>Nie ma takiego porównania (serwer był restartowany?).</p>"
                return Response(
                    layout("Porównanie dokumentów", body + self.form()), "404 Not Found"
                )
            if match.group(2) and job.status == "gotowe":
                return _pdf(job, layout, _base_url(request))
            return Response(layout("Porównanie dokumentów", _job_page(job)))
        if request.method != "POST":
            return Response(layout("Porównanie dokumentów", self.form()))
        try:
            fields = parse_form(request)
            brief_name, brief, brief_id = form_document(
                fields, "pismo", "tekst_pisma", "pismo", self.library
            )
            judgment_name, judgment, judgment_id = form_document(
                fields, "orzeczenie", "tekst_orzeczenia", "orzeczenie", self.library
            )
        except (DocumentError, ValueError) as error:
            return Response(
                layout("Porównanie dokumentów", f"<p class=error>{e(error)}</p>" + self.form()),
                "400 Bad Request",
            )
        job = self.start(
            brief_name,
            brief,
            judgment_name,
            judgment,
            only_reasoning=bool(fields.get("rozwazania")),
            stored=(brief_id, judgment_id),
            skip_formulas=bool(fields.get("formulki")),
        )
        return Response("", "303 See Other", headers=[("Location", f"/porownanie/{job.id}")])

    def form(self):
        return _form(self.library, self.formulas)


def _form(library=None, formulas=None):
    return f"""<h1>Porównanie dokumentów</h1>
<p class=hint>Analiza absencji: które argumenty Twojego pisma (skargi, skargi kasacyjnej, zarzutów)
mają odpowiedź w rozważaniach sądu, a które sąd pominął. Oba dokumenty są cięte na akapity
i porównywane po znaczeniu. Dokumenty zostają na tym komputerze i nie są zapisywane.</p>
<form class=analysis method=post action="/porownanie" enctype="multipart/form-data">
<fieldset><legend>1. Twoje pismo (zarzuty)</legend>
{file_field("pismo", FILE_TYPES)}
{library.choices_html("pismo_teczka") if library else ""}
<p><label>albo tekst<br><textarea name=tekst_pisma rows=5></textarea></label></p></fieldset>
<fieldset><legend>2. Orzeczenie (z uzasadnieniem)</legend>
{file_field("orzeczenie", FILE_TYPES)}
{library.choices_html("orzeczenie_teczka") if library else ""}
<p><label>albo tekst<br><textarea name=tekst_orzeczenia rows=5></textarea></label></p></fieldset>
<p><label><input type=checkbox name=rozwazania value=1 checked> porównuj tylko z rozważaniami sądu
(od „zważył, co następuje” / „THE LAW”) - wcześniej sąd streszcza zarzuty stron</label></p>
{formulas_checkbox(formulas)}
<p><button type=submit>Porównaj</button></p>
</form>"""


def formulas_checkbox(formulas):
    if not formulas:
        return ""
    return f"""<p><label><input type=checkbox name=formulki value=1 checked> pomiń formułki:
w orzeczeniu zdania powtarzane w wielu orzeczeniach bazy ({number(len(formulas))}) i do nich
podobne, typowe formuły („Skarga okazała się bezzasadna.”, „O kosztach orzeczono…”, przytoczenia
p.p.s.a.); w piśmie nagłówki, wnioski (petitum), podpis i załączniki - raport pokaże, co pominięto
i dlaczego</label></p>"""


def why(item, sample=0):
    """Why a sentence was left out (boilerplate.Dropped), for the report."""
    if item.how == "lista":
        of = f" z {number(sample)}" if sample else ""
        return f"w {number(item.count)}{of} orzeczeń bazy"
    if item.how == "wzorzec":
        return f"wzorzec: {item.label}"
    return f"podobne {item.score:.2f} do „{shorten(item.label, 120)}”"


def dropped_html(title, dropped, sample=0, pdf=False):
    """The formulas dropped from one document, each once, with the reason."""
    if not dropped:
        return ""
    unique = list({d.sentence: d for d in dropped}.values())
    items = "".join(
        f"<li>{e(shorten(d.sentence, 300))} <span class=hint>({e(why(d, sample))})</span></li>"
        for d in unique
    )
    counts = ", ".join(
        f"{name} {n}"
        for name, n in (
            ("z listy", sum(d.how == "lista" for d in dropped)),
            ("wzorce", sum(d.how == "wzorzec" for d in dropped)),
            ("podobne", sum(d.how == "podobne" for d in dropped)),
        )
        if n
    )
    return (
        f"<details{' open' if pdf else ''}><summary>{e(title)}: pominięte jako formułka "
        f"({len(dropped)}: {counts})</summary><ul>{items}</ul></details>"
    )


def _job_page(job):
    if job.status == "liczy":
        percent = 100 * job.done / job.total if job.total else 0
        waited = int(time.time() - job.created)
        return f"""<meta http-equiv=refresh content=2>
<h1>Porównanie dokumentów</h1>
<p><b>{e(job.brief_name)}</b> ↔ <b>{e(job.judgment_name)}</b></p>
<div class=progress><div style="width:{percent:.1f}%"></div></div>
<p class=progress-line>Trwa: {e(job.step)} ({waited} s). Strona odświeża się sama.</p>"""
    if job.status == "blad":
        return (
            f"<h1>Porównanie dokumentów</h1><p class=error>Nie udało się: {e(job.error)}</p>"
            '<p><a href="/porownanie">Nowe porównanie</a></p>'
        )
    return _report(job)


def _report(job, pdf=False):
    counts = {key: sum(1 for m in job.matches if m.status == key) for key, _, _ in STATUSES}
    summary = " · ".join(f"{title.lower()}: <b>{counts[key]}</b>" for key, title, _ in STATUSES)
    if job.only_reasoning and not job.marker_found:
        scope = "całe orzeczenie (nie znaleziono „zważył, co następuje” ani „THE LAW”)"
    elif job.only_reasoning:
        scope = "rozważania sądu"
    else:
        scope = "całe orzeczenie"
    tools = (
        "<p class=doc-tools translate=no>"
        f'<a class=button href="/porownanie/{job.id}.pdf">Pobierz PDF</a>'
        ' <a href="/porownanie">Nowe porównanie</a></p>'
    )
    if job.skip_formulas:
        dropped = (
            f"<p>Pominięte formułki: <b>{len(job.dropped.get('pismo', []))}</b> zdań pisma,"
            f" <b>{len(job.dropped.get('orzeczenie', []))}</b> zdań orzeczenia"
            " (nie liczą się ani jako argument, ani jako odpowiedź; „podobne” = zdanie sądu"
            " bliskie znaczeniem formułce z listy - sprawdź, czy nie odpowiada na zarzut).</p>"
            + dropped_html("Pismo", job.dropped.get("pismo"), job.formula_sample, pdf)
            + dropped_html("Orzeczenie", job.dropped.get("orzeczenie"), job.formula_sample, pdf)
        )
    else:
        dropped = "<p class=hint>Formułki nie były pomijane.</p>"
    html = [
        f"""<h1>Porównanie dokumentów</h1>
{"" if pdf else tools}
<p><b>{e(job.brief_name)}</b> ↔ <b>{e(job.judgment_name)}</b> · porównanie z: {e(scope)}
· wektory: {e(job.embedder)} · {job.seconds:.0f} s</p>
<p>{len(job.matches)} argumentów pisma, {number(job.passages)} fragmentów orzeczenia - {summary}</p>
<p class=hint>Podobieństwo znaczeniowe (0-1) wskazuje, czy w rozważaniach jest fragment o tym
samym; nie mówi, czy sąd zarzut uwzględnił, ani czy odpowiedź jest wystarczająca. Progi:
≥ {ANSWERED} odniesiono, ≥ {PARTLY} częściowo, niżej pominięto - przejrzyj zwłaszcza
„pominięte” i „częściowo”.</p>
{dropped}"""
    ]
    for key, title, note in STATUSES:
        rows = [m for m in job.matches if m.status == key]
        html.append(f"<h2>{e(title)} ({len(rows)})</h2><p class=hint>{e(note)}</p>")
        if not rows:
            html.append("<p class=empty>Brak.</p>")
        for m in sorted(rows, key=lambda m: m.score):
            html.append(
                f"""<section class=hit><h3>Argument {m.number} · podobieństwo {m.score:.2f}</h3>
<blockquote>{e(shorten(m.argument, 900))}</blockquote>
<p class=hint>najbliższy fragment orzeczenia:</p>
<blockquote class=answer>{e(shorten(m.passage, 900))}</blockquote></section>"""
            )
    return "\n".join(html)


def _pdf(job, layout, base_url):
    html = layout("Porównanie dokumentów", _report(job, pdf=True))
    html = _ROOT_HREF.sub(lambda m: m.group(1) + base_url + "/", html)
    try:
        data = html_to_pdf(html)
    except RuntimeError as error:
        return Response(
            layout("Porównanie dokumentów", f"<p class=error>PDF niedostępny: {e(error)}</p>"),
            "503 Service Unavailable",
        )
    return Response(
        data,
        content_type="application/pdf",
        headers=[("Content-Disposition", 'attachment; filename="porownanie.pdf"')],
    )
