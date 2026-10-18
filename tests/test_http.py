import pytest
import requests

from judgments_core.http import PoliteClient, SourceError


class FakeResponse:
    def __init__(self, status, body="{}", headers=None):
        self.status_code = status
        self.text = body
        self.headers = headers or {}
        self.url = "https://example.test/x"

    def json(self):
        import json

        return json.loads(self.text)


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def client(responses, **kwargs):
    sleeps = []
    c = PoliteClient(
        "test-agent",
        session=FakeSession(responses),
        sleep=sleeps.append,
        clock=lambda: 0.0,
        **kwargs,
    )
    return c, sleeps


def test_sets_user_agent_and_returns_json():
    c, _ = client([FakeResponse(200, '{"a": 1}')])
    assert c.get_json("https://example.test") == {"a": 1}
    assert c.session.headers["User-Agent"] == "test-agent"


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
    assert c.session.calls == 1


def test_gives_up_after_max_retries():
    c, sleeps = client([FakeResponse(500)] * 3, max_retries=3, pause_s=0)
    with pytest.raises(SourceError, match="nie odpowiada"):
        c.get("u")
    assert sleeps == [2, 4]


def test_bad_json_is_a_source_error():
    c, _ = client([FakeResponse(200, "<html>")])
    with pytest.raises(SourceError, match="JSON"):
        c.get_json("u")
