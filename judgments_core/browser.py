"""GET through a real browser (Playwright, headless Chromium) - local use only.

When Cloudflare blocks both Python and curl, a browser still gets through: it
opens the source's page (passing Cloudflare's check like a visitor) and sends
the requests with fetch() from inside that page, as the site's own scripts
do. Playwright is an optional dependency (requirements-dev.txt plus
`python -m playwright install chromium`), imported only when used, so the
hosting doesn't need it.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import requests
import requests.structures

from judgments_core.http import CurlResponse, _full_url

log = logging.getLogger(__name__)

FETCH_JS = """async ([url, timeoutMs]) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const r = await fetch(url, {signal: controller.signal, credentials: "include"});
        return {status: r.status, headers: Object.fromEntries(r.headers), body: await r.text()};
    } finally {
        clearTimeout(timer);
    }
}"""


class PlaywrightSession:
    """Same interface as requests.Session.get for PoliteClient; close() at the end.

    The browser keeps its own User-Agent: a made-up one next to a real
    Chromium fingerprint looks more suspicious to Cloudflare, not less.
    """

    def __init__(self, headless=True):
        self.headers = {}  # User-Agent z PoliteClient - przeglądarka ma własny
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._page = None
        self._origin = None
        self._reload = True

    def get(self, url, params=None, timeout=60.0):
        url = _full_url(url, params)
        self._start()  # brak Playwrighta/Chromium: błąd od razu, bez ponawiania
        try:
            self._open_origin(url, timeout)
            result = self._page.evaluate(FETCH_JS, [url, int(timeout * 1000)])
        except Exception as e:  # każdy błąd przeglądarki = błąd sieci, PoliteClient ponowi
            self._reload = True
            raise requests.ConnectionError(f"przeglądarka: {type(e).__name__}: {e}") from e
        headers = requests.structures.CaseInsensitiveDict(result["headers"])
        if result["status"] == 403 and headers.get("cf-mitigated"):
            # Cloudflare zatrzymał zapytanie: przed następnym strona od nowa.
            self._reload = True
        return CurlResponse(result["status"], headers, result["body"].encode("utf-8"), url)

    def close(self):
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()
        self._browser = self._playwright = self._page = None

    def _start(self):
        if self._page is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise RuntimeError(
                "brak Playwrighta: pip install -r requirements-dev.txt"
                " && python -m playwright install chromium"
            ) from e
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)
        self._page = self._browser.new_page()

    def _open_origin(self, url, timeout):
        u = urlparse(url)
        origin = f"{u.scheme}://{u.netloc}"
        if self._reload or origin != self._origin:
            log.info("przeglądarka: otwieram %s", origin)
            self._page.goto(origin, wait_until="domcontentloaded", timeout=timeout * 1000)
            # chwila na skrypty strony (i ewentualne sprawdzenie Cloudflare)
            self._page.wait_for_timeout(3000)
            self._origin, self._reload = origin, False
