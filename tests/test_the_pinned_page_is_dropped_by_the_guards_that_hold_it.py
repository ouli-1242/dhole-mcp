"""驻留的那一页由握着它的那几道闸门决定去留 —— 每一条都在这一页上钉住。

`pin=true` 买的是"下一次调用**站在这一页上**做事，不再 new_page、不再 goto"。这件事
一旦做错方向就是两件具体的坏事：

一、**该弃的时候没弃**。那一页可能已经过期（站点的会话超时把 token 换了）、可能被人
关了、可能站着的是另一个站点、可能它身上还挂着**上一发**的 route 与 response 监听器
（那个 route 里坐着上一次的 `entry_url` 和上一次的 SSRF 记录本）。带着上一次那张地图
开车是 SSRF 边界，不是性能问题 —— 所以摘钩摘不掉时这一刀的 chosen action 是**弃页**，
宁可重新导航一次，也不在旧守卫下行动。

二、**不该弃的时候弃了**，或者反过来：默认路径（没有 `pin`）必须与今天逐字一致 ——
每一发都关页，会话收尾时不留任何东西。

这里量的是浏览器层自己的判定（`_pin_take` / `_pin_close` / `_install_page_guards` 与
`fetch` 的复用分支）。参数怎么从 `web_act` 走到这一层、拒绝的话怎么出声，在
`test_a_pinned_page_is_the_one_the_next_call_stands_on.py`；"驻留页真的答对了上一发那道
题"在 live 那一组 `test_a_pinned_page_answers_the_captcha_the_previous_call_show_it.py`。
"""

from __future__ import annotations

import inspect
import time

import pytest

from dhole_mcp import browser as browser_mod
from dhole_mcp.browser import BrowserSession, PIN_TTL_S

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
        assert action.pin_state == {"reused": True, "pinned": True}, action.pin_state

    @pytest.mark.asyncio
    async def test_a_chain_on_a_pinning_call_is_told_the_page_will_stay(self, session):
        """第一次驻留的那一发也要知道：这一页不会跟着调用一起消失，所以提交闸那句
        "把同一串动作再跑一遍"从这一发起就只有半句是真话。"""
        def action(page):
            pass
        action.pin_state = {}
        await _fetch(session, page_action=action)
        assert action.pin_state == {"reused": False, "pinned": True}, action.pin_state

    @pytest.mark.asyncio
    async def test_a_chain_on_an_unpinned_call_is_told_nothing_was_kept(self, session):
        def action(page):
            pass
        action.pin_state = {}
        await session.fetch(URL, page_action=action)
        assert action.pin_state == {"reused": False, "pinned": False}, action.pin_state


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
