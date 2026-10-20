import httpx
import pytest

from cronchords import http as http_module
from cronchords.http import send


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    delays: list[float] = []
    monkeypatch.setattr(http_module.time, "sleep", delays.append)
    return delays


def client_returning(*responses: httpx.Response | Exception) -> httpx.Client:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_retries_rate_limits_after_the_requested_delay(sleeps):
    client = client_returning(
        httpx.Response(429, headers={"Retry-After": "3"}),
        httpx.Response(200, json={"ok": True}),
    )

    assert send(client, "GET", "https://api.example.com/x").status_code == 200
    assert sleeps == [3.0]


def test_backs_off_on_server_errors(sleeps):
    client = client_returning(httpx.Response(503), httpx.Response(502), httpx.Response(200))

    assert send(client, "GET", "https://api.example.com/x").status_code == 200
    assert sleeps == [2.0, 4.0]


def test_does_not_wait_hours_for_a_retry(sleeps):
    client = client_returning(httpx.Response(429, headers={"Retry-After": "7200"}))

    assert send(client, "GET", "https://api.example.com/x").status_code == 429
    assert sleeps == []


def test_gives_up_after_the_last_attempt(sleeps):
    client = client_returning(*[httpx.Response(500)] * 4)

    assert send(client, "GET", "https://api.example.com/x").status_code == 500
    assert len(sleeps) == 3


def test_retries_network_errors(sleeps):
    client = client_returning(httpx.ConnectError("boom"), httpx.Response(200))

    assert send(client, "GET", "https://api.example.com/x").status_code == 200


def test_client_errors_are_returned_right_away(sleeps):
    client = client_returning(httpx.Response(404))

    assert send(client, "GET", "https://api.example.com/x").status_code == 404
    assert sleeps == []
