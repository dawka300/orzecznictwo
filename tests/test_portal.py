"""Several projects' pages on one server (judgments_core.web.Portal) and links under a prefix."""

from wsgiref.util import setup_testing_defaults

from judgments_core.web import Portal, Response, WebApp


class Page(WebApp):
    """Echoes the path it sees, with links written from the server root."""

    def route(self, request):
        return Response(
            f"<p>{request.path}</p><a href=\"/orzeczenie/1\">x</a><a href='/statystyki'>s</a>"
            '<form action="/"></form><a href="//example.org/">ext</a>'
        )


def get(app, path, query=""):
    environ = {"PATH_INFO": path, "QUERY_STRING": query}
    setup_testing_defaults(environ)
    status, headers = [], []

    def start_response(s, h):
        status.append(s)
        headers.extend(h)

    body = b"".join(app(environ, start_response)).decode("utf-8")
    return status[0], dict(headers), body


def portal():
    return Portal({"etpc": ("ETPC", Page(None)), "nsa": ("NSA", Page(None))}, default="etpc")


def test_project_sees_its_own_root_and_links_get_the_prefix():
    status, headers, body = get(portal(), "/nsa/orzeczenie/5")
    assert status.startswith("200") and "<p>/orzeczenie/5</p>" in body
    assert 'href="/nsa/orzeczenie/1"' in body and "href='/nsa/statystyki'" in body
    assert 'action="/nsa/"' in body and 'href="//example.org/"' in body  # inny serwer bez zmian
    assert int(headers["Content-Length"]) == len(body.encode("utf-8"))


def test_home_lists_projects_and_old_addresses_go_to_the_default():
    status, _, body = get(portal(), "/")
    assert status.startswith("200") and 'href="/etpc/statystyki"' in body and ">NSA<" in body
    assert get(portal(), "/nsa")[1]["Location"] == "/nsa/"
    assert get(portal(), "/orzeczenie/7")[1]["Location"] == "/etpc/orzeczenie/7"
    assert get(portal(), "/", "q=klimat")[1]["Location"] == "/etpc/?q=klimat"
    assert get(Portal({"nsa": ("NSA", Page(None))}), "/nic")[0].startswith("404")


def test_alone_without_prefix_links_stay():
    _, _, body = get(Page(None), "/")
    assert 'href="/orzeczenie/1"' in body
