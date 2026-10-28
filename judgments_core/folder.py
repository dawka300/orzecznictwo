"""The case analysis on the pages ("Analiza sprawy"): several documents of one case in
chronological order (a complaint, the answer to it, the judgment, a cassation appeal...),
each with its author's role, read together.

For every document: paragraphs (as in the comparison) and their vectors. Across the case:
who answered whom - every argument of an earlier document gets its closest paragraph in
each later document of another role (a court: only its own reasoning), with the
comparison's thresholds - what was answered and what passed over. A legal analysis: no
psychological measures here. The research tab (research.Research, psychology = True)
adds the psychological layer (psyche.py), the semantic axes (axes.py), the language
markers (markers.py) and the change between documents on the axes.
Signals to read with the text, not a diagnosis. No database: the documents stay in memory
(documents stored in the case folder, library.py, are picked from a list, read already).
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, field

from judgments_core import axes as semantic_axes
from judgments_core import markers as text_markers
from judgments_core import psyche
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
from judgments_core.comparison import (
    ANSWERED,
    PARTLY,
    REASONING_MARKERS,
    STATUSES,
    status_of,
    units,
)
from judgments_core.web import Response, e, file_field, number, shorten

SLOTS = 6  # tyle dokumentów w formularzu
MIN_DOCUMENTS = 2
EMBED_BATCH = 8
EXTREMES = 3  # akapitów na biegun osi w raporcie
ROLES = (
    ("skarzacy", "skarżący / strona"),
    ("organ", "organ / strona przeciwna"),
    ("sad", "sąd"),
    ("inny", "inny"),
)
ROLE_NAMES = dict(ROLES)
SHOWN_MARKERS = ("pewnosc", "absolut", "wyjatki", "przymus", "bezosobowe", "emocje", "ja", "my")
# Sekcje raportu: klucz -> nazwa (kolejność = kolejność w raporcie).
SECTIONS = {
    "psychologia": "Warstwa psychologiczna (emocje, zniekształcenia, styl)",
    "osie": "Osie znaczeniowe w czasie",
    "odpowiedzi": "Kto komu odpowiedział",
    "zmiany": "Zmiany między kolejnymi dokumentami",
    "znaczniki": "Znaczniki słownikowe",
}


@dataclass
class Document:
    number: int
    name: str
    role: str
    label: str
    text: str
    stored_id: int = 0  # numer w teczce (library.py): wektory z bazy; 0 = plik albo tekst
    units: list = field(default_factory=list)
    own: list = field(default_factory=list)  # numery akapitów do odpowiedzi (sąd: rozważania)
    vectors: list = field(default_factory=list)
    scores: list = field(default_factory=list)  # [{oś: wynik}] na akapit
    means: dict = field(default_factory=dict)  # oś -> średnia dokumentu
    psyche: list = field(default_factory=list)  # [{kategoria: wynik}] na akapit
    marked: dict = field(default_factory=dict)  # kategoria -> numery wyraźnych akapitów
    markers: object = None
    style: object = None

    @property
    def title(self):
        return f"{self.number}. {self.label or self.name}"


@dataclass
class Answer:
    source: int  # numer dokumentu z argumentami
    target: int  # numer późniejszego dokumentu
    counts: dict
    matches: list  # (status, argument, wynik, najbliższy akapit)


@dataclass
class Step:
    before: int
    after: int
    change: float  # 1 - cosinus średnich wektorów
    deltas: dict  # oś -> zmiana średniej
    closest: int | None  # najbliższy wcześniejszy dokument innej roli
    closest_score: float


@dataclass
class Folder:
    id: str
    created: float = field(default_factory=time.time)
    status: str = "liczy"
    step: str = "czeka na start"
    done: int = 0
    total: int = 0
    error: str = ""
    embedder: str = ""
    seconds: float = 0.0
    documents: list = field(default_factory=list)
    axes: list = field(default_factory=list)
    categories: list = field(default_factory=list)  # psyche.Category
    thresholds: dict = field(default_factory=dict)
    answers: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    sections: tuple = tuple(SECTIONS)


def court_units(text):
    """(paragraphs, indexes of the court's own reasoning): from "zważył, co następuje" /
    "THE LAW"; without the marker the whole text counts as reasoning."""
    match = REASONING_MARKERS.search(text)
    if not match:
        parts = units(text)
        return parts, list(range(len(parts)))
    before, own = units(text[: match.start()]), units(text[match.end() :])
    return before + own, list(range(len(before), len(before) + len(own)))


def normalised(vector):
    return semantic_axes.unit(vector)


def best(vector, others):
    """(index, cosine) of the closest normalised vector."""
    scores = [semantic_axes.dot(vector, other) for other in others]
    index = max(range(len(scores)), key=scores.__getitem__)
    return index, scores[index]


class CaseFolder:
    """Case folders of one project's pages (handle() = the pages). Children change the
    address, the title, the number of documents and the form (e.g. research.Research)."""

    job_class = None  # None = Folder; potomek: dataclass z dodatkowymi polami (research.py)
    base = "/sprawa"
    title = "Analiza sprawy"
    slots = SLOTS
    min_documents = MIN_DOCUMENTS
    noun = "analiza sprawy"
    button = "Analizuj sprawę"
    library = None  # library.Library: dokumenty z teczki (ustawia strona projektu)
    # Analiza sprawy jest prawna: tylko "kto komu odpowiedział". Miary psychologiczne (emocje,
    # zniekształcenia, osie, styl, znaczniki, zmiany na osiach) liczy tylko Badanie (research.py).
    psychology = False
    default_sections = ("odpowiedzi",)
    intro = """Kilka dokumentów jednej sprawy <b>w kolejności, w jakiej powstały</b> (np. skarga,
odpowiedź na skargę, wyrok, skarga kasacyjna). Raport pokazuje, kto na które argumenty
odpowiedział, a które pominął (sąd: tylko jego rozważania), z cytatami - materiał do
zarzutu braku odniesienia się do argumentów strony. Miary psychologiczne i językowe
pojedynczego tekstu: zakładka Badanie. Pliki: Word, PDF
(także skan - OCR), zdjęcie strony, tekst albo dokument z Teczki sprawy (bez ponownego
czytania i OCR). Dokumenty z plików zostają na tym komputerze i nie są
zapisywane."""

    def __init__(self, settings, semantic_embedder):
        self.settings = settings
        self.semantic_embedder = semantic_embedder
        self.jobs = {}
        self.lock = threading.Lock()
        self.path = re.compile(
            rf"^{re.escape(self.base)}/([0-9a-f]{{12}})(\.pdf|\.csv|-dokumenty\.csv|\.zip)?$"
        )

    def start(self, documents, sections=None):
        job = (self.job_class or Folder)(
            uuid.uuid4().hex[:12], documents=documents, sections=sections or self.default_sections
        )
        with self.lock:
            self.jobs[job.id] = job
            while len(self.jobs) > MAX_JOBS:
                del self.jobs[next(iter(self.jobs))]
        threading.Thread(
            target=self._run, args=(job,), name=f"teczka-{job.id}", daemon=True
        ).start()
        return job

    def get(self, job_id):
        with self.lock:
            return self.jobs.get(job_id)

    def _run(self, job):
        started = time.monotonic()
        try:
            job.step = "sprawdzam LM Studio"
            embedder, how = choose_embedder(self.settings, self.semantic_embedder)
            job.embedder = how
            for doc in job.documents:
                if doc.role == "sad":
                    doc.units, doc.own = court_units(doc.text)
                else:
                    doc.units = units(doc.text)
                    doc.own = list(range(len(doc.units)))
                if not doc.units:
                    raise DocumentError(f"za mało tekstu: {doc.title}")
                if self.psychology:
                    doc.markers = text_markers.analyse(doc.text)
                    doc.style = psyche.style(doc.text)
            texts = [u for doc in job.documents for u in doc.units]
            owned = {u: d.stored_id for d in job.documents if d.stored_id for u in d.units}
            if owned and self.library is not None:  # dokumenty z teczki: wektory z bazy
                embedder = self.library.embedder_for(embedder, owned=owned)
                job.embedder = how = how + ", wektory zapisane w teczce"
            job.total, vectors = len(texts), []
            for start in range(0, len(texts), EMBED_BATCH):
                vectors += embedder.embed(texts[start : start + EMBED_BATCH])
                job.done = len(vectors)
                job.step = f"liczę wektory: {job.done} z {job.total} akapitów ({how})"
            position = 0
            for doc in job.documents:
                count = len(doc.units)
                doc.vectors = [normalised(v) for v in vectors[position : position + count]]
                position += count
            if self.psychology:
                self._psychology(job, embedder, how, vectors)
            job.step = "kto komu odpowiedział"
            job.answers = answers(job.documents)
            if self.psychology:
                job.steps = steps(job.documents, job.axes)
            job.status = "gotowe"
        except Exception as error:  # noqa: BLE001 - błąd do raportu
            job.status, job.error = "blad", f"{type(error).__name__}: {error}"
        finally:
            job.seconds = time.monotonic() - started
            job.step = ""

    def _psychology(self, job, embedder, how, vectors):
        """The research measures: semantic axes, emotions and cognitive distortions."""
        job.step = "osie znaczeniowe"
        job.axes = semantic_axes.cached(embedder, how)
        all_scores = semantic_axes.scores(vectors, job.axes)
        job.step = "emocje i zniekształcenia poznawcze"
        neutral, job.categories = psyche.cached(embedder, how)
        all_psyche = psyche.scores(vectors, neutral, job.categories)
        job.thresholds = psyche.thresholds(all_psyche, job.categories)
        position = 0
        for doc in job.documents:
            count = len(doc.units)
            doc.scores = all_scores[position : position + count]
            doc.means = {a.key: sum(s[a.key] for s in doc.scores) / count for a in job.axes}
            doc.psyche = all_psyche[position : position + count]
            doc.marked = {
                c.key: [
                    k
                    for k, s in enumerate(doc.psyche)
                    if job.thresholds[c.key] and s[c.key] >= job.thresholds[c.key]
                ]
                for c in job.categories
            }
            position += count

    # --- strony ---

    def handle(self, request, layout):
        """<base> (form, POST = start), <base>/<id>, <base>/<id>.pdf (and what a child
        adds in download())."""
        match = self.path.match(request.path)
        if match:
            job = self.get(match.group(1))
            if job is None:
                body = f"<p class=error>Nie ma takiej analizy ({e(self.noun)}; serwer był restartowany?).</p>"
                return Response(layout(self.title, body + self.form()), "404 Not Found")
            if match.group(2) and job.status == "gotowe":
                return self.download(job, match.group(2), layout, request)
            return Response(layout(self.title, self.job_page(job)))
        if request.method != "POST":
            return Response(layout(self.title, self.form(request.get_int("teczka"))))
        try:
            fields = parse_form(request)
            documents = read_documents(fields, self.slots, self.min_documents, self.library)
            sections = self.chosen_sections(fields)
        except (DocumentError, ValueError) as error:
            return Response(
                layout(self.title, f"<p class=error>{e(error)}</p>" + self.form()),
                "400 Bad Request",
            )
        job = self.start(documents, sections)
        return Response("", "303 See Other", headers=[("Location", f"{self.base}/{job.id}")])

    def chosen_sections(self, fields):
        """The report's sections: in the case analysis only who answered whom."""
        return self.default_sections

    def download(self, job, suffix, layout, request):
        if suffix == ".pdf":
            return self.pdf(job, layout, _base_url(request))
        return Response(layout(self.title, "<p>Nie ma takiego pliku.</p>"), "404 Not Found")

    # --- HTML ---

    def form(self, selected=0):
        slots = "".join(
            self.document_html(slot, selected if slot == 1 else 0)
            for slot in range(1, self.slots + 1)
        )
        return f"""<h1>{e(self.title)}</h1>
<p class=hint>{self.intro}</p>
<form class=analysis method=post action="{self.base}" enctype="multipart/form-data">
{self.options_html()}
{slots}
<p><button type=submit>{e(self.button)}</button></p>
</form>"""

    def document_html(self, slot, selected=0):
        """One document's fields: who writes it, its name, a file or a text."""
        options = "".join(f"<option value={key}>{e(name)}</option>" for key, name in ROLES)
        required = " (wymagany)" if slot <= self.min_documents else ""
        return f"""<fieldset><legend>Dokument {slot}{required}</legend>
<p><label>Kto pisze <select name=dok{slot}_rola>{options}</select></label>
<label>Nazwa <input name=dok{slot}_nazwa placeholder="np. skarga, odpowiedź na skargę, wyrok WSA"></label></p>
{file_field(f"dok{slot}_plik", FILE_TYPES)}
{self.library.choices_html(f"dok{slot}_plik_teczka", selected) if self.library else ""}
<p><label>albo tekst<br><textarea name=dok{slot}_tekst rows=3></textarea></label></p></fieldset>"""

    def report_columns(self, d):
        """The report table's cells for one document after its number (name, who writes)."""
        return f"<td>{e(d.label or '-')}</td><td>{e(ROLE_NAMES[d.role])}</td>"

    report_headers = "<th>nazwa</th><th>kto pisze</th>"

    def options_html(self):
        """Extra form fields before the documents (none in the case folder)."""
        return ""

    def job_page(self, job):
        if job.status == "liczy":
            percent = 100 * job.done / job.total if job.total else 0
            waited = int(time.time() - job.created)
            names = " → ".join(f"<b>{e(d.title)}</b>" for d in job.documents)
            return f"""<meta http-equiv=refresh content=2>
<h1>{e(self.title)}</h1><p>{names}</p>
<div class=progress><div style="width:{percent:.1f}%"></div></div>
<p class=progress-line>Trwa: {e(job.step)} ({waited} s). Strona odświeża się sama.</p>"""
        if job.status == "blad":
            return (
                f"<h1>{e(self.title)}</h1><p class=error>Nie udało się: {e(job.error)}</p>"
                f'<p><a href="{self.base}">Nowa analiza</a></p>'
            )
        return self.report(job)

    def tools(self, job):
        return (
            "<p class=doc-tools translate=no>"
            f'<a class=button href="{self.base}/{job.id}.pdf">Pobierz PDF</a>'
            f' <a href="{self.base}">Nowa analiza</a></p>'
        )

    def report(self, job, pdf=False):
        docs = {d.number: d for d in job.documents}
        rows = "".join(
            f"<tr><td>{d.number}</td>{self.report_columns(d)}"
            f"<td>{e(shorten(d.name, 60))}</td><td class=num>{len(d.units)}</td>"
            f"<td class=num>{number(d.markers.words if d.markers else len(d.text.split()))}</td></tr>"
            for d in job.documents
        )
        html = [
            f"""<h1>{e(self.title)}</h1>
{"" if pdf else self.tools(job)}
<p>{len(job.documents)} dokumentów · wektory: {e(job.embedder)} · {job.seconds:.0f} s</p>
<table><tr><th>nr</th>{self.report_headers}<th>plik</th><th>akapitów</th><th>słów</th></tr>{rows}</table>
<p class=hint>Wszystkie liczby to sygnały do przeczytania z tekstem, nie diagnoza ani dowód.</p>"""
        ]
        renderers = {
            "psychologia": lambda: _psyche_html(job, pdf),
            "osie": lambda: _axes_html(job, docs, pdf),
            "odpowiedzi": lambda: _answers_html(job, docs, pdf),
            "zmiany": lambda: _steps_html(job, docs),
            "znaczniki": lambda: _markers_html(job),
        }
        html += [renderers[key]() for key in SECTIONS if key in job.sections]
        return "\n".join(html)

    def pdf_bytes(self, job, layout, base_url):
        """The report as PDF bytes; RuntimeError when the browser can't print it."""
        html = layout(self.title, self.report(job, pdf=True))
        return html_to_pdf(_ROOT_HREF.sub(lambda m: m.group(1) + base_url + "/", html))

    def pdf(self, job, layout, base_url):
        try:
            data = self.pdf_bytes(job, layout, base_url)
        except RuntimeError as error:
            return Response(
                layout(self.title, f"<p class=error>PDF niedostępny: {e(error)}</p>"),
                "503 Service Unavailable",
            )
        filename = self.base.strip("/") + ".pdf"
        return Response(
            data,
            content_type="application/pdf",
            headers=[("Content-Disposition", f'attachment; filename="{filename}"')],
        )


def read_documents(fields, slots=SLOTS, minimum=MIN_DOCUMENTS, library=None):
    """Filled slots of the form, in their order (= chronological); a slot is a file, a
    pasted text or a document of the case folder (library)."""
    documents = []
    for slot in range(1, slots + 1):
        _, data = fields.get(f"dok{slot}_plik") or ("", b"")
        stored = str(fields.get(f"dok{slot}_plik_teczka") or "").strip()
        if not data and not (fields.get(f"dok{slot}_tekst") or "").strip() and not stored:
            continue
        label = (fields.get(f"dok{slot}_nazwa") or "").strip()[:80]
        name, text, stored_id = form_document(
            fields, f"dok{slot}_plik", f"dok{slot}_tekst", label or f"dokument {slot}", library
        )
        role = fields.get(f"dok{slot}_rola") or "inny"
        if role not in ROLE_NAMES:
            role = "inny"
        documents.append(Document(len(documents) + 1, name, role, label, text, stored_id))
    if len(documents) < minimum:
        raise DocumentError(f"za mało dokumentów: potrzeba co najmniej {minimum}")
    return documents


def answers(documents):
    """For every earlier document and every later one of another role: its arguments
    (a court: its reasoning) against the later one's paragraphs (a court: reasoning)."""
    out = []
    for i, source in enumerate(documents):
        for target in documents[i + 1 :]:
            if target.role == source.role:
                continue
            others = [target.vectors[k] for k in target.own]
            matches = []
            for k in source.own:
                index, score = best(source.vectors[k], others)
                passage = target.units[target.own[index]]
                matches.append((status_of(score), source.units[k], score, passage))
            counts = {key: sum(1 for m in matches if m[0] == key) for key, _, _ in STATUSES}
            out.append(Answer(source.number, target.number, counts, matches))
    return out


def steps(documents, axes):
    """Consecutive documents: how much the content moved, the axes' change, and the
    earlier document of another role the later one is closest to."""
    means = [normalised(semantic_axes.mean(doc.vectors)) for doc in documents]
    out = []
    for i in range(1, len(documents)):
        doc = documents[i]
        change = 1 - semantic_axes.dot(means[i - 1], means[i])
        deltas = {a.key: doc.means[a.key] - documents[i - 1].means[a.key] for a in axes}
        earlier = [j for j in range(i) if documents[j].role != doc.role]
        closest, closest_score = None, 0.0
        if earlier:
            j = max(earlier, key=lambda j: semantic_axes.dot(means[j], means[i]))
            closest, closest_score = documents[j].number, semantic_axes.dot(means[j], means[i])
        out.append(
            Step(documents[i - 1].number, doc.number, change, deltas, closest, closest_score)
        )
    return out


# --- HTML ----------------------------------------------------------------------


def _psyche_html(job, pdf):
    parts = [
        """<h2 class=layer>Warstwa psychologiczna</h2>
<p class=hint>Emocje (osiem podstawowych wg Plutchika) i zniekształcenia poznawcze (jak w
terapii poznawczej) rozpoznawane po znaczeniu: akapit porównany z przykładowymi zdaniami
danej kategorii i ze zdaniami neutralnymi, rzeczowymi. „Wyraźne” = w najwyższej dziesiątej
części akapitów tej teczki, więc liczby mówią, gdzie tekst jest najbardziej nacechowany
na tle reszty, a nie ile emocji ma w ogóle. Pismo procesowe często pisze pełnomocnik -
mówi wtedy o strategii pisma, nie o psychice strony. Świadomie bez oceny osobowości
(Wielka Piątka) i zdrowia psychicznego: modele są angielskie, uczone na esejach i forach, a
wnioskowanie o zdrowiu osoby to dane szczególnej kategorii (art. 9 RODO).</p>"""
    ]
    for kind, title in psyche.KINDS.items():
        categories = [c for c in job.categories if c.kind == kind]
        head = "".join(f"<th>{e(c.title)}</th>" for c in categories)
        rows = "".join(
            f"<tr><td>{e(d.title)}<br><span class=hint>{e(ROLE_NAMES[d.role])}</span></td>"
            + "".join(_share(len(d.marked[c.key]), len(d.units)) for c in categories)
            + "</tr>"
            for d in job.documents
        )
        parts.append(
            f"<h3>{e(title)}</h3><p class=hint>wyraźne akapity: liczba (odsetek akapitów dokumentu)</p>"
            f"<table><tr><th>dokument</th>{head}</tr>{rows}</table>"
        )
        for c in categories:
            found = sorted(
                ((d.psyche[k][c.key], d, k) for d in job.documents for k in d.marked[c.key]),
                key=lambda x: -x[0],
            )
            if not found:
                continue
            quotes = "".join(
                f"<blockquote><span class=hint>{e(d.title)}, akapit {k + 1}, {score:+.3f}</span><br>"
                f"{e(shorten(d.units[k], 400))}</blockquote>"
                for score, d, k in found[: EXTREMES * 2]
            )
            parts.append(
                f"<details{' open' if pdf else ''}><summary>{e(c.title)}: najwyraźniejsze akapity "
                f"({len(found)})</summary>{quotes}</details>"
            )
    rows = "".join(
        f"<tr><td>{e(d.title)}</td><td class=num>{d.style.words_per_sentence}</td>"
        f"<td class=num>{d.style.subordinate_per_sentence}</td>"
        f"<td class=num>{d.style.necessity}</td><td class=num>{d.style.possibility}</td>"
        f"<td class=num>{d.style.first_person}</td><td class=num>{d.style.impersonal}</td>"
        f"<td class=num>{d.style.self_focus}</td></tr>"
        for d in job.documents
    )
    parts.append(
        f"""<h3>Styl poznawczy</h3>
<p class=hint>Z gramatyki tekstu (formy słów, bez lematyzacji). Długie zdania z wieloma
zdaniami podrzędnymi = złożona, zintegrowana argumentacja; dużo „musi / należy” przy mało
„może” = przymus i rygoryzm; udział 1. osoby (0-1) względem form bezosobowych = skupienie na
sobie wobec dystansu (pismo urzędowe i wyrok są z natury bezosobowe - porównuj dokumenty
tej samej strony w czasie).</p>
<table><tr><th>dokument</th><th>słów na zdanie</th><th>zdań podrzędnych na zdanie</th>
<th>przymus /1000</th><th>możliwość /1000</th><th>1. osoba /1000</th><th>bezosobowe /1000</th>
<th>udział 1. osoby</th></tr>{rows}</table>"""
    )
    return "\n".join(parts)


def _share(count, total):
    if not count:
        return "<td class=num>-</td>"
    return f"<td class=num>{count} ({100 * count / total:.0f}%)</td>"


def _answers_html(job, docs, pdf):
    parts = [
        f"""<h2 class=layer>Kto komu odpowiedział</h2>
<p class=hint>Każdy argument wcześniejszego dokumentu (sąd: tylko jego rozważania) dostaje
najbliższy znaczeniowo akapit późniejszego dokumentu innej strony. Progi jak w porównaniu
dokumentów: ≥ {ANSWERED} odniesiono, ≥ {PARTLY} częściowo, niżej pominięto. Podobieństwo
nie mówi, czy odpowiedź jest trafna ani wystarczająca.</p>"""
    ]
    if not job.answers:
        parts.append("<p class=empty>Brak par dokumentów różnych stron.</p>")
        return "\n".join(parts)
    head = "".join(f"<th>{e(title.lower())}</th>" for _, title, _ in STATUSES)
    rows = "".join(
        f"<tr><td>{e(docs[a.target].title)} ← {e(docs[a.source].title)}</td>"
        + "".join(f"<td class=num>{a.counts[key]}</td>" for key, _, _ in STATUSES)
        + "</tr>"
        for a in job.answers
    )
    parts.append(f"<table><tr><th>odpowiedź ← argumenty</th>{head}</tr>{rows}</table>")
    for a in job.answers:
        missed = sorted((m for m in a.matches if m[0] != "odniesiono"), key=lambda m: m[2])
        if not missed:
            continue
        items = "".join(
            f"""<section class=hit><h3>{"pominięty" if status == "pominieto" else "częściowo"}
· podobieństwo {score:.2f}</h3><blockquote>{e(shorten(argument, 700))}</blockquote>
<p class=hint>najbliższy akapit: {e(docs[a.target].title)}</p>
<blockquote class=answer>{e(shorten(passage, 500))}</blockquote></section>"""
            for status, argument, score, passage in missed
        )
        parts.append(
            f"<details{' open' if pdf else ''}><summary>{e(docs[a.target].title)} ← "
            f"{e(docs[a.source].title)}: pominięte i częściowe ({len(missed)})</summary>{items}</details>"
        )
    return "\n".join(parts)


def _axes_html(job, docs, pdf):
    head = "".join(
        f"<th>{e(a.title)}<br><span class=hint>- {e(a.minus)} / + {e(a.plus)}</span></th>"
        for a in job.axes
    )
    rows = "".join(
        f"<tr><td>{e(d.title)}<br><span class=hint>{e(ROLE_NAMES[d.role])}</span></td>"
        + "".join(f"<td class=num>{d.means[a.key]:+.3f}</td>" for a in job.axes)
        + "</tr>"
        for d in job.documents
    )
    parts = [
        f"""<h2 class=layer>Osie znaczeniowe w czasie</h2>
<p class=hint>Wektor akapitu po odjęciu średniej całej teczki (wspólny temat sprawy znika),
porównany z kierunkiem od bieguna „-” do „+” (bieguny z przykładowych zdań). Wyniki są małe
(zwykle ±0,2) i porównywalne tylko w tej teczce: akapit z akapitem, dokument z dokumentem.
Pierwsza wersja biegunów - do sprawdzenia na prawdziwych pismach.</p>
<table><tr><th>dokument</th>{head}</tr>{rows}</table>
<p class=legend-roles>{_legend(job.documents)}</p>"""
    ]
    for a in job.axes:
        parts.append(f"<h3>{e(a.title)}</h3>{_chart(job.documents, a)}")
        ranked = [(s[a.key], d, k) for d in job.documents for k, s in enumerate(d.scores)]
        ranked.sort(key=lambda x: x[0])
        for title, chosen in (
            (f"najbliżej „{a.minus}”", ranked[:EXTREMES]),
            (f"najbliżej „{a.plus}”", ranked[::-1][:EXTREMES]),
        ):
            quotes = "".join(
                f"<blockquote><span class=hint>{e(d.title)}, akapit {k + 1}, {score:+.3f}</span><br>"
                f"{e(shorten(d.units[k], 400))}</blockquote>"
                for score, d, k in chosen
            )
            parts.append(
                f"<details{' open' if pdf else ''}><summary>{e(title)}</summary>{quotes}</details>"
            )
    return "\n".join(parts)


ROLE_CLASSES = {"skarzacy": "r-skarzacy", "organ": "r-organ", "sad": "r-sad", "inny": "r-inny"}


def _legend(documents):
    roles = []
    for d in documents:
        if d.role not in roles:
            roles.append(d.role)
    return " ".join(
        f'<span class="dot {ROLE_CLASSES[r]}"></span> {e(ROLE_NAMES[r])}' for r in roles
    )


def _chart(documents, axis, width=760, height=170, pad=28):
    """Paragraph by paragraph across the folder (points coloured by role), the
    document's mean as a line, document borders numbered."""
    points = [(d, k, s[axis.key]) for d in documents for k, s in enumerate(d.scores)]
    total = len(points)
    top = max(0.05, max(abs(v) for _, _, v in points))
    step = (width - 2 * pad) / max(total - 1, 1)

    def y(value):
        return height / 2 - value / top * (height / 2 - 14)

    svg = [
        (
            f'<svg class=axis-chart viewBox="0 0 {width} {height}" role=img '
            f'aria-label="{e(axis.title)} akapit po akapicie">'
        ),
        f"<line class=zero x1={pad} x2={width - pad} y1={y(0):.1f} y2={y(0):.1f} />",
        f"<text class=pole x=4 y=12>+ {e(axis.plus)}</text>",
        f"<text class=pole x=4 y={height - 4}>- {e(axis.minus)}</text>",
    ]
    index = 0
    for d in documents:
        first = index
        last = index + len(d.scores) - 1
        x0, x1 = pad + first * step, pad + last * step
        if first:
            border = pad + (first - 0.5) * step
            svg.append(
                f"<line class=border x1={border:.1f} x2={border:.1f} y1=14 y2={height - 14} />"
            )
        svg.append(f"<text class=doc x={(x0 + x1) / 2:.1f} y={height - 16}>{d.number}</text>")
        mean_y = y(d.means[axis.key])
        svg.append(
            f'<line class="mean {ROLE_CLASSES[d.role]}" x1={x0:.1f} x2={x1:.1f} '
            f"y1={mean_y:.1f} y2={mean_y:.1f} />"
        )
        for k, s in enumerate(d.scores):
            svg.append(
                f'<circle class="{ROLE_CLASSES[d.role]}" cx={pad + (index + k) * step:.1f} '
                f"cy={y(s[axis.key]):.1f} r=3><title>{e(d.title)}, akapit {k + 1}: "
                f"{s[axis.key]:+.3f}</title></circle>"
            )
        index += len(d.scores)
    svg.append("</svg>")
    return "".join(svg)


def _steps_html(job, docs):
    if not job.steps:
        return ""
    head = "".join(f"<th>Δ {e(a.title)}</th>" for a in job.axes)
    rows = "".join(
        f"<tr><td>{e(docs[s.before].title)} → {e(docs[s.after].title)}</td>"
        f"<td class=num>{s.change:.3f}</td>"
        + "".join(f"<td class=num>{s.deltas[a.key]:+.3f}</td>" for a in job.axes)
        + "<td>"
        + (f"{e(docs[s.closest].title)} ({s.closest_score:.2f})" if s.closest else "-")
        + "</td></tr>"
        for s in job.steps
    )
    return f"""<h2 class=layer>Zmiany między kolejnymi dokumentami</h2>
<p class=hint>„Zmiana treści” = 1 - podobieństwo średnich wektorów dwóch dokumentów (duża
także przy zwykłej zmianie tematu); Δ = przesunięcie średniej na osi. „Najbliżej do” =
wcześniejszy dokument innej strony, do którego późniejszy jest najbardziej podobny - czyje
ramy pojęciowe przejmuje.</p>
<table><tr><th>krok</th><th>zmiana treści</th>{head}<th>najbliżej do</th></tr>{rows}</table>"""


def _markers_html(job):
    keys = [k for k in SHOWN_MARKERS if k in text_markers.MARKERS]
    head = "".join(f"<th>{e(text_markers.MARKERS[k][0])}</th>" for k in keys)
    rows = []
    for d in job.documents:
        rates = {m.key: m.per_1000 for m in d.markers.markers}
        rows.append(
            f"<tr><td>{e(d.title)}</td>"
            + "".join(f"<td class=num>{rates.get(k, 0.0)}</td>" for k in keys)
            + "</tr>"
        )
    return f"""<h2 class=layer>Znaczniki słownikowe (na 1000 słów)</h2>
<p class=hint>Znaczniki słownikowe jak w analizie dokumentu (dla tekstów angielskich część
znaczników nie ma listy słów). Rozbieżność z osiami bywa ciekawa: np. treść „otwarta na
porozumienie”, a w języku dużo pewności i kwantyfikatorów absolutnych.</p>
<table><tr><th>dokument</th>{head}</tr>{"".join(rows)}</table>"""
