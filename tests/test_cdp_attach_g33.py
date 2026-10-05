"""G33 → 17.0 §3-16 unit tests: CDP attach 先问再记（confirm 语义 + 落盘围栏），
route 层的 host fence 机制，context 复用，以及无模型登录判定。

17.0 没有总开关了：attach 的许可是**每台主机一次提问** —— 第一次对未授权主机的
attach 被拒绝（什么都不执行），confirm=true 重发才落盘授权；围栏 = 已授权主机
集合，持久在 ``~/.dhole/cdp_hosts.json``（conftest 把它指到 tmp_path）。

The e2e falsification of the attach main path itself lives in
tests/e2e_cdp_attach_test.py (manual, real Chrome). Everything here is the
mock-level regression net that runs in the default suite.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from dhole_mcp.browser import (
    CDPHostNotAllowedError,
    CDPNoLoggedInContextError,
    _create_route_handler,
    _login_verdict,
    _resolve_cdp_context,
)
import dhole_mcp.server as server_mod
from dhole_mcp.server import _cdp_attach_gate, _cdp_authorized_hosts


@pytest.fixture(autouse=True)
def _fresh_cdp_store(monkeypatch, tmp_path):
    """每个用例从空授权集合开始（autouse 的 conftest 重定向已指 tmp，
    这里再清一遍进程内缓存并保证文件不存在）。"""
    store = tmp_path / "cdp_hosts.json"
    if store.exists():
        store.unlink()
    server_mod._cdp_hosts_cache, server_mod._cdp_hosts_stamp = frozenset(), None
    yield
    server_mod._cdp_hosts_cache, server_mod._cdp_hosts_stamp = frozenset(), None


class TestCdpAttachGate:
    """批次 7：授权与围栏说的是**这一次点名要读的那个站**，不是调试端口。

    2026-10-05 实测：闸写的是 `urlparse(cdp_url).hostname`（永远 `127.0.0.1`），而
    route 层拿**目标主机**比那名册 —— 两边各说各话，名册里永远不可能出现要读的那个站，
    于是 attach 每一次都撞死在围栏上（实测名册 `["127.0.0.1"]`、目标
    `i.mooc.chaoxing.com` 被 `cdp_host_not_allowed` 拦下，`content: []`）。这个类钉的
    就是"两边说同一套主机名"这一条等式。
    """

    CDP = "http://127.0.0.1:9222"
    SITE = "https://site.example.test/space/index"

    def test_first_attach_to_a_target_host_is_refused_and_authorizes_nothing(self):
        error, allowed = _cdp_attach_gate(self.CDP, targets=[self.SITE])
        assert error is not None
        assert "cdp_authorization_required" in error
        assert "confirm=true" in error
        assert "site.example.test" in error, "拒绝要点名**要读的那个站**"
        assert "127.0.0.1" not in error, "调试端口不是被授权的对象，别写进拒绝里"
        assert allowed is None
        assert _cdp_authorized_hosts() == frozenset()

    def test_confirm_authorizes_the_target_host_not_the_endpoint(self, tmp_path):
        error, allowed = _cdp_attach_gate(self.CDP, confirm=True, targets=[self.SITE])
        assert error is None
        assert allowed == frozenset({"site.example.test"})
        raw = json.loads((tmp_path / "cdp_hosts.json").read_text(encoding="utf-8"))
        assert raw == {"hosts": ["site.example.test"]}, (
            "名册里出现 127.0.0.1 就是这次事故的形状：它匹配不上任何目标请求")

    def test_second_call_to_the_same_target_needs_no_confirm(self):
        assert _cdp_attach_gate(self.CDP, confirm=True, targets=[self.SITE])[0] is None
        error, allowed = _cdp_attach_gate(self.CDP, targets=[self.SITE])
        assert error is None
        assert allowed == frozenset({"site.example.test"})

    def test_another_target_is_still_refused_after_one_is_authorized(self):
        # 授权的是**主机**，不是某一条 URL：换路径不算换主机，换 hostname 才算。
        _cdp_attach_gate(self.CDP, confirm=True, targets=[self.SITE])
        error, _ = _cdp_attach_gate(self.CDP, targets=["https://other.example.test/"])
        assert error is not None and "other.example.test" in error
        assert _cdp_attach_gate(self.CDP,
                                targets=["https://site.example.test/other?page=2"])[0] is None

    def test_a_bulk_call_authorizes_every_host_it_names(self, tmp_path):
        urls = ["https://a.example.test/1", "https://b.example.test/2",
                "https://A.EXAMPLE.TEST:443/3"]
        error, _ = _cdp_attach_gate(self.CDP, targets=urls)
        assert error is not None
        assert "a.example.test" in error and "b.example.test" in error, "缺哪台说要哪台"
        _cdp_attach_gate(self.CDP, confirm=True, targets=urls)
        raw = json.loads((tmp_path / "cdp_hosts.json").read_text(encoding="utf-8"))
        assert raw["hosts"] == ["a.example.test", "b.example.test"], "去重且小写"

    def test_the_gate_and_the_fence_read_the_same_name_list(self):
        """这次修复的全部内容就是这条等式，所以用**真的** route handler 跑一遍，
        而不是比字符串：闸授权过的主机，围栏必须放行；没授权过的第三方 origin，
        围栏仍然拦（cookie 不跟着走）。"""
        _, allowed = _cdp_attach_gate(self.CDP, confirm=True, targets=[self.SITE])
        handler = _create_route_handler(False, allowed_hosts=allowed)
        mine = _FakeRoute("https://site.example.test/page")
        asyncio.run(handler(mine))
        assert mine.continued and not mine.aborted, "授权了还拦，就是原 bug"
        third = _FakeRoute("https://tracker.example.test/pixel.gif", resource_type="image")
        asyncio.run(handler(third))
        assert third.aborted, "围栏的另一半不许被修松"

    def test_the_entry_pre_screen_admits_the_authorized_target(self):
        """`fetch()` 的入口快失败与 route 层同一份名册 —— 它从前也必然拦死。"""
        from dhole_mcp.browser import BrowserSession
        _, allowed = _cdp_attach_gate(self.CDP, confirm=True, targets=[self.SITE])
        session = BrowserSession(cdp_url=self.CDP, allowed_hosts=allowed)
        session._is_alive = True
        with pytest.raises(CDPHostNotAllowedError, match="cdp_host_not_allowed"):
            asyncio.run(session.fetch("https://never-approved.example.test/"))

    def test_authorize_merges_never_clobbers(self, tmp_path):
        _cdp_attach_gate(self.CDP, confirm=True, targets=["https://a.example.test/"])
        _cdp_attach_gate(self.CDP, confirm=True, targets=["https://b.example.test/"])
        raw = json.loads((tmp_path / "cdp_hosts.json").read_text(encoding="utf-8"))
        assert raw["hosts"] == ["a.example.test", "b.example.test"]

    def test_no_cdp_url_is_a_noop(self):
        assert _cdp_attach_gate(None, targets=[self.SITE]) == (None, None)
        assert _cdp_attach_gate(None, confirm=True, targets=[self.SITE]) == (None, None)

    def test_refusal_and_authorization_are_audited(self, tmp_path, monkeypatch):
        log = tmp_path / "usage.jsonl"
        monkeypatch.setenv("DHOLE_USAGE_LOG", str(log))
        _cdp_attach_gate(self.CDP, targets=[self.SITE])              # refused
        _cdp_attach_gate(self.CDP, confirm=True, targets=[self.SITE])  # authorized
        events = [json.loads(line) for line in
                  log.read_text(encoding="utf-8").splitlines()]
        kinds = [e["event"] for e in events]
        assert "cdp_attach_refused" in kinds and "cdp_authorize" in kinds
        # 审计记的是**要读的主机**与端点形状，不是任何 cookie/参数值
        refused = next(e for e in events if e["event"] == "cdp_attach_refused")
        assert refused["hosts"] == ["site.example.test"]
        assert refused["endpoint"] == self.CDP


class TestTheRefusalReachesTheCallerWhole:
    """批次 7 的后半：一条**设计好的**拒绝不许被剪成半句。

    从前闸口 `raise ValueError`，于是这条拒绝被交给 transport 的通用异常处理器 ——
    `call_tool` 的 `str(e)[:300]` 把它剪在 `…confirm=true to authori`（本机名
    `site.example.test` 实测 567 字符，剪在单词中间），丢掉的后半正是「授权哪一台、
    `sys_state(clear=['cdp:host=…'])` 怎么撤」，而 summary / next_action / content_ok
    一起消失：最需要被读明白的那一条响应 arrives as a bare ``{"error": ...}``。围栏那条
    （`CDPHostNotAllowedError`，同一主机实测 388 字符）是同一族的第二个受害者，200 字符
    的帽子把它剪在 "…igation is fenced to t" 上。
    """

    CDP = "http://127.0.0.1:9222"
    SITE = "https://site.example.test/space/index"

    def _refusal(self, monkeypatch):
        from dhole_mcp.server import MasterFetchServer

        def _never(*a, **k):
            raise AssertionError("被闸拦住的调用不许进任何一层抓取")

        monkeypatch.setattr(MasterFetchServer, "bulk_stealthy_fetch", _never)
        monkeypatch.setattr(MasterFetchServer, "get", _never)
        return asyncio.run(MasterFetchServer().web_read(url=self.SITE, cdp_url=self.CDP))

    def test_the_first_attach_refusal_travels_the_envelope(self, monkeypatch):
        out = self._refusal(monkeypatch)
        assert out.error.startswith("cdp_authorization_required")
        assert "sys_state(clear=" in out.error, "撤销路径必须活着走到调用方手里"
        assert out.content == [] and out.content_ok is False
        assert _cdp_authorized_hosts() == frozenset()

    def test_the_refusal_says_what_to_do_and_what_cannot_work(self, monkeypatch):
        out = self._refusal(monkeypatch)
        assert "confirm=true" in out.next_action, out.next_action
        # 批次 3 那条规矩的镜像：不许建议一个点了没反应的旋钮
        assert "no parameter widens" in out.next_action.lower(), out.next_action
        assert "awaiting approval" in out.summary, out.summary
        assert "network error" not in out.summary, "一道等批准的闸不是网络故障"

    def test_the_fence_message_survives_the_tier_clip(self):
        from dhole_mcp.server import _tier_error_text

        designed = CDPHostNotAllowedError("cdp_host_not_allowed: " + "x" * 400)
        assert len(_tier_error_text(designed)) > 300, (
            "dhole 自己写的拒绝被帽子吃掉，结尾那句 remedy 就没了")
        noise = RuntimeError("playwright call log " + "y" * 400)
        assert len(_tier_error_text(noise)) == 200, "噪声仍然要剪，否则信封被 call log 灌满"

    def test_the_no_context_refusal_also_survives_the_clip(self):
        """批次 8 那一支的 remedy 同样写在**结尾**，帽子不许吃它。"""
        from dhole_mcp.server import _tier_error_text

        real = CDPNoLoggedInContextError(
            "cdp_no_logged_in_context: connected to the CDP endpoint, but it exposed "
            "0 browser context(s) (0 target(s)), so there was no logged-in profile to "
            "ride and NOTHING was fetched. Re-call the same url once; if it repeats, "
            "check --user-data-dir and --remote-debugging-port.")
        assert _tier_error_text(real) == str(real)

    def test_the_no_context_refusal_gets_its_own_words(self):
        """这一态从前没有词：它长得像一次成功的抓取，只是少了一个字段。"""
        from dhole_mcp.server import ResponseModel, _with_agent_hints

        out = _with_agent_hints(ResponseModel(
            url=self.SITE, status=0, content=[], fetcher_used="none",
            extracted_type="markdown",
            error="cdp_no_logged_in_context: connected to the CDP endpoint, but it "
                  "exposed 0 browser context(s), so NOTHING was fetched."))
        assert "no logged-in context" in out.summary, out.summary
        assert "network error" not in out.summary, "端点答过话了，没什么网络可查"
        assert "NOTHING was fetched" in out.next_action and "anonymous" in out.next_action, \
            out.next_action
        assert "the SAME url once" in out.next_action, "先给那条不要钱的出路"


class TestAttachIsPerCallNotPerBatch:
    """``urls=`` + ``cdp_url=`` 必须在闸口**之前**拒绝。

    批量里每个 URL 各自再进一次 web_read，cdp_url 传不下去。若让闸口先过，这次
    调用会做两件互相矛盾的事：把一个主机的授权写进磁盘（用户同意的是"attach"，
    结果一页都没 attach），并且每一页静默地走未 attach 的路。两条都是静默降级，
    所以这里既拒绝调用，也要求名册仍然是空的。
    """

    URLS = ["https://a.example.test/1", "https://b.example.test/2"]
    CDP = "http://127.0.0.1:9222"

    def test_bulk_with_cdp_url_is_refused_and_authorizes_nothing(self, tmp_path, monkeypatch):
        from dhole_mcp.server import MasterFetchServer

        def _never(*a, **k):
            raise AssertionError("被拒绝的批量调用不许进入抓取层")
        monkeypatch.setattr(MasterFetchServer, "_smart_fetch_bulk", _never)

        bulk = asyncio.run(MasterFetchServer().web_read(
            url="", urls=self.URLS, cdp_url=self.CDP, confirm=True))

        assert bulk.total == 2 and bulk.successful == 0
        for row in bulk.results:
            assert "cdp_url= cannot be combined with urls=" in row.error
        # 拒绝发生在闸口之前：一个字节都不该落进授权名册
        assert _cdp_authorized_hosts() == frozenset()
        assert not (tmp_path / "cdp_hosts.json").exists()

    def test_the_same_host_is_still_authorizable_one_call_at_a_time(self, tmp_path):
        """反向半边：拒绝批量不等于拒绝 attach —— 单条 URL 带 confirm 照常落盘。

        没有这一条，上一条守卫靠"永远拒绝"也能通过。
        """
        error, allowed = _cdp_attach_gate(self.CDP, confirm=True,
                                          targets=["https://a.example.test/1"])
        assert error is None
        assert allowed == frozenset({"a.example.test"})
        assert _cdp_authorized_hosts() == frozenset({"a.example.test"})


class _Stop(Exception):
    """抓到"参数已经算好了"这一刻：桩不需要真起浏览器就能看见传进去的 kwargs。"""


class TestCdpLaneReachesTheBrowser:
    """attach 主路径本身在默认套件里必须有人接。

    实测过的漏网形状：CDP 快速通道读了一个只在 `if actions:` 里绑定的局部变量，于是
    **每一次没有 actions 的 attach** 都在组装参数时炸成
    `cannot access local variable 'actions_need_resources'` —— 而这条正是这个功能的主要
    用法（attach 看一眼已登录的页面，什么都不做）。它只在 `-m e2e` 里看得见，默认运行
    deselect 了那条，所以闸必须落在 unit 层。
    """

    CDP = "http://127.0.0.1:9222"

    def _lane_kwargs(self, monkeypatch, **call):
        from dhole_mcp.server import MasterFetchServer

        seen: dict = {}

        async def fake_force_fetch(self, *args, **kwargs):
            seen.update(kwargs)
            raise _Stop()

        monkeypatch.setattr(MasterFetchServer, "_force_fetch", fake_force_fetch)
        with pytest.raises(_Stop):
            asyncio.run(MasterFetchServer().web_read(
                url="https://example.com/", cdp_url=self.CDP, confirm=True, **call))
        return seen

    def test_an_attach_without_actions_reaches_the_fetch_with_the_default_speed_plan(
            self, monkeypatch):
        seen = self._lane_kwargs(monkeypatch)
        assert seen["cdp_url"] == self.CDP
        assert seen["page_action"] is None
        # 没有动作要渲染 → 沿用 `_force_fetch` 自己的默认（丢资源），不是"什么都不知道"
        assert seen["disable_resources"] is True

    def test_an_attach_that_asks_for_a_screenshot_keeps_the_resources(
            self, monkeypatch):
        """反向半边：这条通道不是把 actions 丢了。同一条 lane 要为截图动作把资源拦截
        关掉，否则 attach 视角截出来的是破图占位符。
        """
        seen = self._lane_kwargs(monkeypatch, actions=[{"screenshot": {"selector": "#x"}}])
        assert seen["page_action"] is not None
        assert seen["disable_resources"] is False


# ─── attach 语义: contexts[0] 复用 ───────────────────────────────────────────

class _FakeBrowser:
    def __init__(self, contexts, targets=()):
        self.contexts = list(contexts)
        self.new_context_calls = 0
        self._targets = list(targets)

    async def new_context(self, **kwargs):
        self.new_context_calls += 1
        self.last_context_options = kwargs
        return SimpleNamespace(kind="fresh")

    def targets(self):
        return list(self._targets)


class TestResolveCdpContext:
    """批次 8 的后半：端点报不出 context 时**拒答**，不是退回一个匿名 context。

    判据来自 2026-10-05 的实测：同一台 Chrome、同一个 URL，第一通的信封里没有
    `cdp_attached`，后面每一通都有。那一支走的就是 `new_context()` —— 一个隔离
    profile，页面照样 200、内容照样是真的，只是没有登录态，而"没有"在这条 wire
    上只表现为一个缺席的字段（默认 False 被折叠）。调用方点名 attach 只有一个理由：
    要那个登录态。
    """

    def test_reuses_default_context(self):
        default = SimpleNamespace(kind="default")
        browser = _FakeBrowser(contexts=[default])
        assert asyncio.run(_resolve_cdp_context(browser)) is default
        assert browser.new_context_calls == 0

    def test_no_context_refuses_instead_of_answering_anonymously(self):
        browser = _FakeBrowser(contexts=[], targets=[1, 2])
        with pytest.raises(CDPNoLoggedInContextError) as got:
            asyncio.run(_resolve_cdp_context(browser))
        assert browser.new_context_calls == 0, "拒答就是连一个隔离 context 都不建"
        text = str(got.value)
        assert text.startswith("cdp_no_logged_in_context")
        assert "0 browser context(s) (2 target(s))" in text, "它看见几个就报几个"
        assert "NOTHING was fetched" in text
        assert "Re-call the same url once" in text and "--user-data-dir" in text, \
            "两条出路都得在文案里：再发一次，以及核对启动旗"

    def test_the_anonymous_fallback_is_gone_from_the_source(self):
        """反向守卫：这一支不许被改回 `new_context()`（改回去就是新的谎）。"""
        import inspect

        from dhole_mcp import browser as B

        assert "new_context" not in inspect.getsource(B._resolve_cdp_context).split('"""')[2], \
            "端点没有 context 时又去建匿名 context 了 —— 那一态必须是拒答"

    def test_a_refused_attach_is_not_recorded_as_a_launch_failure(self):
        """端点答了话、只是没有登录态可借 —— 这不该写进"装上了但起不来"那块盘。"""
        import inspect

        from dhole_mcp import browser as B

        body = inspect.getsource(B.BrowserSession.start)
        cut = body.index("except CDPNoLoggedInContextError")
        after = body[cut:body.index("except Exception as e:")]
        assert "record_launch_failure" not in after
        assert "_cleanup_playwright" in after, "拒答也要把连接断开，不许留着一个 playwright"


# ─── host fence 在 route 层机制执行 ──────────────────────────────────────────

class _FakeRoute:
    def __init__(self, url, resource_type="document"):
        self.request = SimpleNamespace(
            url=url, resource_type=resource_type,
            is_navigation_request=lambda: True,
            frame=None, headers={})
        self.aborted = False
        self.continued = False
        self.continue_headers = None

    async def abort(self):
        self.aborted = True

    async def continue_(self, headers=None):
        self.continued = True
        self.continue_headers = headers


class TestRouteHostScope:
    def _handler(self, allowed):
        return _create_route_handler(False, allowed_hosts=allowed)

    def test_blocks_foreign_host(self):
        handler = self._handler(frozenset({"example.com"}))
        route = _FakeRoute("https://evil.example.net/steal")
        asyncio.run(handler(route))
        assert route.aborted is True
        assert route.continued is False

    def test_allows_listed_host(self):
        handler = self._handler(frozenset({"example.com"}))
        route = _FakeRoute("https://example.com/page")
        asyncio.run(handler(route))
        assert route.aborted is False
        assert route.continued is True

    def test_subresource_of_foreign_host_also_blocked(self):
        handler = self._handler(frozenset({"example.com"}))
        route = _FakeRoute("https://tracker.io/pixel.gif", resource_type="image")
        asyncio.run(handler(route))
        assert route.aborted is True

    def test_non_network_schemes_pass_through(self):
        # data:/blob: 不发起网络连接，拒绝它们只会弄坏页面（与 SSRF 守卫同规则）。
        handler = self._handler(frozenset({"example.com"}))
        route = _FakeRoute("data:text/html,hi", resource_type="other")
        asyncio.run(handler(route))
        assert route.aborted is False
        assert route.continued is True

    def test_entry_check_raises_for_unlisted_host(self):
        """fetch() 的入口快失败：白名单外的主机根本不进浏览器。"""
        from dhole_mcp.browser import BrowserSession
        session = BrowserSession(cdp_url="http://127.0.0.1:9222",
                                 allowed_hosts=frozenset({"example.com"}))
        session._is_alive = True
        with pytest.raises(CDPHostNotAllowedError, match="cdp_host_not_allowed"):
            asyncio.run(session.fetch("https://other.org/page"))


# ─── G35: 无模型登录判定 ─────────────────────────────────────────────────────

class TestLoginVerdict:
    def test_logged_in_via_logout_control(self):
        verdict = _login_verdict(
            {"logout_control": True, "password_field": False,
             "login_form": False, "login_control": False},
            ["dh_sid", "consent"])
        assert verdict["logged_in"] is True
        assert "logout_control_present" in verdict["signals"]
        assert "no_login_form" in verdict["signals"]
        assert verdict["cookie_names"] == ["consent", "dh_sid"]  # sorted, names only
        assert verdict["method"] == "dom+cookie-names"

    def test_logged_out_via_login_form(self):
        verdict = _login_verdict(
            {"logout_control": False, "password_field": True,
             "login_form": True, "login_control": True},
            [])
        assert verdict["logged_in"] is False

    def test_uncertain_when_no_dom_evidence(self):
        # 纯 JSON 页：没有 DOM 信号就不下结论，绝不硬猜。
        verdict = _login_verdict({}, ["dh_sid"])
        assert verdict["logged_in"] is None
        assert verdict["signals"] == ["no_login_form"]

    def test_never_contains_cookie_values(self):
        verdict = _login_verdict({"logout_control": True}, ["sid"])
        blob = repr(verdict)
        assert "value" not in verdict  # 结构里根本没有值的字段
        assert "sid" in blob and "secret" not in blob


# ─── 17.0 写死的随附项 ──────────────────────────────────────────────────────

class TestFixedShapes:
    def test_cookie_ttl_is_fixed_24h(self):
        from dhole_mcp import sessions
        assert sessions._cookie_ttl() == sessions.COOKIE_TTL_S

    def test_env_profile_reader_is_gone(self):
        """BROWSER_PROFILE 旋钮已注销：读 env 的入口不存在，命名 profile 的
        能力（cred_defaults / profile_name 参数）走 B5 的显式参数回来。"""
        assert not hasattr(server_mod, "_env_profile_name")
        import dhole_mcp.browser as browser_mod
        assert not hasattr(browser_mod, "_env_profile_name")
