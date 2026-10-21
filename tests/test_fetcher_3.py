"""test_fetcher_3.py —— 由 3 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import pytest
from dhole_mcp import server as server_mod
from dhole_mcp.fetcher import HTTPSession, Response as DholeResponse
from dhole_mcp.server import MasterFetchServer, ResponseModel, _clean_body, _clean_method
import asyncio
from dhole_mcp import browser as browser_mod
from dhole_mcp import identity
from dhole_mcp.server import MasterFetchServer as Server
from dhole_mcp.server import _ANTI_DETECT, _anti_detect
import http.server
import socketserver
import threading
from dhole_mcp.fetcher import Response, _decode_html_bytes, _meta_refresh_target
from dhole_mcp.security import SecurityError


# ============================================================================
# 来源：test_http_methods_g8.py
# ============================================================================
# G8 / G26 回归：HTTP 方法、请求体，以及它们各自的后果。

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
        """任何会改状态的动词都只发一次：失败可能已经落库，重发等于写两遍。

        RFC 把 PUT/DELETE 叫做幂等，说的是"设计好的端点"；这一层没法核对某个端点
        是否真的那样，而 web_request 对调用方的承诺是"写只出去一次"。所以门槛问的是
        "这个动词会不会改状态"，不是"规范说它收不收敛"。
        """
        class _Boom:
            def __init__(self):
                self.n = 0

            def request(self, method, url, **kwargs):
                self.n += 1
                raise RuntimeError("connection reset")

        for verb in ("POST", "PUT", "PATCH", "DELETE"):
            boom = _Boom()
            with pytest.raises(RuntimeError):
                await _session_with(boom).get(URL, method=verb, body=b"x=1",
                                              retries=3)
            assert boom.n == 1, f"a {verb} was resent"

        boom2 = _Boom()
        with pytest.raises(RuntimeError):
            await _session_with(boom2).get(URL, method="GET", retries=3)
        assert boom2.n == 4, "a GET should still be retried"

        boom3 = _Boom()
        with pytest.raises(RuntimeError):
            await _session_with(boom3).get(URL, method="HEAD", retries=3)
        assert boom3.n == 4, "HEAD reads state without changing it - retry is safe"

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


# ─── web_read 的后果表 ───────────────────────────────────────────

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

        out = await MasterFetchServer().web_read(
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

        await MasterFetchServer().web_read(URL, method="POST", body={"q": "x"},
                                              cache_ttl=0, timeout=8000)

        assert seen["body"] == b"q=x"
        assert seen["content_type"] == "application/x-www-form-urlencoded"

    @pytest.mark.asyncio
    async def test_an_unknown_verb_refuses_before_any_request(self, monkeypatch):
        async def boom(self, url, **kwargs):
            raise AssertionError("a request was made with a verb we do not speak")
        monkeypatch.setattr(MasterFetchServer, "get", boom)

        out = await MasterFetchServer().web_read(URL, method="TRACE", cache_ttl=0)

        assert out.status == 0
        assert "unsupported method" in out.error
        assert "GET" in out.next_action and "PATCH" in out.next_action

    @pytest.mark.asyncio
    async def test_a_body_on_a_get_is_said_to_be_ignored(self, monkeypatch):
        async def fake_get(self, url, **kwargs):
            return _html(url)
        monkeypatch.setattr(MasterFetchServer, "get", fake_get)

        out = await MasterFetchServer().web_read(URL, body="ignored", cache_ttl=0)

        assert out.status == 200
        assert "body ignored" in out.summary

    @pytest.mark.asyncio
    async def test_an_oversized_body_refuses_before_any_request(self, monkeypatch):
        async def boom(self, url, **kwargs):
            raise AssertionError("an oversized body reached the network")
        monkeypatch.setattr(MasterFetchServer, "get", boom)

        out = await MasterFetchServer().web_read(
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

        out = await MasterFetchServer().web_read(
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
        first = await srv.web_read(URL, method="POST", body={"a": "1"},
                                     cache_ttl=3600, timeout=8000)
        second = await srv.web_read(URL, method="POST", body={"a": "1"},
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

        out = await MasterFetchServer().web_read(
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


# ============================================================================
# 来源：test_hardening_reaches_the_warm_browser_b5.py
# ============================================================================
# B5-14a-2：档位得真的落在那台浏览器上 —— 共享的自动会话以前是按"不设防"启动的。
# 重名消歧：URL → URL__hardening_reaches_the_wa_0123

URL__hardening_reaches_the_wa_0123 = "https://example.com/"


def _fake_open(monkeypatch, opened: list) -> None:
    """`open_session` 的桩：记下启动参数，并登记一台带着这些参数的活会话。

    登记的会话必须**带着**那两颗启动级开关，因为轮换与复用的判据就是"这台浏览器是
    按什么档起来的" —— 桩如果不带，测的就成了桩。
    """
    from dhole_mcp.server import _SessionEntry

    async def fake_open(self, **kw):
        opened.append(kw)
        sid = kw.get("session_id") or ("sid-" + str(kw.get("profile_name")))

        class _Live:
            _is_alive = True
            cdp_attached = False
            launch_note = ""      # 起的是隐形的那台，所以真会话在这一句上会回空串
            _hide_canvas = kw.get("hide_canvas", False)
            _block_webrtc = kw.get("block_webrtc", False)
            _solve_cloudflare = kw.get("solve_cloudflare", False)

            async def close(self):
                pass

        self._sessions[sid] = _SessionEntry(session=_Live(), session_type="stealthy")
        return type("R", (), {"session_id": sid})()

    monkeypatch.setattr(Server, "open_session", fake_open)
    monkeypatch.setattr(server_mod, "_browser_deps_available", lambda: True)


def _launch_rung(kw: dict) -> tuple:
    """这台浏览器起来时真正带着的两颗启动级开关。"""
    return (kw.get("hide_canvas"), kw.get("block_webrtc"))


def _expected(level: str) -> tuple:
    hard = _anti_detect(level)
    return (hard["hide_canvas"], hard["block_webrtc"])


class TestTheWarmBrowserIsBuiltAtARung:

    def test_the_default_rung_is_what_the_auto_browser_launches_with(self, monkeypatch):
        opened: list = []
        _fake_open(monkeypatch, opened)
        asyncio.run(Server()._ensure_auto_session())
        assert opened, "自动会话根本没建起浏览器"
        assert _launch_rung(opened[0]) == _expected("auto"), (
            f"共享浏览器带着 {_launch_rung(opened[0])} 起来了，而 auto 档要的是 "
            f"{_expected('auto')} —— 每一次 stealthy 抓取都跑在这台上")
        assert opened[0]["solve_cloudflare"] is True, \
            "web_screenshot 不逐次传这颗，它读的是会话启动时的默认"

    def test_a_stored_rung_is_what_the_auto_browser_launches_with(self, monkeypatch):
        """档位能落盘（§3-17），落的就必须是建浏览器时用的那一份。"""
        identity.set_value("anti_detect", "off")
        opened: list = []
        _fake_open(monkeypatch, opened)
        asyncio.run(Server()._ensure_auto_session())
        assert _launch_rung(opened[0]) == (False, False), opened[0]
        assert opened[0]["solve_cloudflare"] is False

    def test_changing_the_rung_rotates_the_warm_browser(self, monkeypatch):
        """与 profile 同一判据：换档之后还复用旧的那台，新的默认就永远不生效。"""
        server = Server()
        opened: list = []
        closed: list = []

        async def fake_close(self, sid):
            closed.append(sid)
            self._sessions.pop(sid, None)
            return 0, False, False

        _fake_open(monkeypatch, opened)
        monkeypatch.setattr(Server, "_close_one_session", fake_close)
        asyncio.run(server._ensure_auto_session())
        assert _launch_rung(opened[0]) == _expected("auto")
        identity.set_value("anti_detect", "off")
        asyncio.run(server._ensure_auto_session())
        assert closed, "换了档位还复用按旧档起来的浏览器"
        assert _launch_rung(opened[-1]) == (False, False), opened[-1]

    def test_the_same_rung_reuses_the_warm_browser(self, monkeypatch):
        """轮换只在真的换了的时候付钱。"""
        server = Server()
        opened: list = []
        _fake_open(monkeypatch, opened)
        asyncio.run(server._ensure_auto_session())
        asyncio.run(server._ensure_auto_session())
        assert len(opened) == 1, "每次抓取都冷启动一遍浏览器"

    def test_a_named_session_gets_the_same_rung(self, monkeypatch):
        """命名会话是登录态的载体，没有理由比共享的那台更不设防。"""
        opened: list = []
        _fake_open(monkeypatch, opened)
        asyncio.run(Server()._ensure_session_browser("sess1"))
        assert _launch_rung(opened[0]) == _expected("auto"), opened[0]


# ─── 复用的那台浏览器：档位不合就不骑它 ─────────────────────────────

def _serve(monkeypatch, *, warm: tuple, asked: str) -> dict:
    """让一次调用去复用一台按 `warm` 档起来的浏览器，问的是 `asked` 档。

    返回"真的跑了这一趟的那台浏览器"带着哪一对启动级开关、它是复用的还是新起的，
    以及回执里的 notes。两条路的 fetch 一律抛出去：`bulk_stealthy_fetch` 用
    gather(return_exceptions) 收，异常成一条失败结果而不会炸测试 —— 这一刀要看的
    只是哪台浏览器服务了这一次，不是它答了什么。
    """
    seen: dict = {"how": None, "launch": None, "created": [], "notes": []}

    class _WarmSession:
        _is_alive = True
        cdp_attached = False
        # 与 _OneShotBrowser 同一句理由：真会话对外说得出这两件事，假件就得说得出。
        launch_note = ""
        _hide_canvas, _block_webrtc = warm

        async def fetch(self, url, **kw):
            seen["how"] = "reused"
            seen["launch"] = (self._hide_canvas, self._block_webrtc)
            raise RuntimeError("probe: stop on the reused session")

    class _OneShotBrowser:
        def __init__(self, **kw):
            seen["created"].append(kw)
            seen["how"] = "one-shot"
            seen["launch"] = (kw.get("hide_canvas"), kw.get("block_webrtc"))
            # 一台真会话对外说得出两件事：attach 没 attach、它是被怎么起来的
            # （批次 11 的 `launch_note`，headless 时是空串）。假件少一颗就是比真件
            # 宽容 —— 那种宽容正好藏住"回执读不到那句话"这类事故。
            self.cdp_attached = False
            self.launch_note = ""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def fetch(self, url, **kw):
            raise RuntimeError("probe: stop after the launch args")

    from dhole_mcp.server import _SessionEntry

    monkeypatch.setattr(browser_mod, "StealthyBrowser", _OneShotBrowser)
    monkeypatch.setattr(server_mod, "_browser_deps_available", lambda: True)
    server = Server()
    server._sessions["warm"] = _SessionEntry(session=_WarmSession(),
                                             session_type="stealthy")
    hard = _anti_detect(asked)
    token = server_mod._ARG_NOTES.set(seen["notes"])
    try:
        asyncio.run(server.bulk_stealthy_fetch(
            [URL__hardening_reaches_the_wa_0123], session_id="warm", hide_canvas=hard["hide_canvas"],
            block_webrtc=hard["block_webrtc"],
            solve_cloudflare=hard["solve_cloudflare"], timeout=5000))
    finally:
        server_mod._ARG_NOTES.reset(token)
    return seen


_HALF = (True, False)
WARMS = {"auto": _expected("auto"), "off": _expected("off"), "half": _HALF}


class TestTheRungIsNotDroppedOnAReusedSession:

    def test_a_matching_session_is_still_reused(self, monkeypatch):
        """这一刀不许把快路径拆了：档位对上就接着用那台热浏览器。"""
        seen = _serve(monkeypatch, warm=_expected("auto"), asked="auto")
        assert seen["how"] == "reused", seen
        assert not seen["created"], seen["created"]

    def test_a_session_at_another_rung_is_not_ridden(self, monkeypatch):
        """以前：off 的请求落在一台开着画布噪声的共享浏览器上，kwargs 走到那一支
        就蒸发，一句响的都没有。现在：这一趟自己起一台。"""
        seen = _serve(monkeypatch, warm=_expected("auto"), asked="off")
        assert seen["how"] == "one-shot", seen
        assert seen["launch"] == _expected("off"), seen

    @pytest.mark.parametrize("warm_name", sorted(WARMS))
    @pytest.mark.parametrize("asked", sorted(_ANTI_DETECT))
    def test_every_rung_reaches_the_browser_that_served_it(self, monkeypatch,
                                                          asked, warm_name):
        """最强的一条：不管复用还是新起，真跑了这一趟的那台浏览器带的就是这一档。

        断言不打在"kwargs 送到了哪一层"—— 上一版的说法正是"送到了"，而送到之后在
        复用的那一支被丢掉。十二种组合全过，才叫档位落在了地上。
        """
        warm = WARMS[warm_name]
        seen = _serve(monkeypatch, warm=warm, asked=asked)
        assert seen["launch"] == _expected(asked), (asked, warm_name, seen)


class TestTheCostIsSaidOutLoud:

    def test_giving_up_the_shared_session_is_reported(self, monkeypatch):
        """换一台不只是慢一点：那一台的 context（登录态）不跟过去。

        静默换台与静默丢档位是同一种错的两面 —— 调用方以为说的是"在这一台上按 off
        跑"，实际是"换一台跑"。回执里那条 note 是唯一看得见的地方。
        """
        seen = _serve(monkeypatch, warm=_expected("auto"), asked="off")
        assert seen["how"] == "one-shot", seen
        assert any("browser" in n.lower() for n in seen["notes"]), seen["notes"]

    def test_the_fast_path_says_nothing(self, monkeypatch):
        seen = _serve(monkeypatch, warm=_expected("auto"), asked="auto")
        assert not seen["notes"], seen["notes"]


class TestTheNoteReachesTheCaller:
    """note 不能只活在一个没人读的 list 里 —— 那条路等于没说。

    上面那组测的是"哪台浏览器跑了这一趟"，这一条测的是"调用方看得见吗"：走一次
    真实的 web_read，桩只换浏览器本身，envelope 那几层（summary / agent hints）
    照原样跑。
    """

    @staticmethod
    def _html_response(url):
        from dhole_mcp.fetcher import Response
        return Response(url=url, body=b"<html><body><p>page</p></body></html>",
                        status=200, headers={"content-type": "text/html"},
                        encoding="utf-8")

    def test_a_rung_that_needed_its_own_browser_says_so_in_the_summary(self, monkeypatch):
        from dhole_mcp.server import _SessionEntry

        class _WarmSession:
            _is_alive = True
            cdp_attached = False
            launch_note = ""
            _hide_canvas, _block_webrtc = _expected("auto")

            async def fetch(self, url, **kw):  # pragma: no cover - 不该被走到
                raise AssertionError("the off call rode the hardened shared browser")

        class _Own:
            cdp_attached = False
            launch_note = ""

            def __init__(self, **kw):
                assert (kw.get("hide_canvas"), kw.get("block_webrtc")) == (False, False)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def fetch(self, url, **kw):
                return TestTheNoteReachesTheCaller._html_response(url)

        server = Server()
        server._sessions["warm"] = _SessionEntry(session=_WarmSession(),
                                                 session_type="stealthy")

        async def fake_auto(self, **kw):
            return "warm"

        monkeypatch.setattr(Server, "_ensure_auto_session", fake_auto)
        monkeypatch.setattr(browser_mod, "StealthyBrowser", _Own)
        monkeypatch.setattr(server_mod, "_browser_deps_available", lambda: True)
        out = asyncio.run(server.web_read(URL__hardening_reaches_the_wa_0123, force_fetcher="stealthy",
                                         anti_detect="off", cache_ttl=0))
        assert not out.error, out
        assert "own browser" in (out.summary or "").lower(), out.summary


class TestWhatACaptureCanSteerPerCall:
    """A1：web_screenshot 那扇门下线了，捕获住进 web_act 的动作链。

    原来钉在这个类里的三件事各有去处：档位逐次决定"要不要答挑战"由 web_act
    自己的 anti_detect 处理（同一扇门上的既有类守）；缺席读存储由 cred_defaults
    的接线守；"捕获不许挡资源"由 actions 的 _has_screenshot 分支与
    test_eval_fixes_g35::test_a_screenshot_action_turns_resource_blocking_off
    守。这里只留接线仍在的凭证：screenshot 键就是那个分支的开关。
    """

    def test_the_screenshot_key_is_the_resource_shortcut_switch(self):
        from dhole_mcp.actions import actions_want_visual
        assert actions_want_visual([{"screenshot": "img"}]) is True
        assert actions_want_visual([{"click": "a"}]) is False
        assert actions_want_visual([{"click": "a"}, {"snapshot": True},
                                {"screenshot": {"selector": "img",
                                                "decode_qr": True}}]) is True


# ============================================================================
# 来源：test_meta_refresh_g29.py
# ============================================================================
# G29 回归：`<meta http-equiv=refresh>` 是写在正文里的重定向。

class _FakeResp:
    """Just enough of a primp response for the parser."""

    def __init__(self, html: str, content_type: str = "text/html; charset=utf-8"):
        self.headers = {"content-type": content_type}
        self.content = html.encode("utf-8")


def _target(html: str, base: str = "https://a.example/x", **kw) -> tuple[str, float]:
    return _meta_refresh_target(_FakeResp(html, **kw), base)


class TestTheParser:
    """四种真实写法都要认出来：测量里那页用的就是没引号的属性值。"""

    def test_quoted_standard_form(self):
        url, delay = _target('<html><head><meta http-equiv="refresh" '
                             'content="0;url=https://b.example/to"></head></html>')
        assert (url, delay) == ("https://b.example/to", 0.0)

    def test_unquoted_attribute_value(self):
        url, _ = _target('<html><head><meta http-equiv=REFRESH '
                         'content="3; URL=./to"></head></html>')
        assert url == "https://a.example/to"

    def test_single_quotes_and_spaces(self):
        url, delay = _target("<html><head><meta http-equiv='refresh' "
                             "content=' 2 ; url = /to '></head></html>")
        assert url == "https://a.example/to"
        assert delay == 2.0

    def test_relative_target_resolves_against_the_current_page(self):
        url, _ = _target('<meta http-equiv="refresh" content="0;url=next/page">',
                         base="https://a.example/docs/index")
        assert url == "https://a.example/docs/next/page"

    def test_self_refresh_is_not_a_redirect(self):
        """0;url=<自己> 是「重新加载」，跟它会白烧一跳，而且容易自己转圈。"""
        url, _ = _target('<meta http-equiv="refresh" content="0;url=/x">',
                         base="https://a.example/x")
        assert url == ""
        url, _ = _target('<meta http-equiv="refresh" content="0;url=/x#frag">',
                         base="https://a.example/x")
        assert url == ""

    def test_javascript_and_blank_targets_are_refused(self):
        for href in ("javascript:alert(1)", "about:blank", "data:text/html,x"):
            url, _ = _target(f'<meta http-equiv="refresh" content="0;url={href}">')
            assert url == "", href

    def test_non_html_body_is_not_parsed(self):
        html = '<meta http-equiv="refresh" content="0;url=https://b.example/to">'
        assert _target(html, content_type="application/json")[0] == ""

    def test_a_page_without_the_tag_returns_nothing(self):
        assert _target("<html><body><p>plain</p></body></html>")[0] == ""

    def test_first_refresh_wins(self):
        url, _ = _target('<meta http-equiv="refresh" content="0;url=/one">'
                         '<meta http-equiv="refresh" content="0;url=/two">')
        assert url == "https://a.example/one"

    def test_garbage_does_not_raise(self):
        for html in ('<meta http-equiv="refresh" content="url">',
                     '<meta http-equiv="refresh" content="">',
                     '<meta http-equiv="refresh">',
                     '<meta content="0;url=/x">',
                     '<meta http-equiv="refresh" content="abc;url=/x">'):
            assert isinstance(_target(html), tuple), html


class TestPageTypeStillSeesThePointer:

    def test_an_unquoted_refresh_tag_is_classified_as_a_redirect(self):
        """不跟随时（HEAD / follow_redirects=false）分类还得说得出「这是指针」。"""
        from dhole_mcp.envelope import detect_page_type
        html = ('<html><head><meta http-equiv=REFRESH content="3; URL=./to">'
                '<title>wait</title></head><body><p>' + "x" * 400 + "</p></body></html>")
        assert detect_page_type(html, "https://a.example/from", "text/html", 400) == "redirect"


class _HopClient:
    """A primp-shaped client whose whole chain of responses is supplied here."""

    def __init__(self, pages: dict[str, tuple[int, str]]):
        self.pages = pages
        self.seen: list[tuple[str, dict]] = []

    def request(self, method, url, **kw):
        self.seen.append((url, dict(kw.get("headers") or {})))
        status, html = self.pages.get(url, (404, "<html>gone</html>"))
        return _FakeHopResponse(url, status, html)


class _FakeHopResponse:
    def __init__(self, url: str, status: int, html: str):
        self.url = url
        self.status_code = status
        self.content = html.encode()
        self.headers = {"content-type": "text/html; charset=utf-8"}
        self.cookies = {}
        self.reason = ""

    @property
    def text(self) -> str:
        return self.content.decode()


async def _get(pages: dict, url: str, **get_kwargs):
    """Run one HTTPSession.get against a scripted hop chain; return (response, client)."""
    client = _HopClient(pages)
    session = HTTPSession()
    await session._init_client()
    session._client = client
    resp = await session.get(url, **get_kwargs)
    return resp, client


@pytest.fixture
def hop_pages():
    return {
        "https://a.example/from": (200, '<html><head><meta http-equiv="refresh" '
                                         'content="0;url=https://a.example/to">'
                                         "</head><body>pointer</body></html>"),
        "https://a.example/to": (200, "<html><body>destination</body></html>"),
        "https://a.example/off": (
            200, '<html><head><meta http-equiv="refresh" '
                 'content="0;url=https://off.example/to"></head></html>'),
        "https://off.example/to": (200, "<html><body>offsite destination</body></html>"),
    }


class TestTheHop:
    """跳要跳，但按 3xx 的规矩跳。"""

    @pytest.mark.asyncio
    async def test_a_refresh_page_is_walked_to_its_target(self, hop_pages):
        resp, client = await _get(hop_pages, "https://a.example/from")

        assert resp.url == "https://a.example/to"
        assert b"destination" in resp.body
        assert resp.meta_refresh == {"from": "https://a.example/from",
                                     "delay_s": 0.0, "hops": 1}
        assert [u for u, _ in client.seen] == ["https://a.example/from",
                                               "https://a.example/to"]

    @pytest.mark.asyncio
    async def test_credentials_do_not_ride_a_cross_origin_meta_hop(self, hop_pages):
        """刷新页指向别的主机：Cookie / Authorization 不能跟着过去。"""
        resp, client = await _get(hop_pages, "https://a.example/off",
                                  headers={"authorization": "Bearer secret",
                                           "cookie": "session=abc"})

        assert resp.url == "https://off.example/to"
        assert len(client.seen) == 2
        first, second = client.seen
        assert first[1].get("authorization") == "Bearer secret"
        assert "authorization" not in {k.lower() for k in second[1]}, second[1]
        assert "cookie" not in {k.lower() for k in second[1]}, second[1]

    @pytest.mark.asyncio
    async def test_same_origin_meta_hop_keeps_the_credentials(self, hop_pages):
        resp, client = await _get(hop_pages, "https://a.example/from",
                                  headers={"authorization": "Bearer secret"})
        assert resp.url == "https://a.example/to"
        assert client.seen[1][1].get("authorization") == "Bearer secret"

    @pytest.mark.asyncio
    async def test_head_does_not_follow(self, hop_pages):
        """HEAD 没有正文可读，跟下去就变成一次「说好不下载」的下载。"""
        resp, _ = await _get(hop_pages, "https://a.example/from", method="HEAD")
        assert resp.url == "https://a.example/from"
        assert resp.meta_refresh is None

    @pytest.mark.asyncio
    async def test_follow_redirects_false_does_not_follow(self, hop_pages):
        resp, _ = await _get(hop_pages, "https://a.example/from",
                             follow_redirects=False)
        assert resp.url == "https://a.example/from"
        assert resp.meta_refresh is None

    @pytest.mark.asyncio
    async def test_a_post_is_not_replayed_at_the_target(self, hop_pages):
        """表单 POST 之后跟着一个刷新页：把 body 寄到第二个地址上是错的。"""
        resp, _ = await _get(hop_pages, "https://a.example/from",
                             method="POST", body=b"field=1",
                             content_type="application/x-www-form-urlencoded")
        assert resp.url == "https://a.example/from"
        assert resp.meta_refresh is None

    @pytest.mark.asyncio
    async def test_a_meta_loop_shares_the_redirect_budget(self):
        pages = {
            "https://a.example/a": (200, '<html><head><meta http-equiv="refresh" '
                                          'content="0;url=https://a.example/b"></head></html>'),
            "https://a.example/b": (200, '<html><head><meta http-equiv="refresh" '
                                          'content="0;url=https://a.example/a"></head></html>'),
        }
        resp, client = await _get(pages, "https://a.example/a", max_redirects=3)
        assert len(client.seen) <= 4  # budget + 1，不是无限
        assert resp.meta_refresh["hops"] <= 4
        # 预算用尽时报的是「最后一次真正答话的地址」，不是正准备去的那一跳。
        assert resp.url == client.seen[-1][0]

    @pytest.mark.asyncio
    async def test_a_private_target_is_rejected_before_dialing(self):
        pages = {
            "https://a.example/loop": (
                200, '<html><head><meta http-equiv="refresh" '
                     'content="0;url=http://127.0.0.1:9/secret"></head></html>'),
        }
        client = _HopClient(pages)
        session = HTTPSession()
        await session._init_client()
        session._client = client
        with pytest.raises(SecurityError):
            await session.get("https://a.example/loop")
        # 一次都不许多：安全校验的「拒绝」不会因为再问一遍而改变，重试等于又被
        # 拒绝了才落回 origin —— 白敲一次服务器。
        assert [u for u, _ in client.seen] == ["https://a.example/loop"]


class TestTheRealServer:
    """端到端：真 socket、真 primp 客户端，只把内网放行当测试脚手架。"""

    @staticmethod
    def _serve(pages: dict[str, str]):
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                html = pages.get(self.path)
                if html is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = html.encode()
                self.send_response(200)
                self.send_header("content-type", "text/html; charset=utf-8")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    @pytest.mark.asyncio
    async def test_a_refresh_on_a_real_server_lands_on_the_destination(self):
        srv = self._serve({
            "/from": '<html><head><meta http-equiv="refresh" content="0;url=/to">'
                     "</head><body>pointer page</body></html>",
            "/to": "<html><body><h1>the real content</h1></body></html>",
        })
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            resp = await HTTPSession().get(f"{base}/from", allow_internal=True)
        finally:
            srv.shutdown()

        assert resp.url == f"{base}/to"
        text = _decode_html_bytes(resp.body, resp.encoding)
        assert "the real content" in text
        assert resp.meta_refresh["from"] == f"{base}/from"

    @pytest.mark.asyncio
    async def test_response_without_a_refresh_reports_nothing_extra(self):
        srv = self._serve({"/plain": "<html><body>plain</body></html>"})
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            resp = await HTTPSession().get(f"{base}/plain", allow_internal=True)
        finally:
            srv.shutdown()

        assert resp.meta_refresh is None
        assert resp.url == f"{base}/plain"


class TestTheResponseCarriesTheReason:

    def test_response_meta_refresh_defaults_to_none(self):
        assert Response(url="https://a.example/x", body=b"", status=200).meta_refresh is None

    def test_translate_response_surfaces_it_in_metadata(self, monkeypatch):
        from dhole_mcp import server as server_mod

        page = Response(url="https://a.example/to",
                        body=b"<html><body><p>destination</p></body></html>",
                        status=200, headers={"content-type": "text/html"},
                        meta_refresh={"from": "https://a.example/from",
                                      "delay_s": 0.0, "hops": 1})
        result = server_mod._translate_response(page, "markdown", None,
                                                "http", 1.0)
        assert result.metadata["meta_refresh"]["from"] == "https://a.example/from"
