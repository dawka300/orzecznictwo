"""The research tab on the pages ("Badanie"): the case folder's measures for the user's
own psychological research - one text (no role or name: several documents of a case are
the case folder's job), the report sections to compute chosen in the form, and the numbers
exported as CSV for statistics (R, JASP, SPSS): one row per paragraph and one per document.

A child of folder.CaseFolder: the same computation (vectors, psychological layer, axes,
answers, markers), another address, form and downloads. Local tool, the documents stay
in memory; the plan of the research is in psychologia.md (local).

"Zapytaj Claude" on a finished report sends Claude the methods, the numbers and the most
marked paragraphs (optionally the full texts) with a reviewer's instruction: describe,
interpret as hypotheses, and say where the measures are probably wrong. Every answer is
saved with what was sent and the CSVs in badania/badanie/<date>_<id>/ (local, outside git), to
learn from later.

Formulas (boilerplate.py: headings, petitum, signature; in a judgment the court's stock
sentences) are left out before measuring, unless the form says otherwise: they say
nothing about the author.
"""

from __future__ import annotations

import csv
import io
import json
import threading
import time
import zipfile
from dataclasses import dataclass

from judgments_core import claude_report
from judgments_core import markers as text_markers
from judgments_core.analysis import FILE_TYPES, DocumentError, _base_url, parse_form
from judgments_core.boilerplate import checkbox_html
from judgments_core.folder import ROLE_NAMES, SECTIONS, CaseFolder, Folder
from judgments_core.web import Response, e, file_field, shorten

MAX_TEXT_CHARS = 200_000  # pełnych tekstów do Claude razem (~60 tys. tokenów)
QUOTES = 3  # najwyraźniejszych akapitów na kategorię w materiale dla Claude
QUOTE_CHARS = 450
# Jeden tekst: "kto komu odpowiedział" i "zmiany" potrzebują kilku (Teczka sprawy).
RESEARCH_SECTIONS = {k: v for k, v in SECTIONS.items() if k not in ("odpowiedzi", "zmiany")}
TEXT_IN_CSV = 300  # tyle znaków akapitu w CSV (do rozpoznania, nie do analizy)
STYLE_FIELDS = (
    ("words_per_sentence", "slow_na_zdanie"),
    ("subordinate_per_sentence", "podrzedne_na_zdanie"),
    ("necessity", "przymus_1000"),
    ("possibility", "mozliwosc_1000"),
    ("first_person", "pierwsza_osoba_1000"),
    ("impersonal", "bezosobowe_1000"),
    ("self_focus", "udzial_1_osoby"),
)


SYSTEM = """Jesteś psychologiem-metodologiem i psycholingwistą. Recenzujesz wyniki lokalnego
badania tekstów (pisma procesowe, pisma organów, orzeczenia, korespondencja) dla badacza,
który jest psychologiem. Dostajesz opis metod, wyniki liczbowe i najwyraźniejsze akapity,
czasem także pełne teksty.

Zadania:
1. OPIS: co wyróżnia tekst i jak miary zmieniają się w jego toku, akapit po akapicie.
   Odwołuj się do konkretnych liczb i akapitów (numer akapitu).
2. INTERPRETACJA: hipotezy w kategoriach psychologicznych - potrzeba domknięcia poznawczego
   (Kruglanski), ramy poznawcze i ich przejmowanie między stronami, zdolności narracyjne
   (spójność, integracja, sprawstwo i wspólnota), wyrażanie i regulacja emocji w tekście,
   pozycjonowanie sprawczości. Każdą jako hipotezę z uzasadnieniem i z tym, co by ją
   obaliło.
3. KRYTYCZNA OCENA TRAFNOŚCI: dla każdej grupy miar powiedz, gdzie wynik jest
   prawdopodobnie trafny, a gdzie to artefakt - cytat albo streszczenie stanowiska strony
   przeciwnej, formułki prawnicze, gatunek tekstu (orzeczenie i pismo organu są z natury
   bezosobowe), autor-pełnomocnik zamiast strony, krótkie akapity, progi względne (górne
   10% analizy zawsze coś wskaże). Jeśli masz pełne teksty, sprawdź na nich wskazane
   akapity i powiedz wprost, które wskazania są fałszywe, a co miary przeoczyły.
4. CO DALEJ: które miary doprecyzować, jakie zdania wzorcowe dodać albo usunąć, co
   zmierzyć inaczej, jakie porównanie byłoby rozstrzygające.

Zasady: analizujesz teksty, nie diagnozujesz ludzi - bez rozpoznań klinicznych i bez ocen
osobowości konkretnych osób. Nie potwierdzaj wyników tylko dlatego, że je dostałeś: badacz
chce wiedzieć, gdzie miary się mylą. Oddzielaj to, co wynika z tekstu, od tego, co jest
tylko w liczbach. Pisz po polsku, rzeczowo; nagłówki jako „## ”, punkty jako „- ”."""

METHODS = """## Metody (jak powstały liczby)
- Jednostka: akapit (krótsze niż 200 znaków łączone z następnym, dłuższe niż 1500 dzielone
  na zdania). W orzeczeniu osobno rozważania sądu (od „zważył, co następuje” / „THE LAW”).
- Wektory: BAAI/bge-m3 (wielojęzyczny model zdań, 1024 wymiary; sentence-transformers albo
  LM Studio), podobieństwo cosinusowe.
- Emocje (8 wg Plutchika) i zniekształcenia poznawcze (6: czarno-białe, katastrofizacja,
  nadmierne uogólnianie, czytanie w myślach, personalizacja, etykietowanie): dla każdej
  kategorii średni wektor 8 polskich zdań wzorcowych; wynik akapitu = cos(akapit,
  kategoria) - cos(akapit, zdania neutralne, rzeczowe). „Wyraźny” akapit = w górnych 10%
  wszystkich akapitów tej analizy (próg względny) i powyżej zera. Walidacja: tylko próba
  na 8 zdaniach spoza wzorców (każde trafiło w swoją kategorię).
- Osie znaczeniowe (sprawczość, kooperacja-eskalacja, otwartość-domknięcie,
  rzeczowość-emocje): oś = różnica średnich wektorów dwóch biegunów (po 10 zdań wzorcowych);
  wektor akapitu wyśrodkowany na średniej wszystkich akapitów analizy (wspólny temat znika);
  wynik = cosinus z osią, zwykle ±0,2, porównywalny tylko w tej analizie.
- Styl poznawczy: wzorce form słów (bez lematyzacji): słów na zdanie, spójniki zdań
  podrzędnych na zdanie, przymus („musi, należy, powinien”) i możliwość („może, można”)
  na 1000 słów, 1. osoba i formy bezosobowe na 1000 słów, udział 1. osoby (0-1).
- Znaczniki słownikowe (tradycja Pennebakera / LIWC i pragmatyki): pewność, przymus,
  kwantyfikatory absolutne, wyjątki, ja, my, bezosobowość, słownictwo nacechowane - listy
  słów (początki słów), liczba na 1000 słów.
- Kto komu odpowiedział: każdy argument wcześniejszego dokumentu porównany z akapitami
  późniejszego dokumentu innej strony (sąd: tylko rozważania); ≥ 0,70 odniesiono,
  ≥ 0,62 częściowo, niżej pominięto (heurystyka dla bge-m3, kalibrowana na jednej sprawie).
- Zmiana treści: 1 - cosinus średnich wektorów kolejnych dokumentów (rośnie też przy zwykłej
  zmianie tematu); „najbliżej do” = najbardziej podobny wcześniejszy dokument innej strony.
- Teksty ze skanów i zdjęć odczytał Tesseract OCR (dopisek „OCR” przy nazwie pliku)."""


@dataclass
class ResearchJob(Folder):
    claude_status: str = ""  # "" / "pisze" / "gotowe" / "blad"
    claude_text: str = ""
    claude_error: str = ""
    claude_with_text: bool = False
    claude_cost: str = ""
    claude_saved: str = ""  # katalog zapisu
    claude_sent_chars: str = ""  # „wysłano X z Y znaków tekstów”
    claude_started: float = 0.0
    claude_prompt: str = ""  # wysłany materiał (do ZIP)
    claude_usage: dict = None


class Research(CaseFolder):
    job_class = ResearchJob
    base = "/badanie"
    title = "Badanie"
    slots = 1
    min_documents = 1
    psychology = True  # miary psychologiczne i językowe (CaseFolder liczy tylko odpowiedzi)
    noun = "badanie"
    button = "Policz"
    intro = """Narzędzie badawcze: miary psychologiczne i językowe jednego tekstu, do własnych
analiz. Wybierz sekcje raportu; liczby akapit po akapicie pobierzesz jako CSV (R, JASP, SPSS,
Excel). Pliki: Word, PDF (także skan - OCR), zdjęcie strony, tekst. Tekst zostaje na tym
komputerze i nie jest zapisywany (dokument z Teczki sprawy: bez ponownego czytania i OCR).
Plan badania i opis miar: psychologia.md. Kilka dokumentów
jednej sprawy (kto komu odpowiedział, zmiany między nimi): Analiza sprawy."""
    report_headers = ""

    def document_html(self, slot, selected=0):
        """One text: a file, a pasted text or a document of the case folder (no role and
        name - nothing to compare with)."""
        return f"""<fieldset><legend>Tekst</legend>
{file_field(f"dok{slot}_plik", FILE_TYPES)}
{self.library.choices_html(f"dok{slot}_plik_teczka", selected) if self.library else ""}
<p><label>albo wklej tekst<br><textarea name=dok{slot}_tekst rows=8></textarea></label></p></fieldset>"""

    def report_columns(self, d):
        return ""

    def options_html(self):
        boxes = "".join(
            f"<label><input type=checkbox name=sekcja_{key} value=1 checked> {e(name)}</label>"
            for key, name in RESEARCH_SECTIONS.items()
        )
        return (
            "<fieldset><legend>Sekcje raportu</legend>"
            "<p class=hint>Wektory, emocje, zniekształcenia, osie i znaczniki liczą się zawsze"
            " (są w CSV); wybór decyduje, co pokazać na stronie i w PDF.</p>"
            f"{boxes}</fieldset>" + self.formulas_html()
        )

    def formulas_html(self):
        return checkbox_html(
            self.formulas,
            "nie badamy ich. W piśmie nagłówki, wstęp, wnioski (petitum), podpis, załączniki,"
            " pouczenie; w orzeczeniu (rozpoznane po „zważył, co następuje” / „THE LAW”)"
            " nagłówek, skład, sentencja i typowe zdania sądu",
        )

    def chosen_formulas(self, fields):
        return bool(fields.get("formulki"))

    def chosen_sections(self, fields):
        chosen = tuple(key for key in RESEARCH_SECTIONS if fields.get(f"sekcja_{key}"))
        if not chosen:
            raise DocumentError("wybierz co najmniej jedną sekcję raportu")
        return chosen

    def tools(self, job):
        without = " (bez oceny Claude)" if job.claude_status == "pisze" else ""
        with_claude = " z oceną Claude" if job.claude_status == "gotowe" else without
        return (
            self.claude_status_html(job) + "<p class=doc-tools translate=no>"
            f'<a class=button href="{self.base}/{job.id}.zip">Pobierz wszystko (ZIP){with_claude}</a>'
            f' <a class=button href="{self.base}/{job.id}.pdf">PDF{with_claude}</a>'
            f' <a class=button href="{self.base}/{job.id}.csv">CSV: akapity</a>'
            f' <a class=button href="{self.base}/{job.id}-dokumenty.csv">CSV: dokumenty</a>'
            f' <a href="{self.base}">Nowe badanie</a></p>' + self.claude_form(job)
        )

    def claude_status_html(self, job):
        """The banner at the top: Claude writing (with time) / ready / failed."""
        if job.claude_status == "pisze":
            waited = int(time.time() - job.claude_started)
            texts = " z pełnymi tekstami" if job.claude_with_text else ""
            return (
                f"<p class=progress-line>Claude czyta materiał{texts} i pisze ocenę:"
                f" {waited // 60}:{waited % 60:02d} (zwykle 1-4 min). Strona odświeża się"
                " sama; ocena pojawi się na końcu raportu i w pobieranych plikach.</p>"
            )
        if job.claude_status == "gotowe":
            return (
                '<p class=notice>Ocena Claude gotowa: <a href="#claude">przejdź do oceny</a>'
                f" · zapisano w {e(job.claude_saved)}</p>"
            )
        if job.claude_status == "blad":
            return f"<p class=error>Claude: nie udało się - {e(job.claude_error)}</p>"
        return ""

    # --- Claude ---

    def handle(self, request, layout):
        """Also POST <base>/<id>/claude: ask Claude about a finished report."""
        prefix = f"{self.base}/"
        if (
            request.method == "POST"
            and request.path.startswith(prefix)
            and request.path.endswith("/claude")
        ):
            job = self.get(request.path[len(prefix) : -len("/claude")])
            if job is None or job.status != "gotowe":
                return Response(
                    layout(self.title, "<p>Nie ma takiego badania.</p>"), "404 Not Found"
                )
            fields = parse_form(request)
            self.ask_claude(job, with_text=bool(fields.get("tekst")))
            return Response("", "303 See Other", headers=[("Location", f"{self.base}/{job.id}")])
        return super().handle(request, layout)

    def ask_claude(self, job, with_text):
        ready, why = claude_report.available(self.settings)
        if not ready:
            job.claude_status, job.claude_error = "blad", why
            return
        if job.claude_status == "pisze":
            return
        job.claude_status, job.claude_error, job.claude_with_text = "pisze", "", with_text
        job.claude_started = time.time()
        threading.Thread(
            target=self._claude, args=(job,), name=f"claude-{job.id}", daemon=True
        ).start()

    def _claude(self, job):
        prompt, job.claude_sent_chars = claude_prompt(job, job.claude_with_text)
        try:
            text, usage = claude_report.ask(self.settings, SYSTEM, prompt)
        except claude_report.ClaudeError as error:
            job.claude_status, job.claude_error = "blad", str(error)
            return
        except Exception as error:  # noqa: BLE001 - błąd do raportu
            job.claude_status, job.claude_error = "blad", f"{type(error).__name__}: {error}"
            return
        job.claude_text, job.claude_cost = text, claude_report.cost(self.settings, usage)
        job.claude_prompt, job.claude_usage = prompt, usage
        try:
            job.claude_saved = str(save_run(job, prompt, usage, self.settings.claude_model))
        except OSError as error:
            job.claude_saved = f"nie zapisano ({error})"
        job.claude_status = "gotowe"

    def claude_form(self, job):
        if job.claude_status == "pisze":
            return ""
        ready, why = claude_report.available(self.settings)
        if not ready:
            return f"<p class=hint>Zapytaj Claude: niedostępne - {e(why)}</p>"
        again = " ponownie" if job.claude_status == "gotowe" else ""
        return f"""<form class=analysis method=post action="{self.base}/{job.id}/claude">
<fieldset><legend>Zapytaj Claude{again}</legend>
<p class=hint>Wysyła opis metod, liczby i najwyraźniejsze akapity; Claude opisuje wyniki,
stawia hipotezy i ocenia, gdzie miary się mylą. Odpowiedź i wysłany materiał zapisują się
lokalnie w badania/ (do nauki i porównań).</p>
<label><input type=checkbox name=tekst value=1> dołącz pełne teksty (do {MAX_TEXT_CHARS // 1000} tys.
znaków razem) - Claude sprawdzi miary na tekście; nie zaznaczaj przy dokumentach klientów bez ich zgody</label>
<p><button type=submit>Zapytaj Claude</button></p></fieldset></form>"""

    def job_page(self, job):
        page = super().job_page(job)
        if job.status == "gotowe" and job.claude_status == "pisze":
            # Skrypt, nie meta refresh: pobranie pliku (PDF, CSV) anuluje meta refresh.
            page = "<script>setTimeout(function(){location.reload()},5000)</script>" + page
        return page

    def report(self, job, pdf=False):
        html = super().report(job, pdf)
        return html + self.claude_html(job)

    def claude_html(self, job):
        if job.claude_status in ("pisze", "blad"):
            return "<h2 class=layer id=claude>Ocena Claude</h2>" + self.claude_status_html(job)
        if job.claude_status != "gotowe":
            return ""
        sent = (
            "liczby, akapity i pełne teksty"
            if job.claude_with_text
            else "liczby i najwyraźniejsze akapity"
        )
        return f"""<h2 class=layer id=claude>Ocena Claude</h2>
<p class=hint>Wysłano: {e(sent)}{e(job.claude_sent_chars)} · {e(job.claude_cost)} ·
zapisano: {e(job.claude_saved)}. To interpretacja modelu - sprawdź ją z tekstem.</p>
<section class=claude>{claude_report.to_html(job.claude_text)}</section>"""

    def download(self, job, suffix, layout, request):
        if suffix == ".csv":
            return _csv(paragraph_rows(job), "badanie-akapity.csv")
        if suffix == "-dokumenty.csv":
            return _csv(document_rows(job), "badanie-dokumenty.csv")
        if suffix == ".zip":
            return self.zip(job, layout, _base_url(request))
        return super().download(job, suffix, layout, request)

    def zip(self, job, layout, base_url):
        """Everything in one file: the report PDF (with Claude's review when there is one),
        the CSVs and, after Claude, claude.md, wyslane.md and meta.json."""
        files = {
            "akapity.csv": _csv_text(paragraph_rows(job)).encode("utf-8-sig"),
            "dokumenty.csv": _csv_text(document_rows(job)).encode("utf-8-sig"),
        }
        try:
            files["raport.pdf"] = self.pdf_bytes(job, layout, base_url)
        except RuntimeError as error:
            files["raport-pdf-niedostepny.txt"] = f"PDF niedostępny: {error}\n".encode()
        if job.claude_status == "gotowe":
            files["claude.md"] = (job.claude_text + "\n").encode("utf-8")
            files["wyslane.md"] = (SYSTEM + "\n\n---\n\n" + job.claude_prompt + "\n").encode(
                "utf-8"
            )
            meta = run_meta(job, job.claude_usage or {}, self.settings.claude_model)
            files["meta.json"] = json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8")
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        return Response(
            out.getvalue(),
            content_type="application/zip",
            headers=[("Content-Disposition", f'attachment; filename="badanie-{job.id}.zip"')],
        )


def paragraph_rows(job):
    """Header + one row per paragraph: the document, the paragraph and every measure."""
    axes = [a.key for a in job.axes]
    categories = [c.key for c in job.categories]
    head = (
        ["dokument", "nazwa", "rola", "akapit", "rozwazania_sadu", "znakow"]
        + [f"os_{k}" for k in axes]
        + [f"psy_{k}" for k in categories]
        + [f"wyrazne_{k}" for k in categories]
        + ["tekst"]
    )
    rows = [head]
    for d in job.documents:
        own = set(d.own) if d.role == "sad" else set()
        for k, unit in enumerate(d.units):
            rows.append(
                [d.number, d.label or d.name, d.role, k + 1, int(k in own), len(unit)]
                + [round(d.scores[k][a], 4) for a in axes]
                + [round(d.psyche[k][c], 4) for c in categories]
                + [int(k in d.marked[c]) for c in categories]
                + [" ".join(unit.split())[:TEXT_IN_CSV]]
            )
    return rows


def document_rows(job):
    """Header + one row per document: style, markers, axis means, marked paragraphs."""
    axes = [a.key for a in job.axes]
    categories = [c.key for c in job.categories]
    markers = list(text_markers.MARKERS)
    head = (
        ["dokument", "nazwa", "rola", "rola_opis", "akapitow", "slow", "zdan", "pominiete_formulki"]
        + [name for _, name in STYLE_FIELDS]
        + [f"znacznik_{k}_1000" for k in markers]
        + [f"os_{k}_srednia" for k in axes]
        + [f"psy_{k}_srednia" for k in categories]
        + [f"wyrazne_{k}_liczba" for k in categories]
    )
    rows = [head]
    for d in job.documents:
        rates = {m.key: m.per_1000 for m in d.markers.markers}
        count = len(d.units)
        rows.append(
            [
                d.number,
                d.label or d.name,
                d.role,
                ROLE_NAMES[d.role],
                count,
                d.style.words,
                d.style.sentences,
                len(d.dropped),
            ]
            + [getattr(d.style, attr) for attr, _ in STYLE_FIELDS]
            + [rates.get(k, 0.0) for k in markers]
            + [round(d.means[a], 4) for a in axes]
            + [round(sum(s[c] for s in d.psyche) / count, 4) for c in categories]
            + [len(d.marked[c]) for c in categories]
        )
    return rows


def _csv(rows, filename):
    """UTF-8 with BOM (Excel reads Polish letters), comma, dot decimals (R, JASP)."""
    return Response(
        _csv_text(rows).encode("utf-8-sig"),
        content_type="text/csv; charset=utf-8",
        headers=[("Content-Disposition", f'attachment; filename="{filename}"')],
    )


# --- materiał dla Claude i zapis ---------------------------------------------------


def claude_prompt(job, with_text):
    """(the user message for Claude, the note about how much text went): methods, the
    documents, every measure per document, the most marked paragraphs, answers and steps;
    with_text adds the full texts up to MAX_TEXT_CHARS (cut proportionally, said so)."""
    docs = {d.number: d for d in job.documents}
    lines = [METHODS, "", "## Dokumenty (w kolejności powstania)"]
    for d in job.documents:
        lines.append(
            f"- {d.number}. {d.label or '-'} | {ROLE_NAMES[d.role]} | plik: {d.name} |"
            f" {d.style.words} słów, {len(d.units)} akapitów"
            + (f", pominięte formułki: {len(d.dropped)} zdań" if job.skip_formulas else "")
        )
    lines += ["", "## Styl poznawczy i znaczniki (na dokument)"]
    for d in job.documents:
        s = d.style
        rates = ", ".join(f"{m.title.lower()} {m.per_1000}" for m in d.markers.markers)
        lines.append(
            f"- {d.number}: {s.words_per_sentence} słów/zdanie, {s.subordinate_per_sentence}"
            f" podrzędnych/zdanie, przymus {s.necessity}, możliwość {s.possibility},"
            f" 1. osoba {s.first_person}, bezosobowe {s.impersonal}, udział 1. osoby"
            f" {s.self_focus}; znaczniki /1000: {rates}"
        )
    lines += ["", "## Osie znaczeniowe (średnia dokumentu; - biegun / + biegun)"]
    for a in job.axes:
        values = ", ".join(f"{d.number}: {d.means[a.key]:+.3f}" for d in job.documents)
        lines.append(f"- {a.title} (- {a.minus} / + {a.plus}): {values}")
        ranked = sorted(
            ((s[a.key], d, k) for d in job.documents for k, s in enumerate(d.scores)),
            key=lambda x: x[0],
        )
        for label, chosen in (("najbliżej -", ranked[:2]), ("najbliżej +", ranked[::-1][:2])):
            for score, d, k in chosen:
                lines.append(
                    f"  - {label}: dok. {d.number}, akapit {k + 1} ({score:+.3f}): „{_quote(d.units[k])}”"
                )
    lines += ["", "## Emocje i zniekształcenia: wyraźne akapity (liczba na dokument) i przykłady"]
    for c in job.categories:
        counts = ", ".join(
            f"{d.number}: {len(d.marked[c.key])}/{len(d.units)}" for d in job.documents
        )
        lines.append(
            f"### {c.title} ({'emocja' if c.kind == 'emocja' else 'zniekształcenie'}) - {counts}"
        )
        found = sorted(
            ((d.psyche[k][c.key], d, k) for d in job.documents for k in d.marked[c.key]),
            key=lambda x: -x[0],
        )
        for score, d, k in found[:QUOTES]:
            lines.append(
                f"- dok. {d.number}, akapit {k + 1} ({score:+.3f}): „{_quote(d.units[k])}”"
            )
    if job.answers:
        lines += [
            "",
            "## Kto komu odpowiedział (odniesiono / częściowo / pominięto) i pominięte argumenty",
        ]
        for a in job.answers:
            c = a.counts
            lines.append(
                f"- {docs[a.target].title} <- {docs[a.source].title}: {c['odniesiono']} / {c['czesciowo']} / {c['pominieto']}"
            )
            for status, argument, score, _ in sorted(a.matches, key=lambda m: m[2])[:3]:
                if status == "pominieto":
                    lines.append(f"  - pominięty ({score:.2f}): „{_quote(argument, 300)}”")
    if job.steps:
        lines += ["", "## Zmiany między kolejnymi dokumentami"]
        for s in job.steps:
            deltas = ", ".join(f"{a.title} {s.deltas[a.key]:+.3f}" for a in job.axes)
            closest = (
                f"; najbliżej do dok. {s.closest} ({s.closest_score:.2f})" if s.closest else ""
            )
            lines.append(
                f"- {s.before} -> {s.after}: zmiana treści {s.change:.3f}; {deltas}{closest}"
            )
    note = ""
    if with_text:
        texts = {d.number: d.clean_text or d.text for d in job.documents}
        total = sum(len(t) for t in texts.values())
        share = min(1.0, MAX_TEXT_CHARS / total) if total else 1.0
        sent = 0
        lines += ["", "## Pełne teksty" + (" (bez formułek)" if job.skip_formulas else "")]
        for d in job.documents:
            text = texts[d.number]
            limit = int(len(text) * share)
            part = text[:limit]
            sent += len(part)
            cut = "" if limit >= len(text) else f" [UCIĘTE: {limit} z {len(text)} znaków]"
            lines += [
                "",
                f"### Dokument {d.number}: {d.label or d.name} ({ROLE_NAMES[d.role]}){cut}",
                part,
            ]
        note = f" ({sent:,} z {total:,} znaków tekstów)".replace(",", " ")
    lines += ["", "Opisz i oceń te wyniki według instrukcji."]
    return "\n".join(lines), note


def _quote(text, limit=QUOTE_CHARS):
    return shorten(" ".join(text.split()), limit)


def save_run(job, prompt, usage, model, root=None):
    """badania/badanie/<date>_<id>/: claude.md, wyslane.md, the two CSVs, meta.json."""
    files = {
        "claude.md": job.claude_text + "\n",
        "wyslane.md": SYSTEM + "\n\n---\n\n" + prompt + "\n",
        "akapity.csv": _csv_text(paragraph_rows(job)),
        "dokumenty.csv": _csv_text(document_rows(job)),
    }
    return claude_report.save("badanie", job.id, files, run_meta(job, usage, model), root)


def run_meta(job, usage, model):
    """What a saved run (and the ZIP) says about itself: model, tokens, cost, documents."""
    return {
        "badanie": job.id,
        "model": model,
        "tokeny": usage,
        "koszt": job.claude_cost,
        "pelne_teksty": job.claude_with_text,
        "wyslane_teksty": job.claude_sent_chars.strip(" ()"),
        "wektory": job.embedder,
        "dokumenty": [
            {
                "nr": d.number,
                "nazwa": d.label,
                "plik": d.name,
                "rola": d.role,
                "akapitow": len(d.units),
                "slow": d.style.words,
            }
            for d in job.documents
        ],
    }


def _csv_text(rows):
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue()
