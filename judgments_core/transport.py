"""How a GET reaches the source: interchangeable transports (strategy pattern).

PoliteClient (judgments_core/http.py) decides when and how often to ask;
a Transport only performs one GET. Strategies:

- RequestsTransport - plain Python (requests); enough for most sources;
- CurlTransport - the system curl: Cloudflare challenges Python's TLS
  handshake but lets curl through;
- PlaywrightTransport (judgments_core/browser.py) - a real headless browser,
  when Cloudflare stops curl too (local use only);
- ProxySellerTransport (judgments_core/proxy_seller.py) - residential proxy
  from Proxy-Seller Client API v2, when credentials and funds are available;
- FallbackTransport - a chain of the above: starts with the first and moves
  to the next one when the current keeps getting Cloudflare challenges.

A project names its chain (Project.transports, e.g. requests -> curl ->
playwright for HUDOC); <PREFIX>_TRANSPORT in the settings overrides it.
"""

from __future__ import annotations

import json
import logging
import subprocess
from abc import ABC, abstractmethod
from urllib.parse import urlencode

import requests
import requests.structures

log = logging.getLogger(__name__)


class Transport(ABC):
    """One GET; the response has status_code, headers, content, text, json(), url.

    Network errors are raised as requests.RequestException, so PoliteClient
    retries them the same way for every transport.
    """

    name = ""

    def __init__(self):
        self.headers = {}  # PoliteClient ustawia User-Agent

    @abstractmethod
    def get(self, url, params=None, timeout=60.0):
        """The response for url (+ params)."""

    def close(self):
        """Release what the transport holds (browser, connections)."""


class RequestsTransport(Transport):
    name = "requests"

    def __init__(self, session=None):
        super().__init__()
        self.session = session or requests.Session()
        self.headers = self.session.headers

    def get(self, url, params=None, timeout=60.0):
        return self.session.get(url, params=params, timeout=timeout)

    def close(self):
        self.session.close()


class Response:
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


class CurlTransport(Transport):
    """GET through the system `curl`, each request a separate process."""

    name = "curl"

    def __init__(self, curl="curl", run=subprocess.run):
        super().__init__()
        self.curl = curl
        self._run = run

    def get(self, url, params=None, timeout=60.0):
        url = full_url(url, params)
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


class FallbackTransport(Transport):
    """A chain of transports: the next one takes over when the current is blocked.

    Blocked = `switch_after` Cloudflare challenges in a row. The request that
    hit the last challenge is repeated at once with the next transport; the
    chain doesn't go back during a pass. Transports are created only when
    reached (Playwright is imported only if it is ever needed).
    """

    name = "lancuch"

    def __init__(self, factories, switch_after=2, on_switch=None):
        """factories: [(name, () -> Transport)] in order; on_switch(old, new, url)."""
        super().__init__()
        if not factories:
            raise ValueError("pusty łańcuch transportów")
        self.factories = list(factories)
        self.switch_after = switch_after
        self.on_switch = on_switch
        self.position = 0
        self.challenges = 0
        self.current = self._create(0)

    @property
    def current_name(self):
        return self.factories[self.position][0]

    def get(self, url, params=None, timeout=60.0):
        while True:
            response = self.current.get(url, params, timeout)
            if not is_cloudflare_challenge(response):
                self.challenges = 0
                return response
            self.challenges += 1
            last = self.position == len(self.factories) - 1
            if last or self.challenges < self.switch_after:
                if hasattr(self.current, "rotate"):
                    self.current.rotate()
                return response  # PoliteClient odczeka i ponowi
            self._switch(full_url(url, params))

    def close(self):
        self.current.close()

    def _switch(self, url):
        old = self.current_name
        self.current.close()
        self.position += 1
        self.challenges = 0
        self.current = self._create(self.position)
        log.warning(
            "transport: %s blokowany przez Cloudflare - przechodzę na %s", old, self.current_name
        )
        if self.on_switch is not None:
            self.on_switch(old, self.current_name, url)

    def _create(self, position):
        transport = self.factories[position][1]()
        transport.headers.update(self.headers)
        self.headers = transport.headers  # User-Agent ustawiony później też trafi dalej
        return transport


def _playwright():
    from judgments_core.browser import PlaywrightTransport  # opcjonalna zależność

    return PlaywrightTransport()


def _proxy_seller():
    from judgments_core.proxy_seller import make_proxy_seller_from_env

    return make_proxy_seller_from_env()


# Strategie po nazwie (ustawienie <PREFIKS>_TRANSPORT, Project.transports).
TRANSPORTS = {
    "proxy_seller": _proxy_seller,
    "requests": RequestsTransport,
    "curl": CurlTransport,
    "playwright": _playwright,
}


def make_transport(names, on_switch=None, extra_transports=None):
    """One transport by name, or a FallbackTransport for several names in order."""
    available = dict(TRANSPORTS)
    if extra_transports:
        available.update(extra_transports)
    names = [n.strip() for n in names if n.strip()]
    unknown = [n for n in names if n not in available]
    if unknown or not names:
        raise ValueError(
            f"nieznany transport: {', '.join(unknown) or '(pusto)'} ({' / '.join(available)})"
        )
    if len(names) == 1:
        return available[names[0]]()
    return FallbackTransport([(n, available[n]) for n in names], on_switch=on_switch)


def is_cloudflare_challenge(response):
    """403 with a Cloudflare bot check instead of the content."""
    return response.status_code == 403 and response.headers.get("cf-mitigated") == "challenge"


def full_url(url, params):
    """URL with the query string (for curl, the browser and the event log)."""
    if not params:
        return url
    return f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"


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
    return Response(int(lines[0].split(" ")[1]), headers, body, url)
