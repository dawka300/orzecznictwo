"""Claude's report on a document's language markers (the second layer of the analysis).

Optional: only with an Anthropic API key (<PREFIX>_CLAUDE_API_KEY, else ANTHROPIC_API_KEY
from the environment or .env) and only when the user ticks it for that analysis - the
markers' numbers and up to judgments_core.markers.EXAMPLES sentences per marker leave the
computer (not the whole document). Claude reads them with a discourse-analysis
instruction (framing, presuppositions, agency, illocution) and writes the report in
Polish. The official SDK (anthropic, in the www image); the model from the settings.
"""

from __future__ import annotations

import html
import json
import os
import re
import time

from judgments_core.settings import DOTENV_PATH, read_dotenv

# Każda odpowiedź Claude (analiza dokumentu, badanie) z tym, co wysłano: materiał do nauki
# i porównań, lokalnie (badania/ w .gitignore).
SAVE_DIR = DOTENV_PATH.parent / "badania"

FALLBACK_MODEL = "claude-opus-4-8"  # gdy model główny odmówi (stop_reason "refusal")
MAX_TOKENS = 16000

SYSTEM = """Jesteś analitykiem dyskursu i psycholingwistą pracującym dla prawnika. Dostajesz nie
cały dokument, lecz jego znaczniki językowe: liczbę wystąpień na 1000 słów, znalezione słowa
i zdania, w których wystąpiły. Dokument to pismo procesowe albo orzeczenie sądu.

Twoim zadaniem jest dekonstrukcja tekstu pod kątem ukrytych założeń, motywów i dynamiki
argumentacji - nie streszczanie faktów. Przeanalizuj materiał w tych krokach:

1. PĘKNIĘCIA W NARRACJI: gdzie argumentacja zmienia ton, pojawiają się uniki, niespójności
   albo nadmiarowe usprawiedliwienia?
2. UKRYTE ZAŁOŻENIA (presupozycje i ramy): co autor przyjmuje za pewnik bez dowodu (zwłaszcza
   w zdaniach z „oczywiście”, „niewątpliwie”, „zawsze”, „nigdy”)? Jakie alternatywne
   interpretacje pominięto? Jak zdefiniowano problem, kogo wskazano jako przyczynę, jakie
   oceny moralne i jaki „jedyny rozsądny” wniosek się podsuwa?
3. POZYCJONOWANIE SPRAWCZOŚCI: jak autor obsadza siebie i strony (ofiara, strażnik reguł,
   arbiter ...)? Gdzie ukrywa sprawcę (strona bierna, bezosobowość) i komu przypisuje
   odpowiedzialność?
4. WARSTWA ILOKUCYJNA: czego autor faktycznie domaga się od odbiorcy, ukrywając to pod
   pozorem neutralnej wypowiedzi?
5. WNIOSKI DLA PRAWNIKA: które miejsca są najsłabsze argumentacyjnie i warto je zaatakować
   albo wzmocnić; czego szukać w pełnym tekście.

Zasady: opieraj się wyłącznie na podanych zdaniach i liczbach i cytuj je; mów, gdzie materiał
nie wystarcza do wniosku. Analizujesz tekst, nie diagnozujesz ludzi - bez ocen psychiatrycznych
i bez przypisywania cech osobom. Pisz po polsku, zwięźle, nagłówki jako „## ”, punkty jako „- ”."""


class ClaudeError(RuntimeError):
    """No report; the message is for the user."""


def api_key(settings):
    return (
        settings.claude_api_key
        or os.environ.get("ANTHROPIC_API_KEY", "")
        or read_dotenv(DOTENV_PATH).get("ANTHROPIC_API_KEY", "")
    )


def available(settings):
    """(True, "") when a report can be asked for, else (False, why)."""
    try:
        import anthropic  # noqa: F401 - obraz www
    except ImportError:
        return False, "brak biblioteki anthropic (docker compose build www)"
    if not api_key(settings):
        return False, "brak klucza API Anthropic (ANTHROPIC_API_KEY w .env)"
    return True, ""


def markers_prompt(markers, filename):
    """The markers as the user message: numbers, words found, example sentences."""
    language = "polski" if markers.lang == "pl" else "angielski"
    lines = [
        f"Dokument: {filename}; {markers.words} słów, {markers.sentences} zdań, język: {language}.",
        "",
        "Znaczniki (liczba na 1000 słów; znalezione słowa; zdania z wystąpieniami):",
    ]
    for m in markers.markers:
        found = ", ".join(f"{w} ×{n}" for w, n in sorted(m.found.items(), key=lambda x: -x[1]))
        lines.append(f"\n### {m.title} - {m.per_1000} na 1000 słów ({m.count})")
        lines.append(f"Co może znaczyć: {m.meaning}")
        if found:
            lines.append(f"Słowa: {found}")
        lines += [f"- „{s}”" for s in m.sentences]
    return "\n".join(lines)


def report(settings, markers, filename):
    """Claude's report as text; ClaudeError when there is none."""
    return ask(settings, SYSTEM, markers_prompt(markers, filename))[0]


# $ za milion tokenów (wejście, wyjście) - do kosztu w raporcie; inny model: tylko tokeny
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0)}


def cost(settings, usage):
    """'12 345 tokenów wejścia, 2 100 wyjścia, ~$0.09' for the report."""
    tokens_in, tokens_out = (f"{usage[k]:,}".replace(",", " ") for k in ("input", "output"))
    text = f"{tokens_in} tokenów wejścia, {tokens_out} wyjścia"
    price = PRICES.get(settings.claude_model)
    if price:
        text += f", ~${(usage['input'] * price[0] + usage['output'] * price[1]) / 1e6:.2f}"
    return text


def ask(settings, system, prompt, timeout_s=600.0):
    """(Claude's answer as text, {"input": tokens, "output": tokens}); ClaudeError when
    there is no answer."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key(settings), timeout=timeout_s)
    try:
        response = client.beta.messages.create(
            model=settings.claude_model,
            max_tokens=MAX_TOKENS,
            betas=["server-side-fallback-2026-06-01"],
            fallbacks=[{"model": FALLBACK_MODEL}],
            output_config={"effort": "high"},
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError as error:
        raise ClaudeError("nieprawidłowy klucz API Anthropic") from error
    except anthropic.PermissionDeniedError as error:
        raise ClaudeError("klucz API bez uprawnień do tego modelu") from error
    except anthropic.RateLimitError as error:
        raise ClaudeError("limit zapytań Anthropic - spróbuj za chwilę") from error
    except anthropic.APIStatusError as error:
        raise ClaudeError(f"błąd API Anthropic ({error.status_code}): {error.message}") from error
    except anthropic.APIConnectionError as error:
        raise ClaudeError("brak połączenia z API Anthropic") from error
    if response.stop_reason == "refusal":
        raise ClaudeError("Claude odmówił analizy tego materiału")
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise ClaudeError("pusta odpowiedź")
    if response.stop_reason == "max_tokens":
        text += "\n\n(odpowiedź ucięta na limicie długości)"
    usage = {"input": response.usage.input_tokens, "output": response.usage.output_tokens}
    return text, usage


def save(kind, ident, files, meta, root=None):
    """badania/<kind>/<date>_<ident>/ with files ({name: text}) and meta.json; the folder.
    A file name ending in .csv gets a BOM (Excel reads Polish letters)."""
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    folder = (root or SAVE_DIR) / kind / f"{stamp}_{ident}"
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        encoding = "utf-8-sig" if name.endswith(".csv") else "utf-8"
        (folder / name).write_text(text, encoding=encoding)
    meta = {"zapisano": stamp, **meta}
    (folder / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return folder


def markers_csv(markers):
    """The markers as CSV: key, title, per 1000 words, count, words found."""
    lines = ["znacznik,nazwa,na_1000_slow,liczba,slowa"]
    for m in markers.markers:
        found = "; ".join(f"{w} x{n}" for w, n in sorted(m.found.items(), key=lambda x: -x[1]))
        lines.append(",".join(_csv_cell(v) for v in (m.key, m.title, m.per_1000, m.count, found)))
    return "\n".join(lines) + "\n"


def _csv_cell(value):
    text = str(value)
    return f'"{text.replace(chr(34), chr(34) * 2)}"' if any(c in text for c in ',"\n') else text


def to_html(text):
    """The report's simple Markdown (## headings, - points, paragraphs) as safe HTML."""
    out, items = [], []

    def flush():
        if items:
            out.append("<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>")
            items.clear()

    for line in text.splitlines():
        line = line.strip()
        safe = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(line))
        if line.startswith("#"):
            flush()
            out.append(f"<h3>{safe.lstrip('#').strip()}</h3>")
        elif line.startswith(("- ", "* ")):
            items.append(safe[2:])
        elif line:
            flush()
            out.append(f"<p>{safe}</p>")
        else:
            flush()
    flush()
    return "\n".join(out)
