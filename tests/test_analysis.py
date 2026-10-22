"""Document analysis on the pages: reading documents, the form, the job and the report."""

import io
import time
import zipfile

import pytest
import requests

from etpc.rag import EtpcRetriever
from judgments_core import analysis
from judgments_core.rag import Fragment, Reference, Retrieval
from judgments_core.settings import Settings
from judgments_core.web import Request
from nsa.chunker import NsaChunker
from nsa.rag import NsaRetriever

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def docx(*paragraphs):
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr(
            "word/document.xml", f"<w:document {W}><w:body>{body}</w:body></w:document>"
        )
    return data.getvalue()


def test_documents_to_text():
    text = analysis.extract_text("Wyrok.DOCX", docx("Sentencja", "Uzasadnienie sądu"))
    assert text == "Sentencja\nUzasadnienie sądu"
    assert analysis.extract_text("a.txt", "zażółć\n\n\n12\nStrona 3 z 9\ntak".encode("cp1250")) == (
        "zażółć\n\ntak"  # numery stron z PDF wypadają, puste wiersze się skracają
    )
    with pytest.raises(analysis.DocumentError, match="docx"):
        analysis.extract_text("a.docx", b"to nie zip")
    with pytest.raises(analysis.DocumentError, match=".doc"):
        analysis.extract_text("stary.doc", b"")
    with pytest.raises(analysis.DocumentError, match="obsługiwane"):
        analysis.extract_text("obraz.png", b"")


def request(body, content_type, path="/analiza"):
    return Request(
        {
            "PATH_INFO": path,
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": content_type,
            "CONTENT_LENGTH": str(len(body)),
            "wsgi.input": io.BytesIO(body),
        }
    )


def multipart(fields, files):
    boundary = "XyZ"
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        for k, v in fields.items()
    ]
    for name, (filename, data) in files.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n".encode()
            + data
            + b"\r\n"
        )
    return b"".join(
        parts
    ) + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def test_form_with_file():
    body, content_type = multipart(
        {"strona": "organ", "tekst": ""}, {"plik": ("w.docx", docx("A"))}
    )
    fields = analysis.parse_form(request(body, content_type))
    assert fields["strona"] == "organ" and fields["plik"][0] == "w.docx"
    assert analysis.extract_text(*fields["plik"]) == "A"
    with pytest.raises(ValueError, match="za duży"):
        analysis.parse_form(request(body, content_type), limit=10)


def test_outcome_groups():
    nsa = NsaRetriever(None)
    assert nsa.outcome_group("Uchylono zaskarżoną decyzję") == "uwzglednione"
    assert nsa.outcome_group("*Oddalono skargę") == "nieuwzglednione"
    assert nsa.outcome_group("Uchylono zaskarżony wyrok i oddalono skargę") == "nieuwzglednione"
    assert nsa.outcome_group("Umorzono postępowanie") == "inne"
    etpc = EtpcRetriever(None)
    assert etpc.outcome_group("Violation of Art. 6-1; Remainder inadmissible") == "uwzglednione"
    assert etpc.outcome_group("No violation of Art. 8") == "nieuwzglednione"
    assert etpc.outcome_group("Inadmissible") == "nieuwzglednione"
    assert etpc.outcome_group("Struck out of the list") == "inne"


def test_lm_studio_off_uses_the_process_model(monkeypatch):
    def down(*args, **kwargs):
        raise requests.ConnectionError("odmowa")

    monkeypatch.setattr(analysis.requests, "get", down)
    own = object()
    embedder, how = analysis.choose_embedder(Settings(), own)
    assert embedder is own and "Python" in how


class FakeRetriever:
    def __init__(self):
        self.calls = []

    def like_text(self, cur, text, chunker, k, embedder=None, progress=None):
        self.calls.append((text, k, embedder))
        progress("wektory", 2, 2)
        progress("szukanie", 1, 2)
        sources = [
            Reference(
                1,
                11,
                "A v. Poland",
                "wyrok",
                "https://a",
                "ENG",
                [Fragment(0, 5, "Twój fragment 1 ~ THE LAW", "tekst")],
                "Violation of Art. 6",
            ),
            Reference(
                2, 12, "B v. Poland", "wyrok", "https://b", "ENG", [], "No violation of Art. 6"
            ),
        ]
        return Retrieval(sources, [11, 12], "znaczeniowe", "Twój tekst: 1 fragmentów:\n  1. tekst…")

    def outcome_group(self, result):
        return EtpcRetriever(None).outcome_group(result)


class FakeConnection:
    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


def test_upload_runs_in_background_and_reports_by_side(monkeypatch):
    monkeypatch.setattr(analysis, "lm_studio_has_model", lambda settings: False)
    retriever = FakeRetriever()
    pages = analysis.DocumentAnalysis(Settings(), retriever, NsaChunker(), FakeConnection, "model")
    layout = lambda title, body: body
    body, content_type = multipart({"strona": "skarzacy", "ile": "7", "tekst": "x " * 200}, {})
    response = pages.handle(request(body, content_type), layout)
    assert response.status == "303 See Other"
    job_path = dict(response.headers)["Location"]
    job = pages.get(job_path.rsplit("/", 1)[1])
    for _ in range(100):
        if job.status != "liczy":
            break
        time.sleep(0.05)
    assert job.status == "gotowe" and retriever.calls[0][1] == 7
    assert retriever.calls[0][2] == "model"  # bez LM Studio: model procesu
    page = pages.handle(Request({"PATH_INFO": job_path}), layout).body.decode()
    support = page.index("Na poparcie (1)")
    against = page.index("Przeciwne (1)")
    assert support < page.index("A v. Poland") < against < page.index("B v. Poland")
    assert "Twój fragment 1 ~ THE LAW" in page and 'href="/orzeczenie/11"' in page
    assert (job.phase, job.done, job.total) == ("szukanie", 1, 2)  # postęp z like_text
    assert f'href="/analiza/{job.id}.pdf"' in page

    # PDF: ten sam raport, linki do stron orzeczeń pełne (adres portalu), adres źródła widoczny
    seen = {}

    def fake_pdf(html):
        seen["html"] = html
        return b"%PDF-1.7 raport"

    monkeypatch.setattr(analysis, "html_to_pdf", fake_pdf)
    environ = {
        "PATH_INFO": f"{job_path}.pdf",
        "HTTP_HOST": "127.0.0.1:8080",
        "SCRIPT_NAME": "/etpc",
    }
    pdf = pages.handle(Request(environ), lambda title, body: f"<html>{body}</html>")
    assert pdf.content_type == "application/pdf" and pdf.body.startswith(b"%PDF")
    assert 'attachment; filename="analiza-tekst_wklejony.pdf"' in dict(pdf.headers).values()
    assert 'href="http://127.0.0.1:8080/etpc/orzeczenie/11"' in seen["html"]
    assert ">https://a</a>" in seen["html"] and "Pobierz PDF" not in seen["html"]


def test_progress_bar():
    job = analysis.Job("a" * 12, "x", "skarzacy", 10, 100)
    assert "width:0.0%" in analysis._progress_bar(job)
    analysis._advance(job, "wektory", 4, 8)
    assert "width:25.0%" in analysis._progress_bar(job) and "4 z 8" in job.step
    analysis._advance(job, "szukanie", 8, 8)
    assert "width:100.0%" in analysis._progress_bar(job)


def test_too_little_text_is_refused():
    pages = analysis.DocumentAnalysis(
        Settings(), FakeRetriever(), NsaChunker(), FakeConnection, None
    )
    body, content_type = multipart({"tekst": "krótko"}, {})
    response = pages.handle(request(body, content_type), lambda title, body: body)
    assert response.status == "400 Bad Request" and "za mało tekstu" in response.body.decode()


# --- warstwa 2: znaczniki językowe i raport Claude -----------------------------

from judgments_core import claude_report, markers


def test_markers_polish_and_english():
    pl = markers.analyse(
        "Oczywiście organ zawsze musi to zrobić. Zostało to ustalone, jednak skarżący "
        "uważa to za rażące. Bez wątpienia sąd się mylił."
    )
    by = {m.key: m for m in pl.markers}
    assert pl.lang == "pl" and by["pewnosc"].count == 2 and by["przymus"].count == 1
    assert by["pewnosc"].sentences[0].startswith("Oczywiście") and by["emocje"].found == {
        "rażące": 1
    }
    en = markers.analyse("The Court clearly held that the State must always comply, however late.")
    assert en.lang == "en" and {m.key for m in en.markers if m.count} >= {"pewnosc", "przymus"}


def test_claude_report_parts(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(claude_report, "read_dotenv", lambda path: {})
    ready, why = claude_report.available(Settings())
    assert not ready and "klucza" in why
    assert claude_report.available(Settings(claude_api_key="sk-test"))[0]
    prompt = claude_report.markers_prompt(markers.analyse("Oczywiście tak. " * 30), "pismo.docx")
    assert (
        "pismo.docx" in prompt and "Pewność deklarowana" in prompt and "„Oczywiście tak.”" in prompt
    )
    html = claude_report.to_html("## Wnioski\n- **słabe** miejsce\nTekst <b>")
    assert "<h3>Wnioski</h3>" in html and "<li><b>słabe</b> miejsce</li>" in html
    assert "&lt;b&gt;" in html  # treść modelu escapowana


def test_report_has_both_layers_and_claude(monkeypatch):
    monkeypatch.setattr(analysis, "lm_studio_has_model", lambda settings: False)
    monkeypatch.setattr(claude_report, "available", lambda settings: (True, ""))
    monkeypatch.setattr(claude_report, "report", lambda s, m, f: "## Założenia\n- teza bez dowodu")
    pages = analysis.DocumentAnalysis(
        Settings(), FakeRetriever(), NsaChunker(), FakeConnection, "m"
    )
    layout = lambda title, body: body
    text = "Oczywiście sąd musi uwzględnić skargę. " * 20
    body, content_type = multipart({"tekst": text, "claude": "1"}, {})
    path = dict(pages.handle(request(body, content_type), layout).headers)["Location"]
    job = pages.get(path.rsplit("/", 1)[1])
    for _ in range(100):
        if job.status != "liczy":
            break
        time.sleep(0.05)
    page = pages.handle(Request({"PATH_INFO": path}), layout).body.decode()
    assert job.claude and job.markers.lang == "pl"
    layer1, layer2 = page.index("Warstwa 1: orzeczenia"), page.index("Warstwa 2: język")
    assert layer1 < layer2 < page.index("Raport Claude") < page.index("<h3>Założenia</h3>")
    assert "Pewność deklarowana" in page
