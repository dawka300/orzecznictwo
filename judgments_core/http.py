"""Polite HTTP client for court-judgment sources (from the SAOS project's SaosClient)."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from urllib.parse import urlencode

import requests
import requests.structures

RETRY_STATUSES = (429, 500, 502, 503, 504)
MAX_WAIT_S = 120
CHALLENGE_WAIT_S = 30

log = logging.getLogger(__name__)


class SourceError(RuntimeError):
    """The source rejected a request or kept failing after all retries."""


class PoliteClient:
    """GET requests with a pause between them and retries with backoff.

    The pause is measured from the previous request, so time spent parsing
    and saving counts towards it.
    """

    def __init__(
        self,
        user_agent,
        pause_s=1.0,
        max_retries=6,
        timeout_s=60.0,
        session=None,
        sleep=time.sleep,
        clock=time.monotonic,
        on_event=None,
    ):
        # on_event(kind, message, url, wait_s): blokady i błędy, np. do fetch_events
        self.on_event = on_event
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.pause_s = pause_s
        self.max_retries = max_retries
        self.timeout_s = timeout_s
        self._sleep = sleep
        self._clock = clock
        self._last_request = None

    @classmethod
    def from_settings(cls, settings, **kwargs):
        return cls(
            settings.user_agent,
            pause_s=settings.pause_s,
            max_retries=settings.max_retries,
            timeout_s=settings.timeout_s,
            **kwargs,
        )

    def get(self, url, params=None, **kwargs):
        """Return the response; retries 429/5xx and network errors, raises SourceError."""
        full_url = _full_url(url, params)
        error = None
        for attempt in range(1, self.max_retries + 1):
            self._wait_for_turn()
            try:
                r = self.session.get(url, params=params, timeout=self.timeout_s, **kwargs)
            except requests.RequestException as e:
                kind, error, wait_s = "siec", str(e), None
            else:
                challenged = _cloudflare_challenge(r)
                if r.status_code not in RETRY_STATUSES and not challenged:
                    if r.status_code >= 400:
                        # 400, 403, 404 ... nie znikną po ponowieniu.
                        error = f"Źródło odrzuciło zapytanie (HTTP {r.status_code})"
                        self._event("odrzucone", error, full_url)
                        raise SourceError(f"{error}: {r.url}")
                    return r
                if challenged:
                    # Cloudflare ocenia klienta i część zapytań zatrzymuje -
                    # po dłuższej przerwie zwykle przepuszcza.
                    kind, error = "cloudflare", "Cloudflare challenge (HTTP 403)"
                    wait_s = CHALLENGE_WAIT_S * attempt
                else:
                    kind = "http_429" if r.status_code == 429 else "http_5xx"
                    error, wait_s = f"HTTP {r.status_code}", _retry_after(r)
            if attempt == self.max_retries:
                self._event(kind, f"{error}; próba {attempt}/{self.max_retries}", full_url)
                break
            wait_s = min(wait_s or 2**attempt, MAX_WAIT_S)
            log.warning(
                "błąd (%s), próba %d/%d, czekam %ss",
                error,
                attempt,
                self.max_retries,
                wait_s,
            )
            self._event(kind, f"{error}; próba {attempt}/{self.max_retries}", full_url, wait_s)
            self._sleep(wait_s)
        self._event("przerwano", f"źródło nie odpowiada po {self.max_retries} próbach", full_url)
        raise SourceError(f"Źródło nie odpowiada ({error}) - przerwano: {url}")

    def _event(self, kind, message, url, wait_s=None):
        if self.on_event is not None:
            self.on_event(kind, message, url, wait_s)

    def get_json(self, url, params=None, **kwargs):
        r = self.get(url, params, **kwargs)
        try:
            return r.json()
        except ValueError as e:
            raise SourceError(f"Odpowiedź nie jest JSON-em: {r.url}") from e

    def get_text(self, url, params=None, **kwargs):
        return self.get(url, params, **kwargs).text

    def _wait_for_turn(self):
        if self._last_request is not None:
            remaining = self.pause_s - (self._clock() - self._last_request)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request = self._clock()


class CurlResponse:
    """The part of requests.Response that PoliteClient and sources use."""

    def __init__(self, status_code, headers, content, url):
        self.status_code = status_code
        self.headers = headers
        self.content = content
        self.url = url

    @property
    def text(self):
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)


class CurlSession:
    """GET through the system `curl` instead of requests.

    Some sources sit behind Cloudflare, which challenges Python's TLS
    handshake (any User-Agent) but lets curl through. Same interface as the
    requests.Session methods PoliteClient uses; network errors are raised as
    requests.ConnectionError so the retries work unchanged.
    """

    def __init__(self, curl="curl", run=subprocess.run):
        self.headers = {}
        self.curl = curl
        self._run = run

    def get(self, url, params=None, timeout=60.0):
        url = _full_url(url, params)
        # Bez --compressed: Cloudflare HUDOC zatrzymywał z nim curl z Windows.
        command = [self.curl, "--silent", "--show-error", "--include"]
        command += ["--max-time", str(timeout)]
        for name, value in self.headers.items():
            command += ["--header", f"{name}: {value}"]
        try:
            done = self._run(command + ["--", url], capture_output=True, check=False)
        except OSError as e:
            raise requests.ConnectionError(f"nie można uruchomić curl: {e}") from e
        if done.returncode != 0:
            stderr = done.stderr.decode("utf-8", errors="replace").strip()
            raise requests.ConnectionError(f"curl (kod {done.returncode}): {stderr}")
        return _parse_curl_output(done.stdout, url)


def _parse_curl_output(output, url):
    """Split `curl --include` output into status, headers and body."""
    head, _, body = output.partition(b"\r\n\r\n")
    # "100 Continue" albo CONNECT proxy poprzedzają czasem właściwą odpowiedź.
    while body.startswith(b"HTTP/") and b"\r\n\r\n" in body:
        head, _, body = body.partition(b"\r\n\r\n")
    lines = head.decode("iso-8859-1").split("\r\n")
    headers = requests.structures.CaseInsensitiveDict()
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip()] = value.strip()
    return CurlResponse(int(lines[0].split(" ")[1]), headers, body, url)


def _full_url(url, params):
    """URL with the query string, for the event log."""
    if not params:
        return url
    return f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"


def _cloudflare_challenge(response):
    """403 with a Cloudflare bot check instead of the content."""
    return response.status_code == 403 and response.headers.get("cf-mitigated") == "challenge"


def _retry_after(response):
    """Seconds from a numeric Retry-After header, else None."""
    value = response.headers.get("Retry-After", "")
    return int(value) if value.isdigit() else None
