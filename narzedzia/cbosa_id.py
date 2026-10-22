"""Zbieranie identyfikatorów orzeczeń z wyszukiwarki CBOSA do plików (narzędzie użytkownika).

UWAGA: robots.txt orzeczenia.nsa.gov.pl zabrania automatom wyszukiwarki i list
wyników (/cbo/search, /cbo/find). Technicznie to działa; uruchomienie to decyzja
i odpowiedzialność użytkownika (komendy.md, sekcja CBOSA). Dlatego to osobne
narzędzie, nie część projektu: projekt (python -m nsa pobierz) pobiera tylko
dozwolone strony /doc/<ID> z plików, które to narzędzie zapisuje.

Zakres dat dzielony na okresy (domyślnie miesiące); każdy okres = jedno
wyszukiwanie i plik <katalog>/<rok>/<okres>.txt (jeden identyfikator w linii).
W trakcie zbierania plik nazywa się .txt.part (już go czyta pobierz), po
skończonym okresie .txt; okresy z gotowym .txt są pomijane przy następnym
uruchomieniu (--nadpisz: zbiera jeszcze raz).

Transport: łańcuch requests -> curl -> playwright; następny przejmuje, gdy
bieżący kilka razy z rzędu nie dostanie poprawnej strony (błąd sieci, 5xx,
pusta odpowiedź). Przerwa między zapytaniami co najmniej 3 s; bez proxy.

    docker compose run --rm --no-deps www python narzedzia/cbosa_id.py --od 2025-03-05 --do 2025-03-31
    python narzedzia/cbosa_id.py --help
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from judgments_core.settings import Settings
from nsa.cbosa import ids_in

log = logging.getLogger("cbosa_id")

BASE_URL = "https://orzeczenia.nsa.gov.pl"
QUERY_URL = f"{BASE_URL}/cbo/query"
SEARCH_URL = f"{BASE_URL}/cbo/search"
FIND_URL = f"{BASE_URL}/cbo/find?p={{page}}"
OUT_DIR = ROOT / "nsa" / "database" / "cbosa-id"
MIN_PAUSE_S = 3.0
TIMEOUT_S = 60.0
# Tyle nieudanych zapytań z rzędu = następny transport (na ostatnim: koniec).
SWITCH_AFTER = 3
# Strona wyników krótsza niż to = coś nie tak (pusta odpowiedź serwera).
MIN_PAGE_CHARS = 500
# Pod każdym wynikiem lista orzeczeń powiązanych (inne daty) - to nie wyniki.
RELATED_BLOCK_RE = re.compile(r'<span class="powiazane">.*?</span>', re.DOTALL)
COUNT_RE = re.compile(r"Znaleziono\s+([\d\s\xa0.]+?)\s+orzecze\w*,\s*Str\.\s*\d+\s+z\s+(\d+)")
KINDS = {"wyrok": "Wyrok", "postanowienie": "Postanowienie", "uchwala": "Uchwała"}
COURT_PREFIXES = {"NSA": "Naczelny Sąd Administracyjny", "WSA": "Wojewódzki Sąd Administracyjny"}


# --- transport: requests, curl, playwright ------------------------------------


class RequestsHttp:
    """Python requests; the session keeps CBOSA's cookies (the search lives in the session)."""

    name = "requests"

    def __init__(self, user_agent):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent

    def fetch(self, url, data=None):
        if data is None:
            r = self.session.get(url, timeout=TIMEOUT_S)
        else:
            r = self.session.post(url, data=data, timeout=TIMEOUT_S)
        return r.status_code, r.text

    def close(self):
        self.session.close()


class CurlHttp:
    """The system curl with a cookie file (another TLS stack: gets through when Python doesn't)."""

    name = "curl"

    def __init__(self, user_agent):
        self.user_agent = user_agent
        with tempfile.NamedTemporaryFile(prefix="cbosa-", suffix=".cookies", delete=False) as f:
            self.jar = f.name

    def fetch(self, url, data=None):
        command = ["curl", "--silent", "--show-error", "--location"]
        command += ["--max-time", str(int(TIMEOUT_S)), "--user-agent", self.user_agent]
        command += ["--cookie", self.jar, "--cookie-jar", self.jar, "--write-out", "\n%{http_code}"]
        for key, value in (data or {}).items():
            command += ["--data-urlencode", f"{key}={value}"]
        done = subprocess.run(command + ["--", url], capture_output=True, check=False)
        if done.returncode != 0:
            stderr = done.stderr.decode("utf-8", errors="replace").strip()
            raise OSError(f"curl (kod {done.returncode}): {stderr}")
        body, _, status = done.stdout.decode("utf-8", errors="replace").rpartition("\n")
        return int(status), body

    def close(self):
        Path(self.jar).unlink(missing_ok=True)


FETCH_JS = """async ([url, data, timeoutMs]) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const options = {signal: controller.signal, credentials: "include"};
        if (data) {
            options.method = "POST";
            options.body = new URLSearchParams(data);
        }
        const r = await fetch(url, options);
        return {status: r.status, body: await r.text()};
    } finally {
        clearTimeout(timer);
    }
}"""


class PlaywrightHttp:
    """A headless Chromium: opens the search form, then sends the requests from inside the page."""

    name = "playwright"

    def __init__(self, user_agent):
        from playwright.sync_api import sync_playwright  # tylko gdy łańcuch tu dojdzie

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        self._page = self._browser.new_page()
        self._page.goto(QUERY_URL, wait_until="domcontentloaded", timeout=TIMEOUT_S * 1000)

    def fetch(self, url, data=None):
        result = self._page.evaluate(FETCH_JS, [url, data, int(TIMEOUT_S * 1000)])
        return result["status"], result["body"]

    def close(self):
        self._browser.close()
        self._playwright.stop()


TRANSPORTS = {"requests": RequestsHttp, "curl": CurlHttp, "playwright": PlaywrightHttp}


class Chain:
    """Polite requests through the transport chain; `generation` changes on a switch
    (a new transport has new cookies, so the search must be sent again)."""

    def __init__(self, names, user_agent, pause_s):
        self.names = names
        self.user_agent = user_agent
        self.pause_s = max(pause_s, MIN_PAUSE_S)
        self.position = -1
        self.generation = 0
        self.http = None
        self._last = None
        self._next()

    def fetch(self, url, data=None):
        failures = 0
        while True:
            self._wait()
            try:
                status, body = self.http.fetch(url, data)
                error = (
                    None
                    if status == 200 and len(body) >= MIN_PAGE_CHARS
                    else f"HTTP {status}, {len(body)} zn."
                )
            except Exception as e:  # noqa: BLE001 - każdy błąd transportu liczy się jako nieudane zapytanie
                error = f"{type(e).__name__}: {e}"
            if error is None:
                return body
            failures += 1
            log.warning(
                "%s: %s (%s, próba %d/%d)", self.http.name, error, url, failures, SWITCH_AFTER
            )
            if failures >= SWITCH_AFTER:
                self._next()
                failures = 0
                if data is None:
                    return None  # nowa sesja: wołający wyszuka jeszcze raz
            else:
                time.sleep(2**failures)

    def close(self):
        if self.http is not None:
            self.http.close()

    def _next(self):
        self.close()
        while True:
            self.position += 1
            if self.position >= len(self.names):
                raise SystemExit(
                    "CBOSA nie odpowiada żadnym transportem - koniec (zebrane okresy zostają)."
                )
            name = self.names[self.position]
            try:
                self.http = TRANSPORTS[name](self.user_agent)
            except Exception as e:  # noqa: BLE001 - np. brak curl albo Playwrighta
                log.warning("transport %s niedostępny: %s", name, e)
                continue
            self.generation += 1
            log.info("transport: %s", name)
            return

    def _wait(self):
        if self._last is not None:
            remaining = self.pause_s - (time.monotonic() - self._last)
            if remaining > 0:
                time.sleep(remaining)
        self._last = time.monotonic()


# --- wyszukiwanie ------------------------------------------------------------


def search_form(args, start, end):
    """The CBOSA search form (fields as on /cbo/query) for one period."""
    form = {
        "wszystkieSlowa": args.words or "",
        "wystepowanie": args.where,
        "odmiana": "on",
        "sygnatura": args.docket or "",
        "sad": court_name(args.court),
        "rodzaj": KINDS.get(args.kind, "dowolny"),
        "symbole": args.symbols or "",
        "odDaty": start.isoformat(),
        "doDaty": end.isoformat(),
        "sedziowie": args.judge or "",
        "funkcja": "dowolna",
        "rodzaj_organu": "",
        "hasla": args.keywords or "",
        "akty": "",
        "przepisy": "",
        "publikacje": "",
        "glosy": "",
        "submit": "Szukaj",
    }
    if args.reasons == "tak":
        form["takUzasadnienie"] = "on"
    elif args.reasons == "nie":
        form["nieUzasadnienie"] = "on"
    return form


def court_name(value):
    """'NSA' -> Naczelny Sąd Administracyjny, 'WSA w Krakowie' -> Wojewódzki Sąd ... w Krakowie."""
    if not value:
        return "dowolny"
    first, _, rest = value.partition(" ")
    if first in COURT_PREFIXES:
        return f"{COURT_PREFIXES[first]} {rest}".strip()
    return value


def collect(chain, form, max_pages, write):
    """All result pages of one search; write(new ids) after each page; returns the count."""
    seen, previous, session, page, retried, expected = set(), None, None, 1, False, None
    while True:
        if session != chain.generation:
            body = chain.fetch(SEARCH_URL, form)  # wyniki od strony 1
            session = chain.generation
            if page > 1:
                body = chain.fetch(FIND_URL.format(page=page))
        else:
            body = chain.fetch(FIND_URL.format(page=page))
        if body is None:
            continue  # zmiana transportu: wyszukaj jeszcze raz, ta sama strona
        found = result_ids(body)
        total, pages = result_count(body)
        fresh = [i for i in found if i not in seen]
        if not fresh or found == previous:
            if page > 1 and not retried and (pages is None or page <= pages):
                # Może wygasła sesja wyszukiwania: jeszcze raz wyszukanie i ta strona.
                retried, session = True, None
                continue
            return _done(seen, expected)
        if page == 1:
            expected = total
            log.info("  znaleziono %s orzeczeń, stron %s", total, pages)
        retried, previous = False, found
        seen.update(fresh)
        write(fresh)
        log.info("  strona %d z %s: nowych %d, razem %d", page, pages, len(fresh), len(seen))
        if (max_pages and page >= max_pages) or (pages is not None and page >= pages):
            return _done(seen, expected)
        page += 1


def result_ids(body):
    """Ids of the results on a page, without the related judgments listed under each."""
    return ids_in(RELATED_BLOCK_RE.sub("", body))


def result_count(body):
    """(results, pages) from 'Znaleziono 8350 orzeczeń, Str. 1 z 835'; (None, None) if absent."""
    match = COUNT_RE.search(body)
    if match is None:
        return None, None
    return int(re.sub(r"\D", "", match.group(1))), int(match.group(2))


def _done(seen, expected):
    if expected is not None and len(seen) < expected:
        log.warning(
            "  zebrano %d z %d orzeczeń (reszta: uruchomić ten okres jeszcze raz)",
            len(seen),
            expected,
        )
    return len(seen)


def periods(start, end, size):
    """(first day, last day) of each period between start and end, inclusive."""
    day = start
    while day <= end:
        if size == "miesiac":
            following = (day.replace(day=1) + timedelta(days=32)).replace(day=1)
        elif size == "tydzien":
            following = day + timedelta(days=7)
        else:
            following = day + timedelta(days=1)
        last = min(following - timedelta(days=1), end)
        yield day, last
        day = last + timedelta(days=1)


def file_name(start, end, args):
    """2025-03 (whole month) or 2025-03-05_2025-03-31, plus the filters: 2025-03_nsa_wyrok."""
    whole_month = start.day == 1 and (end + timedelta(days=1)).day == 1 and start.month == end.month
    name = start.strftime("%Y-%m") if whole_month else f"{start}_{end}"
    filters = [
        args.court,
        args.kind,
        args.words,
        args.docket,
        args.symbols,
        args.judge,
        args.keywords,
    ]
    if args.reasons:
        filters.append(f"uzasadnienie-{args.reasons}")
    return "_".join([name] + [_slug(f) for f in filters if f])


def _slug(text):
    text = unicodedata.normalize("NFKD", text.replace("ł", "l").replace("Ł", "L"))
    text = text.encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40]


# --- polecenie ---------------------------------------------------------------


def build_parser():
    p = argparse.ArgumentParser(
        prog="python narzedzia/cbosa_id.py",
        description="Zbiera identyfikatory orzeczeń z wyszukiwarki CBOSA do plików"
        " <katalog>/<rok>/<okres>.txt; potem: python -m nsa pobierz.",
    )
    p.add_argument(
        "--od",
        dest="date_from",
        type=date.fromisoformat,
        required=True,
        help="data orzeczenia od (RRRR-MM-DD)",
    )
    p.add_argument(
        "--do",
        dest="date_to",
        type=date.fromisoformat,
        help="data orzeczenia do (RRRR-MM-DD, domyślnie dziś)",
    )
    p.add_argument(
        "--okres",
        dest="period",
        choices=("miesiac", "tydzien", "dzien"),
        default="miesiac",
        help="na jakie okresy dzielić zakres (jedno wyszukiwanie i plik na okres; domyślnie miesiac)",
    )
    p.add_argument(
        "--sad", dest="court", help="np. NSA, 'WSA w Krakowie' albo pełna nazwa z formularza CBOSA"
    )
    p.add_argument(
        "--rodzaj", dest="kind", choices=tuple(KINDS), help="wyrok / postanowienie / uchwala"
    )
    p.add_argument(
        "--uzasadnienie",
        dest="reasons",
        choices=("tak", "nie"),
        help="tylko z / tylko bez uzasadnienia",
    )
    p.add_argument("--slowa", dest="words", help="słowa w treści")
    p.add_argument(
        "--gdzie",
        dest="where",
        choices=("gdziekolwiek", "w sentencji", "w tezach", "w uzasadnieniu"),
        default="gdziekolwiek",
        help="gdzie szukać słów (domyślnie gdziekolwiek)",
    )
    p.add_argument("--sygnatura", dest="docket", help="sygnatura (część), np. 'II SA/Wa'")
    p.add_argument("--symbol", dest="symbols", help="symbol sprawy, np. 6110")
    p.add_argument("--sedzia", dest="judge", help="nazwisko sędziego")
    p.add_argument("--hasla", dest="keywords", help="hasło tematyczne")
    p.add_argument(
        "--maks-stron",
        dest="max_pages",
        type=int,
        default=0,
        help="najwyżej tyle stron wyników na okres (0 = wszystkie)",
    )
    p.add_argument(
        "--przerwa",
        dest="pause_s",
        type=float,
        default=MIN_PAUSE_S,
        help=f"sekundy między zapytaniami (najmniej {MIN_PAUSE_S:g})",
    )
    p.add_argument(
        "--transport",
        default="requests,curl,playwright",
        help="łańcuch transportów po przecinku (domyślnie requests,curl,playwright)",
    )
    p.add_argument("--kontakt", dest="contact", help="e-mail do User-Agent (zalecane)")
    p.add_argument(
        "--katalog",
        dest="out_dir",
        type=Path,
        default=OUT_DIR,
        help=f"gdzie zapisywać (domyślnie {OUT_DIR.relative_to(ROOT)})",
    )
    p.add_argument(
        "--nadpisz",
        dest="overwrite",
        action="store_true",
        help="zbierz też okresy, które mają już gotowy plik",
    )
    p.add_argument(
        "--na-sucho",
        dest="dry_run",
        action="store_true",
        help="tylko pokaż okresy i pliki, nic nie pobieraj",
    )
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr
    )
    end = args.date_to or date.today()
    if end < args.date_from:
        raise SystemExit("--do wcześniej niż --od")
    names = [n.strip() for n in args.transport.split(",") if n.strip()]
    unknown = [n for n in names if n not in TRANSPORTS]
    if unknown or not names:
        raise SystemExit(
            f"nieznany transport: {', '.join(unknown) or '(pusto)'} ({' / '.join(TRANSPORTS)})"
        )
    user_agent = Settings.from_env("nsa").user_agent
    if args.contact:
        user_agent = f"{user_agent} (kontakt: {args.contact})"

    todo = []
    for start, last in periods(args.date_from, end, args.period):
        target = args.out_dir / str(start.year) / f"{file_name(start, last, args)}.txt"
        if target.exists() and not args.overwrite:
            log.info("%s - już zebrane, pomijam", target.relative_to(args.out_dir))
            continue
        todo.append((start, last, target))
    log.info("okresów do zebrania: %d (katalog %s)", len(todo), args.out_dir)
    if args.dry_run:
        for start, last, target in todo:
            print(f"  {start} .. {last}  ->  {target}")
        return 0

    chain = Chain(names, user_agent, args.pause_s)
    total = 0
    try:
        for start, last, target in todo:
            log.info("%s .. %s -> %s", start, last, target.relative_to(args.out_dir))
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_name(target.name + ".part")
            with open(part, "w", encoding="utf-8") as out:

                def write(ids, out=out):
                    out.write("".join(f"{i}\n" for i in ids))
                    out.flush()

                count = collect(chain, search_form(args, start, last), args.max_pages, write)
            if args.max_pages:
                log.info("  %d identyfikatorów (--maks-stron: plik zostaje jako .part)", count)
            else:
                part.replace(target)
                log.info("  gotowe: %d identyfikatorów", count)
            total += count
    finally:
        chain.close()
    log.info("Razem %d identyfikatorów. Pobieranie orzeczeń: python -m nsa pobierz", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
