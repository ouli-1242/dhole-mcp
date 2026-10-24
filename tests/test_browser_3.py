"""test_browser_3.py —— 由 3 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import inspect
import time
import pytest
from dhole_mcp import browser as browser_mod
from dhole_mcp.browser import BrowserSession, PIN_TTL_S
import asyncio
from dhole_mcp.browser import CDPHostNotAllowedError, _DocumentFence, _await_stable_or_escape, _fence_blocked_error
from typing import Any, Optional
from dhole_mcp.browser import _create_route_handler, _is_entry_target, _landed_url_refused


# ============================================================================
# 来源：test_the_pinned_page_is_dropped_by_the_guards_that_hold_it.py
# ============================================================================
# 驻留的那一页由握着它的那几道闸门决定去留 —— 每一条都在这一页上钉住。

URL = "https://example.com/"
OTHER = "https://other.example/"


class _Resp:
    def __init__(self, url: str):
        self.url = url


class _Cdp:
    def __init__(self, page: _Page):
        self.page = page

    async def send(self, method, params=None):
        self.page.cdp_used = True

    async def detach(self):
        pass


class _Page:
    """够得上 `fetch` 那一条路径的最小页面替身。

    每个计数器都是一句断言的对象：`goto_calls` 数的是"有没有再导航"（驻留的全部
    意义在它为零），`routes`/`unroutes`/`listeners`/`removed` 数的是守卫的装卸次数。
    """

    def __init__(self, url: str = URL, *, unroute_fails: bool = False):
        self.url = url
        self.main_frame = object()
        self.context = _Context(self)
        self.goto_calls: list[str] = []
        self.routes: list[tuple] = []
        self.unroutes: list[tuple] = []
        self.listeners: list[tuple] = []
        self.removed: list[tuple] = []
        self.load_states: list[str] = []
        self.extra_headers: list[dict] = []
        self.closed = False
        self.cdp_used = False
        self._unroute_fails = unroute_fails

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True

    def set_default_navigation_timeout(self, ms) -> None:
        pass

    def set_default_timeout(self, ms) -> None:
        pass

    async def set_extra_http_headers(self, headers) -> None:
        self.extra_headers.append(dict(headers or {}))

    async def route(self, pattern, handler) -> None:
        self.routes.append((pattern, handler))

    def on(self, event, handler) -> None:
        self.listeners.append((event, handler))

    def remove_listener(self, event, handler) -> None:
        self.removed.append((event, handler))

    async def unroute(self, pattern, handler=None) -> None:
        if self._unroute_fails:
            raise RuntimeError("the hook would not come off")
        self.unroutes.append((pattern, handler))

    async def goto(self, url, referer=None, wait_until=None):
        self.goto_calls.append(url)
        self.url = url
        return _Resp(url)

    async def evaluate(self, script):
        return None

    async def wait_for_load_state(self, state=None) -> None:
        self.load_states.append(state)


class _Context:
    """既是会话的 context（`new_page`），也是页面的 context（`new_cdp_session`）。"""

    def __init__(self, page: _Page | None = None):
        self.queue: list[_Page] = [] if page is None else [page]
        self.pages = page
        self.made = 0
        self.closed = False

    async def new_page(self) -> _Page:
        self.made += 1
        if self.queue:
            return self.queue.pop(0)
        return _Page()

    async def new_cdp_session(self, page):
        page.cdp_used = True
        return _Cdp(page)

    async def cookies(self, url):
        return []

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def session(monkeypatch):
    """一台"起好了"的会话：真 launch 由 conftest 拦着，这里只把 `fetch` 需要的部件装上。

    `installs` 记录每一次 `_install_page_guards` 用到的 `entry_url` —— 复用那一发必须
    以**这一页自己的 URL** 为同站参照，而不是上一发请求的那个 URL。
    """
    s = BrowserSession(headless=True, timeout=5000, retries=1, retry_delay=0)
    s._is_alive = True
    s._init_script = None
    s._browser = None
    s._playwright = None
    s._user_data_dir = None
    s._context = _Context(None)
    pages: list[_Page] = []
    s._context.queue = pages

    installs: list[dict] = []

    def fake_route_handler(disable_resources, blocked_domains=None, ssrf_watch=None,
                           entry_url="", main_frame=None, sent_headers=None,
                           allowed_hosts=None):
        async def _handler(route):  # pragma: no cover - 装上去即可，不驱动
            pass
        installs.append({"entry_url": entry_url, "ssrf_watch": ssrf_watch,
                         "allowed_hosts": allowed_hosts, "handler": _handler})
        return _handler

    monkeypatch.setattr(browser_mod, "_create_route_handler", fake_route_handler)

    built: list[dict] = []

    async def fake_build_response(page, first_response, final_response):
        built.append({"page": page, "first": first_response, "final": final_response})
        return {"url": page.url, "status": 200, "body": b"x"}

    import dhole_mcp.fetcher as fetcher_mod
    monkeypatch.setattr(fetcher_mod, "response_from_browser_page", fake_build_response)

    async def stable(self, page):
        return True

    monkeypatch.setattr(BrowserSession, "_wait_for_stability", stable)

    s._pages = pages
    s.installs = installs
    s.built = built
    return s


async def _fetch(s: BrowserSession, url: str = URL, **kw):
    pin_out: dict = {}
    page = await s.fetch(url, pin=True, pin_out=pin_out, **kw)
    return page, pin_out


class TestTheReuseItself:

    @pytest.mark.asyncio
    async def test_a_pinned_page_stays_open_and_is_handed_over(self, session):
        _, pin_out = await _fetch(session)
        assert pin_out["outcome"] == "pinned", pin_out
        assert session._pin is not None, "驻留槽没立起来"
        assert session._pin["page"].closed is False, "说了驻留，页却关了"

    @pytest.mark.asyncio
    async def test_the_second_call_does_not_navigate(self, session):
        """这一条是整个 pin 的意义：第二次不 goto，token 才是那一枚。"""
        await _fetch(session)
        first = session._pin["page"]
        session.built.clear()
        _, pin_out = await _fetch(session)
        assert pin_out["outcome"] == "reused", pin_out
        assert first.goto_calls == [URL], first.goto_calls
        assert session._context.made == 1, "复用那一发又 new_page() 了一次"
        assert session._pin["page"] is first
        # 复用只等"当前这一笔导航（若有）落地"，不跑给新文档的那一轮稳定性探针。
        assert first.load_states == ["load", "domcontentloaded"], first.load_states
        # 驻留复用只等"当前这一笔导航（若有）落地"，不再跑给新文档用的那个渲染探针。
        assert first.load_states == ["load", "domcontentloaded"], first.load_states

    @pytest.mark.asyncio
    async def test_reuse_moves_the_credential_headers_of_this_call(self, session):
        """上一页留下的凭据头不许悄悄跟着走：`set_extra_http_headers` 是替换语义。"""
        await _fetch(session, extra_headers={"Authorization": "Basic aaa"})
        page = session._pin["page"]
        await _fetch(session, extra_headers=None)
        assert page.extra_headers[-1] == {}, page.extra_headers

    @pytest.mark.asyncio
    async def test_the_action_chain_is_told_it_stood_on_a_pinned_page(self, session):
        """`_submit_gate_row` 那句"重跑同一串动作"在驻留页上是假话 —— 动作链要知道
        自己站在哪，而浏览器层与动作层之间不许有隐形的共享真相（所以挂在链自己身上）。"""
        await _fetch(session)

        def action(page):
            pass
        action.pin_state = {}
        await _fetch(session, page_action=action)
        # 只钉合同（reused/pinned），不冻结整格：页签（1d）往里添了 attach /
        # entry_url / prior_tabs 三颗链内键，那是同一格通信的另一面。
        assert action.pin_state["reused"] is True and \
            action.pin_state["pinned"] is True, action.pin_state

    @pytest.mark.asyncio
    async def test_a_chain_on_a_pinning_call_is_told_the_page_will_stay(self, session):
        """第一次驻留的那一发也要知道：这一页不会跟着调用一起消失，所以提交闸那句
        "把同一串动作再跑一遍"从这一发起就只有半句是真话。"""
        def action(page):
            pass
        action.pin_state = {}
        await _fetch(session, page_action=action)
        assert action.pin_state["reused"] is False and \
            action.pin_state["pinned"] is True, action.pin_state

    @pytest.mark.asyncio
    async def test_a_chain_on_an_unpinned_call_is_told_nothing_was_kept(self, session):
        def action(page):
            pass
        action.pin_state = {}
        await session.fetch(URL, page_action=action)
        assert action.pin_state["reused"] is False and \
            action.pin_state["pinned"] is False, action.pin_state


class TestTheGuardsAreReinstalledNotInherited:

    @pytest.mark.asyncio
    async def test_reuse_unhooks_the_previous_call_and_installs_its_own(self, session):
        await _fetch(session)
        page = session._pin["page"]
        old_route = session._pin["route"]
        old_listener = session._pin["listener"]
        session.installs.clear()
        _, pin_out = await _fetch(session, url=URL)
        assert ("**/*", old_route) in page.unroutes, page.unroutes
        assert ("response", old_listener) in page.removed, page.removed
        assert len(session.installs) == 1, "重装次数不等于 1 = 钩子叠上了"
        assert "dropped" not in pin_out, pin_out
        assert session._pin["route"] is not old_route, "槽里还是上一发那枚 route"

    @pytest.mark.asyncio
    async def test_the_reinstalled_guard_judges_by_the_pages_own_url(self, session):
        """同站豁免的参照物必须是这一页实际站在哪里，不是这一发请求了哪个 URL。"""
        await _fetch(session)
        session._pin["page"].url = URL
        session.installs.clear()
        await _fetch(session)
        assert session.installs[-1]["entry_url"] == URL, session.installs[-1]

    @pytest.mark.asyncio
    async def test_a_page_that_will_not_come_unhooked_is_dropped_not_reused(
            self, session):
        """摘不掉就宁可弃页：带着上一次的 route 与记录本做事是 SSRF 边界。"""
        stuck = _Page(URL, unroute_fails=True)
        session._pages.append(stuck)
        await _fetch(session)
        assert session._pin["page"] is stuck
        session._pages.append(_Page(URL))
        session.installs.clear()
        _, pin_out = await _fetch(session)
        assert pin_out["dropped"] == "unhook", pin_out
        assert pin_out["outcome"] == "pinned", pin_out
        assert stuck.closed is True, "弃页却没关它 = 那一页带着旧钩子活着"
        assert stuck.goto_calls == [URL], "弃掉的那一页不许被第二次导航"
        assert len(session.installs) == 1, session.installs

    @pytest.mark.asyncio
    async def test_the_pinned_response_carries_the_pages_last_navigation(
            self, session):
        """复用的那一发没有新的 goto，所以它的"第一响应"来自这一页自己上一笔导航。"""
        await _fetch(session)
        held = session._pin["response"]
        session.built.clear()
        await _fetch(session)
        assert session.built[0]["first"] is held, session.built[0]


class TestEveryDropReasonHasItsOwnDoor:

    @pytest.mark.asyncio
    async def test_a_page_past_its_clock_is_dropped(self, session):
        await _fetch(session)
        page = session._pin["page"]
        session._pin["stamp"] = time.monotonic() - PIN_TTL_S - 1
        session._pages.append(_Page(URL))
        _, pin_out = await _fetch(session)
        assert pin_out["dropped"] == "ttl", pin_out
        assert page.closed is True
        assert page.goto_calls == [URL], "过期那页被重新用了一次"

    @pytest.mark.asyncio
    async def test_a_page_someone_else_closed_is_dropped(self, session):
        await _fetch(session)
        page = session._pin["page"]
        page.closed = True          # 用户/Chrome 关掉了它，槽还不知道
        session._pages.append(_Page(URL))
        _, pin_out = await _fetch(session)
        assert pin_out["dropped"] == "closed", pin_out
        assert session._pin is not None and session._pin["page"] is not page

    @pytest.mark.asyncio
    async def test_a_page_on_another_site_is_dropped(self, session):
        await _fetch(session)
        page = session._pin["page"]
        page.url = OTHER
        session._pages.append(_Page(URL))
        _, pin_out = await _fetch(session)
        assert pin_out["dropped"] == "host", pin_out
        assert page.closed is True

    @pytest.mark.asyncio
    async def test_an_upgrade_of_the_same_site_is_not_a_different_site(self, session):
        """同站判定只有一个作者（`_is_entry_target`）：80↔443 的默认端口升级不算换站。"""
        await _fetch(session)
        page = session._pin["page"]
        page.url = "http://example.com/other"
        session.installs.clear()
        _, pin_out = await _fetch(session, url="https://example.com/other")
        assert "dropped" not in pin_out, pin_out
        assert pin_out["outcome"] == "reused", pin_out

    @pytest.mark.asyncio
    async def test_the_same_host_on_another_port_is_a_different_site(self, session):
        """内网跳板的常见形态：同一台机器、换一个端口。那一页不能接着站。"""
        await _fetch(session, url="http://127.0.0.1:8080/a")
        page = session._pin["page"]
        session._pages.append(_Page("http://127.0.0.1:9222/b"))
        _, pin_out = await _fetch(session, url="http://127.0.0.1:9222/b")
        assert pin_out["dropped"] == "host", pin_out
        assert page.goto_calls == ["http://127.0.0.1:8080/a"], page.goto_calls

    @pytest.mark.asyncio
    async def test_an_attached_session_never_leaves_a_page_behind(self, session):
        """attach 那一页是用户自己的标签页；那条车道连取都不取、存也不存，且要说出口。"""
        session._cdp_attached = True
        _, pin_out = await _fetch(session)
        assert session._pin is None
        assert "outcome" not in pin_out, pin_out
        assert pin_out["dropped"] == "attach", pin_out
        assert session.built[-1]["page"].closed is True

    @pytest.mark.asyncio
    async def test_a_slot_left_on_an_attached_session_is_dropped_by_the_door(
            self, session):
        """`fetch` 那条守卫是第一道门；`_pin_take` 自己是第二道（身份中途变了也不复用）。"""
        await _fetch(session)
        page = session._pin["page"]
        session._cdp_attached = True
        taken, why = await session._pin_take(URL)
        assert taken is None and why == "attach", why
        assert page.closed is True


class TestTheDefaultPathIsUntouched:

    @pytest.mark.asyncio
    async def test_a_call_without_pin_still_closes_its_page(self, session):
        """默认行为一寸不动：没有 pin 就没有槽、页照关、`pin_out` 不写。"""
        out: dict = {}
        await session.fetch(URL, pin=False, pin_out=out)
        assert session._pin is None
        assert out == {}, out
        assert session.built[0]["page"].closed is True

    @pytest.mark.asyncio
    async def test_a_failed_pinned_call_does_not_leave_the_page(self, session,
                                                                monkeypatch):
        """只有成功路径存槽。而**取走**来复用的那一页在失败时也要关掉 ——
        一个没人复用的驻留页不该活成第二次调用的意外。"""
        await _fetch(session)
        held = session._pin["page"]

        import dhole_mcp.fetcher as fetcher_mod

        async def boom(page, first, final):
            raise RuntimeError("extraction died")
        monkeypatch.setattr(fetcher_mod, "response_from_browser_page", boom)
        with pytest.raises(RuntimeError):
            await session.fetch(URL, pin=True)
        assert session._pin is None
        assert held.closed is True, "取走复用后失败，那一页还挂在会话上"

    @pytest.mark.asyncio
    async def test_closing_the_session_releases_the_page(self, session):
        await _fetch(session)
        page = session._pin["page"]
        await session.close()
        assert page.closed is True
        assert session._pin is None

    @pytest.mark.asyncio
    async def test_a_pinned_page_is_not_asked_to_dump_its_caches(self, session):
        """GC 压力通知会让一张还要截图的页重取资源 —— 驻留那一发不做。"""
        await _fetch(session)
        assert session._pin["page"].cdp_used is False
        out: dict = {}
        await session.fetch(URL, pin=False, pin_out=out)
        assert session.built[-1]["page"].cdp_used is True, "对照：不带驻留照旧压一次"


class TestTheClockIsMonotonic:

    def test_the_ttl_reads_a_clock_that_cannot_be_turned_back(self):
        """墙钟会被 NTP 与手工改时刻往前拨；那一次拨动会把一张刚读了一半的页判成过期
        （或把一张过期的页判成新的）。驻留的时钟必须是单调钟。"""
        src = inspect.getsource(BrowserSession._pin_take)
        assert "time.monotonic()" in src, src
        assert "time.time()" not in src, src

    def test_the_ttl_is_long_enough_to_read_a_picture_and_type_the_answer(self):
        """90 秒不够：体验报告 S10 那一次是人看图、再回头敲字。取空闲回收同一个数。"""
        assert PIN_TTL_S == 300, PIN_TTL_S


# ============================================================================
# 来源：test_a_redirect_out_of_the_roster_is_blocked_before_it_leaves.py
# ============================================================================
# 名册外的那一跳要掐在发出之前 —— `_DocumentFence` 的形状全部按真机实测钉住。
# 重名消歧：OTHER → OTHER__a_redirect_out_of_the_ro_4e59

ROOT = "Aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-ROOT"
OTHER__a_redirect_out_of_the_ro_4e59 = "Bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-FRME"
ROSTER = frozenset({"127.0.0.1"})


class _FakeSession:
    """一只假 CDP 会话：记下每一笔 send，事件手动喂。"""

    def __init__(self, root_id=ROOT, fail_methods=()):
        self.sent: list = []
        self.handlers: dict = {}
        self._root = root_id
        self._fail = set(fail_methods)

    async def send(self, method, params=None):
        self.sent.append((method, params or {}))
        if method in self._fail:
            raise RuntimeError("Target page, context or browser has been closed")
        if method == "Page.getFrameTree":
            if self._root is None:
                return {"frameTree": {}}
            return {"frameTree": {"frame": {"id": self._root}}}
        return {}

    def on(self, event, callback):
        self.handlers[event] = callback

    def methods(self):
        return [m for m, _ in self.sent]

    def paused(self, url, frame_id=ROOT, rid="r1", rtype="Document"):
        """喂一个 Fetch.requestPaused 事件并把它的异步处理跑完。"""
        self.handlers["Fetch.requestPaused"]({
            "requestId": rid, "frameId": frame_id, "resourceType": rtype,
            "request": {"url": url}})
        return _drain()


class _FakeContext:
    def __init__(self, session):
        self._session = session
        self.detached = 0

    async def new_cdp_session(self, page):
        if self._session is None:
            raise RuntimeError("no cdp channel here")
        return self._session


class _FakePage:
    def __init__(self, session):
        self.context = _FakeContext(session)
        self.url = "http://127.0.0.1:9222/home"


def _drain():
    return asyncio.sleep(0)


async def _installed(root_id=ROOT, fail_methods=()):
    session = _FakeSession(root_id=root_id, fail_methods=fail_methods)
    watch: list = []
    fence = _DocumentFence(_FakePage(session), ROSTER, watch)
    await fence.install()
    return session, watch, fence


@pytest.mark.asyncio
class TestTheShapeIsTheMeasuredOne:

    async def test_only_documents_are_intercepted(self):
        """patterns 必须限死 Document：管所有类型会把子资源从 route 手里抢走（实测 V1）。"""
        session, _, _ = await _installed()
        enable = [params for method, params in session.sent if method == "Fetch.enable"]
        assert len(enable) == 1
        patterns = enable[0]["patterns"]
        assert patterns, "Fetch.enable 没带 patterns 等于拦下整个浏览器的每一个请求"
        for pattern in patterns:
            assert pattern["resourceType"] == "Document", (
                f"这只闸伸向了子资源：{pattern}")
            assert pattern["requestStage"] == "Request", (
                "Response 阶段已经发出去了，掐不掉 —— 这一刀要的就是发出之前")
        assert {p["urlPattern"] for p in patterns} == {"http://*", "https://*"}, (
            "只写 http(s)：data:/blob:/about: 不是网络请求，把它们暂停只会弄坏页面")

    async def test_install_fails_loudly_so_the_caller_can_fall_back(self):
        """装不上就抛 —— fetch() 的那一侧靠这个异常退回 route + 结果边界那条线。"""
        fence = _DocumentFence(_FakePage(None), ROSTER, [])
        with pytest.raises(RuntimeError):
            await fence.install()

    async def test_remove_is_idempotent_and_swallows_a_dead_target(self):
        session, _, fence = await _installed(fail_methods=("Fetch.disable",))
        await fence.remove()
        await fence.remove()          # 第二次连会话都没有了，也不许冒异常
        assert "Fetch.disable" in session.methods()


@pytest.mark.asyncio
class TestTheFenceDecision:

    async def test_an_off_roster_main_document_is_ended_before_it_leaves(self):
        session, watch, fence = await _installed()
        await session.paused("http://localhost:9222/whoami")
        assert ("Fetch.failRequest", {"requestId": "r1",
                                      "errorReason": "BlockedByClient"}) in session.sent, (
            "名册外的主文档没被掐 —— 这一跳会带着已登录的 profile 走出去")
        assert watch == [("localhost", True)], f"这一跳没记账：{watch}"

    async def test_an_in_roster_document_is_released_and_says_nothing(self):
        session, watch, fence = await _installed()
        await session.paused("http://127.0.0.1:9222/home")
        assert ("Fetch.continueRequest", {"requestId": "r1"}) in session.sent
        assert watch == []

    async def test_an_off_roster_iframe_is_blocked_but_does_not_refuse_the_page(self):
        """iframe 的文档不是"主文档跑出名册"：拦下那一棵 iframe，整页照返（实测的 root?False）。

        把这个形状与上一条混为一谈，代价是一个嵌着第三方 iframe 的正常站点被整条拒绝 ——
        今天那种页面只是 iframe 被 route 拦掉、正文照样交回。
        """
        session, watch, fence = await _installed()
        await session.paused("http://localhost:9222/frame", frame_id=OTHER__a_redirect_out_of_the_ro_4e59)
        assert any(m == "Fetch.failRequest" for m, _ in session.sent), (
            "名册外的 iframe 该被拦下（route 今天就是这么做的）")
        assert watch == [], f"一棵 iframe 不该把整页判死：{watch}"

    async def test_an_unknown_root_frame_fails_closed(self):
        """读不到根 frame id 时，每一笔名册外的 Document 都按主文档记账。

        偏向哪一边是有代价的：误判成主文档会多拒一个带第三方 iframe 的页面，误判成
        iframe 会漏掉一次真的跑出名册。漏掉那次没有任何东西兜着（落地页是错误页，
        结果边界认不出主机名），多拒一次调用方换个写法就能拿到内容 —— 所以 fail-closed。
        """
        session = _FakeSession(root_id=None)
        watch: list = []
        fence = _DocumentFence(_FakePage(session), ROSTER, watch)
        await fence.install()
        await session.paused("http://localhost:9222/frame", frame_id=OTHER__a_redirect_out_of_the_ro_4e59)
        assert watch == [("localhost", True)], watch

    async def test_a_late_event_after_the_page_closed_raises_nothing(self):
        """页面关完之后还有 paused 事件进来是实测有的（TargetClosedError）。

        这里没有可救的请求：让 send 的失败冒出去只会把一次关闭竞态变成一条 traceback，
        而这条链正跑在用户的浏览器上。
        """
        session, watch, fence = await _installed(fail_methods=(
            "Fetch.continueRequest", "Fetch.failRequest"))
        await session.paused("http://127.0.0.1:9222/home")
        await session.paused("http://localhost:9222/whoami")
        assert any(m == "Fetch.failRequest" for m, _ in session.sent)

    async def test_events_are_answered_even_after_the_fence_is_removed(self):
        session, _, fence = await _installed()
        await fence.remove()
        await session.paused("http://localhost:9222/late")   # 不该抛，也不该再发
        assert session.methods().count("Fetch.failRequest") == 0


@pytest.mark.asyncio
class TestTheStabilityWaitDoesNotEatTheBudget:
    """被掐掉的导航不会再有 load 事件 —— 稳定等待必须让位给那条拒绝。

    不这么写的后果是真机量出来的（2026-10-09 的 `-m e2e`）：围栏确实拦住了那一跳，
    可 `_wait_for_stability` 还在等一个永远不来的 load，30 秒预算花光，先说话的是
    调用级预算那句"这一档超时了"。围栏做了它的工作、回执却报告另一件事，等于白拦。
    """

    async def test_a_blocked_main_document_releases_the_waiter_early(self):
        session, watch, fence = await _installed()
        slept = []

        async def never_settles():
            await asyncio.sleep(30)
            slept.append("the wait finished on its own")

        waiter = asyncio.ensure_future(_await_stable_or_escape(never_settles(), fence))
        await session.paused("http://localhost:9222/whoami")
        await asyncio.wait_for(waiter, timeout=2.0)
        assert slept == [], "稳定等待自己跑完了 —— 那这条链又会把预算花光"

    async def test_an_off_roster_iframe_does_not_release_the_waiter(self):
        """iframe 被拦不算"页面跑出名册"：稳定等待该照常跑完，正文照常交回。"""
        session, watch, fence = await _installed()
        done = []

        async def settles():
            await asyncio.sleep(0.01)
            done.append(True)

        waiter = asyncio.ensure_future(_await_stable_or_escape(settles(), fence))
        await session.paused("http://localhost:9222/frame", frame_id=OTHER__a_redirect_out_of_the_ro_4e59)
        await asyncio.wait_for(waiter, timeout=2.0)
        assert done == [True]
        assert watch == []


class TestTheRefusalWords:
    """拒绝的话必须说清这一跳出去没出去 —— 那正是这两批改动各自挣到的那半句。"""


    def test_blocked_at_the_request_boundary_does_not_claim_it_left(self):
        err = _fence_blocked_error(["localhost"], ROSTER, left_browser=False)
        text = str(err)
        assert text.startswith("cdp_host_not_allowed: ")
        assert "ended before it left the browser" in text, text
        assert "already left the browser" not in text, (
            f"一句已经兑现不了的旧话又回来了：{text}")
        assert isinstance(err, CDPHostNotAllowedError)

    def test_the_result_boundary_still_says_it_left(self):
        """装不上那只闸的通道，承诺要退回原位 —— 不许顺手把没挣到的话说满。"""
        text = str(_fence_blocked_error(["localhost"], ROSTER, left_browser=True))
        assert "already left the browser" in text, text
        assert "ended before it left" not in text, text

    def test_the_way_out_survives_both_wordings(self):
        for left in (True, False):
            text = str(_fence_blocked_error(["localhost"], ROSTER, left_browser=left))
            assert "name it as the url with confirm=true" in text, text

    def test_every_blocked_host_is_named(self):
        text = str(_fence_blocked_error(["localhost", "other.example.test"],
                                        ROSTER, left_browser=False))
        assert "localhost" in text and "other.example.test" in text, text
        assert "['127.0.0.1']" in text, f"名册本身也要说：{text}"


@pytest.mark.asyncio
class TestTheLivenessProbeStaysOnTheHostItAskedAbout:
    """私网目标的探活不许跟着 302 走出名册 —— 它问的从来只是"这个端口有人应答吗"。

    实测（`.scratch/liveverify/fence_attrib.py` 按代码路径记账，两轮一致）：一条
    `127.0.0.1 → localhost` 的 302 在围栏掐掉浏览器那一跳的同时，仍被探活自己跟到了
    `localhost` 并读了正文（靶站日志里那句 `All Trafilatura methods failed for
    http://localhost:.../whoami` 就是它在解析名册外那一页）。判"活"不需要第二跳：3xx
    本身就是一声应答。
    """

    async def _probe(self, status):
        from dhole_mcp.server import MasterFetchServer, ResponseModel

        srv = MasterFetchServer(cache_ttl=0)
        seen: dict = {}

        async def fake_get(url, **kw):
            seen.update(kw)
            seen["url"] = url
            return ResponseModel(url=url, status=status, content=[], error="")

        srv.get = fake_get
        out = await srv._private_health_probe(
            "http://127.0.0.1:9222/fence", None, None, None, 0.0)
        return seen, out

    async def test_the_probe_asks_for_one_hop_only(self):
        seen, _out = await self._probe(302)
        assert seen.get("follow_redirects") is False, (
            f"探活跟了重定向 = 它替被探的那个主机又向外发了一笔：{seen}")

    async def test_a_redirect_still_counts_as_alive(self):
        """3xx 不跟也要算活着 —— 否则浏览器层被跳过，围栏那侧的事再也没人做。"""
        _seen, out = await self._probe(302)
        assert out is None, "3xx 被当成了目标不健康"

    async def test_a_dead_port_still_short_circuits(self):
        _seen, out = await self._probe(0)
        assert out is not None and "skipped_stealthy" in out.escalation_path


# ============================================================================
# 来源：test_browser_ssrf.py
# ============================================================================
# 浏览器层 SSRF 守卫：入口 URL 之外的每一跳都不走 validate_url，这里补一道。

ENTRY = "https://example.com/article"


# ─── 假 route / request（只喂 handler 真正用到的字段）────────────────────────

class _FakeRequest:
    def __init__(self, url: str, resource_type: str = "document",
                 navigation: bool = True, frame: Any = "main"):
        self.url = url
        self.resource_type = resource_type
        self._navigation = navigation
        self.frame = frame

    def is_navigation_request(self) -> bool:
        return self._navigation


class _FakeRoute:
    def __init__(self, url: str, resource_type: str = "document",
                 navigation: bool = True, frame: Any = "main"):
        self.request = _FakeRequest(url, resource_type, navigation, frame)
        self.action = ""
        self.aborted = False

    async def abort(self) -> None:
        self.action = "abort"
        self.aborted = True

    async def continue_(self) -> None:
        self.action = "continue"


def _run(handler, route: _FakeRoute) -> _FakeRoute:
    asyncio.run(handler(route))
    return route


class TestEntryTargetExemption:
    """入口站点自己豁免（含 http↔https 升级），异主机/异端口不豁免。"""

    def test_same_host_same_port_is_entry(self):
        assert _is_entry_target("https://example.com/other", ENTRY)

    def test_http_to_https_upgrade_is_entry(self):
        assert _is_entry_target("http://example.com/x", "http://example.com/x")
        assert _is_entry_target("https://example.com/x", "http://example.com/x")

    def test_same_host_other_port_is_not_entry(self):
        # localhost:8080 -> localhost:9222 是内网跳板的经典形态
        assert not _is_entry_target("http://localhost:9222/json", "http://localhost:8080/")
        assert not _is_entry_target("https://example.com:8443/x", ENTRY)

    def test_other_host_is_not_entry(self):
        assert not _is_entry_target("https://evil.test/x", ENTRY)


class TestRequestGuard:
    """请求前判定：内网目标 abort，正常目标 continue。"""

    def _handler(self, entry: str = ENTRY, watch: Optional[list] = None):
        return _create_route_handler(False, None, watch if watch is not None else [],
                                     entry_url=entry)

    def test_loopback_target_is_aborted_and_recorded(self):
        watch: list = []
        route = _run(self._handler(watch=watch), _FakeRoute("http://127.0.0.1:9222/json"))
        assert route.aborted, "回环目标必须被拦"
        assert watch and watch[0][0] == "127.0.0.1:9222" and watch[0][1] is True

    def test_private_and_metadata_and_variants_are_aborted(self):
        for u in ("http://169.254.169.254/latest/meta-data/",
                  "http://10.1.2.3/admin",
                  "http://192.168.1.1/",
                  "http://[::1]:8080/x",
                  "http://0177.0.0.1/",          # 八进制变体
                  "http://2130706433/",          # 十进制变体
                  "http://localhost/admin"):
            route = _run(self._handler(), _FakeRoute(u))
            assert route.aborted, f"{u} 必须被拦"

    def test_public_host_continues(self):
        route = _run(self._handler(),
                     _FakeRoute("https://example.org/asset.js",
                                resource_type="script", navigation=False))
        assert route.action == "continue", "公网目标被误杀"

    def test_entry_host_continues_even_if_internal(self):
        """入口站点豁免：本地/内网入口（hosts 钉位、开发环境）不该被自家守卫拒掉。"""
        route = _run(self._handler(entry="http://127.0.0.1:8080/page"),
                     _FakeRoute("http://127.0.0.1:8080/next"))
        assert not route.aborted, "同站点请求被误杀"

    def test_non_network_schemes_are_not_touched(self):
        """data:/blob: 不是网络请求 —— 拦它们只会弄坏页面（CF 挑战页自己就用）。"""
        for u in ("data:text/html,<b>x</b>", "blob:https://example.com/uuid", "about:blank"):
            route = _run(self._handler(),
                         _FakeRoute(u, resource_type="image", navigation=False))
            assert not route.aborted, f"{u} 不该被 SSRF 判定拦下"

    def test_disabled_resource_types_still_aborted(self):
        """资源拦截行为不变（守卫是叠加，不是替换）。"""
        handler = _create_route_handler(True, None, [], entry_url=ENTRY)
        route = _run(handler, _FakeRoute("https://cdn.example.org/a.png",
                                        resource_type="image", navigation=False))
        assert route.aborted

    def test_subresource_block_is_recorded_as_non_main(self):
        watch: list = []
        _run(self._handler(watch=watch),
             _FakeRoute("http://127.0.0.1:9/x", resource_type="xhr", navigation=False))
        assert watch == [("127.0.0.1:9", False)], "子资源不该被记成主文档（判罚口径不同）"


class TestLandedUrlRefused:
    """落地判定：内网落地要拒，入口站点落地要放。"""

    def test_internal_landing_is_refused(self):
        assert _landed_url_refused("http://127.0.0.1:9222/json", ENTRY)
        assert _landed_url_refused("http://169.254.169.254/", ENTRY)

    def test_entry_landing_is_allowed(self):
        assert not _landed_url_refused("http://127.0.0.1:8080/done", "http://127.0.0.1:8080/x")

    def test_public_landing_is_allowed(self):
        assert not _landed_url_refused("https://example.org/final", ENTRY)

    def test_non_http_landing_is_not_judged(self):
        assert not _landed_url_refused("about:blank", ENTRY)


# ─── 真浏览器 + 真 HTTP 服务的端到端证明（-m e2e）────────────────────────────

def _serve(handler_cls):
    import socketserver
    import threading

    class _S(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    srv = _S(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


@pytest.mark.e2e
class TestBrowserSSRFEndToEnd:
    """判红依据：内网服务器**一次都没被连上**（只断言"报错了"不算数）。"""

    @pytest.fixture
    def range_(self):
        import http.server

        hits: list = []

        class Inner(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                hits.append(self.path)
                body = b"<html><body>INTERNAL_SECRET_DATA</body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        inner_srv, inner_port = _serve(Inner)
        target = f"http://127.0.0.1:{inner_port}/internal-secret"

        class Outer(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == "/js":
                    html = (f"<html><body><script>location.href='{target}';</script>"
                            "<p>redirecting</p></body></html>")
                elif self.path == "/302":
                    self.send_response(302)
                    self.send_header("Location", target)
                    self.end_headers()
                    return
                elif self.path == "/fetch":
                    html = (f"<html><body><script>fetch('{target}').then(r=>r.text())"
                            ".catch(()=>{});</script><p>fetching</p></body></html>")
                elif self.path == "/iframe":
                    html = (f'<html><body><iframe src="{target}"></iframe>'
                            "<p>framing</p></body></html>")
                else:
                    html = f'<html><body><a href="{target}">x</a>PLAIN_OK</body></html>'
                body = html.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        outer_srv, outer_port = _serve(Outer)
        yield inner_port, outer_port, hits
        outer_srv.shutdown()
        inner_srv.shutdown()

    @staticmethod
    def _fetch(entry: str):
        from dhole_mcp.browser import StealthyBrowser

        async def _go():
            async with StealthyBrowser(headless=True, timeout=25000) as b:
                return await b.fetch(entry)

        return asyncio.run(_go())

    def test_server_side_redirect_is_refused_and_content_never_returned(self, range_):
        """HTTP 3xx：请求会发出去（Playwright 不拦截 route 已放行请求的重定向目标），
        但**内容一个字都不回流**。

        实测（2026-09-21，本机 patchright）：route handler 只看到入口那一次 document
        请求，302 的目标根本不经过它 —— 这是 Playwright 的拦截模型，不是我们漏判。
        要连"盲打请求"一起堵掉得在解析器/代理层做（`--host-resolver-rules` 或本地过滤
        代理），那两条都与 Cloudflare 求解这条唯一能力面冲突且无法在此验证，故不做；
        这里钉住底线：内容不回流 + 明确报错。
        """
        inner_port, outer_port, hits = range_
        from dhole_mcp.browser import BrowserSSRFBlockedError

        with pytest.raises(BrowserSSRFBlockedError) as ei:
            self._fetch(f"http://127.0.0.1:{outer_port}/302")
        assert "ssrf_blocked" in str(ei.value)
        assert str(inner_port) in str(ei.value), "错误里要指出被拒的内网目标"
        # 不断言 hits == []：这里它必然非空（见 docstring）。断言的是内容没回流 ——
        # 报错路径不会带回任何正文（调用方只拿到错误文本）。
        assert "INTERNAL_SECRET_DATA" not in str(ei.value)

    def test_js_redirect_to_internal_is_blocked_before_it_leaves(self, range_):
        inner_port, outer_port, hits = range_
        from dhole_mcp.browser import BrowserSSRFBlockedError

        with pytest.raises(BrowserSSRFBlockedError):
            self._fetch(f"http://127.0.0.1:{outer_port}/js")
        assert hits == [], f"内网服务器被连上了: {hits}"

    def test_page_script_fetch_to_internal_is_blocked(self, range_):
        """子资源路径：抓取照常完成，但内网请求一条都没出去，正文里也没有内网内容。"""
        inner_port, outer_port, hits = range_
        resp = self._fetch(f"http://127.0.0.1:{outer_port}/fetch")
        assert hits == [], f"页面 JS 的内网请求没被拦下: {hits}"
        assert b"INTERNAL_SECRET_DATA" not in resp.body

    def test_iframe_to_internal_is_blocked(self, range_):
        """iframe 的内嵌页面是最典型的内网读取姿势（同源策略帮不了跨源读取之外的忙）。"""
        inner_port, outer_port, hits = range_
        resp = self._fetch(f"http://127.0.0.1:{outer_port}/iframe")
        assert hits == [], f"iframe 的内网请求没被拦下: {hits}"
        assert b"INTERNAL_SECRET_DATA" not in resp.body

    def test_benign_local_entry_still_works(self, range_):
        """正对照：入口站点自己不被误杀（守卫不能把正常抓取一起挡掉）。"""
        inner_port, outer_port, _hits = range_
        resp = self._fetch(f"http://127.0.0.1:{outer_port}/plain")
        assert resp.status == 200 and b"PLAIN_OK" in resp.body
