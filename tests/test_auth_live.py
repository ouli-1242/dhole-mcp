"""G25 联网实测：真服务器收到的凭据长什么样。

默认档不跑；``pytest -m live`` 显式开。选 httpbin 的理由和 G8 一样：它把**收到的
头**原样回显，所以「我们发出去的是 Basic 还是 Bearer、名字有没有被改掉」在这里是可
证的，而不是靠读代码相信。凭据不外泄的那一半（跨源那一跳）离线用假客户端证得更干净，
在 tests/test_auth_g25.py 里。
"""

from __future__ import annotations

import json

import pytest

from dhole_mcp.server import MasterFetchServer

pytestmark = [pytest.mark.live, pytest.mark.asyncio]

HTTPBIN = "https://httpbin.org"


def _json_body(result) -> dict:
    text = "".join(result.content or [])
    start = text.find("{")
    if start < 0:
        pytest.skip(f"no JSON in the answer: {text[:80]!r}")
    return json.loads(text[start:])


async def _fetch(url: str, **kw):
    out = await MasterFetchServer().smart_fetch(url, cache_ttl=0, timeout=25000, **kw)
    if out.status in (-1, 0):
        pytest.skip(f"no usable route to {url}: {out.error[:80]}")
    return out


@pytest.mark.asyncio
async def test_the_right_password_opens_the_page():
    out = await _fetch(f"{HTTPBIN}/basic-auth/user/passwd",
                       auth={"type": "basic", "username": "user", "password": "passwd"})
    assert out.status == 200, out.error
    assert _json_body(out)["authenticated"] is True


@pytest.mark.asyncio
async def test_a_wrong_password_is_reported_as_refused_not_as_a_bot_wall():
    """401 的两种相反修法：凭据被拒 / 压根没给凭据。混成一句「换源重试」就是让
    agent 去调浏览器，而浏览器也变不出一个正确口令。"""
    out = await _fetch(f"{HTTPBIN}/basic-auth/user/passwd",
                       auth={"type": "basic", "username": "user", "password": "nope"})
    assert out.status == 401
    assert "REFUSED" in out.next_action
    assert "nope" not in out.model_dump_json()
    assert out.content_ok is False


@pytest.mark.asyncio
async def test_a_401_without_credentials_tells_you_the_option_exists():
    out = await _fetch(f"{HTTPBIN}/basic-auth/user/passwd")
    assert out.status == 401
    assert "needs credentials" in out.next_action and "options.auth" in out.next_action
    assert "REFUSED" not in out.next_action


@pytest.mark.asyncio
async def test_a_bearer_token_arrives_as_a_bearer_token():
    out = await _fetch(f"{HTTPBIN}/bearer", auth={"type": "bearer", "token": "dhole-live-42"})
    assert out.status == 200, out.error
    assert _json_body(out)["token"] == "dhole-live-42"


@pytest.mark.asyncio
async def test_an_api_key_arrives_under_the_name_it_was_given():
    out = await _fetch(f"{HTTPBIN}/headers",
                       auth={"type": "header", "name": "X-API-Key", "value": "k-123"})
    headers = _json_body(out)["headers"]
    assert headers.get("X-Api-Key") == "k-123", sorted(headers)


@pytest.mark.asyncio
async def test_the_credential_is_still_there_after_a_same_host_redirect():
    """每跳重建头，所以「同源重定向之后还在不在」不是显然的：少了它就等于登录页
    一跳转就把口令丢了。"""
    target = f"{HTTPBIN}/basic-auth/user/passwd"
    out = await _fetch(f"{HTTPBIN}/redirect-to?url={target.replace('/', '%2F')}&status_code=302",
                       auth={"username": "user", "password": "passwd"})
    assert out.status == 200, (out.status, out.error)
    assert _json_body(out)["authenticated"] is True


@pytest.mark.asyncio
async def test_an_explicit_header_wins_and_the_call_says_auth_was_ignored():
    out = await _fetch(f"{HTTPBIN}/headers",
                       auth={"type": "bearer", "token": "not-this-one"},
                       extra_headers={"Authorization": "Bearer the-explicit-one"})
    assert "ignored" in out.summary.lower() or "ignored" in out.next_action.lower()
    assert _json_body(out)["headers"]["Authorization"] == "Bearer the-explicit-one"
