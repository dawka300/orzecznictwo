"""The "Zapytaj model" tab (/zapytaj): a conversation in the browser with the local chat
model in LM Studio (settings.chat_url, an OpenAI-compatible server).

Opening the tab checks the server: not answering = "brak LM Studio"; answering with only
an embedding model loaded (bge-m3 ...) = a wrong model, not for a conversation; a chat
model (Qwen, Mistral ...) = its name and the conversation. LM Studio's own list
(/api/v0/models) says which models are loaded and of what type; other servers have only
/v1/models, where an id with "embed" or "bge" counts as an embedding model.

The conversation is kept in the browser (sessionStorage) and sent whole with each
question (only its end when long); the answer streams back as plain text. After every
answer the browser saves it as JSON in badania/rozmowy/<id>.json (local, outside git;
material for training local models too): the list beside it opens, downloads or deletes
saved conversations. The server adds to the same file a full record of every answer
("tury", class Turn): the exact messages the model got, its answer, the sources and
their passages, the settings and the times - for evaluating answers and fine-tuning.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from judgments_core import claude_report
from judgments_core.chat import ChatError, HttpChat, without_thinking
from judgments_core.pipeline import check_citations
from judgments_core.rag import Fragment, Reference, Retrieval, render
from judgments_core.web import Response, StreamResponse, e, shorten

log = logging.getLogger(__name__)

SYSTEM = (
    "Jesteś pomocnym asystentem prawnika i badacza. Rozmowa dotyczy prawa polskiego i"
    " europejskiego oraz orzecznictwa: NSA to Naczelny Sąd Administracyjny, WSA - wojewódzki"
    " sąd administracyjny, ETPC - Europejski Trybunał Praw Człowieka. Odpowiadaj po polsku,"
    " rzeczowo. Jeśli czegoś nie wiesz, powiedz to wprost i nie zmyślaj przepisów ani orzeczeń."
)
# bez kontekstu prawniczego (pole odznaczone na stronie)
PLAIN = "Jesteś pomocnym asystentem. Odpowiadaj po polsku."


def system_prompt(legal=True):
    return SYSTEM if legal else PLAIN


# tyle końca rozmowy idzie do modelu (kontekst LM Studio ~8 tys. tokenów)
MAX_HISTORY_CHARS = 16_000
MIN_TOKENS = 4000  # modele myślące (Qwen) zużywają część limitu na myślenie
# RAG: tyle tekstu fragmentów (~3 tys. tokenów) i wcześniejszej rozmowy, żeby z odpowiedzią
# zmieścić się w kontekście 8192 tokenów modelu w LM Studio
RAG_BUDGET_CHARS = 10_000
RAG_HISTORY_CHARS = 3_000
# bezpośrednio do modelu / odpowiedź tylko ze źródeł bazy / szkic pisma ze źródłami
MODES = ("bezposrednio", "rag", "pismo")
# Tryb „Pismo”: zamiast reguł RAG („każde twierdzenie [n]”), z domeną projektu przed nimi.
LETTER_RULES = """\
Zadanie: napisz szkic pisma, o które prosi użytkownik (np. wyjaśnienie, wniosek, odwołanie,
pismo do organu).
Zasady:
- Pisz po polsku, językiem pisma urzędowego: miejscowość i data, nadawca, adresat, tytuł,
  treść, podpis.
- Danych, których nie podano (imiona, adresy, numery działek, daty, sygnatury akt sprawy),
  nie wymyślaj - zostaw [___] do uzupełnienia.
- Argumentację prawną opieraj na źródłach z wiadomości i na wcześniejszej rozmowie:
  powołuj orzeczenia z sygnaturą, sądem i datą tak, jak są w źródle; przepisy cytuj tak
  jak w źródle.
- Powołuj WYŁĄCZNIE orzeczenia ze źródeł albo z wcześniejszej rozmowy; żadnych innych
  spraw, sygnatur ani dat z pamięci, nawet znanych.
- Adresata pisma i trybu (do jakiego organu lub sądu, na jakiej podstawie) nie zgaduj:
  jeśli nie wynika z pytania ani ze źródeł, zostaw [adresat] i [podstawa prawna].
- Stanowisko strony (skarżącego, organu) przytoczone w uzasadnieniu orzeczenia to nie
  pogląd sądu - nie przedstawiaj go jako orzecznictwa.
- Jeśli źródeł brak albo nie pasują, napisz pismo bez powoływania orzeczeń i zaznacz to
  jednym zdaniem pod pismem.
- Pod pismem, osobno: które orzeczenia wykorzystano i jakie dane trzeba uzupełnić."""


def search_text(messages, mode="rag"):
    """What the search gets: the question with the one before it (a follow-up such as
    "a co z uchwałą?" has few words of the topic). A letter is searched by the question
    before it: the request ("napisz pismo, wnioskodawca Jan Kowalski") has names, not the
    topic, and a name finds that party's cases."""
    questions = [m["content"] for m in messages if m["role"] == "user"]
    if mode == "pismo" and len(questions) > 1:
        return questions[-2]
    return "\n".join(questions[-2:])


def cited_sources(text, retrieval, mode):
    """([cited numbers], [numbers with no source]): rag cites [n]; a letter names the
    judgments by their numbers (application 36813/97, signature II SA/Gl 626/16), so
    numbers [n] are not checked there."""
    if mode == "pismo":
        return [s.number for s in retrieval.sources if _named_in(s, text)], []
    return check_citations(text, len(retrieval.sources))


_CASE_NUMBER = re.compile(r"\b\d+/\d+\b")  # 36813/97, 626/16 (z „II SA/Gl 626/16”)


def _named_in(source, text):
    """The letter names this judgment: one of its numbers, else its whole title."""
    numbers = set(_CASE_NUMBER.findall(f"{source.title} {source.details}"))
    if numbers:
        return any(re.search(rf"\b{re.escape(n)}\b", text) for n in numbers)
    return bool(source.title) and source.title.lower() in text.lower()


def _reference(saved):
    """A source saved in a turn ("zrodla") back as a rag.Reference."""
    return Reference(
        saved["nr"],
        saved["id"],
        saved["tytul"],
        saved["opis"],
        saved["url"],
        saved.get("jezyk", ""),
        [
            Fragment(f["start"], f["end"], f.get("naglowek", ""), f.get("tekst", ""))
            for f in saved.get("fragmenty", [])
        ],
        saved.get("wynik", ""),
    )


MAX_BODY = 5_000_000
EMBEDDING_HINTS = ("embed", "bge", "e5-", "gte-", "nomic")
SAVE_DIR = claude_report.SAVE_DIR / "rozmowy"
LISTED = 100  # tyle ostatnich rozmów na liście
# id rozmowy nadaje przeglądarka: 20261029-115502-k3x9 (czas i losowa końcówka)
ID = re.compile(r"^\d{8}-\d{6}-[a-z0-9]{4}$")


@dataclass
class LoadedModel:
    id: str
    type: str = ""  # LM Studio: llm, vlm, embeddings; "" = serwer nie mówi
    context: int = 0

    @property
    def embedding(self):
        if self.type:
            return self.type == "embeddings"
        return any(hint in self.id.lower() for hint in EMBEDDING_HINTS)


@dataclass
class ServerState:
    """What the model server has: error = it does not answer (text for the user)."""

    models: list
    error: str = ""

    @property
    def chat_models(self):
        return [m for m in self.models if not m.embedding]


def server_state(chat_url, session=None, timeout_s=5):
    """The loaded models of the server behind chat_url (.../v1/chat/completions)."""
    session = session or requests
    v1 = chat_url.rsplit("/chat/completions", 1)[0]
    try:
        response = session.get(v1.rsplit("/v1", 1)[0] + "/api/v0/models", timeout=timeout_s)
        if response.status_code == 200:
            return ServerState(
                [
                    LoadedModel(m["id"], m.get("type", ""), m.get("loaded_context_length") or 0)
                    for m in response.json().get("data", [])
                    if m.get("state", "loaded") == "loaded"
                ]
            )
        response = session.get(v1 + "/models", timeout=timeout_s)  # nie LM Studio
        response.raise_for_status()
        return ServerState([LoadedModel(m["id"]) for m in response.json().get("data", [])])
    except (requests.RequestException, ValueError, KeyError) as error:
        log.info("zapytaj: serwer modelu %s nie odpowiada: %s", v1, error)
        return ServerState([], f"{type(error).__name__}")


def history(messages, limit=MAX_HISTORY_CHARS):
    """The end of the conversation that fits in limit characters, starting with a question;
    only user/assistant messages with text."""
    clean = clean_messages(messages)
    kept, size = [], 0
    for message in reversed(clean):
        size += len(message["content"])
        if kept and size > limit:
            break
        kept.append(message)
    kept.reverse()
    while kept and kept[0]["role"] != "user":
        kept.pop(0)
    return kept


def clean_messages(messages):
    """Only user/assistant messages with text, as {"role", "content"}."""
    return [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if isinstance(m, dict)
        and m.get("role") in ("user", "assistant")
        and isinstance(m.get("content"), str)
        and m["content"].strip()
    ]


# Zapis z przeglądarki i rekord tury z serwera mogą przyjść naraz (zatrzymana odpowiedź):
# jeden zapis naraz, każdy czyta plik od nowa i zachowuje część drugiego.
_WRITE = threading.Lock()


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ConversationStore:
    """Saved conversations, one JSON file each: <directory>/<id>.json."""

    def __init__(self, directory=None):
        self.directory = directory or SAVE_DIR

    def path(self, ident):
        if not ID.match(ident or ""):
            raise ValueError(f"złe id rozmowy: {ident!r}")
        return self.directory / f"{ident}.json"

    def save(self, ident, messages, model="", base="", legal=True, mode="bezposrednio", prompt=""):
        """Write the conversation as the browser shows it (the first save keeps its creation
        time, the turns written by add_turn stay) with the system prompt of the last
        question (legal = the legal context); its dict."""
        messages = clean_messages(messages)
        first = next((m["content"] for m in messages if m["role"] == "user"), "")
        with _WRITE:
            old = self.load(ident) or {}
            now = _now()
            data = {
                "id": ident,
                "tytul": shorten(first, 80),
                "baza": base or old.get("baza", ""),
                "model": model or old.get("model", ""),
                "utworzono": old.get("utworzono", now),
                "zmieniono": now,
                "kontekst_prawniczy": legal,
                "tryb": mode,
                "polecenie": prompt or system_prompt(legal),
                "wiadomosci": messages,
                "tury": old.get("tury", []),
            }
            self._write(ident, data)
        return data

    def add_turn(self, ident, turn, base=""):
        """Append one answer's full record (what the model got, what it wrote, the sources,
        the settings, the time) to the conversation's "tury"; numbered from 1."""
        with _WRITE:
            data = self.load(ident) or {"id": ident, "baza": base, "utworzono": _now()}
            turns = data.setdefault("tury", [])
            turns.append({"nr": len(turns) + 1, **turn})
            data["zmieniono"] = _now()
            self._write(ident, data)

    def _write(self, ident, data):
        path = self.path(ident)
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)

    def load(self, ident):
        """The saved conversation; None when there is none."""
        path = self.path(ident)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def delete(self, ident):
        path = self.path(ident)
        if path.exists():
            path.unlink()

    def recent(self, limit=LISTED):
        """The newest conversations first (by id = start time), without the messages."""
        if not self.directory.is_dir():
            return []
        out = []
        for path in sorted(self.directory.glob("*.json"), reverse=True)[:limit]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            data["liczba"] = len(data.pop("wiadomosci", []))
            data.pop("tury", None)
            out.append(data)
        return out


class ModelConversation:
    """The pages under /zapytaj of one project (its settings: chat_url, chat_model ...)."""

    base = "/zapytaj"
    title = "Zapytaj model"

    def __init__(self, settings, session=None, store=None, retriever=None, connect=None, rag=None):
        """retriever, connect, rag: the project's base for answers from its judgments (RAG:
        the passages its retriever finds, the prompt of its RagPipeline child); without
        them only the direct conversation."""
        self.settings = settings
        self.session = session
        self.store = store or ConversationStore()
        self.retriever = retriever
        self.connect = connect
        self.rag = rag

    @property
    def can_rag(self):
        return bool(self.retriever and self.connect and self.rag)

    def rag_prompt(self):
        """The system prompt of the project's RAG (its domain and the rules)."""
        return self.rag(self.retriever, None).system_prompt()

    def handle(self, request, layout):
        path, post = request.path, request.method == "POST"
        try:
            if path == self.base + "/odpowiedz" and post:
                return self.answer(request)
            if path == self.base + "/zapisz" and post:
                return self.save(request)
            if path == self.base + "/usun" and post:
                self.store.delete(request.get("r"))
                return Response("", "303 See Other", headers=[("Location", self.base)])
            if path.startswith(self.base + "/") and path.endswith(".json"):
                return self.download(path[len(self.base) + 1 : -len(".json")], layout)
        except ValueError as error:
            return _text(str(error), "400 Bad Request")
        if path != self.base:
            return Response(layout(self.title, "<p>Nie ma takiej strony.</p>"), "404 Not Found")
        loaded = None
        if request.get("r"):
            try:
                loaded = self.store.load(request.get("r"))
            except ValueError:
                loaded = None
        state = server_state(self.settings.chat_url, self.session)
        base_name = request.environ.get("SCRIPT_NAME", "").strip("/").upper()
        return Response(layout(self.title, self.page(state, loaded, request.get("r"), base_name)))

    def chat(self, model, think=False):
        """think: let a reasoning model think first (settings.chat_reasoning_effort; Qwen
        3.5 then thinks for minutes before the first word), else reasoning_effort=none."""
        settings = self.settings
        return HttpChat(
            settings.chat_url,
            model,
            temperature=settings.chat_temperature,
            max_tokens=max(settings.chat_max_tokens, MIN_TOKENS),
            timeout_s=settings.chat_timeout_s,
            reasoning_effort=settings.chat_reasoning_effort if think else "none",
            session=self.session,
        )

    def mode(self, data):
        """bezposrednio / rag / pismo from the request ("rag": true = rag, as before);
        without the project's base always bezposrednio."""
        mode = data.get("mode") or ("rag" if data.get("rag") else "bezposrednio")
        if mode not in MODES or (mode != "bezposrednio" and not self.can_rag):
            return "bezposrednio"
        return mode

    def answer(self, request):
        """The answer to {"id", "model", "messages", "think", "legal", "mode"} as a stream of
        text. mode: bezposrednio (legal = the legal context), rag (an answer only from the
        base's passages) or pismo (a draft letter argued from them); with passages their
        list comes at the end. An error before the first piece = 502 with the message (422
        when RAG found nothing), an error later = the message at the end."""
        try:
            data = _json_body(request)
            messages = history(data.get("messages") or [])
            model = str(data.get("model") or self.settings.chat_model)
        except (ValueError, AttributeError) as error:
            return _text(f"Złe zapytanie: {error}", "400 Bad Request")
        if not messages or messages[-1]["role"] != "user":
            return _text("Brak pytania.", "400 Bad Request")
        think, legal = bool(data.get("think")), bool(data.get("legal", True))
        mode = self.mode(data)
        chat = self.chat(model, think)
        base = request.environ.get("SCRIPT_NAME", "").strip("/")
        ident = str(data.get("id", ""))
        turn = Turn(self.store, ident, base, messages[-1]["content"], mode)
        turn.settings = {
            "temperatura": chat.temperature,
            "max_tokens": chat.max_tokens,
            "reasoning_effort": chat.reasoning_effort,
            "myslenie": think,
            "kontekst_prawniczy": legal,
            "suffix": self.settings.chat_suffix,
        }
        retrieval = None
        if mode != "bezposrednio":
            turn.search_text = search_text(messages, mode)
            try:
                retrieval = self.search(turn.search_text)
            except Exception as error:  # baza, Qdrant, model wektorów: powód dla czytającego
                log.exception("zapytaj: wyszukiwanie w bazie nie powiodło się")
                message = f"Nie udało się przeszukać bazy ({type(error).__name__}: {error})"
                turn.finish(chat, [], "", error=message)
                return _text(message, "502 Bad Gateway")
            turn.search_s = time.monotonic() - turn.started
            if not retrieval.sources:  # dopytanie bez nowych trafień: źródła poprzedniej tury
                retrieval, turn.sources_from = self.earlier_sources(ident, retrieval)
            if not retrieval.sources and mode == "rag":
                message = (
                    "W bazie nic nie znaleziono dla tego pytania, więc nie pytam modelu"
                    " (odpowiadałby tylko ze źródeł)."
                    f"{' ' + retrieval.notice if retrieval.notice else ''} Zmień pytanie albo"
                    " wybierz „Bezpośrednio” lub „Pismo”."
                )
                turn.finish(chat, [], "", retrieval, error=message)
                return _text(message, "422 Unprocessable Entity")
            sent = self.base_messages(mode, chat, messages, retrieval)
        else:
            if self.settings.chat_suffix:  # np. /no_think dla Qwen3
                messages[-1] = dict(
                    messages[-1], content=f"{messages[-1]['content']} {self.settings.chat_suffix}"
                )
            sent = [{"role": "system", "content": system_prompt(legal)}, *messages]
        raw = chat.stream(sent)
        pieces = without_thinking(raw)
        try:
            first = next(pieces, "")
        except ChatError as error:
            turn.finish(chat, sent, "", retrieval, error=str(error))
            return _text(str(error), "502 Bad Gateway")
        turn.first_s = time.monotonic() - turn.started

        def rest():
            text, error, stopped = first, "", True
            try:
                yield first.encode("utf-8")
                for piece in pieces:
                    text += piece
                    yield piece.encode("utf-8")
                stopped = False
                if retrieval is not None:
                    yield sources_footer(text, retrieval, mode).encode("utf-8")
            except ChatError as e:
                error, stopped = str(e), False
                yield f"\n\n[błąd: {e}]".encode()
            finally:  # przeglądarka zatrzymała: LM Studio przestaje pisać od razu
                pieces.close()
                raw.close()
                turn.finish(chat, sent, text, retrieval, error=error, stopped=stopped)

        return StreamResponse(rest(), content_type="text/plain; charset=utf-8")

    def search(self, text):
        """The passages of the project's base for text (its retriever and its database)."""
        with self.connect() as conn, conn.cursor() as cur:
            retrieval = self.retriever.retrieve(cur, self.retriever.query(text), RAG_BUDGET_CHARS)
        log.info(
            "zapytaj: RAG %d źródeł, %d znaków (%s)",
            len(retrieval.sources),
            retrieval.chars,
            retrieval.mode,
        )
        return retrieval

    def earlier_sources(self, ident, empty):
        """(Retrieval, number of the turn they come from): the sources of the last earlier
        turn of this conversation that had some, when the new search found none; else
        (empty, None)."""
        try:
            saved = self.store.load(ident) if ID.match(ident) else None
        except (OSError, ValueError):
            saved = None
        for turn in reversed((saved or {}).get("tury", [])):
            if turn.get("zrodla"):
                sources = [_reference(s) for s in turn["zrodla"]]
                notice = (
                    "Wyszukiwarka nic nowego nie znalazła - model dostał źródła z poprzedniej"
                    f" odpowiedzi (tura {turn['nr']})."
                )
                if empty.notice:
                    notice += f" {empty.notice}"
                return Retrieval(
                    sources, [s.judgment_id for s in sources], "z rozmowy", notice
                ), turn["nr"]
        return empty, None

    def base_messages(self, mode, chat, messages, retrieval):
        """Messages for rag / pismo: the prompt of the project's RAG (pismo: its domain
        with the rules of a letter), the end of the conversation so far, then the question
        with the passages."""
        question = messages[-1]["content"]
        earlier = history(messages[:-1], RAG_HISTORY_CHARS)
        pipeline = self.rag(self.retriever, chat, self.settings.chat_suffix)
        if mode == "pismo":
            user = f"Źródła:\n\n{render(retrieval, empty='(brak źródeł)', ids=False)}\n\nPolecenie: {question}"
            if self.settings.chat_suffix:
                user += f"\n\n{self.settings.chat_suffix}"
            system = {"role": "system", "content": self.letter_prompt()}
            return [system, *earlier, {"role": "user", "content": user}]
        prompt = pipeline.messages(question, retrieval)
        return [prompt[0], *earlier, prompt[1]]

    def letter_prompt(self):
        """The system prompt of a letter: the project's domain and LETTER_RULES."""
        return f"{self.rag.domain}\n\n{LETTER_RULES}"

    def prompt_of(self, mode, legal=True):
        """The system prompt a question in this mode gets."""
        if mode == "rag":
            return self.rag_prompt()
        if mode == "pismo":
            return self.letter_prompt()
        return system_prompt(legal)

    def save(self, request):
        """{"id", "model", "messages", "legal", "rag"} -> badania/rozmowy/<id>.json with the
        system prompt of the last question; the base it came from (/etpc, /nsa) too."""
        try:
            data = _json_body(request)
        except (ValueError, AttributeError) as error:
            return _text(f"Złe zapytanie: {error}", "400 Bad Request")
        base = request.environ.get("SCRIPT_NAME", "").strip("/")
        legal = bool(data.get("legal", True))
        mode = self.mode(data)
        saved = self.store.save(
            str(data.get("id", "")),
            data.get("messages") or [],
            str(data.get("model", "")),
            base,
            legal,
            mode,
            self.prompt_of(mode, legal),
        )
        return Response(
            json.dumps({"id": saved["id"], "zmieniono": saved["zmieniono"]}),
            content_type="application/json",
        )

    def download(self, ident, layout):
        data = self.store.load(ident)
        if data is None:
            return Response(layout(self.title, "<p>Nie ma takiej rozmowy.</p>"), "404 Not Found")
        return Response(
            json.dumps(data, ensure_ascii=False, indent=1),
            content_type="application/json; charset=utf-8",
            headers=[("Content-Disposition", f'attachment; filename="rozmowa-{ident}.json"')],
        )

    def status_html(self, state):
        url = e(self.settings.chat_url.rsplit("/v1", 1)[0])
        if state.error:
            return (
                f"<p class=error><b>Brak LM Studio.</b> Serwer modelu {url} nie odpowiada"
                f" ({e(state.error)}). Uruchom LM Studio, włącz serwer (Developer → Start Server),"
                " wczytaj model rozmowy i odśwież stronę.</p>"
            )
        if not state.models:
            return (
                f"<p class=error><b>Brak wczytanego modelu.</b> LM Studio ({url}) działa, ale nie ma"
                " wczytanego modelu. Wczytaj model rozmowy (np. Qwen, Mistral) i odśwież stronę.</p>"
            )
        if not state.chat_models:
            names = ", ".join(m.id for m in state.models)
            return (
                f"<p class=error><b>Błędny model: {e(names)}.</b> To model wektorów (embedding),"
                " nie nadaje się do rozmowy. Wczytaj w LM Studio model rozmowy (np. Qwen, Mistral)"
                " i odśwież stronę.</p>"
            )
        return ""

    def saved_html(self, current):
        """The list of saved conversations: open, download as JSON, delete."""
        rows = []
        for c in self.store.recent():
            ident = e(c["id"])
            on = " class=on" if c["id"] == current else ""
            when = c.get("zmieniono", "").replace("T", " ").replace("Z", "")[:16]
            rows.append(
                f'<li{on}><a href="{self.base}?r={ident}">{e(c.get("tytul") or "(bez pytania)")}</a>'
                f"<span class=hint>{e(when)} UTC · {c['liczba']} wiad."
                f"{' · ' + e(c['baza'].upper()) if c.get('baza') else ''}</span>"
                f'<span class=talk-tools><a href="{self.base}/{ident}.json">JSON</a>'
                f'<form method=post action="{self.base}/usun?r={ident}"'
                " onsubmit=\"return confirm('Usunąć tę rozmowę?')\">"
                "<button class=link>usuń</button></form></span></li>"
            )
        listing = (
            f"<ul class=talks>{''.join(rows)}</ul>"
            if rows
            else ("<p class=hint>Jeszcze nic. Rozmowa zapisuje się sama po każdej odpowiedzi.</p>")
        )
        return f"""<aside class=talk-list><p><button type=button id=new>+ Nowa rozmowa</button></p>
<h2>Zapisane rozmowy</h2>{listing}
<p class=hint>Pliki JSON w <code>badania/rozmowy/</code> (lokalnie).</p></aside>"""

    def modes_html(self, current, base_name):
        """The choice of mode by the question box (only when the project gave its base)."""
        if not self.can_rag:
            return ""
        name = f" {e(base_name)}" if base_name else ""
        limit = RAG_BUDGET_CHARS // 1000
        choices = (
            ("bezposrednio", "Bezpośrednio", "pytanie idzie prosto do modelu"),
            (
                "rag",
                f"Szukaj w bazie{name} (RAG)",
                (
                    f"model dostaje najlepsze fragmenty orzeczeń (do {limit} tys. znaków) i odpowiada"
                    " tylko z nich, ze źródłami [1], [2]"
                ),
            ),
            (
                "pismo",
                f"Pismo (z orzeczeniami{name})",
                (
                    "szkic pisma; argumenty z orzeczeń z bazy i z wcześniejszej rozmowy, brakujące"
                    " dane jako [___]"
                ),
            ),
        )
        radios = "".join(
            f'<label class=mode><input type=radio name=mode value="{value}"'
            f"{' checked' if value == current else ''}> <b>{label}</b>"
            f" <span class=hint>{hint}</span></label>"
            for value, label, hint in choices
        )
        return f"""<fieldset class=modes><legend>Tryb pytania</legend>{radios}
<p class=hint>Przy dopytaniu wyszukiwarka dostaje też poprzednie pytanie; gdy nic nowego nie
znajdzie, model dostaje źródła z poprzedniej odpowiedzi. Lista źródeł na końcu odpowiedzi.</p>
<details class=hint><summary>Polecenia, które dostaje model (zamiast kontekstu prawniczego)</summary>
<p><b>RAG:</b></p><pre class=legend>{e(self.rag_prompt())}</pre>
<p><b>Pismo:</b></p><pre class=legend>{e(self.letter_prompt())}</pre></details></fieldset>"""

    def page(self, state, loaded=None, wanted="", base_name=""):
        status = self.status_html(state)
        intro = (
            "<h1>Zapytaj model</h1>\n<p class=hint>Rozmowa z lokalnym modelem w LM Studio:"
            " bezpośrednio (z własnej wiedzy modelu) albo z fragmentami orzeczeń z bazy (RAG).</p>"
        )
        if status:
            return f'{intro}{status}<p><a class=button href="{self.base}">Sprawdź ponownie</a></p>'
        missing = (
            "<p class=error>Nie ma takiej zapisanej rozmowy.</p>" if wanted and not loaded else ""
        )
        chat_models = state.chat_models
        preferred = (loaded or {}).get("model") or self.settings.chat_model
        options = "".join(
            f'<option value="{e(m.id)}"{" selected" if m.id == preferred else ""}>{e(m.id)}'
            f"{f' (kontekst {m.context} tokenów)' if m.context else ''}</option>"
            for m in chat_models
        )
        legal = (loaded or {}).get("kontekst_prawniczy", True)
        others = [m.id for m in state.models if m.embedding]
        extra = f" Wczytany też model wektorów: {e(', '.join(others))}." if others else ""
        # wczytana rozmowa dla skryptu; "</" w treści nie zamknie znacznika <script>
        loaded_json = json.dumps(
            {"id": loaded["id"], "messages": loaded["wiadomosci"]} if loaded else None,
            ensure_ascii=False,
        ).replace("</", "<\\/")
        return f"""{intro}{missing}
<div class=talk-layout>{self.saved_html((loaded or {}).get("id", ""))}
<form id=chat class=conversation action="{self.base}/odpowiedz" method=post>
<p class=model-line>Model: <select name=model>{options}</select>{extra}</p>
<label class=check><input type=checkbox name=think> Pozwól modelowi myśleć (dokładniej, ale
Qwen myśli wtedy nawet kilka minut, zanim napisze pierwsze słowo)</label>
<label class="check legal"><input type=checkbox name=legal{" checked" if legal else ""}>
<span>Kontekst prawniczy (model dostaje: „{e(SYSTEM)}”). Odznaczone: tylko „{e(PLAIN)}”</span></label>
<p class=hint id=saved></p>
<div id=messages class=messages></div>
<div class=composer>
<textarea name=q rows=3 placeholder="Napisz pytanie (Enter wysyła, Shift+Enter = nowy wiersz)" autofocus></textarea>
{self.modes_html(saved_mode(loaded), base_name)}
<div class=actions><button type=submit>Wyślij</button>
<button type=button class=secondary id=stop hidden title="albo klawisz Esc">Zatrzymaj</button>
<button type=button class=secondary id=clear>Nowa rozmowa</button>
<span class=hint id=busy></span></div></div></form></div>
<form id=save-to action="{self.base}/zapisz" hidden></form>
<a id=home href="{self.base}" hidden></a>
<script type=application/json id=loaded>{loaded_json}</script>
<script>{CHAT_JS}</script>"""


class Turn:
    """One question and answer as written to the conversation's "tury": the exact messages
    the model got (system prompt, earlier conversation, passages), its whole answer, the
    sources with their passages and the search ranking, the citations checked, the
    settings and the times - material to evaluate answers and to fine-tune models on.
    Without a valid conversation id nothing is written."""

    RANKED = 50  # tyle pierwszych orzeczeń rankingu wyszukiwarki (trudne negatywy)

    def __init__(self, store, ident, base, question, mode="bezposrednio"):
        self.store, self.ident, self.base, self.question = store, ident, base, question
        self.when = _now()
        self.started = time.monotonic()
        self.settings = {}
        self.search_s = None
        self.first_s = None
        self.mode = mode
        self.search_text = ""  # zapytanie do wyszukiwarki (temat rozmowy)
        self.sources_from = None  # nr tury, z której wzięto źródła (nowe nic nie dały)

    def finish(self, chat, sent, text, retrieval=None, error="", stopped=False):
        if not ID.match(self.ident):
            return
        record = {
            "czas": self.when,
            "baza": self.base,
            "model": chat.model,
            "tryb": self.mode,
            "ustawienia": self.settings,
            "pytanie": self.question,
            "wyslane": sent,
            "odpowiedz": text,
            "przerwano": stopped,
            "blad": error,
            "czas_s": round(time.monotonic() - self.started, 1),
            "do_pierwszego_slowa_s": _round(self.first_s),
            "wyszukiwanie_s": _round(self.search_s),
        }
        if retrieval is not None:
            cited, unknown = cited_sources(text, retrieval, self.mode)
            record.update(
                {
                    "wyszukiwanie": {
                        "tryb": retrieval.mode,
                        "uwaga": retrieval.notice,
                        "limit_znakow": RAG_BUDGET_CHARS,
                        "zapytanie": self.search_text,
                        "zrodla_z_tury": self.sources_from,
                        "ranking": retrieval.ranked[: self.RANKED],
                    },
                    "zrodla": [_source(s) for s in retrieval.sources],
                    "cytowane": cited,
                    "zmyslone": unknown,
                }
            )
        try:
            self.store.add_turn(self.ident, record, self.base)
        except (OSError, ValueError):
            log.exception("zapytaj: nie zapisano tury rozmowy %s", self.ident)


def _source(s):
    return {
        "nr": s.number,
        "id": s.judgment_id,
        "tytul": s.title,
        "opis": s.details,
        "url": s.url,
        "jezyk": s.language,
        "wynik": s.result,
        "fragmenty": [
            {"start": f.start, "end": f.end, "naglowek": f.heading, "tekst": f.text}
            for f in s.fragments
        ],
    }


def _round(seconds):
    return None if seconds is None else round(seconds, 1)


def sources_footer(text, retrieval, mode="rag"):
    """The list of sources after a RAG answer: [n] title, details, link; the cited ones
    marked, numbers without a source (made up) and the search's notice reported."""
    cited, unknown = cited_sources(text, retrieval, mode)
    if not retrieval.sources:
        lines = ["", "", "Źródła: w bazie nic nie znaleziono."]
    else:
        used = "powołane w piśmie" if mode == "pismo" else "cytowane w odpowiedzi"
        lines = ["", "", f"Źródła (* = {used}):"]
        for s in retrieval.sources:
            mark = "*" if s.number in cited else " "
            lines.append(f"{mark}[{s.number}] {s.title} - {s.details} {s.url}".rstrip())
    if unknown:
        lines.append(f"Uwaga: numery bez źródła (zmyślone): {', '.join(map(str, unknown))}")
    if retrieval.notice:
        lines.append(f"Uwaga: {retrieval.notice}")
    return "\n".join(lines)


def saved_mode(saved):
    """The mode of a saved conversation's last question ("rag": true in older files)."""
    saved = saved or {}
    return saved.get("tryb") or ("rag" if saved.get("rag") else "bezposrednio")


def _json_body(request):
    data = json.loads(request.body(MAX_BODY).decode("utf-8"))
    return data if isinstance(data, dict) else {}  # nie obiekt = puste zapytanie (400 niżej)


def _text(message, status):
    return Response(message, status, "text/plain; charset=utf-8")


# Rozmowa w przeglądarce: bieżąca (id i wiadomości) w sessionStorage, osobno dla każdej
# bazy; po każdej odpowiedzi zapis na serwerze (badania/rozmowy/<id>.json). Odpowiedź
# czytana ze strumienia kawałek po kawałku. Adresy z action/href (dostają prefiks /etpc).
CHAT_JS = """
const form = document.getElementById('chat'), box = document.getElementById('messages');
const area = form.q, stop = document.getElementById('stop'), busy = document.getElementById('busy');
const savedLine = document.getElementById('saved'), saveUrl = document.getElementById('save-to').action;
const home = document.getElementById('home').href;
// tryb pytania (pola tylko, gdy projekt ma bazę): bezposrednio / rag / pismo
const mode = () => (form.querySelector('input[name=mode]:checked') || {value: 'bezposrednio'}).value;
const key = 'zapytaj:' + form.action;
const newId = () => {
  const d = new Date(), p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}`
    + `${p(d.getMinutes())}${p(d.getSeconds())}-${Math.random().toString(36).slice(2, 6).padEnd(4, '0')}`;
};
let current = {id: newId(), talk: []};
const loaded = JSON.parse(document.getElementById('loaded').textContent);
if (loaded) current = {id: loaded.id, talk: loaded.messages};
else {
  try {
    const kept = JSON.parse(sessionStorage.getItem(key) || 'null');
    if (kept && kept.id) current = kept;
  } catch (err) {}
}
const keep = () => { try { sessionStorage.setItem(key, JSON.stringify(current)); } catch (err) {} };
keep();
// Strona jedzie za odpowiedzią tylko, gdy czytający jest na dole; przewinął w górę = zostaje.
const atBottom = () => window.innerHeight + window.scrollY >= document.body.scrollHeight - 80;
const follow = () => window.scrollTo(0, document.body.scrollHeight);
const bubble = (role, text) => {
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  div.textContent = text;
  box.appendChild(div);
  follow();
  return div;
};
current.talk.forEach(m => bubble(m.role, m.content));
const save = async () => {
  try {
    const res = await fetch(saveUrl, {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: current.id, model: form.model.value, legal: form.legal.checked, mode: mode(),
        messages: current.talk})});
    if (!res.ok) throw new Error(await res.text());
    savedLine.textContent = 'Zapisano ' + new Date().toLocaleTimeString() + ' (lista odświeży się po przejściu na stronę)';
  } catch (err) { savedLine.textContent = 'Nie udało się zapisać rozmowy: ' + err.message; }
};
let controller = null;
const done = () => {
  controller = null; stop.hidden = true; busy.textContent = '';
  form.querySelector('button[type=submit]').disabled = false; area.focus();
};
form.addEventListener('submit', async ev => {
  ev.preventDefault();
  const q = area.value.trim();
  if (!q || controller) return;
  area.value = '';
  current.talk.push({role: 'user', content: q}); keep(); bubble('user', q);
  const out = bubble('assistant', '');
  out.classList.add('pending');
  form.querySelector('button[type=submit]').disabled = true;
  stop.hidden = false;
  busy.textContent = mode() === 'bezposrednio' ? 'Model myśli...' : 'Szukam w bazie, potem model czyta fragmenty...';
  controller = new AbortController();
  let text = '';
  try {
    const res = await fetch(form.action, {method: 'POST', signal: controller.signal,
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: current.id, model: form.model.value, think: form.think.checked,
        legal: form.legal.checked, mode: mode(), messages: current.talk})});
    if (!res.ok) throw new Error(await res.text());
    const reader = res.body.getReader(), dec = new TextDecoder();
    busy.textContent = 'Model pisze...';
    for (;;) {
      const {value, done: end} = await reader.read();
      if (end) break;
      const bottom = atBottom();
      text += dec.decode(value, {stream: true});
      out.textContent = text;
      if (bottom) follow();
    }
  } catch (err) {
    if (err.name !== 'AbortError') {
      out.classList.add('failed');
      out.textContent = (text ? text + '\\n\\n' : '') + 'Błąd: ' + err.message;
    } else if (text) out.textContent = text + '\\n\\n(zatrzymano)';
  }
  out.classList.remove('pending');
  if (text) current.talk.push({role: 'assistant', content: text});
  else if (!out.classList.contains('failed')) out.textContent = '(przerwano)';
  keep(); done(); save();
});
area.addEventListener('keydown', ev => {
  if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); form.requestSubmit(); }
});
stop.addEventListener('click', () => { if (controller) controller.abort(); });
document.addEventListener('keydown', ev => {
  if (ev.key === 'Escape' && controller) { ev.preventDefault(); controller.abort(); }
});
// Nowa rozmowa: poprzednia zostaje zapisana na liście, strona startuje od pustej.
const fresh = () => {
  if (controller) controller.abort();
  current = {id: newId(), talk: []}; keep();
  window.location = home;
};
document.getElementById('clear').addEventListener('click', fresh);
document.getElementById('new').addEventListener('click', fresh);
"""
