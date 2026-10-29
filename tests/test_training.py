"""Training documents (judgments_core/training.py): only with APP_ENV=dev, their own
database, document kinds and the JSON Lines export. Runs on the scratch database."""

import json

from conftest import TEST_DSN
from test_folder import COMPLAINT
from test_library import CountingEmbedder, finish, get, page, post

from judgments_core import training
from judgments_core.settings import Settings
from judgments_core.web import Response, WebApp


def make(app_env="dev", embedder=None):
    settings = Settings(training_dsn=TEST_DSN, app_env=app_env)
    return training.TrainingLibrary(settings, semantic_embedder=embedder)


def test_hidden_outside_dev(conn):
    response = make("prod").handle(get("/trening"), page)
    assert response.status == "404 Not Found"


def test_import_kind_and_export(conn):
    pages = make(embedder=CountingEmbedder())
    pages.handle(post("/trening/katalog", [("nazwa", "Skargi WSA")]), page)
    with pages.store() as store:
        ((catalog_id, _, _),) = store.catalogs()
    files = [("pliki", "skarga.txt", COMPLAINT.encode())]
    job = finish(pages, pages.handle(post("/trening/dodaj", [("k", catalog_id)], files), page))
    ((doc_id, _, _),) = job.added
    with pages.store() as store:
        assert store.vectors_count(doc_id) == len(pages.fragments(COMPLAINT)) > 0
    pages.handle(
        post(
            f"/trening/dokument/{doc_id}",
            [("tytul", "skarga"), ("rola", "skarga"), ("katalog", catalog_id)],
        ),
        page,
    )
    listing = pages.handle(get("/trening", f"k={catalog_id}"), page).body.decode()
    assert "<td>skarga</td>" in listing and "Analizuj zaznaczone" not in listing
    assert "eksport.jsonl?k=" in listing
    exported = pages.handle(get("/trening/eksport.jsonl", f"k={catalog_id}"), page)
    (line,) = exported.body.decode().splitlines()
    row = json.loads(line)
    assert row["rodzaj"] == "skarga" and row["katalog"] == "Skargi WSA"
    assert row["tekst"].startswith("1) Zarzucam")
    assert pages.handle(get("/trening/eksport.jsonl", "k=999"), page).body == b""


class _App(WebApp):
    def route(self, request):
        return Response("<header><nav><a href=/>Szukaj</a></nav></header>")


def call(app):
    environ = {"PATH_INFO": "/", "REQUEST_METHOD": "GET"}
    return b"".join(app(environ, lambda status, headers: None)).decode()


def test_training_tab_in_the_bar_only_in_dev():
    assert 'href="/trening"' in call(_App(Settings(app_env="dev")))
    assert "trening" not in call(_App(Settings(app_env="prod")))


def test_settings_default_to_the_trening_database():
    settings = Settings.from_env("etpc", environ={}, dotenv_path="/nie-ma")
    assert settings.training_dsn.endswith("/trening")


def test_kind_and_remarks_when_adding_and_on_the_document_page(conn):
    pages = make(embedder=CountingEmbedder())
    form = pages.handle(get("/trening"), page).body.decode()
    assert "name=uwagi" in form and "name=rola" in form
    assert "requestSubmit" not in form  # z uwagami: dodawanie przyciskiem, nie od razu
    remarks = "skarga do SKO; przy porównaniu i badaniu pominąć pierwszą część"
    files = [("pliki", "skarga.txt", COMPLAINT.encode())]
    fields = [("rola", "skarga"), ("uwagi", f"  {remarks}  ")]
    job = finish(pages, pages.handle(post("/trening/dodaj", fields, files), page))
    ((doc_id, _, _),) = job.added
    with pages.store() as store:
        stored = store.document(doc_id)
    assert stored.role == "skarga" and stored.remarks == remarks
    assert remarks in pages.handle(get("/trening"), page).body.decode()  # na liście
    pages.handle(
        post(
            f"/trening/dokument/{doc_id}",
            [("tytul", "skarga"), ("rola", "skarga"), ("uwagi", "inna")],
        ),
        page,
    )
    document = pages.handle(get(f"/trening/dokument/{doc_id}"), page).body.decode()
    assert ">inna</textarea>" in document
    (line,) = pages.handle(get("/trening/eksport.jsonl"), page).body.decode().splitlines()
    assert json.loads(line)["uwagi"] == "inna"


def test_case_folder_keeps_adding_at_once_without_remarks(conn):
    from judgments_core.library import Library

    teczka = Library(Settings(library_dsn=TEST_DSN))
    form = teczka.handle(get("/teczka"), page).body.decode()
    assert "name=uwagi" not in form and "requestSubmit" in form
