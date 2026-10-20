import subprocess

import pytest
import requests

from judgments_core.http import PoliteClient, SourceError
from judgments_core.transport import CurlTransport, FallbackTransport, Transport, make_transport


class FakeResponse:
    def __init__(self, status, body="{}", headers=None):
        self.status_code = status
        self.text = body
        self.headers = headers or {}
        self.url = "https://example.test/x"

    def json(self):
        import json

        return json.loads(self.text)


class FakeTransport(Transport):
    name = "fake"

    def __init__(self, responses):
        super().__init__()
        self.responses = list(responses)
        self.calls = 0
        self.closed = False

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def close(self):
        self.closed = True


def client(responses, **kwargs):
    sleeps = []
    c = PoliteClient(
        "test-agent",
        transport=FakeTransport(responses),
        sleep=sleeps.append,
        clock=lambda: 0.0,
        **kwargs,
    )
    return c, sleeps


def test_sets_user_agent_and_returns_json():
    c, _ = client([FakeResponse(200, '{"a": 1}')])
    assert c.get_json("https://example.test") == {"a": 1}
    assert c.transport.headers["User-Agent"] == "test-agent"


def test_pause_between_requests():
    c, sleeps = client([FakeResponse(200), FakeResponse(200)], pause_s=1.5)
    c.get("u")
    c.get("u")
    assert sleeps == [1.5]


def test_retries_server_errors_with_backoff_and_retry_after():
    c, sleeps = client(
        [
            FakeResponse(503),
            FakeResponse(429, headers={"Retry-After": "7"}),
            requests.ConnectionError("x"),
            FakeResponse(200, "ok"),
        ],
        pause_s=0,
    )
    assert c.get_text("u") == "ok"
    assert sleeps == [2, 7, 8]


@pytest.mark.parametrize("status", [400, 403, 404])
def test_client_errors_are_not_retried(status):
    c, _ = client([FakeResponse(status)])
    with pytest.raises(SourceError, match=str(status)):
        c.get("u")
    assert c.transport.calls == 1


def test_cloudflare_challenge_is_retried_after_a_long_wait():
    challenge = FakeResponse(403, "<html>Just a moment...", {"cf-mitigated": "challenge"})
    c, sleeps = client([challenge, challenge, FakeResponse(200, "ok")], pause_s=0)
    assert c.get_text("u") == "ok"
    assert sleeps == [30, 60]


def test_events_report_blocks_and_rejections():
    events = []
    challenge = FakeResponse(403, "<html>", {"cf-mitigated": "challenge"})
    c, _ = client([challenge, FakeResponse(503), FakeResponse(404)], pause_s=0)
    c.on_event = lambda *e: events.append(e)
    with pytest.raises(SourceError):
        c.get("https://example.test/q", params={"a": "b c"})
    url = "https://example.test/q?a=b+c"
    assert events == [
        ("cloudflare", "Cloudflare challenge (HTTP 403); próba 1/6", url, 30),
        ("http_5xx", "HTTP 503; próba 2/6", url, 4),
        ("odrzucone", "Źródło odrzuciło zapytanie (HTTP 404)", url, None),
    ]


def test_gives_up_after_max_retries():
    c, sleeps = client([FakeResponse(500)] * 3, max_retries=3, pause_s=0)
    with pytest.raises(SourceError, match="nie odpowiada"):
        c.get("u")
    assert sleeps == [2, 4]


def test_bad_json_is_a_source_error():
    c, _ = client([FakeResponse(200, "<html>")])
    with pytest.raises(SourceError, match="JSON"):
        c.get_json("u")


def fake_curl(stdout=b"", returncode=0, stderr=b""):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    return run, calls


def test_curl_transport_builds_url_and_parses_response():
    out = 'HTTP/2 200\r\ncontent-type: application/json\r\n\r\n{"a": "ń"}'.encode()
    run, calls = fake_curl(out)
    session = CurlTransport(run=run)
    session.headers["User-Agent"] = "test"
    r = session.get("https://example.test/q", params={"query": 'a="b" OR c', "start": 0})
    assert calls[0][-1] == "https://example.test/q?query=a%3D%22b%22+OR+c&start=0"
    assert "User-Agent: test" in calls[0]
    assert (r.status_code, r.headers["Content-Type"], r.json()) == (
        200,
        "application/json",
        {"a": "ń"},
    )


def test_curl_transport_skips_interim_responses():
    out = b"HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 429 Too Many\r\nRetry-After: 7\r\n\r\nslow"
    r = CurlTransport(run=fake_curl(out)[0]).get("https://example.test/")
    assert (r.status_code, r.headers["retry-after"], r.text) == (429, "7", "slow")


def test_curl_failure_is_a_retryable_network_error():
    run, _ = fake_curl(returncode=28, stderr=b"Operation timed out")
    with pytest.raises(requests.ConnectionError, match="timed out"):
        CurlTransport(run=run).get("https://example.test/")


CHALLENGE = FakeResponse(403, "<html>Just a moment...", {"cf-mitigated": "challenge"})


def chain(*transports, switch_after=2):
    switches = []
    made = []

    def factory(t):
        def make():
            made.append(t.name)
            return t

        return make

    for i, t in enumerate(transports):
        t.name = f"t{i}"
    fallback = FallbackTransport(
        [(t.name, factory(t)) for t in transports],
        switch_after=switch_after,
        on_switch=lambda *e: switches.append(e),
    )
    return fallback, switches, made


def test_fallback_moves_on_after_repeated_challenges():
    first = FakeTransport([CHALLENGE, CHALLENGE])
    second = FakeTransport([FakeResponse(200, "ok"), FakeResponse(200, "ok2")])
    third = FakeTransport([])
    fallback, switches, made = chain(first, second, third)
    c = PoliteClient("ua", transport=fallback, sleep=lambda s: None, clock=lambda: 0.0)
    assert c.get_text("https://example.test/q") == "ok"  # 1. challenge: czekanie, 2.: następny
    assert c.get_text("https://example.test/q") == "ok2"
    assert fallback.current_name == "t1" and first.closed
    assert switches == [("t0", "t1", "https://example.test/q")]
    assert made == ["t0", "t1"]  # trzeci nieutworzony
    assert second.headers["User-Agent"] == "ua"


def test_fallback_last_transport_keeps_returning_challenges():
    last = FakeTransport([CHALLENGE])
    fallback, switches, _ = chain(FakeTransport([CHALLENGE]), last, switch_after=1)
    assert fallback.get("u").status_code == 403
    assert fallback.current_name == "t1" and switches


def test_success_resets_the_challenge_count():
    first = FakeTransport([CHALLENGE, FakeResponse(200), CHALLENGE])
    fallback, switches, _ = chain(first, FakeTransport([]))
    for _ in range(3):
        fallback.get("u")
    assert fallback.current_name == "t0" and not switches


def test_make_transport_by_names():
    assert make_transport(["curl"]).name == "curl"
    assert make_transport(["requests", " curl"]).current_name == "requests"
    with pytest.raises(ValueError, match="nieznany"):
        make_transport(["telnet"])
