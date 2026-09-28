"""G8/G26 联网实测：真 API 端点上的 POST / PUT / DELETE / HEAD。

默认档不跑；``pytest -m live`` 显式开。钉的是「非 GET 在真站上到底发生了什么」：
httpbin 会把它**收到的方法与实体**回显出来，所以「我们发出去的是什么」这件事在这里
是可证的，而不是靠读代码相信。303 之后降成 GET 也是——`/get` 端点只有 GET 才答 200。
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
async def test_a_post_reaches_the_server_as_a_post_with_its_body():
    out = await _fetch(f"{HTTPBIN}/post", method="POST", body={"user": "dhole"})
    seen = _json_body(out)
    assert out.status == 200
    assert seen["form"] == {"user": "dhole"}, seen["form"]


@pytest.mark.asyncio
async def test_a_json_body_arrives_as_json_not_as_a_form():
    out = await _fetch(f"{HTTPBIN}/post", method="POST", body={"a": 1, "b": "x"},
                       content_type="application/json")
    seen = _json_body(out)
    assert json.loads(seen["data"]) == {"a": 1, "b": "x"}
    assert seen["form"] == {}


@pytest.mark.asyncio
async def test_a_put_and_a_delete_are_sent_as_they_were_asked():
    for verb, path in (("PUT", "put"), ("DELETE", "delete")):
        out = await _fetch(f"{HTTPBIN}/{path}", method=verb, body={"verb": verb.lower()})
        assert _json_body(out)["form"] == {"verb": verb.lower()}, (verb, out.status)


@pytest.mark.asyncio
async def test_a_post_that_gets_a_303_is_followed_as_a_get():
    """303 的读法：跟着去的那一次是 GET。`/get` 只对 GET 答 200，所以这条能证伪。"""
    out = await _fetch(f"{HTTPBIN}/redirect-to?url={HTTPBIN}%2Fget&status_code=303",
                       method="POST", body={"x": "1"})
    assert out.status == 200, out.error
    body = _json_body(out)
    assert body["url"] == f"{HTTPBIN}/get", body["url"]
    assert body["args"] == {} and "json" not in body.get("headers", {}), "the body rode along"


@pytest.mark.asyncio
async def test_a_head_probes_without_downloading():
    out = await _fetch(f"{HTTPBIN}/html", method="HEAD")
    assert out.status == 200
    assert out.content_ok is True, out.error
    assert out.content == []
    assert out.total_size_bytes > 0, "the declared length is the point of a HEAD"
    assert "head probe" in out.summary
