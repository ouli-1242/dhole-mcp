"""429/502/503/504 are worth one more question; 403/404 are not.

The HTTP tier already retried connection errors. A gateway 503 or a rate-limit
429 is the other shape that can change a second later, while a 403 wall or a 404
need a different tier or a different source. This lock keeps the retry bounded,
read-only, and away from a Retry-After that would outlive the call.
"""

from __future__ import annotations

import pytest

from dhole_mcp.fetcher import HTTPSession

pytestmark = pytest.mark.smoke


class _Resp:
    def __init__(self, status: int, headers: dict | None = None):
        self.status_code = status
        self.headers = headers or {}
        self.content = b"ok"
        self.url = "https://example.com/"
        self.reason = ""
        self.cookies = []


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sends: list[tuple] = []

    def request(self, *args, **kwargs):
        self.sends.append((args, kwargs))
        return self.responses.pop(0)


def _session(responses, *, retries=1):
    session = HTTPSession(retries=retries, retry_delay=0)
    client = _Client(responses)
    session._client = client
    return session, client


@pytest.mark.asyncio
async def test_a_429_with_a_short_retry_after_is_asked_again():
    session, client = _session([_Resp(429, {"retry-after": "0"}), _Resp(200)])

    out = await session.get("https://example.com/")

    assert out.status == 200
    assert out.retries == 1
    assert len(client.sends) == 2


@pytest.mark.asyncio
async def test_a_long_retry_after_is_returned_for_escalation_not_slept_on():
    session, client = _session([_Resp(429, {"retry-after": "60"})])

    out = await session.get("https://example.com/")

    assert out.status == 429
    assert out.retries == 0
    assert len(client.sends) == 1


@pytest.mark.asyncio
async def test_a_503_is_retried_once_even_without_retry_after():
    session, client = _session([_Resp(503), _Resp(503)])

    out = await session.get("https://example.com/")

    assert out.status == 503
    assert out.retries == 1
    assert len(client.sends) == 2


@pytest.mark.asyncio
async def test_a_write_is_never_replayed():
    session, client = _session([_Resp(503), _Resp(200)])

    out = await session.get("https://example.com/", method="POST", body=b"x=1")

    assert out.status == 503
    assert len(client.sends) == 1


@pytest.mark.asyncio
async def test_a_403_wall_is_not_retried():
    session, client = _session([_Resp(403), _Resp(200)])

    out = await session.get("https://example.com/")

    assert out.status == 403
    assert out.retries == 0
    assert len(client.sends) == 1
