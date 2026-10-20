"""Polite HTTP client for court-judgment sources (from the SAOS project's SaosClient).

When to ask and what to do with failures is decided here, the same for every
source; how the request travels is the Transport strategy
(judgments_core/transport.py).
"""

from __future__ import annotations

import logging
import time

import requests

from judgments_core.transport import RequestsTransport, full_url, is_cloudflare_challenge

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
        transport=None,
        sleep=time.sleep,
        clock=time.monotonic,
        on_event=None,
    ):
        # on_event(kind, message, url, wait_s): blokady i błędy, np. do fetch_events
        self.on_event = on_event
        self.transport = transport or RequestsTransport()
        self.transport.headers["User-Agent"] = user_agent
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

    def get(self, url, params=None):
        """Return the response; retries 429/5xx and network errors, raises SourceError."""
        url_with_params = full_url(url, params)
        error = None
        for attempt in range(1, self.max_retries + 1):
            self._wait_for_turn()
            try:
                r = self.transport.get(url, params, self.timeout_s)
            except requests.RequestException as e:
                kind, error, wait_s = "siec", str(e), None
            else:
                challenged = is_cloudflare_challenge(r)
                if r.status_code not in RETRY_STATUSES and not challenged:
                    if r.status_code >= 400:
                        # 400, 403, 404 ... nie znikną po ponowieniu.
                        error = f"Źródło odrzuciło zapytanie (HTTP {r.status_code})"
                        self._event("odrzucone", error, url_with_params)
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
                self._event(kind, f"{error}; próba {attempt}/{self.max_retries}", url_with_params)
                break
            wait_s = min(wait_s or 2**attempt, MAX_WAIT_S)
            log.warning(
                "błąd (%s), próba %d/%d, czekam %ss",
                error,
                attempt,
                self.max_retries,
                wait_s,
            )
            self._event(
                kind, f"{error}; próba {attempt}/{self.max_retries}", url_with_params, wait_s
            )
            self._sleep(wait_s)
        self._event(
            "przerwano", f"źródło nie odpowiada po {self.max_retries} próbach", url_with_params
        )
        raise SourceError(f"Źródło nie odpowiada ({error}) - przerwano: {url}")

    def _event(self, kind, message, url, wait_s=None):
        if self.on_event is not None:
            self.on_event(kind, message, url, wait_s)

    def get_json(self, url, params=None):
        r = self.get(url, params)
        try:
            return r.json()
        except ValueError as e:
            raise SourceError(f"Odpowiedź nie jest JSON-em: {r.url}") from e

    def get_text(self, url, params=None):
        return self.get(url, params).text

    def close(self):
        self.transport.close()

    def _wait_for_turn(self):
        if self._last_request is not None:
            remaining = self.pause_s - (self._clock() - self._last_request)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request = self._clock()


def _retry_after(response):
    """Seconds from a numeric Retry-After header, else None."""
    value = response.headers.get("Retry-After", "")
    return int(value) if value.isdigit() else None
