"""G25 回归：`options.auth` —— 凭据怎么变成头，以及**它不该出现在哪里**。

报告的缺口是「无认证助手，只能靠 extra_headers.Authorization」（G25）。加一个把
dict 翻成头的函数不难，难的是这个功能天生带着三个静默失效的方向，每一个都比「不支持」
更糟：

    缓存    缓存按 URL 存正文。凭据不进指纹，管理员抓到的私有页面就会被之后的匿名
            请求回放出去 —— 那不是「缓存命中」，那是把别人的内容端出去。
    重定向  HTTP 层跨源重定向已经不发 Cookie/Authorization 了，但**调用方自己起的
            头名**（X-API-Key）不在内置名单里，得由这次调用声明。
    浏览器  `set_extra_http_headers` 的语义是「这个页面的每个请求都带上」，于是一个
            staging 站的 Basic 凭据会跟着图片、字体、分析端点发给每一个第三方 origin。
            升级到隐身层不该等于把凭据撒出去。

外加两条口径：401 要说清「凭据被拒」还是「根本没发凭据」（修法相反），而凭据的
**值**永远不进响应（工具输出会留在 agent 的转录里）。
"""

from __future__ import annotations

import base64

import pytest

from dhole_mcp.browser import _create_route_handler
from dhole_mcp.fetcher import HTTPSession, Response as DholeResponse
from dhole_mcp.security import SecurityError
from dhole_mcp.server import (
    MasterFetchServer,
    ResponseModel,
    _auth_request_headers,
    _cache_context,
)

URL = "https://staging.example.com/admin"
CROSS = "https://third-party-analytics.net/collect"


# ─── 形状 → 头 ───────────────────────────────────────────────────────

class TestTheShapesThatExist:

    def test_basic_is_the_default_when_type_is_omitted(self):
        hdr, kind, names, ignored = _auth_request_headers(
            {"username": "agent", "password": "s3cret"}, None)
        assert kind == "basic" and names == ("authorization",) and ignored is None
        token = hdr["Authorization"].removeprefix("Basic ")
        assert base64.b64decode(token).decode() == "agent:s3cret"

    def test_user_and_pass_are_accepted_because_that_is_the_spelling_in_the_docs(self):
        """G25 原文写的就是 auth:{type,user,pass}；只认 username/password 等于
        照着文档写的人第一次就撞一个 invalid_request。"""
        assert _auth_request_headers({"type": "basic", "user": "a", "pass": "b"}, None)[0] \
            == _auth_request_headers({"type": "basic", "username": "a", "password": "b"}, None)[0]

    def test_an_empty_password_is_still_a_password(self):
        """有些 API 用 token 当用户名、口令留空。键必须在，值可以空。"""
        hdr, *_ = _auth_request_headers({"type": "basic", "username": "tok", "password": ""}, None)
        assert base64.b64decode(hdr["Authorization"].removeprefix("Basic ")).decode() == "tok:"

    def test_bearer_sends_the_token_itself_not_a_base64_of_it(self):
        hdr, kind, names, _ = _auth_request_headers(
            {"type": "bearer", "token": "  eyJhb.abc  "}, None)
        assert (kind, names) == ("bearer", ("authorization",))
        assert hdr["Authorization"] == "Bearer eyJhb.abc"

    def test_an_api_key_header_goes_under_the_name_the_caller_chose(self):
        hdr, kind, names, _ = _auth_request_headers(
            {"type": "header", "name": "X-API-Key", "value": "k123"}, None)
        assert hdr == {"X-API-Key": "k123"}
        assert kind == "header"
        # 这个名字不在任何内置名单里：必须由这次调用声明出来，否则跨源那一跳
        # 没有人知道它是凭据。
        assert names == ("x-api-key",)

    def test_apikey_is_accepted_as_a_name_for_the_header_shape(self):
        assert _auth_request_headers(
            {"type": "apikey", "name": "X-Token", "value": "v"}, None)[0] == {"X-Token": "v"}

    def test_a_scheme_the_server_invented_can_still_go_in_authorization(self):
        """`Authorization: Token abc` 这类自定义 scheme 很常见，不必为此加类型。"""
        hdr, *_ = _auth_request_headers(
            {"type": "header", "name": "Authorization", "value": "Token abc"}, None)
        assert hdr == {"Authorization": "Token abc"}


class TestWhatIsRefusedBeforeAnyRequest:

    @pytest.mark.parametrize("bad", [
        "not-a-dict",
        ["authorization"],
        {"type": "oauth", "token": "x"},
        {"type": "basic", "username": "only-a-user"},
        {"type": "bearer", "token": ""},
        {"type": "bearer", "token": 42},
        {"type": "header", "name": "X Ok", "value": "v"},
        {"type": "header", "name": "X-Key", "value": ""},
        {"type": "header", "value": "no name"},
    ])
    def test_a_shape_that_cannot_be_honoured_raises(self, bad):
        # An empty dict/"" means "no credentials" and is checked separately;
        # a dict that cannot be parsed must not become a silent anonymous request.
        with pytest.raises((ValueError, SecurityError)):
            _auth_request_headers(bad, None)

    def test_a_credential_can_never_smuggle_a_second_header(self):
        """头注入：口令/token 里塞 CRLF 就是在造第二个头。凭据路径不能是例外。"""
        with pytest.raises((ValueError, SecurityError)):
            _auth_request_headers({"type": "bearer", "token": "a\r\nX-Admin: yes"}, None)
        with pytest.raises((ValueError, SecurityError)):
            _auth_request_headers({"type": "basic", "username": "a\n", "password": "b"}, None)

    def test_no_credentials_is_not_an_error(self):
        assert _auth_request_headers(None, None) == ({}, "", (), None)
        assert _auth_request_headers({}, None) == ({}, "", (), None)
        assert _auth_request_headers("", None) == ({}, "", (), None)

    def test_an_explicit_header_already_set_wins_and_says_so(self):
        """调用方自己写了 Authorization，又被 auth 覆盖 = 一个 surprises。
        忽略它并说出来，才是可诊断的。"""
        existing = {"Authorization": "Bearer theirs"}
        hdr, kind, names, ignored = _auth_request_headers(
            {"type": "basic", "username": "a", "password": "b"}, existing)
        assert hdr == {} and names == ()
        assert ignored and "ignored" in ignored and "Authorization" in ignored


# ─── 凭据改变答案，所以必须改变缓存键 ─────────────────────────────────

class TestACredentialedAnswerIsNotPublicContent:

    def test_the_fingerprint_moves_with_the_credential(self):
        plain = _cache_context({"url": URL})
        admin = _cache_context({"url": URL, "auth": {"type": "basic",
                                                    "username": "admin", "password": "x"}})
        other = _cache_context({"url": URL, "auth": {"type": "basic",
                                                     "username": "intern", "password": "x"}})
        assert plain == "", "一个匿名请求的键要和 16.0 之前逐字节相同"
        assert admin and other and admin != other
        assert len(admin) == 12, "指纹是 sha256 截断，不是明文"

    def test_the_secret_itself_is_not_in_the_fingerprint(self):
        bits = _cache_context({"auth": {"type": "bearer", "token": "super-secret-token"}})
        assert "super-secret-token" not in bits

    def test_anonymous_requests_keep_sharing_one_entry(self):
        assert _cache_context({"auth": None}) == _cache_context({}) == ""


# ─── 递到抓取层的东西 ────────────────────────────────────────────────

def _fake_tier(monkeypatch, seen: dict):
    class _Fake:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kwargs):
            seen.update(kwargs)
            return DholeResponse(url=url, body=b"<p>ok</p>", status=200,
                                 headers={"content-type": "text/html"})

    monkeypatch.setattr("dhole_mcp.fetcher.HTTPSession", _Fake)
    monkeypatch.setattr("dhole_mcp.fetcher.tcp_preflight",
                        lambda url, timeout=2.0: (True, ""))


class TestTheTierIsHandedBothTheHeaderAndTheNames:

    @pytest.mark.asyncio
    async def test_the_credential_reaches_the_request_as_a_header(self, monkeypatch):
        seen: dict = {}
        _fake_tier(monkeypatch, seen)

        out = await MasterFetchServer().smart_fetch(
            URL, auth={"type": "bearer", "token": "t0k"}, cache_ttl=0, timeout=8000)

        assert out.status == 200
        assert seen["headers"]["Authorization"] == "Bearer t0k"

    @pytest.mark.asyncio
    async def test_a_custom_key_name_is_declared_a_credential_for_later_hops(self, monkeypatch):
        """Authorization 在内置名单里；X-API-Key 不在。少了这个名字，跨源那一跳
        就没人知道该把它拿掉。"""
        seen: dict = {}
        _fake_tier(monkeypatch, seen)

        await MasterFetchServer().smart_fetch(
            URL, auth={"type": "header", "name": "X-API-Key", "value": "k"},
            cache_ttl=0, timeout=8000)

        assert "x-api-key" in seen["credential_headers"]

    @pytest.mark.asyncio
    async def test_the_response_never_carries_the_credential_back(self, monkeypatch):
        seen: dict = {}
        _fake_tier(monkeypatch, seen)

        out = await MasterFetchServer().smart_fetch(
            URL, auth={"type": "basic", "username": "admin", "password": "hunter2"},
            cache_ttl=0, timeout=8000)

        dumped = out.model_dump_json()
        assert "hunter2" not in dumped
        assert "YWRtaW46aHVudGVyMg==" not in dumped, "the base64 form is the same secret"
        assert "Authorization" not in dumped

    @pytest.mark.asyncio
    async def test_a_credential_that_is_ignored_is_said_out_loud(self, monkeypatch):
        seen: dict = {}
        _fake_tier(monkeypatch, seen)

        out = await MasterFetchServer().smart_fetch(
            URL, auth={"type": "bearer", "token": "ignored-me"},
            extra_headers={"Authorization": "Bearer theirs"},
            cache_ttl=0, timeout=8000)

        assert "ignored" in out.summary.lower()
        assert seen["headers"]["Authorization"] == "Bearer theirs"

    @pytest.mark.asyncio
    async def test_a_bad_shape_refuses_before_any_request(self, monkeypatch):
        async def boom(self, url, **kwargs):
            raise AssertionError("a request went out with an unparsed credential")
        monkeypatch.setattr(MasterFetchServer, "get", boom)

        out = await MasterFetchServer().smart_fetch(URL, auth={"type": "magic"}, cache_ttl=0)

        assert "invalid auth" in out.error
        assert "basic" in out.next_action and "bearer" in out.next_action

    @pytest.mark.asyncio
    async def test_the_contextvar_does_not_smuggle_a_credential_into_the_next_call(
            self, monkeypatch):
        seen: dict = {}
        _fake_tier(monkeypatch, seen)
        srv = MasterFetchServer()

        await srv.smart_fetch(URL, auth={"type": "bearer", "token": "first"},
                              cache_ttl=0, timeout=8000)
        seen.clear()
        await srv.smart_fetch(URL, cache_ttl=0, timeout=8000)

        assert "Authorization" not in str(seen.get("headers") or {})
        assert not seen.get("credential_headers")


# ─── 每一跳：跨源就不带 ─────────────────────────────────────────────

class _Resp:
    def __init__(self, url, status=200, headers=None, body=b"<p>ok</p>", cookies=None):
        self.url = url
        self.status_code = status
        self.headers = headers or {"content-type": "text/html"}
        self.content = body
        self.reason = ""
        self.cookies = cookies or {}


class _Client:
    def __init__(self, routes):
        self.routes = routes
        self.sends: list[dict] = []

    def request(self, method, url, **kwargs):
        self.sends.append({"method": method, "url": url, **kwargs})
        return self.routes.get(url) or _Resp(url, status=404)

    @property
    def last(self) -> dict:
        return self.sends[-1]


def _redirect_to_cross_origin() -> tuple[_Client, _Resp]:
    final = _Resp(CROSS)
    client = _Client({
        URL: _Resp(URL, status=302, headers={"location": CROSS, "content-type": "text/html"}),
        CROSS: final,
    })
    return client, final


class TestCredentialsDoNotRideAHostWeNeverAgreedTo:

    @pytest.mark.asyncio
    async def test_a_declared_api_key_is_stripped_off_origin(self):
        client, _ = _redirect_to_cross_origin()
        session = HTTPSession(retries=0, retry_delay=0)
        session._client = client

        await session.get(
            URL, headers={"X-API-Key": "k", "X-Trace": "keep-me"},
            credential_headers=("x-api-key",))

        assert len(client.sends) == 2, "the redirect should still be followed"
        hop = client.last
        assert hop["url"] == CROSS
        sent_names = {k.lower() for k in hop["headers"]}
        assert "x-api-key" not in sent_names
        assert "x-trace" in sent_names, "只拿掉凭据，别把请求弄坏"

    @pytest.mark.asyncio
    async def test_the_same_origin_still_gets_it(self):
        client = _Client({URL: _Resp(URL)})
        session = HTTPSession(retries=0, retry_delay=0)
        session._client = client

        await session.get(URL, headers={"X-API-Key": "k"}, credential_headers=("x-api-key",))

        assert client.last["headers"]["X-API-Key"] == "k"

    def test_the_http_tier_needs_no_declaration_for_the_standard_names(self):
        """Authorization/Cookie 在内置名单里：调用方没有 auth 参数、直接把头塞进
        extra_headers 的那条老路，也一样受跨源不带的约束。"""
        from dhole_mcp.fetcher import _CREDENTIAL_HEADERS, _hop_headers

        hop = _hop_headers({"authorization": "Basic x", "cookie": "a=1", "accept": "text/html"},
                           CROSS, "example.com", None, "", ())
        assert "authorization" not in hop and "cookie" not in hop
        assert hop["accept"] == "text/html"
        assert "authorization" in _CREDENTIAL_HEADERS


class TestTheBrowserTierBindsThemToo:
    """`set_extra_http_headers` 是「这个页面每个请求都加」，不是「只加给这一站」。

    所以凭据必须在 route handler 里按主机筛掉，否则隐身层成了漏点：一次 staging
    抓取会把口令的 base64 递给页面引用的每一个分析/广告 origin。
    """

    class _Route:
        def __init__(self, url, headers):
            self.request = type("R", (), {
                "url": url, "resource_type": "document", "headers": headers})()
            self.continued_with = None

        async def continue_(self, **kwargs):
            self.continued_with = kwargs

        async def abort(self):
            raise AssertionError("the handler should not abort this request")

    @pytest.mark.asyncio
    async def test_a_third_party_subresource_does_not_get_the_credential(self):
        route = self._Route("https://analytics.example.net/collect.png",
                            {"authorization": "Basic enM6", "accept": "image/*"})
        handler = _create_route_handler(False, None, None, entry_url=URL,
                                        sent_headers={"Authorization": "Basic enM6"})

        await handler(route)

        assert route.continued_with["headers"] == {"accept": "image/*"}

    @pytest.mark.asyncio
    async def test_the_origin_we_named_still_gets_it(self):
        route = self._Route(URL, {"authorization": "Basic enM6", "accept": "text/html"})
        handler = _create_route_handler(False, None, None, entry_url=URL,
                                        sent_headers={"authorization": "Basic enM6"})

        await handler(route)

        assert "headers" not in (route.continued_with or {})

    @pytest.mark.asyncio
    async def test_a_request_that_carries_no_credential_is_not_rewritten_at_all(self):
        """没有凭据时行为必须逐字节不变：给每个请求都造一份 overrides 是拿整个
        隐身层的稳定性去换一个用不到的功能。"""
        route = self._Route("https://cdn.example.net/app.js", {"accept": "*/*"})
        handler = _create_route_handler(False, None, None, entry_url=URL,
                                        sent_headers={"X-Trace": "abc"})

        await handler(route)

        assert "headers" not in (route.continued_with or {})

    @pytest.mark.asyncio
    async def test_a_page_with_no_entry_host_cannot_be_used_to_strip_everything(self):
        route = self._Route(CROSS, {"authorization": "Basic enM6"})
        handler = _create_route_handler(False, None, None, entry_url="",
                                        sent_headers={"Authorization": "Basic enM6"})

        await handler(route)

        assert "headers" not in (route.continued_with or {})


# ─── 401 的两条相反的路 ─────────────────────────────────────────────

class TestA401SaysWhichOneItIs:

    @pytest.mark.asyncio
    async def test_refused_when_credentials_were_sent(self, monkeypatch):
        async def fake_get(self, url, **kwargs):
            return ResponseModel(url=url, status=401, content=["<p>no</p>"],
                                 fetcher_used="http")
        monkeypatch.setattr(MasterFetchServer, "get", fake_get)

        out = await MasterFetchServer().smart_fetch(
            URL, auth={"type": "basic", "username": "a", "password": "wrong-on-purpose"},
            cache_ttl=0)

        assert "REFUSED" in out.next_action
        assert "basic" in out.next_action
        assert "wrong-on-purpose" not in out.next_action

    @pytest.mark.asyncio
    async def test_never_sent_when_no_credentials_were_given(self, monkeypatch):
        async def fake_get(self, url, **kwargs):
            return ResponseModel(url=url, status=401, content=["<p>no</p>"],
                                 fetcher_used="http")
        monkeypatch.setattr(MasterFetchServer, "get", fake_get)

        out = await MasterFetchServer().smart_fetch(URL, cache_ttl=0)

        assert "needs credentials" in out.next_action
        assert "options.auth" in out.next_action
        assert "REFUSED" not in out.next_action
