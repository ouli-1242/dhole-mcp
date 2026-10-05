"""MVP-3：cookie 双向桥 + session 专用浏览器会话（登录流兜底路径的载体）。

设计要点（计划第七章）：
* 桥只服务兜底路径（session_id 显式命名的调用）；主路径 CDP attach 用不到它，
  而且 attach 的 context 是用户真实 profile——**绝不能**成为播种/回写的对象。
* 播种（jar → context）与回写（context → jar）只允许发生在**会话专用**的浏览器
  上：共享 auto 会话的 context 是所有匿名调用共用的，往里灌某个 session 的
  cookie 等于跨会话泄露。所以桥的开关读的是调用方命名的 _SESSION_ID，而不是
  内部传的会话 id——auto 会话借道 bulk_stealthy_fetch 时绝不能触发桥。
* cookie 值只在这一条进程内路径上移动（jar ↔ context），不进任何响应、日志
  或缓存；jar 侧的纪律（只报名字）由 sessions.py 保持不变。
"""

from __future__ import annotations

import pytest

from dhole_mcp import server as server_mod
from dhole_mcp import sessions
from dhole_mcp.browser import StealthyBrowser
from dhole_mcp.server import (
    MasterFetchServer,
    ResponseModel,
    _SESSION_ID,
)


@pytest.fixture
def jar(tmp_path, monkeypatch):
    monkeypatch.setenv("DHOLE_HOME", str(tmp_path))
    sessions.reset_for_tests()
    yield
    sessions.reset_for_tests()


class _FakeContext:
    def __init__(self):
        self.added: list = []
        self.jar: list = []

    async def add_cookies(self, payload):
        self.added.extend(payload)

    async def cookies(self, url):
        return self.jar


class _FakeBrowserResp:
    def __init__(self, url):
        self.url = url


class _FakeSess:
    """session.fetch 的最小替身 + 桥钩子的记录仪。"""

    _is_alive = True
    cdp_attached = False
    # 会话对外说得出"我是被怎么起来的"（批次 11），车道那句读的是这个属性而不是
    # getattr 的默认 —— 假件缺它就该红，因为真会话不会缺。
    launch_note = ""

    def __init__(self, harvest=None):
        self.seeded: list = []
        self.harvested: list = []
        self._harvest = harvest or {}

    async def seed_cookies(self, url, cookies):
        self.seeded.append((url, dict(cookies)))

    async def harvest_cookies(self, url):
        self.harvested.append(url)
        return dict(self._harvest)

    async def fetch(self, url, **kw):
        return _FakeBrowserResp(url)


# ─── browser.py 的两个桥钩子 ────────────────────────────────────────────────


class TestBrowserBridgeHooks:

    @staticmethod
    def _browser(context):
        # 13d 之前这里写 StealthyBrowser(wait=0)：那颗参数只是构造时的占位，删了
        # 就等于不传（0 从来不是这条桥的自变量）。时机本身改成浏览器层认出壳页才
        # 付的那 5 秒，证人在 tests/test_render_settle_b5.py。
        b = StealthyBrowser()
        b._is_alive = True
        b._context = context
        return b

    @pytest.mark.asyncio
    async def test_seed_pushes_host_cookies_into_the_context(self):
        ctx = _FakeContext()
        b = self._browser(ctx)
        await b.seed_cookies("https://shop.example.com/x", {"sid": "abc"})
        assert ctx.added == [{"name": "sid", "value": "abc",
                              "url": "https://shop.example.com/x"}]

    @pytest.mark.asyncio
    async def test_seed_with_no_cookies_is_a_noop(self):
        ctx = _FakeContext()
        b = self._browser(ctx)
        await b.seed_cookies("https://shop.example.com/x", {})
        assert ctx.added == []

    @pytest.mark.asyncio
    async def test_harvest_reads_name_value_pairs(self):
        ctx = _FakeContext()
        ctx.jar = [{"name": "sid", "value": "abc"},
                   {"name": "empty", "value": None},
                   {"no_name": True}]
        b = self._browser(ctx)
        assert await b.harvest_cookies("https://shop.example.com/x") == {"sid": "abc"}

    @pytest.mark.asyncio
    async def test_hooks_on_a_dead_browser_are_noops(self):
        ctx = _FakeContext()
        b = self._browser(ctx)
        b._is_alive = False
        await b.seed_cookies("https://x.example.com/", {"a": "1"})
        assert await b.harvest_cookies("https://x.example.com/") == {}
        assert ctx.added == []


# ─── server 侧的播种/回写 ───────────────────────────────────────────────────


class TestBridgeDirection:

    @pytest.mark.asyncio
    async def test_seed_uses_the_jar_for_that_host(self, jar):
        sessions.store("bob", "https://shop.example.com/x", {"sid": "abc"})
        fake = _FakeSess()
        await MasterFetchServer()._bridge_seed(
            fake, ["https://shop.example.com/a"], "bob")
        assert fake.seeded == [("https://shop.example.com/a", {"sid": "abc"})]

    @pytest.mark.asyncio
    async def test_seed_skips_hosts_the_jar_does_not_know(self, jar):
        fake = _FakeSess()
        await MasterFetchServer()._bridge_seed(
            fake, ["https://unknown.example.com/a"], "bob")
        assert fake.seeded == []

    @pytest.mark.asyncio
    async def test_harvest_lands_in_the_jar_namespaced_by_session(self, jar):
        fake = _FakeSess(harvest={"sid": "abc"})
        await MasterFetchServer()._bridge_harvest(
            fake, ["https://shop.example.com/a"], "bob")
        # 名字出得来，值留在 jar 里（sessions 的纪律不受桥影响）
        assert sessions.names_for("bob", "https://shop.example.com/x") == ["sid"]
        assert sessions.header_for("bob", "https://shop.example.com/x") == {"sid": "abc"}
        assert sessions.header_for("someone-else", "https://shop.example.com/x") == {}

    @pytest.mark.asyncio
    async def test_hosts_are_deduped_per_call(self, jar):
        fake = _FakeSess(harvest={"sid": "abc"})
        await MasterFetchServer()._bridge_harvest(
            fake, ["https://shop.example.com/a", "https://shop.example.com/b",
                   "https://other.example.com/c"], "bob")
        assert len(fake.harvested) == 2, "同 host 的多页只回写一次"


# ─── 专用会话与 bulk 路径的桥开关 ───────────────────────────────────────────


class TestTheNamedSessionAndTheGate:

    @pytest.mark.asyncio
    async def test_a_live_named_session_is_reused_not_rebuilt(self):
        s = MasterFetchServer()
        entry = server_mod._SessionEntry(session=_FakeSess(),
                                         session_type="stealthy")
        s._sessions["bob"] = entry
        called = []

        async def no_open(*a, **kw):
            called.append(1)
            raise AssertionError("a live session browser was rebuilt")
        monkey_open = no_open
        saved = s.open_session
        s.open_session = monkey_open  # type: ignore[method-assign]
        try:
            assert await s._ensure_session_browser("bob") == "bob"
        finally:
            s.open_session = saved  # type: ignore[method-assign]
        assert called == []

    @pytest.mark.asyncio
    async def test_a_dead_named_session_is_rebuilt_in_place(self, monkeypatch):
        s = MasterFetchServer()
        dead = server_mod._SessionEntry(session=_FakeSess(),
                                        session_type="stealthy")
        dead.session._is_alive = False
        s._sessions["bob"] = dead
        opened = {}

        async def fake_open(self, session_id=None, **kw):
            opened["sid"] = session_id
            new = server_mod._SessionEntry(session=_FakeSess(),
                                           session_type="stealthy")
            self._sessions[session_id] = new
            return server_mod.SessionCreatedModel(
                session_id=session_id, session_type="stealthy",
                created_at="t", is_alive=True, message="ok")

        monkeypatch.setattr(MasterFetchServer, "open_session", fake_open)
        assert await s._ensure_session_browser("bob") == "bob"
        assert opened == {"sid": "bob"}
        assert "bob" in s._sessions

    @pytest.mark.asyncio
    async def test_the_bridge_runs_only_for_a_named_session(self, jar, monkeypatch):
        """auto 会话借道同一条 bulk 路径时绝不能触发桥。"""
        seeded, harvested = [], []

        class _GateSess(_FakeSess):
            async def seed_cookies(self, url, cookies):
                seeded.append(url)

            async def harvest_cookies(self, url):
                harvested.append(url)
                return {}

        entry = server_mod._SessionEntry(session=_GateSess(),
                                         session_type="stealthy")

        async def fake_get(self, sid, expected_type=None):
            return entry

        monkeypatch.setattr(MasterFetchServer, "_get_session", fake_get)
        monkeypatch.setattr("dhole_mcp.server._browser_deps_available", lambda: True)
        monkeypatch.setattr(
            server_mod, "_translate_response",
            lambda page, *a, **kw: ResponseModel(url=page.url, status=200,
                                                 content=["x"],
                                                 fetcher_used="stealthy"))
        s = MasterFetchServer()

        sessions.store("bob", "https://shop.example.com/x", {"sid": "abc"})
        token = _SESSION_ID.set("bob")
        try:
            await s.bulk_stealthy_fetch(urls=["https://shop.example.com/a"], session_id="bob")
            assert seeded == ["https://shop.example.com/a"], "命名会话要播种"
            assert harvested == ["https://shop.example.com/a"], "命名会话要回写"
        finally:
            _SESSION_ID.reset(token)

        seeded.clear()
        harvested.clear()
        await s.bulk_stealthy_fetch(urls=["https://shop.example.com/a"], session_id="bob")
        assert seeded == [] and harvested == [], "没命名 session 就不桥"

    @pytest.mark.asyncio
    async def test_an_attached_session_never_bridges(self, jar, monkeypatch):
        """attach 的 context 是用户真实 profile：播种/回写都必须是空操作。"""
        class _AttachSess(_FakeSess):
            cdp_attached = True

        seeded, harvested = [], []

        class _RecordingAttach(_AttachSess):
            async def seed_cookies(self, url, cookies):
                seeded.append(url)

            async def harvest_cookies(self, url):
                harvested.append(url)
                return {}

        entry = server_mod._SessionEntry(session=_RecordingAttach(),
                                         session_type="stealthy")

        async def fake_get(self, sid, expected_type=None):
            return entry

        monkeypatch.setattr(MasterFetchServer, "_get_session", fake_get)
        monkeypatch.setattr("dhole_mcp.server._browser_deps_available", lambda: True)
        monkeypatch.setattr(
            server_mod, "_translate_response",
            lambda page, *a, **kw: ResponseModel(url=page.url, status=200,
                                                 content=["x"],
                                                 fetcher_used="stealthy"))
        s = MasterFetchServer()
        token = _SESSION_ID.set("bob")
        try:
            await s.bulk_stealthy_fetch(urls=["https://shop.example.com/a"], session_id="bob")
        finally:
            _SESSION_ID.reset(token)
        assert seeded == [] and harvested == []
