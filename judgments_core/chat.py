"""Language models that write the answer (strategy pattern, like the embedders).

HttpChat = an OpenAI-compatible server: LM Studio, Ollama, llama.cpp server,
vLLM - POST <url> {"model", "messages", "stream"...}; the answer streams in
pieces (Server-Sent Events). Model empty = the first chat model the server
has loaded (GET .../models, embedding models skipped). Runs where the model
is (a PC), not on the hosting.

Reasoning models (Qwen3 ...) may write "<think>...</think>" before the
answer; without_thinking() drops it from a stream. LM Studio may instead
send the reasoning apart (delta.reasoning_content, skipped here); its tokens
still count against max_tokens. reasoning_effort="none" switches it off
for models that honour it (qwen3.8 does, Qwen3 needs /no_think in the prompt).
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod

import requests

log = logging.getLogger(__name__)


class ChatError(RuntimeError):
    """The model server can't answer; the message is for the user."""


class ChatModel(ABC):
    name = ""

    def __init__(self, model):
        self.model = model

    @abstractmethod
    def stream(self, messages):
        """Pieces of the answer to messages ([{"role", "content"}]) as they come."""

    def complete(self, messages):
        return "".join(self.stream(messages))

    def describe(self):
        return f"{self.name} {self.model}"


class HttpChat(ChatModel):
    name = "http"

    def __init__(
        self,
        url,
        model="",
        *,
        temperature=0.2,
        max_tokens=2000,
        timeout_s=600.0,
        api_key="",
        reasoning_effort="",
        session=None,
    ):
        super().__init__(model)
        self.url = url
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        self.reasoning_effort = reasoning_effort  # "" = nie wysyłać
        self.session = session or requests.Session()

    def stream(self, messages):
        body = {
            "model": self.model or self.loaded_model(),
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        try:
            response = self.session.post(
                self.url, json=body, stream=True, timeout=self.timeout_s, headers=self.headers
            )
        except requests.RequestException as e:
            raise ChatError(
                f"serwer modelu {self.url} nie odpowiada ({type(e).__name__}: {e})"
            ) from e
        with response:
            if response.status_code != 200:
                raise ChatError(
                    f"serwer modelu: HTTP {response.status_code}: {response.text[:300]}"
                )
            wrote, finish = False, None
            try:
                # Bajty, nie tekst: iter_lines(decode_unicode=True) dzieli też na U+2028,
                # U+0085 ... wewnątrz treści i ucina wiersz JSON-u.
                for raw in response.iter_lines():
                    line = raw.decode("utf-8")
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    choices = json.loads(data).get("choices") or [{}]
                    finish = choices[0].get("finish_reason") or finish
                    piece = (choices[0].get("delta") or {}).get("content")
                    if piece:
                        wrote = True
                        yield piece
            except requests.RequestException as e:
                raise ChatError(
                    f"serwer modelu przerwał odpowiedź ({type(e).__name__}: {e})"
                ) from e
            if not wrote and finish == "length":
                raise ChatError(
                    f"model zużył limit {self.max_tokens} tokenów, zanim zaczął odpowiadać"
                    " (myślenie?): ustaw <PREFIKS>_CHAT_REASONING_EFFORT=none albo większe"
                    " <PREFIKS>_CHAT_MAX_TOKENS"
                )

    def loaded_model(self):
        """The first loaded chat model (not an embedding model); remembered."""
        url = self.url.rsplit("/chat/completions", 1)[0] + "/models"
        try:
            response = self.session.get(url, timeout=30, headers=self.headers)
            ids = [m["id"] for m in response.json().get("data", [])]
        except (requests.RequestException, ValueError, KeyError) as e:
            raise ChatError(f"serwer modelu {url} nie odpowiada ({type(e).__name__}: {e})") from e
        chat = [i for i in ids if "embed" not in i.lower()]
        if not chat:
            raise ChatError(
                f"serwer modelu nie ma wczytanego modelu czatu (są: {', '.join(ids) or 'żadne'})"
                " - wczytaj go w LM Studio albo ustaw <PREFIKS>_CHAT_MODEL"
            )
        self.model = chat[0]
        log.info("model czatu: %s", self.model)
        return self.model

    def describe(self):
        return f"{self.model or '(wczytany)'} ({self.url})"


# Ceny Claude za 1 mln tokenów (wejście, wyjście) w USD - tylko do szacunku
# wydatków i limitu max_usd; aktualne ceny są w cenniku Anthropic.
CLAUDE_PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
# Modele, którym przy odmowie (stop_reason "refusal") API samo podstawia inny
# model (fallbacks "default"); Haiku tego nie ma.
CLAUDE_WITH_FALLBACK = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-fable-5-1")


class ClaudeChat(ChatModel):
    """Claude over the Anthropic API (the official SDK, in the www image), for short
    one-shot tasks such as naming topics: the answer comes whole (no streaming), the
    tokens and the estimated cost are counted, and past max_usd it stops (ChatError).
    A refusal gives an empty answer, so the caller skips that one item."""

    name = "claude"

    def __init__(self, model, api_key, *, max_tokens=4000, effort="low", max_usd=0.0, client=None):
        super().__init__(model)
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.effort = effort
        self.max_usd = max_usd  # 0 = bez limitu
        self.client = client
        self.input_tokens = 0
        self.output_tokens = 0

    @property
    def cost_usd(self):
        price_in, price_out = CLAUDE_PRICES.get(self.model, (0.0, 0.0))
        return (self.input_tokens * price_in + self.output_tokens * price_out) / 1e6

    def stream(self, messages):
        import anthropic

        if self.max_usd and self.cost_usd >= self.max_usd:
            raise ChatError(
                f"osiągnięty limit wydatków ${self.max_usd:.2f} (wydano ~${self.cost_usd:.2f})"
            )
        if self.client is None:
            self.client = anthropic.Anthropic(api_key=self.api_key, timeout=300.0)
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "output_config": {"effort": self.effort},
            "system": system,
            "messages": [m for m in messages if m["role"] != "system"],
        }
        try:
            if self.model in CLAUDE_WITH_FALLBACK:
                response = self.client.beta.messages.create(
                    **body, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
                )
            else:
                response = self.client.messages.create(**body)
        except anthropic.AuthenticationError as e:
            raise ChatError("nieprawidłowy klucz API Anthropic") from e
        except anthropic.PermissionDeniedError as e:
            raise ChatError(f"klucz API bez uprawnień do modelu {self.model}") from e
        except anthropic.RateLimitError as e:
            raise ChatError("limit zapytań Anthropic - spróbuj za chwilę") from e
        except anthropic.APIStatusError as e:
            raise ChatError(f"błąd API Anthropic ({e.status_code}): {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise ChatError("brak połączenia z API Anthropic") from e
        self.input_tokens += response.usage.input_tokens
        self.output_tokens += response.usage.output_tokens
        if response.stop_reason == "refusal":
            log.warning("Claude odmówił odpowiedzi (%s)", getattr(response, "stop_details", None))
            return
        yield "".join(block.text for block in response.content if block.type == "text")

    def describe(self):
        return f"Claude {self.model}"


def make_chat(settings):
    return HttpChat(
        settings.chat_url,
        settings.chat_model,
        temperature=settings.chat_temperature,
        max_tokens=settings.chat_max_tokens,
        timeout_s=settings.chat_timeout_s,
        reasoning_effort=settings.chat_reasoning_effort,
    )


def make_judge(settings):
    """The model that rates answers: judge_* settings, else the chat model; temperature 0."""
    return HttpChat(
        settings.judge_url or settings.chat_url,
        settings.judge_model or ("" if settings.judge_url else settings.chat_model),
        temperature=0.0,
        max_tokens=3000,
        timeout_s=settings.chat_timeout_s,
        api_key=settings.judge_api_key,
        reasoning_effort="" if settings.judge_url else settings.chat_reasoning_effort,
    )


def without_thinking(pieces):
    """The stream without a leading <think>...</think> block (reasoning models)."""
    head = ""
    pieces = iter(pieces)
    for piece in pieces:
        head += piece
        stripped = head.lstrip()
        if not stripped:
            continue
        if not "<think>".startswith(stripped[:7]):
            yield head  # nie zaczyna się od myślenia
            break
        if len(stripped) < 7:
            continue  # jeszcze nie wiadomo
        end = head.find("</think>")
        if end >= 0:
            rest = head[end + len("</think>") :].lstrip()
            if rest:
                yield rest
            break
    else:
        return
    yield from pieces
