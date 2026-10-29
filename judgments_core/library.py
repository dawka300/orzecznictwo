"""The case folder on the pages ("Teczka sprawy"): the user's documents kept in the
database `teczki` (settings.library_dsn), in nested catalogs.

A document is read once on import (Word, PDF, a scan or a photo through OCR, a text) and
its text is stored with the original file; the other tabs (Analiza dokumentu,
Porównanie, Analiza sprawy, Badanie) pick it from a list ("albo z teczki") instead of
reading and OCR-ing the file again. Documents of a catalog can be analysed together
(the case analysis, folder.CaseFolder) in the order of their dates.

Shared by the projects: the same database whichever project's page shows it. Local
only, outside git and outside the backup to another disk (narzedzia/kopia-na-dysk.sh).
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import re
import threading
import time
import uuid
from array import array
from dataclasses import dataclass, field
from datetime import date

import pymysql

from judgments_core import db
from judgments_core.analysis import (
    FILE_TYPES,
    MAX_JOBS,
    MAX_UPLOAD,
    DocumentError,
    choose_embedder,
    clean,
    parse_form,
    read_document,
)
from judgments_core.comparison import units
from judgments_core.folder import Document, court_units
from judgments_core.rag import OWN_CHUNKS
from judgments_core.web import Response, e, file_field, number, shorten

log = logging.getLogger(__name__)

MAX_IMPORT = 3 * MAX_UPLOAD  # bajtów w jednym imporcie (kilka plików naraz)
PRECOMPUTE_BATCH = 16  # fragmentów na jedno wywołanie modelu przy imporcie (pasek postępu)
ROLES = (
    ("inny", "inny / nie wiem"),
    ("skarzacy", "skarżący / strona"),
    ("organ", "organ / państwo"),
    ("sad", "sąd"),
)
ROLE_NAMES = dict(ROLES)
_DOCUMENT = re.compile(r"^/teczka/dokument/(\d+)(/plik)?$")
_CATALOG = re.compile(r"^/teczka/katalog/(\d+)/(nazwa|usun)$")
_IMPORT = re.compile(r"^/teczka/import/([0-9a-f]{12})$")


class LibraryUnavailable(RuntimeError):
    """The database `teczki` can't be reached; the message is for the user."""


class CachedEmbedder:
    """An embedder for the fragments of stored documents: vectors already in the database
    (key: model + sha256 of the fragment's text) are read, the others computed by the
    inner model and saved with the document they belong to. When the database fails, the
    inner model computes everything (the analysis goes on)."""

    def __init__(self, library, inner, owner=None, owned=None):
        self.library = library
        self.inner = inner
        self.owner = owner
        self.owned = owned or {}
        self.model = library.settings.embed_model

    def embed(self, texts):
        keys = [_sha(t) for t in texts]
        try:
            with self.library.store() as store:
                found = store.vectors_lookup(self.model, set(keys))
        except (LibraryUnavailable, pymysql.Error) as error:
            log.warning("teczka: wektory z bazy niedostępne: %s", error)
            return self.inner.embed(texts)
        missing = [i for i, key in enumerate(keys) if key not in found]
        if missing:
            computed = self.inner.embed([texts[i] for i in missing])
            by_owner = {}
            for i, vector in zip(missing, computed):
                found[keys[i]] = _pack(vector)
                owner = self.owned.get(texts[i], self.owner)
                if owner:
                    by_owner.setdefault(owner, []).append((keys[i], found[keys[i]]))
            try:
                with self.library.store() as store:
                    for owner, rows in by_owner.items():
                        store.vectors_save(owner, self.model, rows)
            except (LibraryUnavailable, pymysql.Error) as error:
                log.warning("teczka: wektory nie zapisane: %s", error)
        return [_unpack(found[key]) for key in keys]


@dataclass
class ImportJob:
    id: str
    catalog_id: int | None
    files: list  # [(nazwa pliku, bajty)]
    total: int = 0
    created: float = field(default_factory=time.time)
    status: str = "liczy"  # liczy / gotowe
    step: str = "czeka na start"
    done: int = 0
    added: list = field(default_factory=list)  # (id, tytuł, uwaga)
    skipped: list = field(default_factory=list)  # (nazwa, powód)


class Library:
    """The pages under /teczka and the "albo z teczki" choice for the other tabs."""

    base = "/teczka"
    title = "Teczka sprawy"

    def __init__(self, settings, case_folder=None, chunker=None, semantic_embedder=None):
        self.settings = settings
        self.case_folder = case_folder  # folder.CaseFolder: "Analizuj zaznaczone"
        self.chunker = chunker  # krojenie projektu (Analiza dokumentu: like_text)
        self.semantic_embedder = semantic_embedder  # model w procesie, gdy brak LM Studio
        self.jobs = {}
        self.lock = threading.Lock()
        self.migrated = False
        self.computing = {}  # numer dokumentu -> postęp liczenia wektorów (strona dokumentu)

    # --- baza ---

    @contextlib.contextmanager
    def store(self):
        """A LibraryStore on a fresh connection; the tables are created on first use."""
        if not self.settings.library_dsn:
            raise LibraryUnavailable("brak ustawienia library_dsn")
        try:
            conn = db.connect(self.settings.library_dsn, connect_timeout=3)
        except pymysql.Error as error:
            raise LibraryUnavailable(
                f"baza teczki niedostępna ({error.args[-1] if error.args else error});"
                " utwórz ją: komendy.md, sekcja „Teczka sprawy”"
            ) from error
        try:
            store = db.LibraryStore(conn)
            if not self.migrated:
                store.migrate()
                self.migrated = True
            yield store
        finally:
            conn.close()

    def read(self, document_id):
        """(name for the report, text) of a stored document; DocumentError when missing."""
        try:
            with self.store() as store:
                stored = store.document(document_id)
                text = store.text(document_id) if stored else None
        except LibraryUnavailable as error:
            raise DocumentError(str(error)) from error
        if stored is None or text is None:
            raise DocumentError("nie ma takiego dokumentu w teczce (usunięty?)")
        return label(stored), text

    def stored(self, document_id):
        """(StoredDocument, text) or None; for the case analysis of chosen documents."""
        with self.store() as store:
            stored = store.document(document_id)
            return (stored, store.text(document_id)) if stored else None

    def choices_html(self, name, selected=0):
        """<select name=…> of the stored documents grouped by catalog (path), or "" when
        there are none or the database can't be reached: the form then has no choice."""
        try:
            with self.store() as store:
                catalogs = store.catalogs()
                documents = store.documents(everywhere=True)
        except (LibraryUnavailable, pymysql.Error):
            return ""
        if not documents:
            return ""
        paths = catalog_paths(catalogs)
        groups = {}
        for d in documents:
            groups.setdefault(d.catalog_id, []).append(d)
        options = ['<option value="">- wybierz dokument z teczki -</option>']
        for catalog_id in sorted(groups, key=lambda c: (c is not None, paths.get(c, ""))):
            items = "".join(
                f"<option value={d.id}{' selected' if d.id == selected else ''}>"
                f"{e(shorten(label(d), 90))}</option>"
                for d in groups[catalog_id]
            )
            options.append(
                f'<optgroup label="{e(paths.get(catalog_id, "teczka (bez katalogu)"))}">'
                f"{items}</optgroup>"
            )
        return (
            f"<p><label>albo z teczki <select name={name}>{''.join(options)}</select></label></p>"
        )

    # --- import w tle ---

    def start_import(self, catalog_id, files):
        job = ImportJob(uuid.uuid4().hex[:12], catalog_id, files, total=len(files))
        with self.lock:
            self.jobs[job.id] = job
            while len(self.jobs) > MAX_JOBS:
                del self.jobs[next(iter(self.jobs))]
        threading.Thread(
            target=self._import, args=(job,), name=f"teczka-{job.id}", daemon=True
        ).start()
        return job

    def _import(self, job):
        try:
            for name, data in job.files:
                job.step = f"czytam {name}" + (
                    " (skan: OCR może potrwać)" if _maybe_scan(name) else ""
                )
                self._import_one(job, name, data)
                job.done += 1
        except Exception as error:
            log.exception("teczka: import")
            job.skipped.append(("(import)", f"{type(error).__name__}: {error}"))
        finally:
            job.files = []  # bajty plików nie zostają w pamięci
            job.status, job.step = "gotowe", ""

    def _import_one(self, job, name, data):
        sha = hashlib.sha256(data).hexdigest()
        with self.store() as store:
            existing = store.find_sha(sha)
        if existing:
            job.skipped.append((name, f"już jest w teczce (dokument {existing})"))
            return
        try:
            text, note = read_document(name, data)
        except DocumentError as error:
            job.skipped.append((name, str(error)))
            return
        if len(text) < 20:
            job.skipped.append((name, "brak tekstu"))
            return
        title = title_of(name)
        with self.store() as store:
            new_id = store.add_document(job.catalog_id, title, name, "inny", note, sha, text, data)
        job.added.append((new_id, title, note))
        try:
            self.precompute(new_id, text, lambda done, total: _vector_step(job, name, done, total))
        except Exception as error:  # noqa: BLE001 - dokument zostaje, wektory policzy analiza
            log.warning("teczka: wektory dokumentu %s: %s", new_id, error)
            job.skipped.append((name, f"zapisany bez wektorów ({type(error).__name__}: {error})"))

    # --- wektory ---

    def fragments(self, text, embedder=None):
        """Every text the analyses cut this document into and embed: paragraphs (the
        comparison, the case analysis, the research), the court's reasoning split off, and
        the project's chunks (the document analysis searching the base). Unique, in order."""
        texts = units(text)
        court, _ = court_units(text)
        texts += court
        formulas = getattr(self.case_folder, "formulas", None)
        if formulas:  # Porównanie i Analiza sprawy tną tekst bez formułek (pismo / sąd)
            texts += units(formulas.strip(text, "brief")[0])
            texts += court_units(text, lambda part: formulas.strip(part, "court", embedder)[0])[0]
        if self.chunker is not None:
            own_chunks = self.chunker.chunk(0, text, "", {})[:OWN_CHUNKS]
            texts += [self.chunker.embedding_input(c) for c in own_chunks]
        return list(dict.fromkeys(texts))

    def precompute(self, document_id, text, progress=None):
        """Vectors of all fragments of a stored document, saved with it (once, on import)."""
        embedder, _ = choose_embedder(self.settings, self.semantic_embedder)
        if embedder is None:
            raise RuntimeError("brak modelu wektorów (ani LM Studio, ani modelu w procesie)")
        cached = self.embedder_for(embedder, owner=document_id)
        texts = self.fragments(text, embedder)
        for start in range(0, len(texts), PRECOMPUTE_BATCH):
            cached.embed(texts[start : start + PRECOMPUTE_BATCH])
            if progress:
                progress(min(start + PRECOMPUTE_BATCH, len(texts)), len(texts))

    def embedder_for(self, inner, owner=None, owned=None):
        """The model for texts of stored documents: vectors in the database are read, the
        rest computed by inner and saved with their document (owned: {text: document id};
        owner: the document of every other text). Without a document: inner unchanged."""
        if not owner and not owned:
            return inner
        return CachedEmbedder(self, inner, owner, owned or {})

    def start_vectors(self, document_id, text):
        """Missing vectors of a stored document in the background (documents imported
        before vectors were stored, or after a change of cutting)."""
        with self.lock:
            if document_id in self.computing:
                return
            self.computing[document_id] = "start"

        def run():
            def step(done, total):
                self.computing[document_id] = f"{done} z {total}"

            try:
                self.precompute(document_id, text, step)
            except Exception as error:  # noqa: BLE001 - do logu, strona pokaże stan bazy
                log.warning("teczka: wektory dokumentu %s: %s", document_id, error)
            finally:
                with self.lock:
                    self.computing.pop(document_id, None)

        threading.Thread(target=run, name=f"teczka-wektory-{document_id}", daemon=True).start()

    def vectors_note(self, document_id, count):
        progress = self.computing.get(document_id)
        if progress:
            return f"liczę ({e(progress)} fragmentów, strona odświeża się sama)"
        return f"{number(count)} fragmentów" if count else "brak - kliknij „Policz wektory”"

    # --- strony ---

    def handle(self, request, layout):
        try:
            return self._handle(request, layout)
        except LibraryUnavailable as error:
            body = f"<h1>{e(self.title)}</h1><p class=error>{e(error)}</p>"
            return Response(layout(self.title, body), "503 Service Unavailable")

    def _handle(self, request, layout):
        path = request.path
        if path == self.base or path == self.base + "/":
            page = self.catalog_page(request.get_int("k"), request.get("uwaga"))
            return Response(layout(self.title, page))
        match = _IMPORT.match(path)
        if match:
            return self.import_page(match.group(1), layout)
        match = _DOCUMENT.match(path)
        if match:
            return self.document(int(match.group(1)), bool(match.group(2)), request, layout)
        if request.method != "POST":
            return Response(layout(self.title, "<p>Nie ma takiej strony.</p>"), "404 Not Found")
        if path == f"{self.base}/dodaj":
            return self.add(request, layout)
        if path == f"{self.base}/katalog":
            fields = parse_form(request)
            parent = _int(fields.get("k"))
            name = (fields.get("nazwa") or "").strip()[:150]
            if not name:
                return self._back(parent)
            with self.store() as store:
                return self._back(store.add_catalog(name, parent))
        match = _CATALOG.match(path)
        if match:
            return self.catalog_action(int(match.group(1)), match.group(2), request, layout)
        if path == f"{self.base}/zaznaczone":
            return self.chosen(request, layout)
        return Response(layout(self.title, "<p>Nie ma takiej strony.</p>"), "404 Not Found")

    def _back(self, catalog_id, note=""):
        where = f"{self.base}?k={catalog_id}" if catalog_id else self.base
        if note:
            where += ("&" if catalog_id else "?") + "uwaga=" + note
        return Response("", "303 See Other", headers=[("Location", where)])

    def add(self, request, layout):
        try:
            fields = parse_form(request, MAX_IMPORT, lists=("pliki",))
        except ValueError as error:
            return Response(
                layout(self.title, f"<p class=error>{e(error)}</p>" + self.catalog_page(None)),
                "400 Bad Request",
            )
        catalog_id = _int(fields.get("k"))
        files = [(name, data) for name, data in fields.get("pliki", []) if data]
        pasted = clean(fields.get("tekst") or "")
        if pasted:
            title = (fields.get("tytul") or "").strip()[:200] or "tekst wklejony"
            files.append((title + ".txt", pasted.encode("utf-8")))
        if not files:
            return self._back(catalog_id)
        job = self.start_import(catalog_id, files)
        return Response("", "303 See Other", headers=[("Location", f"{self.base}/import/{job.id}")])

    def import_page(self, job_id, layout):
        with self.lock:
            job = self.jobs.get(job_id)
        if job is None:
            return Response(
                layout(self.title, "<p>Nie ma takiego importu (serwer był restartowany?).</p>"),
                "404 Not Found",
            )
        back = f"{self.base}?k={job.catalog_id}" if job.catalog_id else self.base
        if job.status == "liczy":
            total = job.total
            percent = 100 * job.done / total if total else 0
            body = f"""<meta http-equiv=refresh content=2>
<h1>Import do teczki</h1>
<div class=progress><div style="width:{percent:.1f}%"></div></div>
<p class=progress-line>Plik {job.done + 1} z {total}: {e(job.step)}. Strona odświeża się sama.</p>"""
            return Response(layout(self.title, body))
        added = "".join(
            f'<li><a href="{self.base}/dokument/{i}">{e(t)}</a>'
            f"{f' <span class=hint>({e(n)})</span>' if n else ''}</li>"
            for i, t, n in job.added
        )
        skipped = "".join(f"<li>{e(n)}: {e(why)}</li>" for n, why in job.skipped)
        body = f"""<h1>Import do teczki</h1>
<p>Dodano {len(job.added)}{f", pominięto {len(job.skipped)}" if job.skipped else ""}.</p>
{f"<ul>{added}</ul>" if added else ""}
{f"<p class=error>Pominięte:</p><ul>{skipped}</ul>" if skipped else ""}
<p><a class=button href="{back}">Wróć do katalogu</a></p>"""
        return Response(layout(self.title, body))

    def catalog_action(self, catalog_id, action, request, layout):
        fields = parse_form(request)
        with self.store() as store:
            parent = {c[0]: c[1] for c in store.catalogs()}.get(catalog_id)
            if action == "nazwa":
                name = (fields.get("nazwa") or "").strip()[:150]
                if name:
                    store.rename_catalog(catalog_id, name)
                return self._back(catalog_id)
            if not store.catalog_is_empty(catalog_id):
                return self._back(catalog_id, "niepusty")
            store.delete_catalog(catalog_id)
        return self._back(parent)

    def chosen(self, request, layout):
        """POST from the list: analyse / move / delete the ticked documents."""
        fields = parse_form(request, lists=("d",))
        catalog_id = _int(fields.get("k"))
        ids = [int(i) for i in fields.get("d", []) if str(i).isdigit()]
        action = fields.get("akcja")
        if not ids:
            return self._back(catalog_id, "brak")
        if action == "przenies":
            target = _int(fields.get("cel"))
            with self.store() as store:
                store.move_documents(ids, target)
            return self._back(target)
        if action == "usun":
            with self.store() as store:
                store.delete_documents(ids)
            return self._back(catalog_id)
        if action == "analizuj" and self.case_folder is not None:
            return self.analyse(ids, catalog_id, layout)
        return self._back(catalog_id)

    def analyse(self, ids, catalog_id, layout):
        """The case analysis (folder.CaseFolder) of the chosen documents, by date."""

        with self.store() as store:
            chosen = [store.document(i) for i in ids]
            chosen = [d for d in chosen if d is not None]
            chosen.sort(key=lambda d: (d.doc_date is None, d.doc_date or date.min, d.id))
            texts = {d.id: store.text(d.id) for d in chosen}
        folder = self.case_folder
        if len(chosen) < folder.min_documents:
            return self._back(catalog_id, "malo")
        documents = [
            Document(
                n, label(d), d.role if d.role in ROLE_NAMES else "inny", d.title, texts[d.id], d.id
            )
            for n, d in enumerate(chosen, 1)
        ]
        job = folder.start(documents)
        return Response("", "303 See Other", headers=[("Location", f"{folder.base}/{job.id}")])

    def document(self, document_id, original, request, layout):
        with self.store() as store:
            stored = store.document(document_id)
            if stored is None:
                return Response(
                    layout(self.title, "<p>Nie ma takiego dokumentu.</p>"), "404 Not Found"
                )
            if original:
                data = store.original(document_id)
                name = stored.filename or f"dokument-{document_id}.txt"
                return Response(
                    data,
                    content_type=_content_type(name),
                    headers=[("Content-Disposition", f'attachment; filename="{_ascii(name)}"')],
                )
            if request.method == "POST":
                fields = parse_form(request)
                if fields.get("akcja") == "usun":
                    store.delete_documents([document_id])
                    return self._back(stored.catalog_id)
                if fields.get("akcja") == "wektory":
                    self.start_vectors(document_id, store.text(document_id))
                    return Response(
                        "",
                        "303 See Other",
                        headers=[("Location", f"{self.base}/dokument/{document_id}")],
                    )
                store.update_document(
                    document_id,
                    (fields.get("tytul") or "").strip()[:250] or stored.title,
                    fields.get("rola") if fields.get("rola") in ROLE_NAMES else stored.role,
                    _date(fields.get("data")),
                    _int(fields.get("katalog")),
                )
                return Response(
                    "",
                    "303 See Other",
                    headers=[("Location", f"{self.base}/dokument/{document_id}")],
                )
            text = store.text(document_id)
            vectors = store.vectors_count(document_id)
            catalogs = store.catalogs()
        page = self.document_page(stored, text, catalogs, vectors)
        return Response(layout(self.title, page))

    # --- HTML ---

    def catalog_page(self, catalog_id, note=""):
        with self.store() as store:
            catalogs = store.catalogs()
            known = {c[0] for c in catalogs}
            if catalog_id not in known:
                catalog_id = None
            documents = store.documents(catalog_id)
        paths = catalog_paths(catalogs)
        children = [c for c in catalogs if c[1] == catalog_id]
        crumbs = [f'<a href="{self.base}">Teczka</a>']
        for c_id in ancestry(catalogs, catalog_id):
            name = {c[0]: c[2] for c in catalogs}[c_id]
            crumbs.append(f'<a href="{self.base}?k={c_id}">{e(name)}</a>')
        k = catalog_id or ""
        sub = "".join(
            f'<li><a href="{self.base}?k={c_id}">📁 {e(name)}</a>'
            f" <span class=hint>{count_in(catalogs, c_id)}</span></li>"
            for c_id, _, name in children
        )
        rows = "".join(
            f"<tr><td><input type=checkbox name=d value={d.id} form=zaznaczone></td>"
            f'<td><a href="{self.base}/dokument/{d.id}">{e(d.title)}</a>'
            f"{f'<br><span class=hint>{e(d.note)}</span>' if d.note else ''}</td>"
            f"<td>{e(ROLE_NAMES.get(d.role, d.role))}</td><td>{e(d.doc_date or '')}</td>"
            f"<td class=num>{number(d.chars)}</td></tr>"
            for d in documents
        )
        targets = "".join(
            f"<option value={c_id}>{e(paths[c_id])}</option>"
            for c_id in sorted(paths, key=lambda c: paths[c])
        )
        here = paths.get(catalog_id, "teczka (bez katalogu)")
        table = (
            f"""<form id=zaznaczone method=post action="{self.base}/zaznaczone"></form>
<input type=hidden name=k value="{k}" form=zaznaczone>
<table class=library><tr><th></th><th>dokument</th><th>kto pisze</th><th>data</th><th>znaków</th></tr>{rows}</table>
<p class=library-actions>
<button name=akcja value=analizuj form=zaznaczone title="Analiza sprawy: kto komu odpowiedział, osie, zmiany; kolejność według dat">Analizuj zaznaczone</button>
<button name=akcja value=przenies form=zaznaczone class=secondary>Przenieś do</button>
<select name=cel form=zaznaczone><option value="">teczka (bez katalogu)</option>{targets}</select>
<button name=akcja value=usun form=zaznaczone class=danger onclick="return confirm('Usunąć zaznaczone dokumenty z teczki?')">Usuń</button></p>"""
            if documents
            else "<p class=hint>Brak dokumentów w tym miejscu.</p>"
        )
        manage = ""
        if catalog_id:
            current = {c[0]: c[2] for c in catalogs}[catalog_id]
            manage = f"""<details class=library-manage><summary>Katalog: zmień nazwę albo usuń</summary>
<form method=post action="{self.base}/katalog/{catalog_id}/nazwa"><input name=nazwa value="{e(current)}"> <button class=secondary>Zmień nazwę</button></form>
<form method=post action="{self.base}/katalog/{catalog_id}/usun"><button class=danger>Usuń katalog</button> <span class=hint>tylko pusty</span></form></details>"""
        notes = {
            "niepusty": "Katalog nie jest pusty: najpierw przenieś albo usuń jego dokumenty i podkatalogi.",
            "brak": "Nie zaznaczono żadnego dokumentu.",
            "malo": "Do analizy sprawy zaznacz co najmniej dwa dokumenty.",
        }
        message = f"<p class=error>{e(notes[note])}</p>" if note in notes else ""
        return f"""<h1>{e(self.title)}</h1>
<p class=hint>Dokumenty sprawy zapisane na tym komputerze (baza MariaDB <code>teczki</code>, poza gitem
i poza kopią na dysk). Każdy plik czytany jest raz (także OCR skanu); w Analizie dokumentu,
Porównaniu, Analizie sprawy i Badaniu wybierzesz go z listy „albo z teczki”. Zaznaczone dokumenty
przeanalizujesz razem przyciskiem „Analizuj zaznaczone” (w kolejności dat).</p>
{message}
<p class=crumbs>{" / ".join(crumbs)}</p>
{f"<ul class=catalogs>{sub}</ul>" if sub else ""}
<form class="analysis library-add" method=post action="{self.base}/dodaj" enctype="multipart/form-data">
<input type=hidden name=k value="{k}">
<fieldset><legend>Dodaj do: {e(here)}</legend>
{file_field("pliki", FILE_TYPES, multiple=True, auto=True)}
<details><summary>albo wklej tekst</summary>
<p><label>Tytuł <input name=tytul placeholder="np. notatka ze spotkania"></label></p>
<p><textarea name=tekst rows=6></textarea></p></details>
<p><button type=submit>Dodaj do teczki</button></p></fieldset></form>
{table}
<form class=library-new method=post action="{self.base}/katalog"><input type=hidden name=k value="{k}">
<input name=nazwa placeholder="nazwa nowego katalogu"> <button class=secondary>Utwórz katalog</button></form>
{manage}"""

    def document_page(self, stored, text, catalogs, vectors=0):
        paths = catalog_paths(catalogs)
        roles = "".join(
            f"<option value={key}{' selected' if key == stored.role else ''}>{e(name)}</option>"
            for key, name in ROLES
        )
        targets = "".join(
            f"<option value={c_id}{' selected' if c_id == stored.catalog_id else ''}>"
            f"{e(paths[c_id])}</option>"
            for c_id in sorted(paths, key=lambda c: paths[c])
        )
        back = f"{self.base}?k={stored.catalog_id}" if stored.catalog_id else self.base
        original = (
            f' <a class=button href="{self.base}/dokument/{stored.id}/plik">Oryginał'
            f" ({e(stored.filename)})</a>"
            if stored.size
            else ""
        )
        refresh = "<meta http-equiv=refresh content=3>" if stored.id in self.computing else ""
        return f"""{refresh}<h1>{e(stored.title)}</h1>
<p class=crumbs><a href="{back}">← {e(paths.get(stored.catalog_id, "Teczka"))}</a></p>
<p class=doc-tools translate=no>{original}
<a class=button href="/badanie?teczka={stored.id}">Badanie</a>
<a class=button href="/analiza?teczka={stored.id}">Analiza dokumentu</a></p>
<form class=analysis method=post action="{self.base}/dokument/{stored.id}">
<fieldset><legend>Opis</legend>
<p><label>Tytuł <input name=tytul value="{e(stored.title)}"></label></p>
<p><label>Kto pisze <select name=rola>{roles}</select></label>
<label>Data dokumentu <input type=date name=data value="{e(stored.doc_date or "")}"></label>
<label>Katalog <select name=katalog><option value="">teczka (bez katalogu)</option>{targets}</select></label></p>
<p><button type=submit>Zapisz</button>
<button name=akcja value=wektory class=secondary title="Wektory fragmentów do analiz (liczone raz, potem brane z bazy)">Policz wektory</button>
<button name=akcja value=usun class=danger onclick="return confirm('Usunąć dokument z teczki?')">Usuń dokument</button></p>
</fieldset></form>
<p class=hint>Plik: {e(stored.filename or "tekst wklejony")}{f" · {e(stored.note)}" if stored.note else ""}
· {number(stored.chars)} znaków · wektory: {self.vectors_note(stored.id, vectors)}
· dodano {e(stored.created_at)} UTC. Tekst poniżej to to, co
analizują pozostałe zakładki (sprawdź OCR: sygnatury, daty, kwoty).</p>
<div class="text library-text">{e(text)}</div>"""


# --- pomocnicze ---------------------------------------------------------------


def label(stored):
    """The name of a stored document in reports and lists: title, date, OCR note."""
    when = f" ({stored.doc_date})" if stored.doc_date else ""
    note = f" [{stored.note}]" if stored.note else ""
    return f"{stored.title}{when}{note}"


def title_of(filename):
    """A file name without its extension, underscores as spaces: the default title."""
    stem = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem = stem.rsplit(".", 1)[0] if "." in stem else stem
    return " ".join(stem.replace("_", " ").split())[:200] or "dokument"


def catalog_paths(catalogs):
    """{id: "A / B / C"} for the catalog tree [(id, parent_id, name)]."""
    by_id = {c[0]: c for c in catalogs}
    paths = {}
    for c_id in by_id:
        parts, seen, current = [], set(), c_id
        while current in by_id and current not in seen:
            seen.add(current)
            parts.append(by_id[current][2])
            current = by_id[current][1]
        paths[c_id] = " / ".join(reversed(parts))
    return paths


def ancestry(catalogs, catalog_id):
    """Ids from the top catalog down to catalog_id (empty for the top level)."""
    parents = {c[0]: c[1] for c in catalogs}
    chain, seen = [], set()
    while catalog_id in parents and catalog_id not in seen:
        seen.add(catalog_id)
        chain.append(catalog_id)
        catalog_id = parents[catalog_id]
    return list(reversed(chain))


def count_in(catalogs, catalog_id):
    subs = sum(1 for c in catalogs if c[1] == catalog_id)
    return f"{subs} podkatalog." if subs else ""


def _maybe_scan(name):
    return name.lower().endswith((".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"))


def _int(value):
    value = str(value or "").strip()
    return int(value) if value.isdigit() else None


def _date(value):
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def _content_type(name):
    types = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".txt": "text/plain; charset=utf-8",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }
    return types.get("." + name.lower().rsplit(".", 1)[-1], "application/octet-stream")


def _ascii(name):
    """A file name safe in a header (Content-Disposition)."""
    return re.sub(r"[^A-Za-z0-9._ -]", "_", name) or "dokument"


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _pack(vector):
    """A vector as float32 bytes (4 KB for 1024 dimensions)."""
    return array("f", (float(x) for x in vector)).tobytes()


def _unpack(data):
    values = array("f")
    values.frombytes(data)
    return values.tolist()


def _vector_step(job, name, done, total):
    job.step = f"{name}: wektory {done} z {total} fragmentów"
