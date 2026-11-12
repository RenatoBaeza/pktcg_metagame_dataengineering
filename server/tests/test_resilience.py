"""Retry behaviour. A run lasts ~45 minutes, so transient faults must not abort it."""

import httpx2 as httpx
import pytest

from app import supabase
from app.limitless import fetch_pairings


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else []
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    """Replays a scripted sequence of responses/exceptions."""

    def __init__(self, sequence):
        self.sequence = list(sequence)
        self.calls = 0

    def _next(self):
        self.calls += 1
        item = self.sequence.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def request(self, method, url, **kwargs):
        return self._next()

    def get(self, url, **kwargs):
        return self._next()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(supabase.time, "sleep", lambda _: None)
    import app.limitless

    monkeypatch.setattr(app.limitless.time, "sleep", lambda _: None)


def test_send_retries_timeout_then_succeeds():
    client = FakeClient([httpx.ReadTimeout("timed out"), FakeResponse(200)])
    response = supabase._send(client, "GET", "http://example/x")
    assert response.status_code == 200
    assert client.calls == 2


def test_send_retries_transient_5xx():
    client = FakeClient([FakeResponse(503), FakeResponse(200)])
    assert supabase._send(client, "GET", "http://example/x").status_code == 200
    assert client.calls == 2


def test_send_gives_up_after_max_attempts():
    client = FakeClient([httpx.ReadTimeout("nope")] * supabase.MAX_ATTEMPTS)
    with pytest.raises(RuntimeError, match="failed after"):
        supabase._send(client, "GET", "http://example/x")
    assert client.calls == supabase.MAX_ATTEMPTS


def _error(status_code, text):
    response = FakeResponse(status_code)
    response.text = text
    return response


def test_send_retries_statement_timeout():
    """PostgREST reports a statement timeout as a 500 carrying code 57014."""
    timeout = _error(500, '{"code":"57014","message":"canceling statement due to statement timeout"}')
    client = FakeClient([timeout, FakeResponse(200)])
    assert supabase._send(client, "POST", "http://example/rpc/x").status_code == 200
    assert client.calls == 2


def test_send_does_not_retry_other_500():
    """Any other 500 is a real error in the statement - surface it at once."""
    client = FakeClient([_error(500, '{"code":"42703","message":"column does not exist"}')])
    assert supabase._send(client, "POST", "http://example/rpc/x").status_code == 500
    assert client.calls == 1


def test_send_does_not_retry_client_error():
    """A 400 is our bug, not a blip - surface it immediately."""
    client = FakeClient([FakeResponse(400)])
    assert supabase._send(client, "POST", "http://example/x").status_code == 400
    assert client.calls == 1


def test_fetch_pairings_waits_out_rate_limit():
    client = FakeClient(
        [
            FakeResponse(429, headers={"retry-after": "0"}),
            FakeResponse(200, json_data=[{"phase": 1, "round": 1, "player1": "a"}]),
        ]
    )
    assert len(fetch_pairings(client, "tid")) == 1
    assert client.calls == 2


def test_fetch_pairings_retries_timeout():
    client = FakeClient([httpx.ConnectTimeout("x"), FakeResponse(200, json_data=[])])
    assert fetch_pairings(client, "tid") == []
    assert client.calls == 2


def test_fetch_pairings_treats_404_as_empty():
    client = FakeClient([FakeResponse(404)])
    assert fetch_pairings(client, "tid") == []


def test_fetch_pairings_ignores_non_list_payload():
    client = FakeClient([FakeResponse(200, json_data={"error": "nope"})])
    assert fetch_pairings(client, "tid") == []
