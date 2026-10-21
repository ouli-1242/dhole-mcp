"""test_browser_4.py —— 由 5 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import pytest
from dhole_mcp import server as server_mod
from dhole_mcp import sessions
from dhole_mcp.browser import StealthyBrowser
from dhole_mcp.server import MasterFetchServer, ResponseModel, _SESSION_ID
import ast
import asyncio
import inspect
import pathlib
from dhole_mcp import browser as browser_mod
from dhole_mcp.browser import _DocumentFence, _fence_blocked_error, _landed_host_outside_fence
import json
import socket
import sys
import threading
from pathlib import Path
from dhole_mcp import identity
from dhole_mcp.server import PIN_NOTES
import time
from dhole_mcp.browser import PIN_TTL_S, BrowserSession
from dhole_mcp.server import _SessionEntry
from dhole_mcp.wire import _WIRE_OMIT, _wire_dump
from dhole_mcp.browser import DEFAULT_ARGS, HARMFUL_ARGS, STEALTH_ARGS, _FINGERPRINT_PROFILES, _generate_fingerprint_profile, _build_stealth_init_script


# ============================================================================
# 来源：test_cookie_bridge_mvp3.py
# ============================================================================
# MVP-3：cookie 双向桥 + session 专用浏览器会话（登录流兜底路径的载体）。

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


# ============================================================================
# 来源：test_a_fence_that_cannot_be_installed_leaves_nothing_half_on.py
# ============================================================================
# 围栏装不上的那一发：退路必须是接线，不是一句注释

BROWSER_SRC = pathlib.Path(inspect.getsourcefile(browser_mod)).read_text(encoding="utf-8")


def _fetch_body() -> ast.AsyncFunctionDef:
    tree = ast.parse(BROWSER_SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "fetch":
            return node
    raise AssertionError("browser.py 里找不到 fetch —— 判据的坐标断了")


def _install_try(func: ast.AST):
    """那个 `await candidate.install()` 所在的 try —— 按被包住的语句认，不按行号。"""
    for node in ast.walk(func):
        if not isinstance(node, ast.Try):
            continue
        for stmt in node.body:
            if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Await)
                    and isinstance(stmt.value.value, ast.Call)
                    and getattr(stmt.value.value.func, "attr", None) == "install"):
                yield node, stmt
    return


class TestTheFallbackIsWired:
    """退路的三颗钉：except 臂摘干净、fence 先有 None、赛跑那一支看着 fence。"""

    def test_the_except_arm_both_clears_the_fence_and_removes_the_candidate(self):
        tries = list(_install_try(_fetch_body()))
        assert len(tries) == 1, f"装不上这条线只该有一处，量到 {len(tries)} 处"
        node, _install_stmt = tries[0]
        assert node.handlers, "install 的 try 没有 except 臂 —— 装不上就是整条调用炸掉"
        arm = node.handlers[0]
        dumped = ast.dump(arm)
        # ① fence 交回 None
        clears = [a for a in arm.body if isinstance(a, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == "fence" for t in a.targets)
                  and isinstance(a.value, ast.Constant) and a.value.value is None]
        assert clears, "except 臂没把 fence 交回 None —— 后面每一支都还在当它有闸"
        # ② candidate.remove() 真被 await（只 logger.debug 不算摘：那时钩子还挂着）
        removes = [n for n in ast.walk(arm)
                   if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
                   and getattr(n.value.func, "attr", None) == "remove"]
        assert removes, ("except 臂没有 await …remove()：install 是在监听器已挂上之后才抛的，"
                         "不摘就是留一只半挂上的 Fetch 钩子（不回的那一笔把导航吊 20 秒）")

    def test_the_fence_has_a_none_before_the_try(self):
        """`fence = None` 必须住在 try 之前。

        少了它，`_allowed_hosts is None`（非 attach 的每一条通道）那次 `fetch` 走到
        `if fence is not None` 就是一次 NameError —— 而它会被外面那个通用兜底包成
        "一次失败的抓取"，看不出是接线断了。
        """
        func = _fetch_body()
        installs = list(_install_try(func))
        assert installs, "找不到 install 那处 try"
        install_line = installs[0][1].lineno
        priors = [n for n in ast.walk(func) if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == "fence" for t in n.targets)
                  and isinstance(n.value, ast.Constant) and n.value.value is None
                  and n.lineno < install_line]
        assert priors, ("install 之前没有 `fence = None` —— 那条 None 是这一支唯一的"
                        f"入口证据（install 在第 {install_line} 行）")

    def test_the_escape_race_and_the_teardown_both_guard_on_the_fence(self):
        func = _fetch_body()
        for name in ("_await_stable_or_escape",):
            calls = [n for n in ast.walk(func) if isinstance(n, ast.Call)
                     and getattr(n.func, "id", None) == name]
            assert calls, f"fetch 里再没有 {name} 的调用 —— 那一支被摘掉了？"
            for call in calls:
                guarded = [n for n in ast.walk(func) if isinstance(n, ast.If)
                           and n.lineno <= call.lineno <= n.end_lineno
                           and "fence" in ast.dump(n.test)]
                assert guarded, f"{name} 的调用没被 `if fence …` 看着（第 {call.lineno} 行）"
        # 收尾那一次 remove 也只看 fence：None 的那一发已经没有可摘的东西
        removes = [n for n in ast.walk(func) if isinstance(n, ast.Await)
                   and isinstance(n.value, ast.Call)
                   and getattr(n.value.func, "attr", None) == "remove"
                   and getattr(n.value.func, "value", None) is not None
                   and getattr(n.value.func.value, "id", None) == "fence"]
        assert removes, "收尾没有 await fence.remove() —— 闸会活过这一页"


class TestAHalfInstalledFenceRemovesCleanly:
    """`Fetch.enable` 抛的那一刻，session 与监听器都已经在了 —— 摘必须真摘。"""

    class _Session:
        def __init__(self, *, fail_enable: bool = True, fail_disable: bool = False):
            self.sent: list[str] = []
            self.events: list = []
            self._fail_enable = fail_enable
            self._fail_disable = fail_disable

        async def send(self, method, params=None):
            self.sent.append(method)
            if method == "Fetch.enable" and self._fail_enable:
                raise RuntimeError("Fetch.enable refused by this Chrome build")
            if method == "Fetch.disable" and self._fail_disable:
                raise RuntimeError("TargetClosedError: page already closed")
            if method == "Page.getFrameTree":
                return {"frameTree": {"frame": {"id": "ROOT"}}}
            return {}

        def on(self, event, handler):
            self.events.append(event)

    class _Context:
        def __init__(self, session):
            self._session = session

        async def new_cdp_session(self, _page):
            return self._session

    class _Page:
        def __init__(self, session):
            self.context = TestAHalfInstalledFenceRemovesCleanly._Context(session)

    def _fence(self, *, fail_disable: bool = False):
        session = self._Session(fail_disable=fail_disable)
        fence = _DocumentFence(self._Page(session), frozenset({"in.test"}), [])
        return fence, session

    @pytest.mark.asyncio
    async def test_install_raises_after_the_listener_is_already_attached(self):
        """失败的形状是"钩子挂上了、开关没开成"，不是"什么都没发生"。"""
        fence, session = self._fence()
        with pytest.raises(RuntimeError, match="Fetch.enable"):
            await fence.install()
        assert session.events == ["Fetch.requestPaused"], session.events
        assert "Page.getFrameTree" in session.sent

    @pytest.mark.asyncio
    async def test_remove_after_a_failed_install_does_not_raise(self):
        fence, session = self._fence()
        with pytest.raises(RuntimeError):
            await fence.install()
        await fence.remove()                      # 必须安静
        assert fence._session is None, "摘完还留着 session —— 后面那笔事件仍会去应答它"
        assert "Fetch.disable" in session.sent

    @pytest.mark.asyncio
    async def test_remove_is_silent_when_the_page_already_closed(self):
        """摘不成也不许冒出去：那一发已经输了，回执该说拒绝，不该说 traceback。"""
        fence, _session = self._fence(fail_disable=True)
        with pytest.raises(RuntimeError):
            await fence.install()
        await fence.remove()
        assert fence._session is None

    @pytest.mark.asyncio
    async def test_a_late_event_after_removal_answers_nothing_and_does_not_raise(self):
        fence, session = self._fence()
        with pytest.raises(RuntimeError):
            await fence.install()
        await fence.remove()
        before = list(session.sent)
        await fence._resolve({"requestId": 7, "frameId": "ROOT",
                              "request": {"url": "https://out.test/next"}})
        assert session.sent == before, "摘掉之后还对着死 session 发号 —— 那是一条 traceback"
        assert fence._watch == [], "摘掉的闸不许再把这一笔记进围栏账上"

    @pytest.mark.asyncio
    async def test_remove_without_an_installed_session_is_a_no_op(self):
        fence, session = self._fence()
        await fence.remove()
        assert session.sent == []


class TestTheRefusalSaysWhichSideItIs:
    """两支措辞互不越界 —— 这是"装不上"那条退路对用户唯一可见的形状。"""

    def test_the_request_boundary_says_it_never_left(self):
        err = _fence_blocked_error(["out.test"], frozenset({"in.test"}),
                                   left_browser=False)
        text = str(err)
        assert "ended before it left the browser" in text
        assert "already left" not in text, "请求边界那句不许顺手承认第二件事"

    def test_the_result_boundary_admits_the_request_went_out(self):
        err = _fence_blocked_error(["out.test"], frozenset({"in.test"}),
                                   left_browser=True)
        text = str(err)
        assert "already left the browser" in text
        assert "ended before it left" not in text

    def test_both_branches_still_name_the_way_out(self):
        for left in (True, False):
            text = str(_fence_blocked_error(["out.test"], frozenset({"in.test"}),
                                             left_browser=left))
            assert "confirm=true" in text, left
            assert "cdp_host_not_allowed:" in text, left

    def test_the_landed_check_is_the_one_the_no_fence_path_falls_back_on(self):
        """退路仍然拦得住内容：落地名册外那一支读的是 landed，与 fence 在不在无关。"""
        assert _landed_host_outside_fence("https://out.test/x", frozenset({"in.test"})) \
            == "out.test"
        assert _landed_host_outside_fence("https://in.test/x", frozenset({"in.test"})) == ""


# ============================================================================
# 来源：test_a_pinned_page_answers_the_captcha_the_previous_call_show_it.py
# ============================================================================
# 驻留的那一页答的就是它上一发显示的那道题（真浏览器 + 真靶站，`-m live` 才选上）。

EVALS = Path(__file__).resolve().parent.parent / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))


_REAL_GETADDRINFO = socket.getaddrinfo
_READ_TOKEN = [{"read": {"selector": "input[name=\"token\"]",
                         "attribute": "value", "as": "token"}}]


def _browser_or_skip():
    from dhole_mcp.browser import check_browser_available
    if not check_browser_available():
        pytest.skip("no browser deps for the live tier")
    return True


@pytest.fixture
def target_site(monkeypatch):
    """真 socket + 真 DNS：SSRF 守卫要看真地址，替身会把 127.0.0.1 变成假 IP。"""
    import fixture_site as fs

    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    srv = fs.make_server("127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", srv
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def private_door_open():
    """回环靶站要过 SSRF 门；conftest 把 identity.db 重定向进每条用例自己的 tmp_path。"""
    identity.set_value("allow_private", True)
    yield


def _rows(result) -> list[dict]:
    return (result.metadata or {}).get("actions") or []


@pytest.fixture
def one_server():
    """两次调用必须落在**同一个** server 实例上：会话表是实例状态（`self._sessions`），
    各造一个 `MasterFetchServer()` 的话第二次拿到的是另一台浏览器 —— 驻留自然不成立，
    而那是一句关于测试装置的话，不是一句关于 dhole 的话。"""
    return MasterFetchServer()


def _read_token(result) -> str:
    rows = _rows(result)
    assert rows and rows[0].get("ok") is True, rows
    return rows[0]["read"]


def _last_captcha_record() -> dict:
    import fixture_site as fs

    lines = fs.CAPTCHA_LOG.read_text(encoding="utf-8").strip().splitlines()
    assert lines, "站点那侧一条尝试都没记下"
    return json.loads(lines[-1])


@pytest.mark.live
@pytest.mark.asyncio
async def test_the_pinned_page_answers_the_challenge_it_showed(target_site,
                                                              private_door_open,
                                                              one_server):
    """第二次调用不重新导航，所以它答的是第一次那一道 —— 站点自己判的卷。"""
    import fixture_site as fs
    _browser_or_skip()
    base, _ = target_site
    fs.CAPTCHA_LOG.unlink(missing_ok=True)

    first = await one_server.web_act(
        url=f"{base}/captcha", session_id="pin-answer", pin=True,
        actions=_READ_TOKEN, timeout=90000)
    try:
        token = _read_token(first)
        assert PIN_NOTES["pinned"] in (first.summary or ""), first.summary

        second = await one_server.web_act(
            url=f"{base}/captcha", session_id="pin-answer", pin=True,
            actions=_READ_TOKEN
            + [{"fill": {"selector": "#answer", "text": fs.captcha_text(token)}},
               {"submit": {"selector": "#captcha-btn", "confirm": True}}],
            timeout=90000)
        rows = _rows(second)
        assert PIN_NOTES["reused"] in (second.summary or ""), second.summary
        # 同一枚 token = 这一页没有重新导航（token 是页面里的那一格，不是 dhole 记的）。
        assert rows[0]["read"] == token, (rows[0], token)
        assert rows[-1].get("submitted") is True, rows[-1]
        assert "验证码正确" in "".join(second.content or []), second.summary

        record = _last_captcha_record()
        assert record["ok"] is True, record
        assert record["token"] == token, record
        assert record["answer"] == fs.captcha_text(token), record
    finally:
        await one_server._close_one_session("pin-answer")


@pytest.mark.live
@pytest.mark.asyncio
async def test_the_same_two_calls_without_pin_answer_the_wrong_question(
        target_site, private_door_open, one_server):
    """反面对照：不驻留时第二次是另一道题，第一次的答案递过去就是 403。

    这一条不是"再证一遍旧结论"：它把"两次导航 token 不同"的后果说完 —— 后果是被拒，
    而拒的是**上一次的**那道题。
    """
    import fixture_site as fs
    _browser_or_skip()
    base, _ = target_site
    fs.CAPTCHA_LOG.unlink(missing_ok=True)

    first = await one_server.web_act(
        url=f"{base}/captcha", session_id="pin-absent",
        actions=_READ_TOKEN, timeout=90000)
    try:
        token = _read_token(first)

        second = await one_server.web_act(
            url=f"{base}/captcha", session_id="pin-absent",
            actions=_READ_TOKEN
            + [{"fill": {"selector": "#answer", "text": fs.captcha_text(token)}},
               {"submit": {"selector": "#captcha-btn", "confirm": True}}],
            timeout=90000)
        rows = _rows(second)
        assert rows[0]["read"] != token, "不驻留也拿到了同一枚 token —— 靶站改了形状"
        assert "验证码错误" in "".join(second.content or []), second.summary
        assert _last_captcha_record()["ok"] is False, _last_captcha_record()
        for summary in (first.summary or ""), (second.summary or ""):
            assert "pinned" not in summary, summary
    finally:
        await one_server._close_one_session("pin-absent")


@pytest.mark.live
@pytest.mark.asyncio
async def test_a_blocked_submit_on_a_pinned_page_says_the_page_is_pinned(
        target_site, private_door_open, one_server):
    """驻留页上"把同一串动作重跑一遍"是半句假话，所以那句话必须点名驻留。"""
    import fixture_site as fs
    _browser_or_skip()
    base, _ = target_site
    fs.CAPTCHA_LOG.unlink(missing_ok=True)

    result = await one_server.web_act(
        url=f"{base}/captcha", session_id="pin-gate", pin=True,
        actions=[{"fill": {"selector": "#answer", "text": "ABCDE"}},
                 {"submit": {"selector": "#captcha-btn"}}],
        timeout=90000)
    try:
        gated = [r for r in _rows(result) if r.get("pending_approval")]
        assert gated, _rows(result)
        text = gated[-1]["error"]
        assert "THIS PAGE IS PINNED" in text, text
        assert "re-run just this submit step" in text, text
        assert fs.CAPTCHA_LOG.exists() is False, "被拦下的那一发不该把表单送出去"
    finally:
        await one_server._close_one_session("pin-gate")


# ============================================================================
# 来源：test_a_pinned_page_answers_before_the_fact.py
# ============================================================================
# R02-029 · 驻留的时间账要能在**事前**问到，不是等它过期后从 summary 里得知。
# 重名消歧：_rows → _rows__a_pinned_page_answers_be_db3e

PINNED = "http://127.0.0.1:8399/account"


class _Page:
    def __init__(self, url: str):
        self.url = url
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    def remove_listener(self, *a):
        pass

    async def close(self):
        self.closed = True


def _pinned(url: str = PINNED, *, age_s: float = 0.0) -> BrowserSession:
    s = BrowserSession()
    s._is_alive = True  # 手上真有一页的会话是活着的（普查只在活着的那一侧问驻留）
    s._pin = {"page": _Page(url), "stamp": time.monotonic() - age_s,
              "response": None, "listener": None, "route": None}
    return s


# ─── 1 · 事前问得到的那两格 ─────────────────────────────────────────────────

def test_a_session_without_a_pin_says_nothing_is_pinned():
    got = BrowserSession().pin_census()
    # 没钉着那一支交 None 而不是 0：带地址的 0 是判决，压实层要把它留在网上
    # （R02-073 改的就是「用 0 表示没钉着」这个期望本身，见
    # `tests/test_a_expired_pin_answers_the_zero_on_the_wire.py`）。
    assert got == {"pinned_url": "", "pinned_left_s": None}, got


def test_a_fresh_pin_names_the_page_and_counts_down_from_the_same_ttl():
    got = _pinned().pin_census()
    assert got["pinned_url"] == PINNED, got
    assert 0 < got["pinned_left_s"] <= PIN_TTL_S, got


def test_the_countdown_is_the_ttl_constant_minus_the_age_of_the_slot():
    """不是「另一个 300」：句里那个数必须与 `_pin_take` 用的是同一个作者。"""
    for age in (120, 240, PIN_TTL_S - 1):
        got = _pinned(age_s=age).pin_census()
        assert abs(got["pinned_left_s"] - (PIN_TTL_S - age)) <= 1, (age, got)


def test_a_pin_whose_window_has_passed_still_names_its_page():
    """0 秒 + 有地址 = 「这一页还在槽里，只是过期了，下一发看它时才放掉」。

    这与「什么都没钉」是两件不同的事，说成一件就是把一种状态抹成另一种。
    """
    got = _pinned(age_s=PIN_TTL_S + 5).pin_census()
    assert got["pinned_left_s"] == 0, got
    assert got["pinned_url"] == PINNED, got


def test_a_pinned_page_that_someone_else_closed_is_not_reported_as_still_there():
    s = _pinned()
    s._pin["page"].closed = True
    assert s.pin_census() == {"pinned_url": "", "pinned_left_s": None}, s.pin_census()


def test_reading_the_census_does_not_touch_the_pin():
    """普查是只读的：不许为了「还剩几秒」把那一步的复用性一起付掉。"""
    s = _pinned()
    s.pin_census()
    assert s._pin is not None, "问一眼就把驻留页摘了/关了"
    assert s._pin["page"].closed is False


# ─── 2 · 与那道门同一个作者（两侧对得上）────────────────────────────────────

@pytest.mark.asyncio
async def test_the_census_counts_down_to_exactly_where_the_gate_refuses():
    for age, want_left_positive, want_why in (
            (PIN_TTL_S - 2, True, ""),
            (PIN_TTL_S + 2, False, "ttl")):
        s = _pinned(age_s=age)
        left = s.pin_census()["pinned_left_s"]
        assert (left > 0) is want_left_positive, (age, left)
        slot, why = await s._pin_take(PINNED)
        assert why == want_why, (age, why)
        assert (slot is not None) is want_left_positive, (age, slot)


# ─── 3 · 普查真的把这两格交出来（现场那发问的就是这个）──────────────────────

def _rows__a_pinned_page_answers_be_db3e(server: MasterFetchServer) -> list:
    return asyncio.run(server._session_rows())


def test_the_census_row_carries_the_pin_that_the_call_left_behind():
    server = MasterFetchServer()
    server._sessions["shop"] = _SessionEntry(session=_pinned(),
                                             session_type="stealthy")
    row = [r for r in _rows__a_pinned_page_answers_be_db3e(server) if r.session_id == "shop"][0]
    assert row.pinned_url == PINNED, row
    assert 0 < row.pinned_left_s <= PIN_TTL_S, row


def test_a_jar_only_session_is_not_claimed_to_hold_a_page():
    """她的普查里就有这样一行（只登录过、没开浏览器）—— 不许读成「有一页钉着，0 秒」。"""
    from dhole_mcp import sessions as jar

    jar.store("onlyjar", PINNED, {"sid": "v"})
    try:
        server = MasterFetchServer()
        row = [r for r in _rows__a_pinned_page_answers_be_db3e(server) if r.session_id == "onlyjar"]
        assert row, "名册里找不到这一行"
        assert row[0].browser_open is False
        assert row[0].pinned_url == "" and row[0].pinned_left_s is None, row[0]
    finally:
        jar.clear("onlyjar")


# ─── 4 · 沉默与线的形状 ─────────────────────────────────────────────────────

def test_both_pin_fields_ride_off_the_wire_when_nothing_is_pinned():
    """没驻留时不许在每一行加两格样板；正控制：真驻留着时必须上得了线。"""
    assert {"pinned_url", "pinned_left_s"} <= _WIRE_OMIT["SessionCensusRow"]
    server = MasterFetchServer()
    server._sessions["idle"] = _SessionEntry(session=BrowserSession(),
                                             session_type="stealthy")
    server._sessions["live"] = _SessionEntry(session=_pinned(),
                                             session_type="stealthy")
    by = {r["session_id"]: r for r in (_wire_dump(x) for x in _rows__a_pinned_page_answers_be_db3e(server))}
    assert "pinned_url" not in by["idle"], by["idle"]
    assert by["live"]["pinned_url"] == PINNED, by["live"]
    assert by["live"]["pinned_left_s"] > 0, by["live"]


# ─── 5 · 问得到，也要查得着（手册层：她那发是先读手册再决定要不要查）────────

def test_the_tools_handbook_says_the_pin_is_askable_before_the_fact():
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "src" / "dhole_mcp" / "skills"
            / "dhole-web" / "references" / "tools.md").read_text(encoding="utf-8")
    line = [entry for entry in text.splitlines()
            if entry.startswith("- No arguments = **the census**")]
    assert len(line) == 1, "普查那一行丢了或被拆成两行"
    said = line[0]
    assert "pinned_url" in said and "pinned_left_s" in said, said
    # 三种状态各有说法，不许两种抹成一种
    assert "nothing pinned" in said and "window passed" in said, said
    # 判据是同一个作者，手册不许把它写成第二只钟
    assert "same gate the next pinned call applies" in said, said


# ============================================================================
# 来源：test_stealth.py
# ============================================================================
# Stealth engine tests: fingerprint profiles, init script generation,
# browser args (memory + anti-detection), human behavior simulation.

# ─── Browser args ──────────────────────────────────────────────────

class TestBrowserArgs:
    """单个魔法参数的成员断言（内存调优值、某个 stealth flag）已删——
    参数表是随检测手段演进持续调优的数据，冻结它只会让正常调参报假警。
    保留关系型不变量：我们自己的参数列表永不包含有害的自动化信号参数。"""

    def test_no_headless_flag_in_default_args(self):
        # --headless is not in DEFAULT_ARGS (added conditionally at launch)
        for arg in DEFAULT_ARGS:
            assert not arg.startswith("--headless")

    def test_stealth_args_does_not_contain_harmful(self):
        # No harmful args should appear in stealth args
        for harmful in HARMFUL_ARGS:
            assert harmful not in STEALTH_ARGS
            assert harmful not in DEFAULT_ARGS


# ─── Fingerprint profiles ─────────────────────────────────────────

class TestFingerprintProfiles:
    """数量断言（4 个档案、3 个 Win32、5 个插件、设备内存枚举）已删——
    增删档案属正常演进。保留的是反检测的核心契约：档案结构完整、
    内部自洽（平台与 WebGL 厂商匹配）、GPU 厂商有多样性。"""

    def test_all_profiles_have_required_fields(self):
        required = {"platform", "languages", "hardware_concurrency",
                    "device_memory", "webgl_vendor", "webgl_renderer", "plugins"}
        for profile in _FINGERPRINT_PROFILES:
            missing = required - set(profile.keys())
            assert not missing, f"Profile missing fields: {missing}"

    def test_win32_profiles_cover_major_gpu_vendors(self):
        """GPU 厂商多样性：会话不能全部声称同一款显卡。"""
        win32_profiles = [p for p in _FINGERPRINT_PROFILES if p["platform"] == "Win32"]
        renderers = [p["webgl_vendor"] for p in win32_profiles]
        assert any("NVIDIA" in r for r in renderers)
        assert any("Intel" in r for r in renderers)
        assert any("AMD" in r for r in renderers)

    def test_macintel_profiles_have_apple_webgl(self):
        """自洽：MacIntel 平台必须配 Apple GPU（检测器做交叉验证）。"""
        mac_profiles = [p for p in _FINGERPRINT_PROFILES if p["platform"] == "MacIntel"]
        assert mac_profiles, "no MacIntel profile"
        for profile in mac_profiles:
            assert "Apple" in profile["webgl_vendor"]

    def test_all_profiles_have_plugins(self):
        """headless 下 navigator.plugins 为空是机器人信号——档案必须伪造。"""
        for profile in _FINGERPRINT_PROFILES:
            assert len(profile["plugins"]) >= 1

    def test_hardware_concurrency_reasonable(self):
        for profile in _FINGERPRINT_PROFILES:
            assert 4 <= profile["hardware_concurrency"] <= 16


# ─── Fingerprint generation ───────────────────────────────────────

class TestGenerateFingerprint:

    def test_returns_a_copy_not_reference(self):
        p1 = _generate_fingerprint_profile()
        p2 = _generate_fingerprint_profile()
        # Mutating one should not affect the other
        p1["platform"] = "modified"
        assert p2["platform"] != "modified"

    def test_returns_valid_profile(self):
        profile = _generate_fingerprint_profile()
        assert profile in _FINGERPRINT_PROFILES or profile["platform"] in ("Win32", "MacIntel")


# ─── Init script generation ───────────────────────────────────────

class TestInitScript:

    def test_essential_patches_present(self):
        script = _build_stealth_init_script(_FINGERPRINT_PROFILES[0], full=False)
        # navigator.webdriver -> undefined
        assert "webdriver" in script
        assert "undefined" in script
        # HeadlessChrome replacement
        assert "HeadlessChrome" in script
        # Languages
        assert "en-US" in script
        # Canvas noise (both toDataURL and getImageData)
        assert "toDataURL" in script
        assert "getImageData" in script
        # Permissions API
        assert "permissions" in script

    def test_full_patches_present_when_full_true(self):
        script = _build_stealth_init_script(_FINGERPRINT_PROFILES[0], full=True)
        # WebGL vendor/renderer
        assert "webgl_vendor" in script or "getParameter" in script
        # Plugins
        assert "PDF Viewer" in script or "plugins" in script
        # Platform
        assert "platform" in script
        # Hardware concurrency
        assert "hardwareConcurrency" in script
        # window.chrome
        assert "chrome" in script

    def test_full_patches_absent_when_full_false(self):
        script = _build_stealth_init_script(_FINGERPRINT_PROFILES[0], full=False)
        # Should not contain the full patches
        # Check that WebGL renderer value is NOT injected
        assert _FINGERPRINT_PROFILES[0]["webgl_renderer"] not in script

    def test_canvas_noise_uses_let_not_const(self):
        # The _seed variable must use let, not const (const was a bug that
        # crashed the entire init script)
        script = _build_stealth_init_script(_FINGERPRINT_PROFILES[0], full=False)
        assert "let _seed" in script
        assert "const _seed" not in script

    def test_canvas_noise_intercepts_both_methods(self):
        script = _build_stealth_init_script(_FINGERPRINT_PROFILES[0], full=False)
        assert "toDataURL" in script
        assert "getImageData" in script
        # Both should be intercepted (not just one)
        assert script.count("toDataURL") >= 2  # original + override
        assert script.count("getImageData") >= 2

    def test_no_device_scale_factor_2(self):
        # device_scale_factor=2 was a Mac Retina giveaway, removed in v11.1.0
        script = _build_stealth_init_script(_FINGERPRINT_PROFILES[0], full=True)
        assert "deviceScaleFactor" not in script or "deviceScaleFactor" not in script.replace("2", "")

    def test_different_profiles_produce_different_scripts(self):
        win_profile = next(p for p in _FINGERPRINT_PROFILES if p["platform"] == "Win32")
        mac_profile = next(p for p in _FINGERPRINT_PROFILES if p["platform"] == "MacIntel")
        script_win = _build_stealth_init_script(win_profile, full=True)
        script_mac = _build_stealth_init_script(mac_profile, full=True)
        assert script_win != script_mac

    def test_script_is_valid_js_syntax(self):
        # Basic syntax check: balanced braces
        script = _build_stealth_init_script(_FINGERPRINT_PROFILES[0], full=True)
        assert script.count("{") == script.count("}")
        assert script.count("(") == script.count(")")
