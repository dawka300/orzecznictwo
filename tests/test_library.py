"""The case folder (judgments_core/library.py): documents stored once, nested catalogs,
the choice "albo z teczki" in the other tabs. Runs on the scratch database."""

import io
import time

import pytest
from conftest import TEST_DSN
from test_folder import ANSWER, COMPLAINT, KeywordEmbedder

from judgments_core import folder, library, research
from judgments_core.analysis import form_document
from judgments_core.settings import Settings
from judgments_core.web import Request


def post(path, fields=(), files=()):
    """A multipart POST; fields and files as lists of pairs (names may repeat)."""
    boundary = "B"
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        for k, v in fields
    ]
    parts += [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n".encode()
        + data
        + b"\r\n"
        for k, name, data in files
    ]
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    return Request(
        {
            "PATH_INFO": path,
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": f"multipart/form-data; boundary={boundary}",
            "CONTENT_LENGTH": str(len(body)),
            "wsgi.input": io.BytesIO(body),
        }
    )


def get(path, query=""):
    return Request({"PATH_INFO": path, "QUERY_STRING": query})


def page(title, body):
    return body


def make(conn, case_folder=None):
    return library.Library(Settings(library_dsn=TEST_DSN), case_folder)


def finish(pages, response):
    job_id = dict(response.headers)["Location"].rsplit("/", 1)[1]
    job = pages.jobs[job_id]
    for _ in range(200):
        if job.status == "gotowe":
            break
        time.sleep(0.02)
    return job


def test_import_stores_the_text_once_and_skips_the_same_file(conn):
    pages = make(conn)
    files = [
        ("pliki", "skarga_do_WSA.txt", COMPLAINT.encode()),
        ("pliki", "odpowiedz.txt", ANSWER.encode()),
    ]
    response = pages.handle(post("/teczka/dodaj", files=files), page)
    assert response.status == "303 See Other"
    job = finish(pages, response)
    assert [title for _, title, _ in job.added] == ["skarga do WSA", "odpowiedz"]
    assert not job.files  # bajty plików nie zostają w pamięci
    with pages.store() as store:
        (first, _) = store.documents()
        assert store.text(first.id).startswith("1) Zarzucam") and first.size > 0
        assert store.original(first.id) == COMPLAINT.encode()
    again = finish(pages, pages.handle(post("/teczka/dodaj", files=files[:1]), page))
    assert not again.added and "już jest w bazie" in again.skipped[0][1]
    listing = pages.handle(get("/teczka"), page).body.decode()
    assert "skarga do WSA" in listing and "Analizuj zaznaczone" in listing


def test_nested_catalogs_paths_and_a_full_catalog_is_not_deleted(conn):
    pages = make(conn)
    pages.handle(post("/teczka/katalog", [("nazwa", "Egzamin radcowski")]), page)
    with pages.store() as store:
        (top,) = store.catalogs()
    pages.handle(post("/teczka/katalog", [("k", top[0]), ("nazwa", "WSA")]), page)
    with pages.store() as store:
        catalogs = store.catalogs()
    paths = library.catalog_paths(catalogs)
    child = next(c for c in catalogs if c[1] == top[0])
    assert paths[child[0]] == "Egzamin radcowski / WSA"
    finish(
        pages,
        pages.handle(
            post("/teczka/dodaj", [("k", child[0])], [("pliki", "w.txt", COMPLAINT.encode())]), page
        ),
    )
    body = pages.handle(get("/teczka", f"k={child[0]}"), page).body.decode()
    assert "Egzamin radcowski</a> / <a" in body and ">w</a>" in body
    refused = pages.handle(post(f"/teczka/katalog/{top[0]}/usun"), page)
    assert "uwaga=niepusty" in dict(refused.headers)["Location"]
    with pages.store() as store:
        assert len(store.catalogs()) == 2


def test_other_tabs_pick_a_stored_document_without_reading_the_file(conn):
    pages = make(conn)
    finish(
        pages,
        pages.handle(
            post("/teczka/dodaj", files=[("pliki", "skarga.txt", COMPLAINT.encode())]), page
        ),
    )
    with pages.store() as store:
        (stored,) = store.documents()
    choice = pages.choices_html("plik_teczka", stored.id)
    assert f"<option value={stored.id} selected>skarga</option>" in choice
    assert "teczka (bez katalogu)" in choice
    name, text, stored_id = form_document(
        {"plik_teczka": str(stored.id)}, "plik", "tekst", "pismo", pages
    )
    assert stored_id == stored.id
    assert name == "skarga" and text.startswith("1) Zarzucam")
    form = research.Research(Settings(), KeywordEmbedder())
    form.library = pages
    assert "dok1_plik_teczka" in form.form(stored.id) and " selected>skarga" in form.form(stored.id)


def test_without_documents_or_database_there_is_no_choice(conn):
    assert make(conn).choices_html("plik_teczka") == ""
    assert library.Library(Settings()).choices_html("plik_teczka") == ""


def test_chosen_documents_go_to_the_case_analysis_by_date(conn, monkeypatch):
    monkeypatch.setattr(folder, "choose_embedder", lambda settings, own: (own, "test"))
    case = folder.CaseFolder(Settings(), KeywordEmbedder())
    pages = make(conn, case)
    files = [
        ("pliki", "odpowiedz.txt", ANSWER.encode()),
        ("pliki", "skarga.txt", COMPLAINT.encode()),
    ]
    finish(pages, pages.handle(post("/teczka/dodaj", files=files), page))
    with pages.store() as store:
        answer, complaint = store.documents()
    for doc, role, when in ((complaint, "skarzacy", "2026-01-10"), (answer, "organ", "2026-02-01")):
        pages.handle(
            post(
                f"/teczka/dokument/{doc.id}", [("tytul", doc.title), ("rola", role), ("data", when)]
            ),
            page,
        )
    chosen = [("akcja", "analizuj"), ("d", answer.id), ("d", complaint.id)]
    response = pages.handle(post("/teczka/zaznaczone", chosen), page)
    location = dict(response.headers)["Location"]
    assert location.startswith("/sprawa/")
    job = case.get(location.rsplit("/", 1)[1])
    assert [d.label for d in job.documents] == ["skarga", "odpowiedz"]  # według dat
    assert [d.role for d in job.documents] == ["skarzacy", "organ"]
    one = pages.handle(post("/teczka/zaznaczone", [("akcja", "analizuj"), ("d", answer.id)]), page)
    assert "uwaga=malo" in dict(one.headers)["Location"]


def test_move_and_delete_chosen_documents(conn):
    pages = make(conn)
    pages.handle(post("/teczka/katalog", [("nazwa", "Sprawa")]), page)
    finish(
        pages,
        pages.handle(post("/teczka/dodaj", files=[("pliki", "a.txt", COMPLAINT.encode())]), page),
    )
    with pages.store() as store:
        (catalog,) = store.catalogs()
        (doc,) = store.documents()
    pages.handle(
        post("/teczka/zaznaczone", [("akcja", "przenies"), ("cel", catalog[0]), ("d", doc.id)]),
        page,
    )
    with pages.store() as store:
        assert store.documents() == [] and len(store.documents(catalog[0])) == 1
    pages.handle(
        post("/teczka/zaznaczone", [("akcja", "usun"), ("k", catalog[0]), ("d", doc.id)]), page
    )
    with pages.store() as store:
        assert store.documents(everywhere=True) == [] and store.original(doc.id) == b""


class CountingEmbedder(KeywordEmbedder):
    def __init__(self):
        self.texts = 0

    def embed(self, texts):
        self.texts += len(texts)
        return super().embed(texts)


def test_import_stores_vectors_and_analyses_reuse_them(conn):
    model = CountingEmbedder()
    pages = library.Library(Settings(library_dsn=TEST_DSN), semantic_embedder=model)
    files = [("pliki", "skarga.txt", COMPLAINT.encode())]
    finish(pages, pages.handle(post("/teczka/dodaj", files=files), page))
    with pages.store() as store:
        (stored,) = store.documents()
        assert store.vectors_count(stored.id) == len(pages.fragments(COMPLAINT)) > 0
    computed = model.texts
    cached = pages.embedder_for(model, owner=stored.id)
    paragraphs = folder.units(COMPLAINT)
    expected = [model.embed([p])[0] for p in paragraphs]
    for got, want in zip(cached.embed(paragraphs), expected):
        assert got == pytest.approx(want, abs=1e-6)  # float32 z bazy
    assert model.texts == computed + len(paragraphs)  # tylko porównanie wyżej liczyło
    new = cached.embed(["Zupełnie nowy akapit, którego nie było przy imporcie dokumentu."])
    assert len(new) == 1
    with pages.store() as store:
        assert store.vectors_count(stored.id) == len(pages.fragments(COMPLAINT)) + 1
        store.delete_documents([stored.id])
        assert store.vectors_lookup(pages.settings.embed_model, {library._sha(paragraphs[0])}) == {}


def test_ad_hoc_texts_are_not_saved(conn):
    pages = make(conn)
    model = CountingEmbedder()
    assert pages.embedder_for(model) is model  # bez dokumentu z teczki: zwykły model
