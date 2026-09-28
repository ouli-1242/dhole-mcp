"""G7 联网实测：真站上的 cookie 会话（默认档不跑；``pytest -m live`` 显式开）。

``test_sessions_g7.py`` 在假客户端上钉的是**逻辑**——逐跳建头、按主机取 jar。这个文件
钉的是**真站点**：primp 的 ``Response.cookies`` 到底是什么形状（16.0 之前把映射当列表
遍历，于是永远读到空）、重定向那一跳的 ``Set-Cookie`` 收不收得到、以及跨源跳转之后
调用方点名的 cookie 还在不在请求头里。

观察者用的是会回显请求头的站点（postman-echo），否则「没有发出去」这件事在客户端
根本无法证明——不发出去的东西是看不见的。

robots.txt 在本文件里保持关闭（conftest 的进程级豁免），这样测的是会话而不是合规；
合规的联网实测在 ``test_robots_live.py``。
"""

from __future__ import annotations

import json

import pytest

from dhole_mcp import sessions
from dhole_mcp.server import MasterFetchServer

pytestmark = [pytest.mark.live, pytest.mark.asyncio]

HTTPBIN = "https://httpbin.org"
ECHO = "https://postman-echo.com"


@pytest.fixture(autouse=True)
def _clean_jar():
    sessions.reset_for_tests()
    yield
    sessions.reset_for_tests()


def _body(result) -> str:
    return "".join(result.content or [])


async def _get(url: str, **kw):
    srv = MasterFetchServer()
    out = await srv.smart_fetch(url, cache_ttl=0, timeout=25000, **kw)
    if out.status in (-1, 0) or (out.error and "timeout" in out.error.lower()):
        pytest.skip(f"no usable route to {url}: {out.error[:80]}")
    return out


@pytest.mark.asyncio
async def test_a_cookie_set_by_the_site_comes_back_on_the_next_call():
    """报告 G7 的原始复现：/cookies/set?dhole=probe1 之后 /cookies 是空的。"""
    await _get(f"{HTTPBIN}/cookies/set?dhole=probe1", session_id="g7live")
    assert sessions.header_for("g7live", f"{HTTPBIN}/cookies") == {"dhole": "probe1"}

    second = await _get(f"{HTTPBIN}/cookies", session_id="g7live")
    assert "probe1" in _body(second), "the next call went out without the stored cookie"


@pytest.mark.asyncio
async def test_a_stateless_call_still_sees_nothing():
    """默认路径没被这次改动改变：不点名 session_id 就还是一次性 cookie。"""
    await _get(f"{HTTPBIN}/cookies/set?dhole=leakme", session_id="g7other")
    plain = await _get(f"{HTTPBIN}/cookies")
    assert "leakme" not in _body(plain)


@pytest.mark.asyncio
async def test_a_cookie_set_on_the_redirect_hop_is_kept():
    """登录的常见形态：Set-Cookie 落在那张 302 上，而不是最终页上。"""
    out = await _get(f"{HTTPBIN}/cookies/set?onhop=302", session_id="g7hop")
    assert sessions.header_for("g7hop", f"{HTTPBIN}/cookies") == {"onhop": "302"}
    assert "onhop" in _body(out), "the hop after the 302 did not present it"


@pytest.mark.asyncio
async def test_the_response_reports_names_and_never_values():
    out = await _get(f"{HTTPBIN}/cookies/set?shown=secretvalue", session_id="g7names")
    assert "shown" in out.session_cookie_names
    assert out.session_id == "g7names"
    # 值会出现在**正文**里，因为 /cookies 这一页就是回显 cookie 的；`original_url` 里
    # 也有，那是调用方自己写在查询串里的。要钉的是 dhole **自己派生**的那些字段不含值
    # （名单、summary、next_action、error、metadata）——正文进 transcript 是抓取的目的，
    # 凭据进元数据不是。
    derived = json.dumps({
        "session_cookie_names": out.session_cookie_names, "summary": out.summary,
        "next_action": out.next_action, "error": out.error, "metadata": out.metadata,
    }, default=str)
    assert "secretvalue" not in derived


@pytest.mark.asyncio
async def test_a_caller_named_cookie_does_not_ride_a_cross_origin_redirect():
    """此前：header 字典一次建好、逐跳原样重发 —— 站点把我们转到别的域，
    cookie 就跟着送过去了。postman-echo 会把它收到的头回显出来。"""
    target = f"{ECHO}/get"
    out = await _get(
        f"{HTTPBIN}/redirect-to?url={target}&status_code=302",
        session_id="g7xorigin", cookies={"crosstest": "should-not-travel"})
    assert out.url == target, out.url
    assert "should-not-travel" not in _body(out), "the cookie reached the host we jumped to"


@pytest.mark.asyncio
async def test_the_same_origin_still_gets_the_caller_named_cookie():
    """对照：同源的跳转（重定向后还在 httpbin）必须照带，否则这条防线就成了「cookie 全不带」。"""
    out = await _get(f"{HTTPBIN}/cookies/set?same=1",
                     session_id="g7same", cookies={"sitetest": "stays-here"})
    assert "stays-here" in _body(out)
