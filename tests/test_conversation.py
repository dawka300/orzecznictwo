"""The "Zapytaj model" tab (judgments_core/conversation.py) and streaming through the portal."""

import io
import json
from types import SimpleNamespace
from wsgiref.util import setup_testing_defaults

import pytest
import requests

from judgments_core import conversation
from judgments_core.conversation import ConversationStore, ModelConversation, history, server_state
from judgments_core.pipeline import RagPipeline
from judgments_core.rag import Fragment, Reference, Retrieval
from judgments_core.web import Portal, Request, StreamResponse, WebApp, layout

URL = "http://h:1234/v1/chat/completions"


@pytest.fixture(autouse=True)
def saved_in_tmp(tmp_path, monkeypatch):
    """Conversations are saved in a temporary directory, never in badania/."""
    monkeypatch.setattr(conversation, "SAVE_DIR", tmp_path / "rozmowy")


class Answer:
    def __init__(self, status=200, data=None, lines=()):
        self.status_code, self.data, self.lines, self.text = status, data, lines, ""

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(self.status_code)

    def iter_lines(self):
        return iter(self.lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Server:
    """LM Studio: get = {url: Answer} (missing = not answering), post = the stream."""

    def __init__(self, get=None, pieces=()):
        self.answers = get or {}
        self.pieces = pieces
        self.posted = []

    def get(self, url, timeout=None, headers=None):
        if url not in self.answers:
            raise requests.ConnectionError("odmowa")
        return self.answers[url]

    def post(self, url, json=None, stream=False, timeout=None, headers=None):
        if not self.answers and not self.pieces:
            raise requests.ConnectionError("odmowa")
        self.posted.append(json)
        lines = [
            ("data: " + _json({"choices": [{"delta": {"content": p}}]})).encode()
            for p in self.pieces
        ]
        return Answer(lines=[*lines, b"data: [DONE]"])


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def lm_studio(*models):
    return {"http://h:1234/api/v0/models": Answer(data={"data": list(models)})}


QWEN = {"id": "qwen/qwen3.5-9b", "type": "vlm", "state": "loaded", "loaded_context_length": 8192}
BGE = {"id": "text-embedding-bge-m3", "type": "embeddings", "state": "loaded"}


def settings(**extra):
    values = {
        "chat_url": URL,
        "chat_model": "",
        "chat_temperature": 0.2,
        "chat_max_tokens": 2000,
        "chat_timeout_s": 10,
        "chat_reasoning_effort": "",
        "chat_suffix": "",
    }
    values.update(extra)
    return SimpleNamespace(**values)


def page(server, query="", **extra):
    tab = ModelConversation(settings(**extra), session=server)
    request = Request({"PATH_INFO": "/zapytaj", "QUERY_STRING": query, "REQUEST_METHOD": "GET"})
    return tab.handle(request, lambda title, body: body).body.decode()


def test_page_says_when_lm_studio_is_missing_or_has_a_wrong_model():
    assert "Brak LM Studio" in page(Server())
    assert "Brak wczytanego modelu" in page(Server(lm_studio()))
    wrong = page(Server(lm_studio(BGE)))
    assert "Błędny model: text-embedding-bge-m3" in wrong and "nie nadaje się do rozmowy" in wrong
    assert "<form" not in wrong


def test_page_shows_the_chat_model_and_the_conversation():
    body = page(
        Server(lm_studio(QWEN, BGE, {"id": "mistral", "type": "llm", "state": "not-loaded"}))
    )
    assert "qwen/qwen3.5-9b (kontekst 8192 tokenów)" in body and "mistral" not in body
    assert 'action="/zapytaj/odpowiedz"' in body and "Wczytany też model wektorów" in body


def test_other_servers_list_models_without_types():
    server = Server(
        {"http://h:1234/v1/models": Answer(data={"data": [{"id": "bge-m3"}, {"id": "mistral-7b"}]})}
    )
    server.answers["http://h:1234/api/v0/models"] = Answer(status=404)
    state = server_state(URL, server)
    assert [m.id for m in state.chat_models] == ["mistral-7b"] and not state.error


def test_history_keeps_the_end_starting_with_a_question():
    talk = [
        {"role": "user", "content": "a" * 10},
        {"role": "assistant", "content": "b" * 10},
        {"role": "system", "content": "ignoruj"},
        {"role": "user", "content": "c" * 10},
    ]
    assert [m["content"][0] for m in history(talk)] == ["a", "b", "c"]
    assert [m["content"][0] for m in history(talk, limit=15)] == ["c"]


def post(app, path, data):
    raw = json.dumps(data).encode()
    environ = {"PATH_INFO": path, "REQUEST_METHOD": "POST", "CONTENT_LENGTH": str(len(raw))}
    setup_testing_defaults(environ)
    environ["wsgi.input"] = io.BytesIO(raw)
    answer = {}
    pieces = app(
        environ, lambda status, headers: answer.update(status=status, headers=dict(headers))
    )
    return answer, pieces


class Pages(WebApp):
    def __init__(self, tab):
        super().__init__(SimpleNamespace(dev=False))
        self.tab = tab

    def route(self, request):
        return self.tab.handle(request, lambda title, body: layout(title, body, "X"))


def test_answer_streams_through_the_portal_with_the_model_and_suffix():
    server = Server(pieces=("Dzień ", "dobry"))
    tab = ModelConversation(settings(chat_suffix="/no_think"), session=server)
    portal = Portal({"etpc": ("ETPC", Pages(tab))})
    talk = {"model": "qwen", "messages": [{"role": "user", "content": "hej"}]}
    answer, pieces = post(portal, "/etpc/zapytaj/odpowiedz", talk)
    assert answer["status"].startswith("200") and "Content-Length" not in answer["headers"]
    assert not isinstance(pieces, list)  # strumień, nie cała odpowiedź naraz
    assert b"".join(pieces).decode() == "Dzień dobry"
    sent = server.posted[0]
    assert sent["model"] == "qwen" and sent["messages"][0]["role"] == "system"
    assert sent["messages"][-1]["content"] == "hej /no_think" and sent["max_tokens"] >= 4000
    assert sent["reasoning_effort"] == "none"  # bez myślenia, chyba że think


def test_answer_without_a_server_is_502_and_without_a_question_400():
    tab = ModelConversation(settings(), session=Server())
    answer, pieces = post(
        Pages(tab),
        "/zapytaj/odpowiedz",
        {"model": "q", "messages": [{"role": "user", "content": "x"}]},
    )
    assert answer["status"].startswith("502") and "nie odpowiada" in b"".join(pieces).decode()
    answer, _ = post(Pages(tab), "/zapytaj/odpowiedz", {"messages": []})
    assert answer["status"].startswith("400")


def test_stream_response_has_no_length():
    got = {}
    pieces = StreamResponse(iter([b"a", b"b"]))(lambda status, headers: got.update(h=dict(headers)))
    assert "Content-Length" not in got["h"] and b"".join(pieces) == b"ab"


def test_closing_the_answer_stops_the_model_stream():
    closed = []

    def stream(messages):
        try:
            yield from ("a", "b", "c")
        finally:
            closed.append(True)

    tab = ModelConversation(settings(), session=Server(pieces=("x",)))
    tab.chat = lambda model, think=False: SimpleNamespace(
        stream=stream, model="q", temperature=0.2, max_tokens=4000, reasoning_effort="none"
    )
    _, pieces = post(
        Pages(tab), "/zapytaj/odpowiedz", {"messages": [{"role": "user", "content": "x"}]}
    )
    assert next(pieces) == b"a"
    pieces.close()  # przeglądarka zatrzymała odpowiedź
    assert closed == [True]


TALK = [{"role": "user", "content": "Co to jest NSA?"}, {"role": "assistant", "content": "Sąd."}]


def test_store_saves_lists_and_deletes(tmp_path):
    store = ConversationStore(tmp_path)
    first = store.save("20261029-115502-k3x9", TALK, "qwen", "nsa")
    again = store.save("20261029-115502-k3x9", [*TALK, {"role": "system", "content": "x"}])
    assert again["utworzono"] == first["utworzono"] and again["model"] == "qwen"
    assert again["baza"] == "nsa" and again["wiadomosci"] == TALK  # bez wiadomości systemu
    store.save("20261030-080000-aaaa", TALK[:1])
    listed = store.recent()
    assert [c["id"] for c in listed] == ["20261030-080000-aaaa", "20261029-115502-k3x9"]
    assert listed[1]["tytul"] == "Co to jest NSA?" and listed[1]["liczba"] == 2
    store.delete("20261030-080000-aaaa")
    assert [c["id"] for c in store.recent()] == ["20261029-115502-k3x9"]
    with pytest.raises(ValueError):
        store.path("../../.env")


def test_save_from_the_browser_then_open_download_and_delete():
    tab = ModelConversation(settings(), session=Server(lm_studio(QWEN)))
    portal = Portal({"nsa": ("NSA", Pages(tab))})
    ident = "20261029-115502-k3x9"
    answer, pieces = post(
        portal, "/nsa/zapytaj/zapisz", {"id": ident, "model": "qwen", "messages": TALK}
    )
    assert answer["status"].startswith("200") and ident in b"".join(pieces).decode()
    assert tab.store.load(ident)["baza"] == "nsa"
    listing = page(Server(lm_studio(QWEN)))
    assert "Co to jest NSA?" in listing and f"/zapytaj/{ident}.json" in listing
    opened = page(Server(lm_studio(QWEN)), f"r={ident}")
    assert '"messages": [{"role": "user", "content": "Co to jest NSA?"}' in opened
    request = Request({"PATH_INFO": f"/zapytaj/{ident}.json", "REQUEST_METHOD": "GET"})
    download = tab.handle(request, lambda title, body: body)
    assert "attachment" in download.headers[0][1] and json.loads(download.body)["id"] == ident
    answer, _ = post(portal, "/nsa/zapytaj/zapisz", {"id": "../zle", "messages": TALK})
    assert answer["status"].startswith("400")
    answer, _ = post(portal, f"/nsa/zapytaj/usun?r={ident}".split("?")[0], {})
    assert tab.store.load(ident) is not None  # bez r nic nie usuwa
    environ = {
        "PATH_INFO": "/nsa/zapytaj/usun",
        "QUERY_STRING": f"r={ident}",
        "REQUEST_METHOD": "POST",
    }
    setup_testing_defaults(environ)
    got = {}
    portal(environ, lambda status, headers: got.update(status=status, headers=dict(headers)))
    assert got["status"].startswith("303") and got["headers"]["Location"] == "/nsa/zapytaj"
    assert tab.store.load(ident) is None


def test_legal_context_can_be_switched_off_and_is_saved(tmp_path):
    server = Server(pieces=("ok",))
    tab = ModelConversation(settings(), session=server, store=ConversationStore(tmp_path))
    question = [{"role": "user", "content": "Jak być dobrym człowiekiem?"}]
    tab.settings.chat_model = "qwen"
    for legal in (None, False):
        data = {"messages": question} if legal is None else {"messages": question, "legal": legal}
        _, pieces = post(Pages(tab), "/zapytaj/odpowiedz", data)
        b"".join(pieces)
    assert (
        server.posted[0]["messages"][0]["content"] == conversation.SYSTEM
    )  # domyślnie z kontekstem
    assert server.posted[1]["messages"][0]["content"] == conversation.PLAIN
    ident = "20261029-120000-abcd"
    post(Pages(tab), "/zapytaj/zapisz", {"id": ident, "messages": question, "legal": False})
    saved = tab.store.load(ident)
    assert saved["kontekst_prawniczy"] is False and saved["polecenie"] == conversation.PLAIN
    request = Request(
        {"PATH_INFO": "/zapytaj", "QUERY_STRING": f"r={ident}", "REQUEST_METHOD": "GET"}
    )
    tab.session = Server(lm_studio(QWEN))
    body = tab.handle(request, lambda title, body: body).body.decode()
    assert (
        "name=legal>" in body and conversation.SYSTEM in body
    )  # wczytana bez kontekstu: odznaczone
    assert "name=legal checked>" in page(Server(lm_studio(QWEN)))


class FakeRetriever:
    def __init__(self, sources):
        self.sources = sources
        self.asked = []

    def query(self, text):
        return text

    def retrieve(self, cur, query, budget_chars=None):
        self.asked.append((query, budget_chars))
        return Retrieval(self.sources, [s.judgment_id for s in self.sources], "hybrydowe")


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self


class Rag(RagPipeline):
    domain = "DOMENA: orzecznictwo testowe."


KUDLA = Reference(
    1,
    5,
    "Kudła v. Poland",
    "wyrok, 26.10.2000",
    "https://hudoc/5",
    "ENG",
    [Fragment(0, 20, "THE LAW", "reasonable time")],
)


def rag_tab(server, sources=(KUDLA,)):
    retriever = FakeRetriever(list(sources))
    tab = ModelConversation(
        settings(chat_model="qwen"),
        session=server,
        retriever=retriever,
        connect=FakeConnection,
        rag=Rag,
    )
    return tab, retriever


def test_rag_sends_the_passages_and_lists_the_sources_after_the_answer():
    server = Server(pieces=("Przewlekłość narusza art. 6 [1], zob. też [4].",))
    tab, retriever = rag_tab(server)
    talk = [
        {"role": "user", "content": "Co to jest NSA?"},
        {"role": "assistant", "content": "Sąd."},
        {"role": "user", "content": "przewlekłość postępowania"},
    ]
    _, pieces = post(Pages(tab), "/zapytaj/odpowiedz", {"messages": talk, "rag": True})
    text = b"".join(pieces).decode()
    # dopytanie: wyszukiwarka dostaje też poprzednie pytanie
    asked = "Co to jest NSA?\nprzewlekłość postępowania"
    assert retriever.asked == [(asked, conversation.RAG_BUDGET_CHARS)]
    sent = server.posted[0]["messages"]
    assert sent[0]["role"] == "system" and sent[0]["content"].startswith("DOMENA")
    assert [m["content"] for m in sent[1:3]] == ["Co to jest NSA?", "Sąd."]  # wcześniejsza rozmowa
    assert "reasonable time" in sent[-1]["content"] and "przewlekłość" in sent[-1]["content"]
    assert "*[1] Kudła v. Poland - wyrok, 26.10.2000 https://hudoc/5" in text
    assert "numery bez źródła (zmyślone): 4" in text


def test_without_rag_the_question_goes_straight_to_the_model():
    server = Server(pieces=("ok",))
    tab, retriever = rag_tab(server)
    _, pieces = post(Pages(tab), "/zapytaj/odpowiedz", {"messages": TALK[:1]})
    assert b"".join(pieces) == b"ok" and retriever.asked == []
    assert server.posted[0]["messages"][0]["content"] == conversation.SYSTEM


def test_rag_switch_only_with_a_base_and_its_prompt_is_shown_and_saved(tmp_path):
    tab, _ = rag_tab(Server(lm_studio(QWEN)))
    tab.store = ConversationStore(tmp_path)
    request = Request({"PATH_INFO": "/zapytaj", "REQUEST_METHOD": "GET", "SCRIPT_NAME": "/etpc"})
    body = tab.handle(request, lambda title, body: body).body.decode()
    assert "Szukaj w bazie ETPC (RAG)" in body and "DOMENA: orzecznictwo testowe." in body
    assert "name=mode value" not in page(Server(lm_studio(QWEN)))  # zakładka bez bazy: bez trybów
    ident = "20261029-130000-abcd"
    post(Pages(tab), "/zapytaj/zapisz", {"id": ident, "messages": TALK, "rag": True})
    saved = tab.store.load(ident)
    assert saved["tryb"] == "rag" and saved["polecenie"].startswith("DOMENA")


def test_rag_failure_is_reported():
    class Broken(FakeRetriever):
        def retrieve(self, cur, query, budget_chars=None):
            raise RuntimeError("Qdrant nie odpowiada")

    tab, _ = rag_tab(Server(pieces=("x",)))
    tab.retriever = Broken([])
    answer, pieces = post(Pages(tab), "/zapytaj/odpowiedz", {"messages": TALK[:1], "rag": True})
    assert (
        answer["status"].startswith("502") and "Qdrant nie odpowiada" in b"".join(pieces).decode()
    )


def test_every_answer_is_recorded_with_what_the_model_got(tmp_path):
    server = Server(pieces=("Przewlekłość [1].",))
    tab, _ = rag_tab(server)
    tab.store = ConversationStore(tmp_path)
    ident = "20261029-140000-abcd"
    question = [{"role": "user", "content": "przewlekłość"}]
    _, pieces = post(
        Pages(tab), "/zapytaj/odpowiedz", {"id": ident, "messages": question, "rag": True}
    )
    b"".join(pieces)
    post(Pages(tab), "/zapytaj/zapisz", {"id": ident, "messages": question, "rag": True})
    _, pieces = post(Pages(tab), "/zapytaj/odpowiedz", {"id": ident, "messages": question})
    next(pieces)
    pieces.close()  # zatrzymana w przeglądarce
    saved = tab.store.load(ident)
    assert saved["wiadomosci"] == question  # zapis z przeglądarki nie skasował tur
    rag, direct = saved["tury"]
    assert rag["nr"] == 1 and rag["tryb"] == "rag" and rag["model"] == "qwen"
    assert rag["pytanie"] == "przewlekłość" and rag["odpowiedz"] == "Przewlekłość [1]."
    assert rag["wyslane"] == server.posted[0]["messages"] and rag["cytowane"] == [1]
    assert rag["zrodla"][0]["fragmenty"][0]["tekst"] == "reasonable time"
    assert rag["wyszukiwanie"]["ranking"] == [5] and rag["ustawienia"]["reasoning_effort"] == "none"
    assert rag["przerwano"] is False and rag["czas_s"] >= 0
    assert direct["tryb"] == "bezposrednio" and direct["przerwano"] is True
    assert "zrodla" not in direct and direct["wyslane"][0]["content"] == conversation.SYSTEM
    assert "tury" not in tab.store.recent()[0]


def test_failed_answer_is_recorded_and_without_an_id_nothing_is_written(tmp_path):
    tab = ModelConversation(
        settings(chat_model="q"), session=Server(), store=ConversationStore(tmp_path)
    )
    ident = "20261029-140000-zzzz"
    post(Pages(tab), "/zapytaj/odpowiedz", {"id": ident, "messages": TALK[:1]})
    assert "nie odpowiada" in tab.store.load(ident)["tury"][0]["blad"]
    post(Pages(tab), "/zapytaj/odpowiedz", {"messages": TALK[:1]})
    assert [c["id"] for c in tab.store.recent()] == [ident]


def test_follow_up_without_hits_gets_the_sources_of_the_earlier_turn(tmp_path):
    server = Server(pieces=("Pismo [___].",))
    tab, retriever = rag_tab(server)
    tab.store = ConversationStore(tmp_path)
    ident = "20261029-150000-abcd"
    talk = [{"role": "user", "content": "dobre sąsiedztwo"}]
    b"".join(
        post(Pages(tab), "/zapytaj/odpowiedz", {"id": ident, "messages": talk, "mode": "rag"})[1]
    )
    retriever.sources = []  # dopytanie: nic nowego
    talk += [
        {"role": "assistant", "content": "Zasada..."},
        {"role": "user", "content": "napisz pismo"},
    ]
    _, pieces = post(
        Pages(tab), "/zapytaj/odpowiedz", {"id": ident, "messages": talk, "mode": "pismo"}
    )
    text = b"".join(pieces).decode()
    sent = server.posted[1]["messages"]
    assert sent[0]["content"].startswith("DOMENA") and "szkic pisma" in sent[0]["content"]
    assert (
        "reasonable time" in sent[-1]["content"]
        and "Polecenie: napisz pismo" in sent[-1]["content"]
    )
    assert "poprzedniej odpowiedzi (tura 1)" in text and "powołane w piśmie" in text
    turn = tab.store.load(ident)["tury"][1]
    assert turn["tryb"] == "pismo" and turn["wyszukiwanie"]["zrodla_z_tury"] == 1
    assert turn["wyszukiwanie"]["zapytanie"] == "dobre sąsiedztwo"  # pismo: temat, nie prośba


def test_rag_without_any_sources_does_not_ask_the_model(tmp_path):
    server = Server(pieces=("x",))
    tab, _ = rag_tab(server, sources=())
    tab.store = ConversationStore(tmp_path)
    ident = "20261029-150000-zzzz"
    answer, pieces = post(
        Pages(tab), "/zapytaj/odpowiedz", {"id": ident, "messages": TALK[:1], "mode": "rag"}
    )
    assert answer["status"].startswith("422") and "nic nie znaleziono" in b"".join(pieces).decode()
    assert server.posted == []  # model nie dostał pytania
    assert "nic nie znaleziono" in tab.store.load(ident)["tury"][0]["blad"]
    # pismo bez źródeł: model pisze bez powoływania orzeczeń
    _, pieces = post(Pages(tab), "/zapytaj/odpowiedz", {"messages": TALK[:1], "mode": "pismo"})
    assert (
        b"".join(pieces).startswith(b"x")
        and "(brak źródeł)" in server.posted[0]["messages"][-1]["content"]
    )


def test_letter_marks_judgments_named_by_title():
    retrieval = Retrieval([KUDLA], [5])
    footer = conversation.sources_footer("Por. wyrok Kudła v. Poland.", retrieval, "pismo")
    assert "*[1] Kudła v. Poland" in footer and "zmyślone" not in footer
    assert (
        conversation.saved_mode({"rag": True}) == "rag"
        and conversation.saved_mode(None) == "bezposrednio"
    )


def test_letter_recognises_judgments_by_their_numbers():
    scordino = Reference(
        1, 7, "CASE OF SCORDINO v. ITALY (No. 1)", "wyrok; skargi 36813/97", "u", "ENG"
    )
    nsa = Reference(2, 8, "II SA/Gl 626/16", "Wyrok WSA w Gliwicach", "u", "")
    other = Reference(3, 9, "CASE OF ECKLE v. GERMANY", "skargi 8130/78", "u", "ENG")
    text = "W sprawie Scordino v. Italy [36813/97] ... por. wyrok WSA II SA/Gl 626/16."
    assert conversation.cited_sources(text, Retrieval([scordino, nsa, other], []), "pismo") == (
        [1, 2],
        [],
    )
