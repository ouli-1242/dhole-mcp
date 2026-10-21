"""test_browser_2.py —— 由 2 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import base64
import pytest
from dhole_mcp import identity
from dhole_mcp.browser import _create_route_handler
from dhole_mcp.fetcher import HTTPSession, Response as DholeResponse
from dhole_mcp.security import SecurityError
from dhole_mcp.server import MasterFetchServer, ResponseModel, _auth_request_headers, _cache_context
import asyncio
import json
from types import SimpleNamespace
from dhole_mcp import env_registry
from dhole_mcp import browser as B
from dhole_mcp.browser import BrowserBusyError
from dhole_mcp.server import BulkResponseModel


# ============================================================================
# 来源：test_auth_g25.py
# ============================================================================
# G25 回归：`auth` —— 凭据怎么变成头，以及**它不该出现在哪里**。

URL = "https://staging.example.com/admin"
CROSS = "https://third-party-analytics.net/collect"
# 凭据从此是**存储里**的一行，而 `auth` 是 host-scoped 的键：这里的 scope 必须认得
# URL 的那个主机，值才会应用上去。写错一个字符就是一条红 —— 那正是这次改造要盯着
# 的事实（一条为 example.com 存的凭据不该去敲 evil-example.com 的门）。
HOST = "staging.example.com"
OTHER = "https://other.example.org/dashboard"


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
        identity.set_value("auth", {"type": "bearer", "token": "t0k"}, scope=HOST)

        out = await MasterFetchServer().web_read(URL, cache_ttl=0, timeout=8000)

        assert out.status == 200
        assert seen["headers"]["Authorization"] == "Bearer t0k"

    @pytest.mark.asyncio
    async def test_a_custom_key_name_is_declared_a_credential_for_later_hops(self, monkeypatch):
        """Authorization 在内置名单里；X-API-Key 不在。少了这个名字，跨源那一跳
        就没人知道该把它拿掉。"""
        seen: dict = {}
        _fake_tier(monkeypatch, seen)
        identity.set_value("auth", {"type": "header", "name": "X-API-Key", "value": "k"},
                           scope=HOST)

        await MasterFetchServer().web_read(URL, cache_ttl=0, timeout=8000)

        assert "x-api-key" in seen["credential_headers"]

    @pytest.mark.asyncio
    async def test_the_response_never_carries_the_credential_back(self, monkeypatch):
        seen: dict = {}
        _fake_tier(monkeypatch, seen)
        identity.set_value("auth", {"type": "basic", "username": "admin",
                                    "password": "hunter2"}, scope=HOST)

        out = await MasterFetchServer().web_read(URL, cache_ttl=0, timeout=8000)

        dumped = out.model_dump_json()
        assert "hunter2" not in dumped
        assert "YWRtaW46aHVudGVyMg==" not in dumped, "the base64 form is the same secret"
        assert "Authorization" not in dumped

    @pytest.mark.asyncio
    async def test_a_header_the_caller_cannot_pass_anymore_is_refused_not_ignored(
            self, monkeypatch):
        """`extra_headers` 没有替代品：一条凭据形状的默认属于 auth（它认 host），
        不属于一次调用现挂的头。老契约里「调用方自己写的 Authorization 盖住 auth，
        并说一句 ignored」随那条参数一起没了，所以这里钉还活着的两件事：传它是硬拒
        绝（不是静默吞掉），而没存凭据的调用真的是匿名的 —— 存储是唯一来源。
        """
        seen: dict = {}
        _fake_tier(monkeypatch, seen)

        with pytest.raises(ValueError, match="Unsupported argument") as exc:
            await MasterFetchServer()._dispatch(
                "web_read", {"url": URL,
                             "extra_headers": {"Authorization": "Bearer theirs"}})
        assert "extra_headers" in str(exc.value)
        assert seen == {}, "被拒绝的这次调用不该发出任何请求"

        out = await MasterFetchServer().web_read(URL, cache_ttl=0, timeout=8000)

        assert out.status == 200
        assert "authorization" not in {k.lower() for k in (seen.get("headers") or {})}
        assert not seen.get("credential_headers")

    @pytest.mark.asyncio
    async def test_a_bad_shape_refuses_before_any_request(self, monkeypatch):
        async def boom(self, url, **kwargs):
            raise AssertionError("a request went out with an unparsed credential")
        monkeypatch.setattr(MasterFetchServer, "get", boom)
        identity.set_value("auth", {"type": "magic"}, scope=HOST)

        out = await MasterFetchServer().web_read(URL, cache_ttl=0)

        assert "invalid auth" in out.error
        # D-01（2026-10-08 验收）：三形状列表在一颗回执里只有一个作者 —— raise 那一位。
        # `next_action` 从前自己再抄一遍，现在它说"去哪看"和"什么都没发出去"。
        assert "basic" in out.error and "bearer" in out.error and "header" in out.error
        assert "No request was made" in out.next_action
        assert out.error.count("{type:'bearer',token}") == 1
        assert "{type:'bearer',token}" not in out.next_action

    @pytest.mark.asyncio
    async def test_a_stored_credential_does_not_smuggle_itself_into_the_next_host(
            self, monkeypatch):
        """凭据的「下一次调用」从此带一个条件：同一个 host。

        这一条原来防的是 contextvar 把上一次的凭据漏进下一次调用。存储之后，同一主机
        的下一次调用**本该**再拿到它（那正是"设一次、之后每一次都用"的意义），所以
        泄漏的定义换成了「跟着去了一个没为它存过的主机」。忘了那一行之后连本机都不该
        再发，所以末尾一并钉住：存储是唯一来源。
        """
        seen: dict = {}
        _fake_tier(monkeypatch, seen)
        srv = MasterFetchServer()
        identity.set_value("auth", {"type": "bearer", "token": "first"}, scope=HOST)

        await srv.web_read(URL, cache_ttl=0, timeout=8000)
        assert seen["headers"]["Authorization"] == "Bearer first", \
            "先证明存的那一行真的应用了，否则下面两条只是空断言"

        seen.clear()
        await srv.web_read(OTHER, cache_ttl=0, timeout=8000)

        assert "Authorization" not in str(seen.get("headers") or {})
        assert not seen.get("credential_headers")

        seen.clear()
        identity.unset(["auth"])
        await srv.web_read(URL, cache_ttl=0, timeout=8000)

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

    def test_a_scheme_downgrade_is_cross_origin_for_credentials(self):
        """同主机的 https→http 降级也是跨源（G25 评审实测洞）：凭据跟着降级跳
        走明文线，等于把它递给链路上的每个中间人。浏览器按 Fetch 规范剥掉，
        HTTP 层现在一样剥——生产调用点传 origin_url 走严格口径。"""
        from dhole_mcp.fetcher import _hop_headers

        hop = _hop_headers({"authorization": "Basic x", "cookie": "a=1"},
                           "http://api.example.com/next", "api.example.com",
                           None, "", (),
                           origin_url="https://api.example.com/start")
        assert "authorization" not in hop and "cookie" not in hop

    def test_same_scheme_same_port_still_carries_credentials(self):
        from dhole_mcp.fetcher import _hop_headers

        hop = _hop_headers({"authorization": "Basic x", "cookie": "a=1"},
                           "https://api.example.com/next", "api.example.com",
                           None, "", (),
                           origin_url="https://api.example.com/start")
        assert hop["authorization"] == "Basic x"
        assert hop["cookie"] == "a=1"

    def test_a_port_change_is_cross_origin_too(self):
        """_host_of 不带端口，端口在严格口径里补齐：同主机 :8443→:9999 也按
        跨源剥凭据（评审注记：问题面比降级一例更宽）。"""
        from dhole_mcp.fetcher import _hop_headers

        hop = _hop_headers({"authorization": "Basic x"},
                           "http://api.example.com:9999/next", "api.example.com",
                           None, "", (),
                           origin_url="http://api.example.com:8443/start")
        assert "authorization" not in hop


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
        identity.set_value("auth", {"type": "basic", "username": "a",
                                    "password": "wrong-on-purpose"}, scope=HOST)

        out = await MasterFetchServer().web_read(URL, cache_ttl=0)

        assert "REFUSED" in out.next_action
        assert "basic" in out.next_action
        assert "wrong-on-purpose" not in out.next_action

    @pytest.mark.asyncio
    async def test_never_sent_when_no_credentials_were_given(self, monkeypatch):
        async def fake_get(self, url, **kwargs):
            return ResponseModel(url=url, status=401, content=["<p>no</p>"],
                                 fetcher_used="http")
        monkeypatch.setattr(MasterFetchServer, "get", fake_get)

        out = await MasterFetchServer().web_read(URL, cache_ttl=0)

        assert "needs credentials" in out.next_action
        # 17.0 §3-17：凭据不再是逐次参数，所以这句话的门是 cred_defaults。
        assert "auth" in out.next_action and "cred_defaults" in out.next_action
        assert "REFUSED" not in out.next_action


# ============================================================================
# 来源：test_the_operator_can_ask_for_a_visible_browser.py
# ============================================================================
# 批次 11 → 看得见与否是**一次调用**要问的问题，不是一台机器的配置。

# ─── 1 · 配置面退回原样 ──────────────────────────────────────────────────────

def test_no_browser_knobs_survived_the_reversal():
    """两颗旋钮整体退掉：注册表里没有名字，设了也不读（不是"文档没写"，是不存在）。"""
    names = {e.name for e in env_registry.ALL}
    assert "DHOLE_BROWSER_HEADED" not in names
    assert "DHOLE_BROWSER_CHANNEL" not in names
    assert len(env_registry.ALL) == 15, (
        f"注册表应当是 15 颗（那两颗浏览器旋钮整体退掉之后，行级垃圾过滤补了一颗；"
        f"多模型档位又补了 DHOLE_RERANK_MODEL），现在 {len(env_registry.ALL)} 颗：{sorted(names)}")
    assert not hasattr(B, "_browser_headed") and not hasattr(B, "_forced_browser_channel"), (
        "读旋钮的那两个函数还活着 —— 名字退了、代码没退，等于留了两扇门")


def test_setting_the_retired_channel_env_changes_nothing(monkeypatch):
    """反向守卫：把旧旋钮名设上，启动判定必须**当作没看见**。

    留着读它的代码就是留着第二扇门：`dhole config set` 拒它，但 env 谁都设得了。
    """
    monkeypatch.setenv("DHOLE_BROWSER_CHANNEL", "msedge")
    monkeypatch.setenv("DHOLE_BROWSER_HEADED", "1")
    assert B._launch_channel(False) == B._detect_chrome_channel()
    assert B.BrowserSession()._headless is True


def test_headed_is_not_a_persistent_state():
    """`headed` 不进 cred_defaults：那是"这台机器一直这样"，而它每次都可能不同。"""
    assert "headed" not in identity.GLOBAL_KEYS


# ─── 2 · 门只有一扇 ─────────────────────────────────────────────────────────

def test_the_public_door_is_web_act_only():
    defs = {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}
    act = json.dumps(defs["web_act"], ensure_ascii=False)
    read = json.dumps(defs["web_read"], ensure_ascii=False)
    assert '"headed"' in act, "参数上了 web_act 的 schema —— 没有它这条能力没人能调用"
    assert "headed" not in read and "headless" not in read, (
        "web_read 的 schema 里出现可见性：那扇内部门被开到了外面")


def test_callers_cannot_pass_headed_to_web_read():
    """web_read 收这个 kwarg（web_act 要用），但表外名拒答必须仍然拒得住外部调用方。

    拒答的形态是 dispatch **抛** ValueError（`_reject_unknown_args` 就这么设计的），
    不是返回一条信封 —— 所以判据问的是"有没有被拒"，不是"回执长什么样"。
    """
    with pytest.raises(ValueError) as boom:
        asyncio.run(MasterFetchServer()._dispatch(
            "web_read", {"url": "https://example.test/", "headed": True}))
    msg = str(boom.value)
    assert "headed" in msg, f"拒的不是这颗名字：{msg[:300]}"
    assert "Unsupported argument" in msg
    assert "headed" not in msg.split("Supported:")[1], (
        "白名单把 headed 放行了 —— 那扇内部门就被开到了外面")


def _act_schema_names() -> set:
    """dispatch 认的参数名（`_TOP_LEVEL_ARGS`）—— 不是 schema，两者必须一致。"""
    from dhole_mcp import server as S
    return set(S._TOP_LEVEL_ARGS["web_act"])


def test_headed_is_whitelisted_for_web_act_only():
    """门只有一扇的另一半：白名单里 web_act 有、web_read 没有。

    判据落在 `_TOP_LEVEL_ARGS` 而不是 `_TOOL_DEFS`，因为 dispatch 读的是前者 —— schema
    上写了却没进白名单，参数会被拒；进了白名单却没上 schema，调用方无从得知。两边都查。
    """
    from dhole_mcp import server as S
    assert "headed" in _act_schema_names()
    assert "headed" not in S._TOP_LEVEL_ARGS["web_read"]
    defs = {d["name"]: d for d in S.MasterFetchServer._TOOL_DEFS}
    assert set(defs["web_act"]["inputSchema"]["properties"]) == _act_schema_names(), (
        "web_act 的 schema 与 dispatch 白名单不一致：多出来的名字要么没人能调，"
        "要么调了被静默拒 —— 两边都算说谎")
    assert "headed" not in defs["web_read"]["inputSchema"]["properties"]


# ─── 3 · 这次的请求怎么变成那台浏览器的形态 ──────────────────────────────────

def _fake_lane(monkeypatch):
    """整条 stealthy 车道：起浏览器那一刀与抓取那一刀各记一次 headless。

    两个都要记，因为它们是两个不同的时刻：`_ensure_auto_session` 决定**起**的是哪一台
    （一起来就改不回去了），`bulk_stealthy_fetch` 只是沿用同一个要求。只查后者的话，
    "起了一台 headless 的、回执却说这是可见调用"这种谎抓不住。
    """
    seen = {}

    async def _ensure(self, *, lock_wait=None, profile_name=None, headless=True):
        seen["launched_headless"] = headless
        return "fake-session"

    async def _fake(self, urls, **kwargs):
        seen["headless"] = kwargs.get("headless")
        return BulkResponseModel(
            results=[ResponseModel(url=urls[0], status=200, content=["ok"],
                                   fetcher_used="stealthy")],
            total=1, successful=1)

    monkeypatch.setattr(MasterFetchServer, "_ensure_auto_session", _ensure)
    monkeypatch.setattr(MasterFetchServer, "bulk_stealthy_fetch", _fake)
    return seen


def test_act_headed_true_launches_hidden_false(monkeypatch):
    seen = _fake_lane(monkeypatch)
    asyncio.run(MasterFetchServer().web_act(url="https://example.test/",
                                            actions=[{"snapshot": True}], headed=True))
    assert seen["headless"] is False, "headed=true 到了抓取层又变回 headless —— 白说"
    assert seen["launched_headless"] is False, (
        "起浏览器那一刀还是 headless：抓取层说不要，launcher 照样起了台看不见的，"
        "于是回执讲的是这次**想要**什么，不是屏幕上**有没有**")


def test_act_without_headed_stays_headless(monkeypatch):
    seen = _fake_lane(monkeypatch)
    asyncio.run(MasterFetchServer().web_act(url="https://example.test/",
                                            actions=[{"snapshot": True}]))
    assert seen["headless"] is True, (
        "默认必须还是档位表写死的 headless —— 这批不许顺手改反爬行为")
    assert seen["launched_headless"] is True


def test_a_string_false_is_not_a_window(monkeypatch):
    """字符串客户端的 `headed: "false"` 必须读成**假**。

    按裸值传，非空字符串就是真 —— 于是一次没人要窗口的调用把浏览器开到用户屏幕上。
    这是 `confirm` 一族的老缺陷（"false" 授权过一次 attach），而这一颗授权的是"往
    屏幕上放东西"，代价不比那道门小。判据问 dispatch 之后的**实际**形态，不问参数到没到。
    """
    seen = _fake_lane(monkeypatch)
    asyncio.run(MasterFetchServer()._dispatch(
        "web_act", {"url": "https://example.test/",
                    "actions": [{"snapshot": True}], "headed": "false"}))
    assert seen["launched_headless"] is True, (
        '"false" 被读成真：窗口弹出来了，而调用方说的是不要')
    assert seen["headless"] is True


def test_a_string_true_is_a_window(monkeypatch):
    """反向也要成立：`"true"` 读成真，否则这颗参数只对一半的客户端存在。"""
    seen = {}

    async def _ensure(self, *, lock_wait=None, profile_name=None, headless=True):
        seen["launched_headless"] = headless
        raise BrowserBusyError("browser_busy: stop here - the form is already decided")

    monkeypatch.setattr(MasterFetchServer, "_ensure_auto_session", _ensure)
    out = asyncio.run(MasterFetchServer()._dispatch(
        "web_act", {"url": "https://example.test/",
                    "actions": [{"snapshot": True}], "headed": "true"}))
    assert seen["launched_headless"] is False
    assert out[1]["error"].startswith("browser_busy"), "拒绝没走信封"


def test_headed_is_not_a_cache_or_stealth_rung(monkeypatch):
    """可见性只动 headless 一位：不缓存照旧，档位表其余部分一个字不改。

    两半各有历史事故。①`headed` 若让人顺手带上缓存，窗口里读到的就是别的时刻的页面，
    用户正看着的那个和回执说的不是同一份。②它若进了 `_ANTI_DETECT` 的档位，就等于把
    "看不见"从反爬强度里摘掉了 —— 而 headless 正是那台浏览器存在的理由之一。
    """
    from dhole_mcp import rungs

    seen = {}

    async def _fake_read(self, url, **kwargs):
        seen.update(kwargs)
        return ResponseModel(url=url, status=200, content=["ok"],
                             fetcher_used="stealthy")

    monkeypatch.setattr(MasterFetchServer, "web_read", _fake_read)
    asyncio.run(MasterFetchServer().web_act(url="https://example.test/",
                                            actions=[{"snapshot": True}], headed=True))
    assert seen["headed"] is True, "headed 没传到执行车道 —— 门在，链没接上"
    assert seen["cache_ttl"] == 0, "可见的那台也不能从缓存里读页面 —— 动作要落在真页面上"
    for rung in ("off", "basic", "auto", "aggressive"):
        assert rungs._anti_detect(rung)["headless"] is True, (
            f"档位 {rung} 的 headless 被动过：可见性不该是反爬强度的一档")


# ─── 4 · 复用判据：不对称，且不接管不关窗 ────────────────────────────────────

def _warm(monkeypatch, *, headless, profile=None):
    """一台已经热着的 auto 会话，形态由它自己说。

    那颗假浏览器的画布噪声 / WebRTC 位必须**照当下档位表**摆：能不能复用问的是这两颗
    加上 profile 加上可见与否，三位里任何一位对不上都会走到"另起一台"，而这批的用例
    在离线跑 —— conftest 的 `_nobody_launches_a_browser` 会把它拦成红。
    """
    from dhole_mcp import rungs

    hard = rungs._default_hardening()
    srv = MasterFetchServer()
    session = SimpleNamespace(_is_alive=True, _hide_canvas=hard["hide_canvas"],
                              _block_webrtc=hard["block_webrtc"], _headless=headless)
    srv._sessions = {"warm": SimpleNamespace(session=session, _alive=True)}
    srv._auto_stealthy_id = "warm"
    srv._auto_stealthy_last_used = 0
    srv._auto_stealthy_profile = profile
    monkeypatch.setattr("dhole_mcp.server._browser_deps_available", lambda: True)
    monkeypatch.setattr(MasterFetchServer, "_ensure_idle_monitor", lambda self: None)
    return srv


def test_a_hidden_browser_is_not_taken_over_and_not_closed(monkeypatch):
    srv = _warm(monkeypatch, headless=True)
    closed = []
    monkeypatch.setattr(MasterFetchServer, "_close_one_session",
                        lambda self, sid: closed.append(sid))
    with pytest.raises(BrowserBusyError):
        asyncio.run(srv._ensure_auto_session(headless=False))
    assert closed == [], "为了让这次可见就把别人的浏览器关掉了 —— 扔的是用户正在用的标签页"


def test_a_visible_browser_serves_a_background_call(monkeypatch):
    srv = _warm(monkeypatch, headless=False)
    assert asyncio.run(srv._ensure_auto_session(headless=True)) == "warm"


def test_a_visible_browser_is_reused_by_a_visible_call(monkeypatch):
    srv = _warm(monkeypatch, headless=False)
    assert asyncio.run(srv._ensure_auto_session(headless=False)) == "warm"


def test_headless_behaviour_is_unchanged(monkeypatch):
    """反向：后台请求之间的复用一条都没动（这批不许顺手改反爬行为）。"""
    srv = _warm(monkeypatch, headless=True)
    assert asyncio.run(srv._ensure_auto_session(headless=True)) == "warm"


# ─── 4b · 同一条规则也要管具名会话（2026-10-08 验收 D-22）───────────────────

def _warm_named(monkeypatch, *, headless, sid="qa"):
    """一台已经活着的**具名**会话浏览器。

    具名会话走 `_ensure_session_browser`，不是 `_ensure_auto_session`：那里管的是共享的
    auto 池，而具名会话的浏览器就是那个会话的意义本身（登录态在里面）。批次 11 把可见性
    规则只写进了前者，于是实连测到：隐藏浏览器在场的同一个 session_id 上
    `web_act(headed=true)` 回 200、`metadata.browser` 为空 —— 用户等的那个窗口从来没出现，
    而回执一句话都没说。
    """
    srv = MasterFetchServer()
    session = SimpleNamespace(_is_alive=True, _headless=headless)
    srv._sessions = {sid: SimpleNamespace(session=session, session_type="stealthy",
                                          _alive=True)}
    monkeypatch.setattr("dhole_mcp.server._browser_deps_available", lambda: True)
    return srv, sid


def test_a_named_session_with_a_hidden_browser_refuses_a_visible_call(monkeypatch):
    srv, sid = _warm_named(monkeypatch, headless=True)
    closed = []
    monkeypatch.setattr(MasterFetchServer, "_close_one_session",
                        lambda self, s: closed.append(s))
    with pytest.raises(BrowserBusyError) as boom:
        asyncio.run(srv._ensure_session_browser(sid, headless=False))
    assert closed == [], "为了让这次可见就把会话浏览器关了 —— 里面可能是用户的登录态"
    assert sid in str(boom.value), "拒绝必须点名是哪个会话，否则没人知道要交回哪一台"
    assert "sys_state(clear=" in str(boom.value), "出路要写在同一句里"


def test_a_named_session_with_a_visible_browser_serves_a_hidden_call(monkeypatch):
    """不对称照旧：可见的那台乐意干后台的活。"""
    srv, sid = _warm_named(monkeypatch, headless=False)
    assert asyncio.run(srv._ensure_session_browser(sid, headless=True)) == sid


def test_a_named_visible_session_is_reused_by_a_visible_call(monkeypatch):
    srv, sid = _warm_named(monkeypatch, headless=False)
    assert asyncio.run(srv._ensure_session_browser(sid, headless=False)) == sid


# ─── 5 · 那句话问实例，不问旋钮 ─────────────────────────────────────────────

def test_a_headless_session_says_nothing():
    assert B.BrowserSession()._headless is True
    assert B.BrowserSession().launch_note == "", (
        "headless 是常态，说了就是给每一次抓取加一句废话")


def test_a_visible_session_names_the_profile_it_survives_on():
    s = B.BrowserSession(headless=False, profile_name="main")
    s._persistent_profile = True
    note = s.launch_note
    assert "visible window" in note and "main" in note and "survive" in note
    assert "sys_state" in note, "把窗口交回来的那一步必须写在同一句里"


def test_a_visible_session_on_a_throwaway_dir_says_that():
    note = B.BrowserSession(headless=False).launch_note
    assert "THROWAWAY" in note and "cred_defaults" in note, \
        "只说问题不给出路，等于让调用方猜那颗旋钮叫什么"


class _FakeBrowser:
    """一台"被 dhole 起来的"假浏览器：只回答两件事 —— 我怎么起来的、抓取回来了什么。"""

    def __init__(self, note):
        self.cdp_attached = False
        self.launch_note = note

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def fetch(self, url, **kw):
        return SimpleNamespace(url=url, html="<p>x</p>", status_code=200, title="",
                               headers={}, redirect_url="", response_time=0.01)


def _live_row(monkeypatch, note):
    # 抓取车道里 `StealthyBrowser` 是**函数内**从 dhole_mcp.browser 取的（server 顶层
    # 没有这个名字 —— 浏览器依赖不在的时候导入期就不能炸），所以假件要装在它被取的那
    # 个模块上，装在 server 上只会 AttributeError，或更糟：悄悄加一个没人读的属性。
    monkeypatch.setattr("dhole_mcp.server._browser_deps_available", lambda: True)
    monkeypatch.setattr("dhole_mcp.browser.StealthyBrowser",
                        lambda *a, **k: _FakeBrowser(note))
    monkeypatch.setattr("dhole_mcp.server._translate_response",
                        lambda page, *a, **k: ResponseModel(
                            url=page.url, status=200, content=["ok"],
                            fetcher_used="stealthy"))
    bulk = asyncio.run(MasterFetchServer().bulk_stealthy_fetch(
        urls=["https://example.test/"]))
    return bulk.results[0]


def test_a_live_visible_fetch_stamps_every_row(monkeypatch):
    """回执说**实际跑的那台**，而不是这次想要的那种。"""
    row = _live_row(monkeypatch, "visible window, profile 'main'")
    assert row.metadata["browser"] == "visible window, profile 'main'"


def test_a_headless_fetch_stays_silent(monkeypatch):
    row = _live_row(monkeypatch, "")
    assert "browser" not in (row.metadata or {}), (
        "后台抓取也被盖了一句 visible —— 那句话就不再是事实而是噪音了")


def test_a_cache_hit_does_not_replay_a_window_claim(monkeypatch):
    """真跑一次"抓到 → 存下 → 从缓存读回"，而不是在测试里重写一遍那段字典推导。

    上一版这条是把生产代码的那一行抄进测试再断言它等于自己 —— 那种测试永远绿，而
    存进去的那句 "visible window" 讲的是**上一次抓取时开着的那台机器**，缓存命中时
    它可能早就退了。整份 metadata 一起丢也不行（标题没了）。
    """
    url = "https://example.test/window-claim"
    srv = MasterFetchServer()

    async def _ensure(self, *, lock_wait=None, profile_name=None, headless=True):
        return "fake-session"

    async def _fake(self, urls, **kwargs):
        return BulkResponseModel(
            results=[ResponseModel(url=urls[0], status=200, content=["body text"],
                                   fetcher_used="stealthy",
                                   metadata={"browser": "visible window, profile 'main'",
                                             "title": "the page"})],
            total=1, successful=1)

    monkeypatch.setattr(MasterFetchServer, "_ensure_auto_session", _ensure)
    monkeypatch.setattr(MasterFetchServer, "bulk_stealthy_fetch", _fake)

    first = asyncio.run(srv.web_read(url=url, force_fetcher="stealthy", cache_ttl=600,
                                     ignore_robots=True))
    assert first.metadata.get("browser") == "visible window, profile 'main'", first.metadata
    second = asyncio.run(srv.web_read(url=url, force_fetcher="stealthy", cache_ttl=600,
                                      ignore_robots=True))
    assert second.fetcher_used == "cache", (
        f"第二次没吃到缓存（{second.fetcher_used}），这条就在测别的东西")
    assert "browser" not in (second.metadata or {}), second.metadata
    assert second.metadata.get("title") == "the page", "摘一句话不该把标题也带走"


# ─── 6 · 拒绝走信封，并有自己的词 ───────────────────────────────────────────

def test_browser_busy_travels_the_envelope(monkeypatch):
    from dhole_mcp import security  # noqa: F401  (import parity with the lane)

    async def _boom(*a, **k):
        raise BrowserBusyError("browser_busy: a browser is already running on profile "
                               "'main' and this call asked for a VISIBLE one.")

    monkeypatch.setattr(MasterFetchServer, "_ensure_auto_session",
                        lambda self, **k: _boom())
    out = asyncio.run(MasterFetchServer().web_read(
        url="https://example.test/", force_fetcher="stealthy", cache_ttl=0))
    assert out.status == 0 and out.content == [] and out.content_ok is False
    assert out.error.startswith("browser_busy")
    assert "browser in use" in out.summary, out.summary
    assert "network error" not in out.summary, "没有请求在路上，没什么网络可查"
    assert "clear=" in out.next_action and "headed=true" in out.next_action, out.next_action


def test_a_busy_refusal_fetches_nothing(monkeypatch):
    """拒绝发生在起浏览器之前：一个页面都不该被打开。"""
    def _never(*a, **k):
        raise AssertionError("被 browser_busy 拦住的调用不许起浏览器")

    monkeypatch.setattr(B, "StealthyBrowser", _never)
    monkeypatch.setattr(B, "BrowserSession", _never)
    with pytest.raises(BrowserBusyError):
        asyncio.run(_warm(monkeypatch, headless=True)._ensure_auto_session(headless=False))


# ─── 7 · 空闲回收问实例 ─────────────────────────────────────────────────────

def _reap_once(monkeypatch, *, headless):
    """跑**一轮**空闲回收（会话当作早已过期），返回它关掉的 session id。

    行为判据而不是源码判据：源码守卫只看得见"`_headless` 有没有出现在函数里"，而真正的
    缺陷是把算出来的 `_visible` 从那条 `if` 里忘掉 —— 算而不问，函数里照样有这个字样。
    """
    from dhole_mcp import server as S

    closed: list = []
    srv = _warm(monkeypatch, headless=headless)
    srv._auto_stealthy_last_used = 0          # 远过 AUTO_SESSION_IDLE_TIMEOUT

    async def _close(self, sid):
        closed.append(sid)

    passes: list = []

    async def _one_sleep(_seconds):
        passes.append(1)
        if len(passes) > 1:
            raise asyncio.CancelledError      # 第二圈进 sleep 就收摊：只看一轮

    monkeypatch.setattr(MasterFetchServer, "_close_one_session", _close)
    monkeypatch.setattr(S, "asyncio_sleep", _one_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(srv._start_idle_monitor())
    return closed


def test_the_reaper_leaves_a_visible_window_alone(monkeypatch):
    assert _reap_once(monkeypatch, headless=False) == [], (
        "屏幕上开着的那台被空闲回收关掉了 —— 人在里面打字，扔掉的是半张登录表单")


def test_the_reaper_still_closes_the_hidden_auto_browser(monkeypatch):
    """反向：不许有人用"保护窗口"当理由把回收整条关掉，那笔代价是每个会话的浏览器。"""
    assert _reap_once(monkeypatch, headless=True) == ["warm"]


def test_the_idle_reaper_asks_the_browser_itself():
    import inspect

    body = inspect.getsource(MasterFetchServer._start_idle_monitor)
    assert "_headless" in body, (
        "回收器又在只读时间了 —— 屏幕上开着的窗口不能因为 5 分钟没流量就被关掉")
    assert "env_registry" not in body and "DHOLE_BROWSER_HEADED" not in body, \
        "它读回旋钮了：旋钮是'这次想要什么'，不是'现在有没有一台'"
    assert "AUTO_SESSION_IDLE_TIMEOUT" in body, "headless 的那台照旧回收"
