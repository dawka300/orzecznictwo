"""Polite HTTP client for court-judgment sources (from the SAOS project's SaosClient)."""

from __future__ import annotations

import logging
import time

import requests

RETRY_STATUSES = (429, 500, 502, 503, 504)
MAX_WAIT_S = 120

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
    ):
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
        error = None
        for attempt in range(1, self.max_retries + 1):
            self._wait_for_turn()
            try:
                r = self.session.get(url, params=params, timeout=self.timeout_s, **kwargs)
            except requests.RequestException as e:
                error, wait_s = e, None
            else:
                if r.status_code not in RETRY_STATUSES:
                    if r.status_code >= 400:
                        # 400, 403, 404 ... nie znikną po ponowieniu.
                        raise SourceError(
                            f"Źródło odrzuciło zapytanie (HTTP {r.status_code}): {r.url}"
                        )
                    return r
                error, wait_s = f"HTTP {r.status_code}", _retry_after(r)
            if attempt == self.max_retries:
                break
            wait_s = min(wait_s or 2**attempt, MAX_WAIT_S)
            log.warning(
                "błąd (%s), próba %d/%d, czekam %ss",
                error,
                attempt,
                self.max_retries,
                wait_s,
            )
            self._sleep(wait_s)
        raise SourceError(f"Źródło nie odpowiada ({error}) - przerwano: {url}")

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


def _retry_after(response):
    """Seconds from a numeric Retry-After header, else None."""
    value = response.headers.get("Retry-After", "")
    return int(value) if value.isdigit() else None
