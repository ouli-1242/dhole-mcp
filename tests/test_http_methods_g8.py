"""G8 / G26 回归：HTTP 方法、请求体，以及它们各自的后果。

报告 G8 的缺口是「只能 GET」：JSON API、表单提交、GraphQL 全都做不到，`actions`
里 fill+click 绕过去既重又不稳。加上参数不难，难的是**非 GET 带来的每一个默认行为
都得跟着改**，否则就是「看起来成功了，其实回答的是另一个问题」——这个项目反复在修
的那一类：

    重试      上一次 POST 很可能已经落库，重试就是再写一遍
    缓存      缓存按 URL 存正文；把 POST 的答复存进去，下一个 GET 就回放了这次写
    浏览器升级 那一层只会导航（GET），它没有我们的 body 可发——渲染出来的是另一个请求
    archive   快照是这一 URL 某天的 GET 结果，答不了「我的 POST 成功了吗」
    HEAD      按定义没有正文，所以「空正文」的一整套质量启发式必须闭嘴

重定向的方法转换按浏览器/curl 的读法：301/302/303 把写降成 GET 并丢 body，
307/308 保留方法与 body（目标仍逐跳 SSRF 校验）。
"""

from __future__ import annotations

import pytest

from dhole_mcp import server as server_mod
from dhole_mcp.fetcher import HTTPSession, Response as DholeResponse
from dhole_mcp.server import MasterFetchServer, ResponseModel, _clean_body, _clean_method

URL = "https://api.example.com/v1/items"
CROSS = "https://other.example.org/hopper"


class _Resp:
    def __init__(self, url, status=200, headers=None, body=b'{"ok":true}', cookies=None):
        self.url = url
        self.status_code = status
        self.headers = headers or {}
        self.content = body
        self.reason = ""
        self.cookies = cookies or {}


class _Client:
    """Records how each dial was made; answers from a scripted chain."""

    def __init__(self, routes):
        self.routes = routes
        self.sends: list[dict] = []

    def request(self, method, url, **kwargs):
        self.sends.append({"method": method, "url": url, **kwargs})
        return self.routes.get(url) or _Resp(url, status=404)

    @property
    def last(self) -> dict:
        return self.sends[-1]


def _session_with(client) -> HTTPSession:
    s = HTTPSession(retries=0, retry_delay=0)
    s._client = client
    return s


# ─── 抓取层：方法、body、重定向语义 ────────────────────────────────────

class TestTheTierSendsWhatWasAsked:

    @pytest.mark.asyncio
    async def test_a_post_carries_the_body_and_its_type(self):
        client = _Client({URL: _Resp(URL, 201, body=b'{"id":7}')})
        out = await _session_with(client).get(
            URL, method="post", body=b'{"a":1}', content_type="application/json")

        assert client.last["method"] == "POST"
        assert client.last["content"] == b'{"a":1}'
        assert client.last["headers"]["content-type"] == "application/json"
        assert out.status == 201

    @pytest.mark.asyncio
    async def test_a_get_does_not_pass_a_body_at_all(self):
        """GET 传 content=b'' 与不传 content 不是同一个请求（有些服务器会 400）。"""
        client = _Client({URL: _Resp(URL)})
        await _session_with(client).get(URL)

        assert "content" not in client.last

    @pytest.mark.asyncio
    async def test_a_303_after_a_post_is_followed_as_a_get_without_the_body(self):
        client = _Client({
            URL: _Resp(URL, 303, {"location": "https://api.example.com/done"}),
            "https://api.example.com/done": _Resp("https://api.example.com/done"),
        })
        await _session_with(client).get(URL, method="POST", body=b"x=1",
                                        content_type="application/x-www-form-urlencoded")

        second = client.sends[-1]
        assert second["method"] == "GET"
        assert "content" not in second
        assert "content-type" not in second["headers"], "a GET claiming a body it does not have"

    @pytest.mark.asyncio
    async def test_a_head_does_not_follow_a_redirect(self):
        """HEAD 的答复里 Location/status/length 就是全部有用信息；跟过去就变成一次
        被明说「不要下载」的下载。"""
        client = _Client({URL: _Resp(URL, 301, {"location": "https://api.example.com/moved"})})
        out = await _session_with(client).get(URL, method="HEAD")

        assert len(client.sends) == 1
        assert out.status == 301

    @pytest.mark.asyncio
    async def test_a_307_keeps_the_method_and_the_body(self):
        client = _Client({
            URL: _Resp(URL, 307, {"location": "https://api.example.com/v2/items"}),
            "https://api.example.com/v2/items": _Resp("https://api.example.com/v2/items"),
        })
        await _session_with(client).get(URL, method="POST", body=b"x=1")

        assert client.sends[-1]["method"] == "POST"
        assert client.sends[-1]["content"] == b"x=1"

    @pytest.mark.asyncio
    async def test_a_write_is_not_retried_while_a_get_is(self):
        """POST 的失败可能已经落库；重发等于写两遍。"""
        class _Boom:
            def __init__(self):
                self.n = 0

            def request(self, method, url, **kwargs):
                self.n += 1
                raise RuntimeError("connection reset")

        boom = _Boom()
        s = _session_with(boom)
        with pytest.raises(RuntimeError):
            await s.get(URL, method="POST", body=b"x=1", retries=3)
        assert boom.n == 1, "a POST was resent"

        boom2 = _Boom()
        s2 = _session_with(boom2)
        with pytest.raises(RuntimeError):
            await s2.get(URL, method="GET", retries=3)
        assert boom2.n == 4, "a GET should still be retried"

    @pytest.mark.asyncio
    async def test_a_cross_origin_307_carries_the_body_but_not_the_credentials(self):
        """保留 body 是规范要求的；凭据不跟跳是我们要求的。两者互不牺牲。"""
        client = _Client({
            URL: _Resp(URL, 307, {"location": "https://other.example.org/hopper"}),
            CROSS: _Resp(CROSS),
        })
        await _session_with(client).get(URL, method="POST", body=b"x=1",
                                        cookies={"sid": "secret"})

        hop = client.sends[-1]
        assert hop["url"] == CROSS
        assert hop["content"] == b"x=1"
        assert "cookie" not in hop["headers"]


# ─── 参数口径 ────────────────────────────────────────────────────────

class TestBodyNormalization:

    def test_a_dict_is_a_form_unless_json_is_named(self):
        assert _clean_body({"a": "1", "b": "2"}, "") == b"a=1&b=2"
        sent = _clean_body({"a": 1}, "application/json")
        assert sent == b'{"a": 1}'

    def test_a_string_is_sent_as_is(self):
        assert _clean_body('{"raw":true}', "application/json") == b'{"raw":true}'

    def test_no_body_means_no_body(self):
        assert _clean_body(None, "") is None
        assert _clean_body(b"", "") is None

    def test_an_unserializable_body_is_none_not_an_exception(self):
        assert _clean_body(object(), "") is None

    def test_the_verb_is_case_insensitive_and_unknown_stays_unknown(self):
        assert _clean_method("post") == "POST"
        assert _clean_method(None) == "GET"
        assert _clean_method("TRACE") == "TRACE", "the caller must see it was not accepted"


# ─── smart_fetch 的后果表 ───────────────────────────────────────────

def _html(url: str, status: int = 200) -> ResponseModel:
    return ResponseModel(url=url, status=status, content=['{"ok":true}'],
                         content_type="application/json", fetcher_used="http")


def _fake_tier(monkeypatch, seen: dict, body: bytes = b'{"ok":true}',
               headers: dict | None = None):
    """Replace the primp session inside the server, recording what it was handed.

    The tier's own method is still called ``get`` (primp's shape) — what changed is
    that it now also receives method/body/content_type.
    """

    class _Fake:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kwargs):
            seen.update(kwargs)
            # The real tier hands the pipeline a dhole Response (status/body/
            # headers), not primp's raw object - so the fake must too.
            return DholeResponse(url=url, body=body, status=200,
                                 headers=headers or {"content-type": "application/json"})

    # server.py imports HTTPSession lazily from the module at call time, so the
    # patch has to land on dhole_mcp.fetcher (patching an attribute of server.py
    # would miss every call that resolves the name inside the function body).
    monkeypatch.setattr("dhole_mcp.fetcher.HTTPSession", _Fake)
    monkeypatch.setattr("dhole_mcp.fetcher.tcp_preflight",
                        lambda url, timeout=2.0: (True, ""))


class TestSmartFetchHonoursTheVerb:

    @pytest.mark.asyncio
    async def test_the_reached_tier_carries_the_verb_and_body(self, monkeypatch):
        seen: dict = {}
        _fake_tier(monkeypatch, seen)

        out = await MasterFetchServer().smart_fetch(
            URL, method="POST", body={"q": "x"}, content_type="application/json",
            cache_ttl=0, timeout=8000)

        assert out.status == 200
        assert seen["method"] == "POST"
        assert seen["body"] == b'{"q": "x"}', "a dict with a json content_type is a JSON body"
        assert seen["content_type"] == "application/json"

    @pytest.mark.asyncio
    async def test_a_form_body_without_a_content_type_gets_the_type_it_implies(self, monkeypatch):
        """实测：dict body 不发 Content-Type 时，httpbin 的 /post 回的是空 form——
        我们编码成了表单，却没人知道它是表单。"""
        seen: dict = {}
        _fake_tier(monkeypatch, seen)

        await MasterFetchServer().smart_fetch(URL, method="POST", body={"q": "x"},
                                              cache_ttl=0, timeout=8000)

        assert seen["body"] == b"q=x"
        assert seen["content_type"] == "application/x-www-form-urlencoded"

    @pytest.mark.asyncio
    async def test_an_unknown_verb_refuses_before_any_request(self, monkeypatch):
        async def boom(self, url, **kwargs):
            raise AssertionError("a request was made with a verb we do not speak")
        monkeypatch.setattr(MasterFetchServer, "get", boom)

        out = await MasterFetchServer().smart_fetch(URL, method="TRACE", cache_ttl=0)

        assert out.status == 0
        assert "unsupported method" in out.error
        assert "GET" in out.next_action and "PATCH" in out.next_action

    @pytest.mark.asyncio
    async def test_a_body_on_a_get_is_said_to_be_ignored(self, monkeypatch):
        async def fake_get(self, url, **kwargs):
            return _html(url)
        monkeypatch.setattr(MasterFetchServer, "get", fake_get)

        out = await MasterFetchServer().smart_fetch(URL, body="ignored", cache_ttl=0)

        assert out.status == 200
        assert "body ignored" in out.summary

    @pytest.mark.asyncio
    async def test_an_oversized_body_refuses_before_any_request(self, monkeypatch):
        async def boom(self, url, **kwargs):
            raise AssertionError("an oversized body reached the network")
        monkeypatch.setattr(MasterFetchServer, "get", boom)

        out = await MasterFetchServer().smart_fetch(
            URL, method="POST", body="x" * (server_mod.MAX_REQUEST_BODY_BYTES + 1),
            cache_ttl=0)

        assert out.status == 0 and "too large" in out.error

    @pytest.mark.asyncio
    async def test_a_write_never_reaches_the_browser_or_the_archive(self, monkeypatch):
        """升级与快照都是「用 GET 重放同一个 URL」——它们答的不是你问的那个请求。"""
        calls: list[str] = []

        async def fake_get(self, url, **kwargs):
            calls.append("get")
            return ResponseModel(url=url, status=500, content=[b'{"error":"boom"}'],
                                 content_type="application/json", fetcher_used="http")

        async def no_browser(self, *a, **k):
            calls.append("stealthy")
            raise AssertionError("a POST escalated to a GET-shaped browser render")

        async def no_archive(self, url, *a, **k):
            calls.append("archive")
            raise AssertionError("a POST was answered from a snapshot")

        monkeypatch.setattr(MasterFetchServer, "get", fake_get)
        monkeypatch.setattr(MasterFetchServer, "stealthy_fetch", no_browser)
        monkeypatch.setattr(MasterFetchServer, "_fetch_from_archive", no_archive)

        out = await MasterFetchServer().smart_fetch(
            URL, method="POST", body={"a": "1"}, cache_ttl=0, timeout=8000)

        assert calls == ["get"], calls
        assert out.status == 500, "the server's own answer is the answer"

    @pytest.mark.asyncio
    async def test_a_write_bypasses_the_cache_and_says_so(self, monkeypatch):
        hits = {"n": 0}

        async def fake_get(self, url, **kwargs):
            hits["n"] += 1
            return _html(url)
        monkeypatch.setattr(MasterFetchServer, "get", fake_get)

        srv = MasterFetchServer()
        first = await srv.smart_fetch(URL, method="POST", body={"a": "1"},
                                     cache_ttl=3600, timeout=8000)
        second = await srv.smart_fetch(URL, method="POST", body={"a": "1"},
                                       cache_ttl=3600, timeout=8000)

        assert first.cached is False and second.cached is False
        assert hits["n"] == 2, "the second POST was served from the cache"
        assert "cache bypassed" in first.summary

    @pytest.mark.asyncio
    async def test_a_head_is_not_diagnosed_as_an_empty_page(self, monkeypatch):
        """HEAD 按定义没有正文；把「没有正文」当病症就成了假故障。"""
        seen: dict = {}
        _fake_tier(monkeypatch, seen, body=b"",
                   headers={"content-type": "text/html", "content-length": "3741"})

        out = await MasterFetchServer().smart_fetch(
            URL, method="HEAD", cache_ttl=0, timeout=8000)

        assert seen["method"] == "HEAD"
        assert out.status == 200
        assert not out.error.startswith("js_shell"), out.error
        assert out.content == []
        assert out.content_ok is True, out.error
        assert "head probe" in out.summary, out.summary
        # The declared length IS the answer for a probe: there are no bytes here
        # to count, and reporting 0 makes HEAD look like an empty page.
        assert out.total_size_bytes == 3741
