"""G33-G35 unit tests: CDP-attach gate, host scope, context reuse, persistent
profiles, and the model-free login verdict.

The e2e falsification of the attach main path itself lives in
tests/e2e_cdp_attach_test.py (manual, real Chrome). Everything here is the
mock-level regression net that runs in the default suite.
"""
import asyncio
from types import SimpleNamespace

import pytest

from dhole_mcp.browser import (
    CDPHostNotAllowedError,
    _create_route_handler,
    _env_profile_name,
    _login_verdict,
    _resolve_cdp_context,
)
from dhole_mcp.server import _cdp_attach_gate


# ─── 安全闸 1: cdp attach 默认关 ─────────────────────────────────────────────

class TestCdpAttachGate:
    def test_refused_by_default(self, monkeypatch):
        monkeypatch.delenv("DHOLE_CDP_ATTACH", raising=False)
        monkeypatch.delenv("DHOLE_CDP_HOSTS", raising=False)
        error, allowed = _cdp_attach_gate("http://127.0.0.1:9222")
        assert error is not None
        assert "DHOLE_CDP_ATTACH" in error
        assert allowed is None

    def test_enabled_without_scope_is_unrestricted(self, monkeypatch):
        monkeypatch.setenv("DHOLE_CDP_ATTACH", "1")
        monkeypatch.delenv("DHOLE_CDP_HOSTS", raising=False)
        error, allowed = _cdp_attach_gate("http://127.0.0.1:9222")
        assert error is None
        assert allowed is None  # attach 放行，但白名单未设 = 不限制

    def test_host_scope_parsing(self, monkeypatch):
        monkeypatch.setenv("DHOLE_CDP_ATTACH", "true")
        monkeypatch.setenv("DHOLE_CDP_HOSTS", "Example.com, b.org;c.net")
        error, allowed = _cdp_attach_gate("http://x")
        assert error is None
        assert allowed == frozenset({"example.com", "b.org", "c.net"})

    def test_no_url_is_a_noop_even_when_enabled(self, monkeypatch):
        monkeypatch.setenv("DHOLE_CDP_ATTACH", "1")
        assert _cdp_attach_gate(None) == (None, None)


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


# ─── 安全闸 2: host scope 在 route 层机制执行 ────────────────────────────────

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


# ─── 持久 profile 命名校验 ───────────────────────────────────────────────────

class TestProfileName:
    def test_unset_is_none(self, monkeypatch):
        monkeypatch.delenv("DHOLE_BROWSER_PROFILE", raising=False)
        assert _env_profile_name() is None

    def test_valid_name_passes_through(self, monkeypatch):
        monkeypatch.setenv("DHOLE_BROWSER_PROFILE", "github-main.01")
        assert _env_profile_name() == "github-main.01"

    def test_path_traversal_rejected(self, monkeypatch):
        monkeypatch.setenv("DHOLE_BROWSER_PROFILE", "../evil")
        with pytest.raises(ValueError, match="not a valid profile name"):
            _env_profile_name()

    def test_absolute_path_rejected(self, monkeypatch):
        monkeypatch.setenv("DHOLE_BROWSER_PROFILE", r"C:\temp\p")
        with pytest.raises(ValueError):
            _env_profile_name()


# ─── COOKIE_TTL 可配 ─────────────────────────────────────────────────────────

class TestCookieTtl:
    def _store_and_read_expiry(self, now):
        from dhole_mcp import sessions
        sessions.store("ttl-test", "https://example.com/login", {"sid": "v"}, now=now)
        with sessions._lock:
            conn = sessions._connect()
            row = conn.execute(
                "SELECT expires_at FROM cookies WHERE session_id='ttl-test'").fetchone()
        return row[0]

    def test_default_is_24h(self, monkeypatch):
        monkeypatch.delenv("DHOLE_COOKIE_TTL_S", raising=False)
        from dhole_mcp import sessions
        assert sessions._cookie_ttl() == sessions.COOKIE_TTL_S

    def test_env_override_extends_ttl(self, monkeypatch):
        monkeypatch.setenv("DHOLE_COOKIE_TTL_S", str(7 * 24 * 3600))
        assert self._store_and_read_expiry(1000.0) == pytest.approx(1000.0 + 7 * 24 * 3600)

    def test_env_override_clamped_to_minimum(self, monkeypatch):
        monkeypatch.setenv("DHOLE_COOKIE_TTL_S", "5")  # 5s → clamp 至 60s
        assert self._store_and_read_expiry(1000.0) == pytest.approx(1060.0)

    def test_invalid_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("DHOLE_COOKIE_TTL_S", "tomorrow")
        from dhole_mcp import sessions
        assert self._store_and_read_expiry(1000.0) == pytest.approx(1000.0 + sessions.COOKIE_TTL_S)
