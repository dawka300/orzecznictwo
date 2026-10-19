"""Minimal WSGI helpers and a threaded local server - standard library only.

A project's web pages are a plain WSGI application, so the same code runs
locally (`python -m <projekt> www`) and on the hosting under Passenger.
"""

from __future__ import annotations

import logging
from html import escape
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlencode
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

log = logging.getLogger(__name__)


class Request:
    def __init__(self, environ):
        self.environ = environ
        self.path = environ.get("PATH_INFO", "/") or "/"
        self.params = {k: v[-1] for k, v in parse_qs(environ.get("QUERY_STRING", "")).items()}

    def get(self, name, default=""):
        return self.params.get(name, default).strip()

    def get_int(self, name, default=0):
        value = self.get(name)
        return int(value) if value.isdigit() else default


class Response:
    def __init__(self, body, status="200 OK", content_type="text/html; charset=utf-8"):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.content_type = content_type

    def __call__(self, start_response):
        start_response(
            self.status,
            [("Content-Type", self.content_type), ("Content-Length", str(len(self.body)))],
        )
        return [self.body]


def url(path, **params):
    """path?params without empty values."""
    query = urlencode({k: v for k, v in params.items() if v not in ("", None, 0)})
    return f"{path}?{query}" if query else path


def e(value):
    """HTML-escaped text; None -> ''."""
    return "" if value is None else escape(str(value))


class _ThreadingServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        log.info("www: %s", format % args)


def serve(app, host, port):
    """Serve the WSGI app until Ctrl+C."""
    with make_server(
        host, port, app, server_class=_ThreadingServer, handler_class=_QuietHandler
    ) as server:
        log.info("Wyszukiwarka: http://%s:%d/ (Ctrl+C kończy)", host, port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            log.info("Zatrzymano.")
