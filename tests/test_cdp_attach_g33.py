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
    def test_first_attach_refused_nothing_authorized(self):
        error, allowed = _cdp_attach_gate("http://127.0.0.1:9222")
        assert error is not None
        assert "cdp_authorization_required" in error
        assert "confirm=true" in error
        assert allowed is None
        assert _cdp_authorized_hosts() == frozenset()

    def test_confirm_authorizes_and_persists(self, tmp_path):
        error, allowed = _cdp_attach_gate("http://127.0.0.1:9222", confirm=True)
        assert error is None
        assert allowed == frozenset({"127.0.0.1"})
        # 落盘：文件存在、内容是主机集合、权限收紧
        raw = json.loads((tmp_path / "cdp_hosts.json").read_text(encoding="utf-8"))
        assert raw == {"hosts": ["127.0.0.1"]}

    def test_second_call_needs_no_confirm(self):
        first, _ = _cdp_attach_gate("http://127.0.0.1:9222", confirm=True)
        assert first is None
        error, allowed = _cdp_attach_gate("http://127.0.0.1:9222")
        assert error is None
        assert allowed == frozenset({"127.0.0.1"})

    def test_other_host_still_refused_after_authorize(self):
        # 围栏按主机记账：同主机换端口仍是已授权（9222 → 9333 都是 127.0.0.1），
        # 真正的"另一台主机"要换 hostname。
        _cdp_attach_gate("http://127.0.0.1:9222", confirm=True)
        error, _ = _cdp_attach_gate("http://127.0.0.1:9333")
        assert error is None
        error, _ = _cdp_attach_gate("http://other.example.test:9222")
        assert error is not None
        assert "cdp_authorization_required" in error

    def test_endpoint_host_is_extracted_and_lowered(self, tmp_path):
        _cdp_attach_gate("http://LOCALhost.DAILY-BUGLE.test:9222/json", confirm=True)
        assert _cdp_authorized_hosts() == frozenset({"localhost.daily-bugle.test"})

    def test_authorize_merges_never_clobbers(self, tmp_path):
        _cdp_attach_gate("http://a.example.test:9222", confirm=True)
        _cdp_attach_gate("http://b.example.test:9222", confirm=True)
        raw = json.loads((tmp_path / "cdp_hosts.json").read_text(encoding="utf-8"))
        assert raw["hosts"] == ["a.example.test", "b.example.test"]

    def test_no_url_is_a_noop(self):
        assert _cdp_attach_gate(None) == (None, None)
        assert _cdp_attach_gate(None, confirm=True) == (None, None)

    def test_refusal_and_authorization_are_audited(self, tmp_path, monkeypatch):
        log = tmp_path / "usage.jsonl"
        monkeypatch.setenv("DHOLE_USAGE_LOG", str(log))
        _cdp_attach_gate("http://127.0.0.1:9222")                      # refused
        _cdp_attach_gate("http://127.0.0.1:9222", confirm=True)        # authorized
        events = [json.loads(line) for line in
                  log.read_text(encoding="utf-8").splitlines()]
        kinds = [e["event"] for e in events]
        assert "cdp_attach_refused" in kinds and "cdp_authorize" in kinds
        # 审计记的是主机与端点形状，不是任何 cookie/参数值
        refused = next(e for e in events if e["event"] == "cdp_attach_refused")
        assert refused["host"] == "127.0.0.1"


class TestAttachIsPerCallNotPerBatch:
    """``urls=`` + ``cdp_url=`` 必须在闸口**之前**拒绝。

    批量里每个 URL 各自再进一次 smart_fetch，cdp_url 传不下去。若让闸口先过，这次
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

        bulk = asyncio.run(MasterFetchServer().smart_fetch(
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
        error, allowed = _cdp_attach_gate(self.CDP, confirm=True)
        assert error is None
        assert allowed == frozenset({"127.0.0.1"})
        assert _cdp_authorized_hosts() == frozenset({"127.0.0.1"})


# ─── attach 语义: contexts[0] 复用 ───────────────────────────────────────────

class _FakeBrowser:
    def __init__(self, contexts):
        self.contexts = list(contexts)
        self.new_context_calls = 0

    async def new_context(self, **kwargs):
        self.new_context_calls += 1
        self.last_context_options = kwargs
        return SimpleNamespace(kind="fresh")


class TestResolveCdpContext:
    def test_reuses_default_context(self):
        default = SimpleNamespace(kind="default")
        browser = _FakeBrowser(contexts=[default])
        context, reused = asyncio.run(_resolve_cdp_context(browser, {"color_scheme": "dark"}))
        assert context is default
        assert reused is True
        assert browser.new_context_calls == 0

    def test_falls_back_to_new_context_when_none_exist(self):
        browser = _FakeBrowser(contexts=[])
        context, reused = asyncio.run(_resolve_cdp_context(browser, {"color_scheme": "dark"}))
        assert reused is False
        assert browser.new_context_calls == 1
        assert browser.last_context_options == {"color_scheme": "dark"}


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
