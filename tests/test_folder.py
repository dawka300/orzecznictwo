"""The case folder (several documents of one case) with its psychological layer and axes,
without a model or a database."""

import hashlib
import io
import time

from judgments_core import axes, folder, psyche
from judgments_core.settings import Settings
from judgments_core.web import Request

COMPLAINT = """1) Zarzucam naruszenie art. 74a p.p.s.a. przez uznanie, że doręczenie przez ePUAP było skuteczne, choć nie wybrałem drogi elektronicznej.

2) Zarzucam naruszenie art. 45 Konstytucji RP przez pozbawienie mnie prawa do sądu. Boję się, że stracę wszystko, urząd zawsze tak postępuje i nigdy nikt mnie nie słucha."""

ANSWER = """Organ wnosi o oddalenie skargi. Doręczenie przez ePUAP było skuteczne, ponieważ strona wcześniej wniosła pismo elektronicznie w tej sprawie i należy to uznać za wybór tej drogi.

Termin do wniesienia skargi upłynął, dlatego skarga powinna zostać odrzucona zgodnie z przepisami ustawy."""

JUDGMENT = """Skarżący zarzucił naruszenie art. 74a p.p.s.a. oraz art. 45 Konstytucji.

Wojewódzki Sąd Administracyjny zważył, co następuje:

Doręczenie przez ePUAP było skuteczne, bo strona wniosła pismo elektronicznie w tej sprawie, a organ prawidłowo ustalił datę doręczenia.
Termin do wniesienia skargi upłynął przed jej złożeniem, dlatego skargę odrzucono na podstawie przepisów."""

KEYWORDS = (
    ("epuap", "doręcz"),
    ("konstytuc",),
    ("boję", "lęk", "strach", "obaw", "przeraż"),
    ("zawsze", "nigdy", "wszyscy", "każd"),
    ("termin", "upłyn"),
    ("wściek", "oburz", "skandal"),
)


class KeywordEmbedder:
    """Fake model: keyword hits plus a little deterministic noise per text."""

    def embed(self, texts):
        out = []
        for t in texts:
            low = t.lower()
            hits = [float(any(k in low for k in group)) for group in KEYWORDS]
            digest = hashlib.md5(t.encode()).digest()
            out.append(hits + [b / 2550 for b in digest[:4]] + [0.1])
        return out


def form(fields, path="/sprawa"):
    boundary = "B"
    body = (
        b"".join(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
            for k, v in fields.items()
        )
        + f"--{boundary}--\r\n".encode()
    )
    return Request(
        {
            "PATH_INFO": path,
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": f"multipart/form-data; boundary={boundary}",
            "CONTENT_LENGTH": str(len(body)),
            "wsgi.input": io.BytesIO(body),
        }
    )


def test_court_units_split_the_reasoning():
    parts, own = folder.court_units(JUDGMENT)
    assert own and all(parts[k] for k in own)
    assert all(
        "zarzucił" not in parts[k] for k in own
    )  # streszczenie przed "zważył" poza rozważaniami
    parts, own = folder.court_units(ANSWER)  # bez znacznika: całość
    assert own == list(range(len(parts)))


def test_style_counts_grammar():
    s = psyche.style(
        "Muszę to zrobić, bo należy. Ja wiem, że mogę. Uznaje się, że wniesiono pismo."
    )
    assert s.sentences == 3 and s.necessity > 0 and s.possibility > 0
    assert s.first_person > 0 and s.impersonal > 0 and 0 < s.self_focus < 1
    assert s.subordinate_per_sentence > 0


def test_axes_and_categories_from_examples():
    model = KeywordEmbedder()
    built = axes.build(model)
    assert [a.key for a in built] == list(axes.AXES)
    vectors = model.embed(["Boję się, że stracę dom.", "Termin upłynął w poniedziałek."])
    assert set(axes.scores(vectors, built)[0]) == set(axes.AXES)
    neutral, categories = psyche.build(model)
    scored = psyche.scores(vectors, neutral, categories)
    # zdanie o strachu bliżej kategorii „strach” niż rzeczowe zdanie o terminie
    assert scored[0]["strach"] > scored[1]["strach"]
    cuts = psyche.thresholds(scored, categories, share=0.5)
    assert cuts["strach"] == max(scored[0]["strach"], 0.0)


def test_folder_report(monkeypatch):
    monkeypatch.setattr(folder, "choose_embedder", lambda settings, own: (own, "test"))
    pages = folder.CaseFolder(Settings(), KeywordEmbedder())
    layout = lambda title, body: body
    response = pages.handle(
        form(
            {
                "dok1_rola": "skarzacy",
                "dok1_nazwa": "skarga",
                "dok1_tekst": COMPLAINT,
                "dok2_rola": "organ",
                "dok2_nazwa": "odpowiedź na skargę",
                "dok2_tekst": ANSWER,
                "dok3_rola": "sad",
                "dok3_nazwa": "wyrok WSA",
                "dok3_tekst": JUDGMENT,
            }
        ),
        layout,
    )
    assert response.status == "303 See Other"
    path = dict(response.headers)["Location"]
    job = pages.get(path.rsplit("/", 1)[1])
    for _ in range(200):
        if job.status != "liczy":
            break
        time.sleep(0.05)
    assert job.status == "gotowe", job.error
    assert [d.role for d in job.documents] == ["skarzacy", "organ", "sad"]
    # pary różnych stron: odpowiedź <- skarga, wyrok <- skarga, wyrok <- odpowiedź
    assert [(a.target, a.source) for a in job.answers] == [(2, 1), (3, 1), (3, 2)]
    wyrok_skarga = next(a for a in job.answers if (a.target, a.source) == (3, 1))
    statuses = [m[0] for m in wyrok_skarga.matches]
    assert statuses[0] == "odniesiono"  # zarzut o ePUAP ma odpowiedź w rozważaniach
    # analiza sprawy jest prawna: bez miar psychologicznych (te liczy tylko Badanie)
    assert job.steps == [] and job.axes == [] and job.documents[0].markers is None
    page = pages.handle(Request({"PATH_INFO": path}), layout).body.decode()
    assert "Kto komu odpowiedział" in page
    for title in (
        "Warstwa psychologiczna",
        "Zniekształcenia poznawcze",
        "Styl poznawczy",
        "Osie znaczeniowe w czasie",
        "Zmiany między kolejnymi dokumentami",
    ):
        assert title not in page
    assert f'href="/sprawa/{job.id}.pdf"' in page


def test_research_keeps_the_psychological_measures(monkeypatch):
    from judgments_core import research

    monkeypatch.setattr(folder, "choose_embedder", lambda settings, own: (own, "test"))
    pages = research.Research(Settings(), KeywordEmbedder())
    sections = tuple(research.RESEARCH_SECTIONS)
    job = pages.start([folder.Document(1, "skarga", "skarzacy", "skarga", COMPLAINT)], sections)
    for _ in range(200):
        if job.status != "liczy":
            break
        time.sleep(0.05)
    assert job.status == "gotowe", job.error
    assert job.documents[0].marked["strach"]  # akapit ze „boję się” wyróżniony
    page = pages.report(job)
    for title in ("Warstwa psychologiczna", "Styl poznawczy", "Osie znaczeniowe w czasie"):
        assert title in page
    assert page.count("<svg class=axis-chart") == len(axes.AXES)


def test_one_document_is_refused():
    pages = folder.CaseFolder(Settings(), KeywordEmbedder())
    response = pages.handle(form({"dok1_tekst": COMPLAINT}), lambda title, body: body)
    assert response.status == "400 Bad Request" and "co najmniej 2" in response.body.decode()


def test_research_one_text_chosen_sections_and_csv(monkeypatch):
    from judgments_core import research

    monkeypatch.setattr(folder, "choose_embedder", lambda settings, own: (own, "test"))
    pages = research.Research(Settings(), KeywordEmbedder())
    layout = lambda title, body: body
    request = form({"sekcja_psychologia": "1", "dok1_tekst": COMPLAINT}, "/badanie")
    response = pages.handle(request, layout)
    assert response.status == "303 See Other"
    path = dict(response.headers)["Location"]
    assert path.startswith("/badanie/")
    job = pages.get(path.rsplit("/", 1)[1])
    for _ in range(200):
        if job.status != "liczy":
            break
        time.sleep(0.05)
    assert job.status == "gotowe", job.error
    page = pages.handle(Request({"PATH_INFO": path}), layout).body.decode()
    assert "Warstwa psychologiczna" in page and "Osie znaczeniowe w czasie" not in page
    assert f'href="/badanie/{job.id}.csv"' in page
    paragraphs = pages.handle(Request({"PATH_INFO": f"{path}.csv"}), layout)
    assert paragraphs.content_type.startswith("text/csv")
    lines = paragraphs.body.decode("utf-8-sig").splitlines()
    assert lines[0].startswith("dokument,nazwa,rola,akapit") and "psy_strach" in lines[0]
    assert len(lines) == 1 + len(job.documents[0].units)
    documents = pages.handle(Request({"PATH_INFO": f"{path}-dokumenty.csv"}), layout)
    rows = documents.body.decode("utf-8-sig").splitlines()
    assert len(rows) == 2 and "slow_na_zdanie" in rows[0] and "wyrazne_strach_liczba" in rows[0]


def test_research_needs_a_section():
    from judgments_core import research

    pages = research.Research(Settings(), KeywordEmbedder())
    request = form({"dok1_tekst": COMPLAINT}, "/badanie")
    response = pages.handle(request, lambda title, body: body)
    assert response.status == "400 Bad Request" and "sekcję" in response.body.decode()


def test_research_asks_claude_and_saves(monkeypatch, tmp_path):
    from judgments_core import claude_report, research

    monkeypatch.setattr(folder, "choose_embedder", lambda settings, own: (own, "test"))
    monkeypatch.setattr(claude_report, "available", lambda settings: (True, ""))
    monkeypatch.setattr(claude_report, "SAVE_DIR", tmp_path)
    sent = {}

    def ask(settings, system, prompt):
        sent["system"], sent["prompt"] = system, prompt
        return "## Ocena trafności\n- strach w akapicie 2 trafny", {"input": 5000, "output": 800}

    monkeypatch.setattr(claude_report, "ask", ask)
    pages = research.Research(Settings(), KeywordEmbedder())
    layout = lambda title, body: body
    fields = {"sekcja_psychologia": "1", "dok1_tekst": COMPLAINT}
    path = dict(pages.handle(form(fields, "/badanie"), layout).headers)["Location"]
    job = pages.get(path.rsplit("/", 1)[1])
    for _ in range(200):
        if job.status != "liczy":
            break
        time.sleep(0.05)
    assert job.status == "gotowe", job.error
    assert "Zapytaj Claude" in pages.handle(Request({"PATH_INFO": path}), layout).body.decode()
    response = pages.handle(form({"tekst": "1"}, f"{path}/claude"), layout)
    assert response.status == "303 See Other"
    for _ in range(200):
        if job.claude_status not in ("", "pisze"):
            break
        time.sleep(0.05)
    assert job.claude_status == "gotowe", job.claude_error
    assert "nie diagnozujesz" in sent["system"] and "Metody" in sent["prompt"]
    assert "## Pełne teksty" in sent["prompt"] and "Boję się" in sent["prompt"]
    page = pages.handle(Request({"PATH_INFO": path}), layout).body.decode()
    assert "Ocena Claude" in page and "<h3>Ocena trafności</h3>" in page and "$0.04" in page
    (saved,) = (tmp_path / "badanie").iterdir()
    names = sorted(p.name for p in saved.iterdir())
    assert names == ["akapity.csv", "claude.md", "dokumenty.csv", "meta.json", "wyslane.md"]
    assert '"pelne_teksty": true' in (saved / "meta.json").read_text(encoding="utf-8")
    # status na górze, ZIP z całością (raport PDF podstawiony - bez przeglądarki)
    assert page.index("Ocena Claude gotowa") < page.index("Warstwa psychologiczna")
    monkeypatch.setattr(pages, "pdf_bytes", lambda job, layout, base: b"%PDF-test")
    archive = pages.handle(Request({"PATH_INFO": f"{path}.zip"}), layout)
    import zipfile as _zip

    names = sorted(_zip.ZipFile(io.BytesIO(archive.body)).namelist())
    assert names == [
        "akapity.csv",
        "claude.md",
        "dokumenty.csv",
        "meta.json",
        "raport.pdf",
        "wyslane.md",
    ]


def test_research_form_has_one_text_without_role_and_name():
    from judgments_core import research

    page = research.Research(Settings(), KeywordEmbedder()).form()
    assert "dok1_plik" in page and "dok2_plik" not in page
    assert "Kto pisze" not in page and "dok1_nazwa" not in page
    assert "sekcja_odpowiedzi" not in page and "sekcja_zmiany" not in page
    assert "class=drop" in page


def test_case_folder_form_keeps_role_name_and_drop():
    page = folder.CaseFolder(Settings(), KeywordEmbedder()).form()
    assert "Kto pisze" in page and "dok6_plik" in page and page.count("class=drop") == 6


def test_layout_adds_drop_script_only_with_a_file_field():
    from judgments_core.web import file_field, layout

    with_file = layout("t", file_field("plik", ".pdf"), "Orzecznictwo")
    assert "<script>" in with_file and "dataTransfer" in with_file
    assert "<script>" not in layout("t", "<p>bez pliku</p>", "Orzecznictwo")
