"""Comparing a brief with a judgment (absence analysis), without a model or a database."""

import io
import time

from judgments_core import comparison
from judgments_core.settings import Settings
from judgments_core.web import Request

JUDGMENT = """Skarżąca zarzuciła naruszenie art. 74a p.p.s.a. oraz art. 45 Konstytucji.

Naczelny Sąd Administracyjny zważył, co następuje:

Doręczenie przez ePUAP było skuteczne, bo strona wniosła pismo elektronicznie w tej sprawie.
Termin do wniesienia skargi kasacyjnej upłynął przed jej złożeniem, dlatego skargę odrzucono."""

BRIEF = """1) art. 74a p.p.s.a. przez uznanie, że doręczenie przez ePUAP było skuteczne, choć strona nie wybrała drogi elektronicznej w tej sprawie.

2) art. 45 Konstytucji RP przez pozbawienie strony prawa do sądu i rozpoznania sprawy."""


class KeywordEmbedder:
    """Fake model: a vector of keyword hits - 'ePUAP'/'doręcz' vs 'Konstytuc'."""

    def embed(self, texts):
        return [
            [float("ePUAP" in t or "doręcz" in t.lower()), float("Konstytuc" in t), 0.1]
            for t in texts
        ]


def test_units_and_reasoning():
    parts = comparison.units(BRIEF)
    assert len(parts) == 2 and parts[1].startswith("2) art. 45")
    own, found = comparison.reasoning(JUDGMENT)
    assert found and own.startswith("Doręczenie przez ePUAP")
    assert "zarzuciła" not in own  # streszczenie zarzutów przed "zważył" pominięte
    assert comparison.reasoning("bez znacznika")[1] is False
    assert comparison.status_of(0.75) == "odniesiono"
    assert comparison.status_of(0.65) == "czesciowo"
    assert comparison.status_of(0.40) == "pominieto"


def form(fields):
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
            "PATH_INFO": "/porownanie",
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": f"multipart/form-data; boundary={boundary}",
            "CONTENT_LENGTH": str(len(body)),
            "wsgi.input": io.BytesIO(body),
        }
    )


def test_brief_against_judgment(monkeypatch):
    monkeypatch.setattr(comparison, "choose_embedder", lambda settings, own: (own, "test"))
    pages = comparison.DocumentComparison(Settings(), KeywordEmbedder())
    layout = lambda title, body: body
    response = pages.handle(
        form({"tekst_pisma": BRIEF, "tekst_orzeczenia": JUDGMENT, "rozwazania": "1"}), layout
    )
    assert response.status == "303 See Other"
    path = dict(response.headers)["Location"]
    job = pages.get(path.rsplit("/", 1)[1])
    for _ in range(100):
        if job.status != "liczy":
            break
        time.sleep(0.05)
    assert job.status == "gotowe", job.error
    statuses = {m.number: m.status for m in job.matches}
    # zarzut o ePUAP ma odpowiedź w rozważaniach, konstytucyjny - nie (był tylko w streszczeniu)
    assert statuses == {1: "odniesiono", 2: "pominieto"}
    page = pages.handle(Request({"PATH_INFO": path}), layout).body.decode()
    assert page.index("Pominięte przez sąd (1)") < page.index("Sąd się odniósł (1)")
    assert f'href="/porownanie/{job.id}.pdf"' in page


def test_missing_document_is_refused():
    pages = comparison.DocumentComparison(Settings(), KeywordEmbedder())
    response = pages.handle(form({"tekst_pisma": BRIEF}), lambda title, body: body)
    assert response.status == "400 Bad Request" and "orzeczenie" in response.body.decode()
