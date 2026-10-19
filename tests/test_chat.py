"""Chat models (judgments_core/chat.py) and the RAG pipeline (judgments_core/pipeline.py)."""

import json

import pytest

from judgments_core.chat import ChatError, ChatModel, HttpChat, without_thinking
from judgments_core.pipeline import RagPipeline, check_citations
from judgments_core.rag import Fragment, Reference, Retrieval


class Response:
    def __init__(self, status=200, lines=(), data=None, text=""):
        self.status_code = status
        self.lines = lines
        self.data = data
        self.text = text

    def iter_lines(self, decode_unicode=False):
        return iter(self.lines)

    def json(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Session:
    def __init__(self, post=None, get=None):
        self.post_response, self.get_response = post, get
        self.posted = []

    def post(self, url, json=None, stream=False, timeout=None):
        self.posted.append((url, json))
        return self.post_response

    def get(self, url, timeout=None):
        self.got = url
        return self.get_response


def sse(*pieces):
    lines = [f"data: {json.dumps({'choices': [{'delta': {'content': p}}]})}" for p in pieces]
    return [": ping", "", *lines, "data: [DONE]", "data: never"]


def test_http_chat_streams_pieces_and_finds_the_loaded_model():
    session = Session(
        post=Response(lines=sse("Dzień ", "dobry")),
        get=Response(data={"data": [{"id": "text-embedding-bge-m3"}, {"id": "qwen3-14b"}]}),
    )
    chat = HttpChat("http://h:1234/v1/chat/completions", session=session)
    assert chat.complete([{"role": "user", "content": "hej"}]) == "Dzień dobry"
    assert session.got == "http://h:1234/v1/models"
    url, body = session.posted[0]
    assert url == "http://h:1234/v1/chat/completions"
    assert body["model"] == "qwen3-14b" and body["stream"] is True
    assert chat.model == "qwen3-14b"  # zapamiętany


def test_http_chat_errors_are_for_the_user():
    session = Session(get=Response(data={"data": [{"id": "text-embedding-bge-m3"}]}))
    with pytest.raises(ChatError, match="nie ma wczytanego modelu czatu"):
        HttpChat("http://h/v1/chat/completions", session=session).complete([])
    session = Session(post=Response(status=400, text="context length exceeded"))
    with pytest.raises(ChatError, match="HTTP 400: context length"):
        HttpChat("http://h/v1/chat/completions", "m", session=session).complete([])


def test_thinking_block_is_dropped():
    assert "".join(without_thinking(["<thi", "nk>hmm", "...</think>\n\nOdpowiedź", " [1]"])) == (
        "Odpowiedź [1]"
    )
    assert "".join(without_thinking(["Od razu", " odpowiedź"])) == "Od razu odpowiedź"
    assert "".join(without_thinking(["\n", "<", "b>tekst"])) == "\n<b>tekst"
    assert "".join(without_thinking(["<think>bez końca"])) == ""


def test_citations_are_checked():
    text = "Tak [1]. Zob. też [2, 3] i [1][7]; ust. [a] to nie cytat."
    assert check_citations(text, 3) == ([1, 2, 3], [7])
    assert check_citations("Brak źródeł.", 0) == ([], [])


class FakeChat(ChatModel):
    name = "fake"

    def __init__(self, pieces):
        super().__init__("fake")
        self.pieces = pieces
        self.messages = None

    def stream(self, messages):
        self.messages = messages
        yield from self.pieces


class Pipeline(RagPipeline):
    domain = "Źródła to orzeczenia testowe."


def retrieval():
    fragment = Fragment(0, 10, "THE LAW", "Some text.")
    source = Reference(1, 5, "A v. Poland", "wyrok z 2020-01-01", "https://x/1", "ENG", [fragment])
    return Retrieval([source], [5])


def test_pipeline_prompt_and_answer():
    chat = FakeChat(["<think>x</think>", "Naruszenie art. 6 [1], ", "zob. [4]."])
    pipeline = Pipeline(retriever=None, chat=chat)
    answer = pipeline.answer("Czy naruszono?", retrieval())
    system, user = chat.messages
    assert (
        system["content"].startswith("Źródła to orzeczenia testowe.") and "[1]" in system["content"]
    )
    assert user["content"].startswith("Źródła:\n\n[1] A v. Poland (id 5)")
    assert user["content"].endswith("Pytanie: Czy naruszono?")
    assert answer.text == "Naruszenie art. 6 [1], zob. [4]."
    assert (answer.cited, answer.unknown) == ([1], [4])
