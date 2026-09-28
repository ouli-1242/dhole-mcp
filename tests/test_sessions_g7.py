"""G7 回归：`options.session_id` 的 cookie jar（sessions.py + 逐跳的凭据边界）。

报告 G7 的复现是「`/cookies/set?x=1` 之后 `/cookies` 是空的」——cookie 只活一次调用。
修法是一个按 (session_id, host) 存的 jar。这个文件钉的是它的两面：

    该带的要带上：同一 session、同一主机，上一轮 Set-Cookie 这一轮必须出现在请求头里
    不该带的不能带：跨源重定向不再把 Cookie / Authorization 送给跳转后的那台主机

第二面是新引入的能力与新引入的风险同形：jar 让凭据活得更久，所以「递给谁」必须比
以前更窄，而不是更宽。旧代码里 header 字典是一次调用建一次、逐跳原样重发，本来就是这个
泄露；现在逐跳重建，泄露面按跳收口。

cookie **值**不进响应（只进 `session_cookie_names` 名单）：工具输出会落进 agent 的
transcript，正文还会写进 ~/.dhole/cache.db，一条会话 cookie 就是凭据。
"""

from __future__ import annotations

import json
import time

import pytest

from dhole_mcp import sessions
from dhole_mcp.fetcher import HTTPSession
from dhole_mcp.server import (
    MasterFetchServer, ResponseModel, _cache_context, _SESSION_ID,
)

LOGIN = "https://shop.example.com/login"
ME = "https://shop.example.com/account"
OTHER = "https://elsewhere.example.org/landed"


class _Resp:
    def __init__(self, url, status=200, headers=None, cookies=None, body=b"ok"):
        self.url = url
        self.status_code = status
        self.headers = headers or {}
        self.content = body
        self.reason = ""
        self.cookies = [{"name": k, "value": v} for k, v in (cookies or {}).items()]


class _Client:
    """Scripted answers keyed by exact URL; records the headers of every dial."""

    def __init__(self, routes):
        self.routes = routes
        self.dials: list[tuple[str, dict]] = []
        self.methods: list[str] = []

    def request(self, method, url, **kwargs):
        self.dials.append((url, dict(kwargs.get("headers") or {})))
        self.methods.append(method)
        return self.routes.get(url) or _Resp(url, status=404)

    @property
    def last_cookie(self) -> str:
        return self.dials[-1][1].get("cookie", "")


def _session_with(client) -> HTTPSession:
    s = HTTPSession()
    s._client = client
    return s


# ─── jar 的行为 ──────────────────────────────────────────────────────

class TestTheJarRoundTrip:

    @pytest.mark.asyncio
    async def test_a_cookie_the_site_set_comes_back_on_the_next_call(self):
        client = _Client({LOGIN: _Resp(LOGIN, 200, cookies={"sid": "abc"})})
        await _session_with(client).get(LOGIN, session_id="bob")

        assert sessions.header_for("bob", ME) == {"sid": "abc"}, "jar did not keep it"

        await _session_with(client).get(ME, session_id="bob")
        assert client.last_cookie == "sid=abc"

    @pytest.mark.asyncio
    async def test_a_cookie_set_on_a_redirect_is_kept_too(self):
        """登录后 302 到首页、cookie 落在那张 302 上——最常见的形态。"""
        client = _Client({
            LOGIN: _Resp(LOGIN, 302, {"location": "/account"}, cookies={"sid": "abc"}),
            ME: _Resp(ME, 200),
        })
        await _session_with(client).get(LOGIN, session_id="bob", follow_redirects=True)

        assert sessions.header_for("bob", ME) == {"sid": "abc"}
        assert client.dials[-1][1].get("cookie") == "sid=abc", "the hop after the 302 got no cookie"

    @pytest.mark.asyncio
    async def test_no_session_id_means_no_jar_at_all(self):
        """默认（不点名 session_id）必须和 16.0 之前一模一样：一次性 cookie。"""
        client = _Client({LOGIN: _Resp(LOGIN, 200, cookies={"sid": "abc"})})
        await _session_with(client).get(LOGIN)

        assert sessions.header_for("", ME) == {}
        assert client.dials[-1][1].get("cookie") is None or "sid" not in client.last_cookie

    @pytest.mark.asyncio
    async def test_a_session_is_scoped_per_host(self):
        sessions.store("bob", "https://a.example.com/", {"sid": "secret-a"})

        client = _Client({ME: _Resp(ME, 200)})
        await _session_with(client).get(ME, session_id="bob")

        assert "secret-a" not in client.last_cookie, "cookie from another host leaked in"

    @pytest.mark.asyncio
    async def test_a_stored_cookie_expires(self):
        sessions.store("bob", ME, {"sid": "abc"}, now=time.time() - sessions.COOKIE_TTL_S - 1)
        assert sessions.header_for("bob", ME) == {}

    @pytest.mark.asyncio
    async def test_the_caller_named_cookie_wins_for_its_own_origin(self):
        sessions.store("bob", ME, {"sid": "from-jar"})
        client = _Client({ME: _Resp(ME, 200)})

        await _session_with(client).get(ME, session_id="bob", cookies={"sid": "named-now"})

        assert client.last_cookie == "sid=named-now"


# ─── 凭据的边界 ──────────────────────────────────────────────────────

class TestCredentialsDoNotRideARedirect:

    @pytest.mark.asyncio
    async def test_an_off_origin_redirect_gets_no_cookie(self):
        """此前 header 字典建一次、逐跳原样重发：站点把我们转到别的域，cookie（和
        Authorization）就跟着送过去了——那台主机是它选的，不是我们约的。"""
        client = _Client({
            LOGIN: _Resp(LOGIN, 302, {"location": "https://elsewhere.example.org/landed"}),
            OTHER: _Resp(OTHER, 200),
        })
        await _session_with(client).get(
            LOGIN, session_id="bob", cookies={"sid": "abc"},
            headers={"authorization": "Basic c2VjcmV0"}, follow_redirects=True)

        assert len(client.dials) == 2
        assert "cookie" not in client.dials[-1][1], client.dials[-1][1]
        assert "authorization" not in client.dials[-1][1], client.dials[-1][1]

    @pytest.mark.asyncio
    async def test_the_jar_of_the_host_being_dialed_is_still_presented(self):
        """同主机才算数：跳到另一台我们存过 cookie 的机器，那台的 cookie 照带——
        这是正常网页行为，也才是「登录后被转到 CDN 域」能走通的原因。"""
        sessions.store("bob", OTHER, {"cdn": "yes"})
        client = _Client({
            LOGIN: _Resp(LOGIN, 302, {"location": "https://elsewhere.example.org/landed"}),
            OTHER: _Resp(OTHER, 200),
        })
        await _session_with(client).get(LOGIN, session_id="bob", follow_redirects=True)

        assert client.dials[-1][1].get("cookie") == "cdn=yes"

    @pytest.mark.asyncio
    async def test_a_cookie_header_the_caller_placed_in_extra_headers_is_not_duplicated(self):
        """base 里已有 Cookie（大小写都算）时，jar 是替换它而不是再叠一行。"""
        client = _Client({ME: _Resp(ME, 200)})
        sessions.store("bob", ME, {"sid": "jar"})

        await _session_with(client).get(
            ME, session_id="bob", headers={"Cookie": "mine=1"})

        cookie_values = [v for k, v in client.dials[-1][1].items() if k.lower() == "cookie"]
        assert len(cookie_values) == 1, cookie_values
        assert cookie_values[0] == "sid=jar"

    @pytest.mark.asyncio
    async def test_same_origin_keeps_a_base_cookie_when_the_jar_has_nothing(self):
        client = _Client({ME: _Resp(ME, 200)})
        await _session_with(client).get(ME, headers={"cookie": "mine=1"})
        assert client.dials[-1][1].get("cookie") == "mine=1"


# ─── 响应 cookie 到底读得到吗（jar 的地基）────────────────────────────

class TestResponseCookiesAreReadAtAll:
    """primp 的 ``resp.cookies`` 是**映射**（``{'a':'1'}``）。旧代码按「列表 of
    dict / cookie 对象」去遍历它，遍历到的是键（字符串），两个分支都不匹配，于是
    每次都返回 ``{}`` —— 这正是报告里「所有结果 session_id 空、cookie 看不见」的
    机制：不是没人想读，是读的人一直读到空。jar 建在它上面就会一起塌。"""

    class _R:
        def __init__(self, cookies):
            self.cookies = cookies

    def test_the_mapping_shape_the_client_actually_returns(self):
        from dhole_mcp.fetcher import _resp_cookies
        assert _resp_cookies(self._R({"dhole": "probe1", "csrf": "tok"})) == {
            "dhole": "probe1", "csrf": "tok"}

    def test_the_sequence_shapes_still_read(self):
        """浏览器层自己拼的是列表，两种形状都得认。"""
        from dhole_mcp.fetcher import _resp_cookies

        class _C:
            name = "sid"
            value = "v"

        assert _resp_cookies(self._R([{"name": "a", "value": "1"}])) == {"a": "1"}
        assert _resp_cookies(self._R([_C()])) == {"sid": "v"}

    def test_a_response_without_cookies_reads_empty(self):
        from dhole_mcp.fetcher import _resp_cookies
        assert _resp_cookies(self._R(None)) == {}
        assert _resp_cookies(object()) == {}


# ─── smart_fetch 这一层 ──────────────────────────────────────────────

def _html_result(url: str) -> ResponseModel:
    return ResponseModel(url=url, status=200, content=["<p>ok</p>"],
                         content_type="text/html", fetcher_used="http")


class TestSmartFetchSeesTheSession:

    @pytest.mark.asyncio
    async def test_the_id_and_the_names_come_back_but_never_the_values(self, monkeypatch):
        sessions.store("bob", ME, {"sid": "super-secret", "csrf": "tok"})

        async def fake_get(self, url, **kwargs):
            return _html_result(url)
        monkeypatch.setattr(MasterFetchServer, "get", fake_get)

        out = await MasterFetchServer().smart_fetch(ME, cache_ttl=0, session_id="bob")

        assert out.session_id == "bob"
        assert sorted(out.session_cookie_names) == ["csrf", "sid"]
        # The jar's values must not surface in anything dhole derives. (A page body
        # that echoes its own cookies is the site talking, not dhole.)
        derived = json.dumps({
            "names": out.session_cookie_names, "summary": out.summary,
            "next_action": out.next_action, "error": out.error,
            "metadata": out.metadata,
        }, default=str)
        assert "super-secret" not in derived

    @pytest.mark.asyncio
    async def test_a_stateless_call_reports_no_session(self, monkeypatch):
        async def fake_get(self, url, **kwargs):
            return _html_result(url)
        monkeypatch.setattr(MasterFetchServer, "get", fake_get)

        out = await MasterFetchServer().smart_fetch(ME, cache_ttl=0)

        assert out.session_id == "" and out.session_cookie_names == []

    @pytest.mark.asyncio
    async def test_the_bulk_path_does_not_lose_the_session(self, monkeypatch):
        """urls=[...] 会按 URL 再调一次 smart_fetch；不转发 session_id 的话，第二批
        请求全部以「无会话」发出，cookie 静默不带上——而响应看起来完全正常。"""
        seen: list[str] = []

        async def fake_get(self, url, **kwargs):
            seen.append(_SESSION_ID.get())
            return _html_result(url)
        monkeypatch.setattr(MasterFetchServer, "get", fake_get)

        out = await MasterFetchServer().smart_fetch(
            "", urls=[ME, LOGIN], cache_ttl=0, session_id="bob")

        assert out.results[0].session_id == "bob"
        assert out.results[1].session_id == "bob"
        assert seen == ["bob", "bob"], seen

    @pytest.mark.asyncio
    async def test_an_unusable_id_is_said_out_loud_and_the_page_still_comes(self, monkeypatch):
        async def fake_get(self, url, **kwargs):
            return _html_result(url)
        monkeypatch.setattr(MasterFetchServer, "get", fake_get)

        out = await MasterFetchServer().smart_fetch(
            ME, cache_ttl=0, session_id="../etc/passwd")

        assert out.status == 200, "rejecting the id must not reject the page"
        assert out.session_id == ""
        assert "session_id" in out.summary and "ignored" in out.summary

    def test_the_cache_key_separates_sessions(self):
        """A 会话取回的正文不能被 B 的回放命中——那等于把一个人的页面端给另一个人。"""
        a = _cache_context({"session_id": "alice"})
        b = _cache_context({"session_id": "bob"})
        assert a and b and a != b
        assert _cache_context({}) == ""


# ─── 清得掉 ──────────────────────────────────────────────────────────

class TestTheJarCanBeForgotten:

    @pytest.mark.asyncio
    async def test_cache_clear_forgets_the_jar(self):
        sessions.store("bob", ME, {"sid": "abc"})
        out = await MasterFetchServer().cache_clear()
        assert sessions.header_for("bob", ME) == {}
        assert "cookie" in out.message.lower()

    @pytest.mark.asyncio
    async def test_close_session_forgets_the_jar_even_without_a_browser(self):
        """close_session 以前只认浏览器会话；只有 jar 的 id 会先抛「not found」，
        cookie 就留在磁盘上了。"""
        sessions.store("bob", ME, {"sid": "abc"})
        out = await MasterFetchServer().close_session("bob")
        assert sessions.header_for("bob", ME) == {}
        assert "cookie" in out.message.lower()

    @pytest.mark.asyncio
    async def test_an_unknown_id_is_answered_in_the_envelope(self):
        """以前这里 `raise ValueError("Session 'x' not found.")`。

        接上 wire 之后改成信封里的 `error` + 一句列出**确实开着**的 id：一个只会
        抛的工具名到客户端只剩一句文本，而这条错误的价值全在清单上——调用方要的
        是「那我该关哪个」，不是「你给的名字不存在」。
        """
        sessions.store("real-one", ME, {"sid": "abc"})
        out = await MasterFetchServer().close_session("never-existed")
        assert out.error.startswith("Nothing is open under 'never-existed'")
        assert "real-one" in out.next_action
        assert out.closed == 0 and out.cookies_forgotten == 0
        assert sessions.header_for("real-one", ME) == {"sid": "abc"}, "报错不该顺手关掉别人"
