"""G11 回归：`scroll` 要真的把懒加载滚出来。

报告的可复现失败是 quotes.toscrape.com/scroll：`actions:[{wait_selector:".quote"},
{scroll:3}]` 被接受、不报错，页面停在首屏 10 条 + 「Loading...」。旧实现是
`scrollBy(0, innerHeight)` 再固定睡 700ms，两处都不对：

* 懒加载的判断条件是「离底还有多远」，而它每加载一段就把底又往下推一段 —— 一步滚
  一次只到**旧的**底，之后再也没有第二次请求。所以现在每检测到增长就再滚一次。
* 700ms 是对一次网络往返的猜测。真正该等的是「DOM 不再变化」，而且一旦安静下来就
  不必再等。

还有一个必须同时存在的约束：一条永远在加载的信息流不能变成一个永远不返回的请求，所
以整个 scroll 动作有总预算（SCROLL_TOTAL_MS）。
"""

from __future__ import annotations

import re

import pytest

from dhole_mcp import actions as actions_mod
from dhole_mcp.actions import (
    MAX_SCROLL,
    SCROLL_STEP_MS_MAX,
    _scroll,
    _validate_actions,
)


class TestTheShapesThatAreAccepted:

    def test_a_bare_number_still_means_steps(self):
        spec = _validate_actions([{"scroll": 3}])[0]["scroll"]
        assert spec["steps"] == 3
        assert spec["selector"] is None

    def test_a_container_and_a_wait_budget_can_be_named(self):
        spec = _validate_actions([{"scroll": {
            "steps": 5, "selector": ".feed", "ms_per_step": 4000}}])[0]["scroll"]
        assert spec == {"steps": 5, "selector": ".feed", "ms_per_step": 4000}

    def test_the_numbers_are_clamped_rather_than_obeyed(self):
        spec = _validate_actions([{"scroll": {"steps": 10_000, "ms_per_step": 9_000_000}}])[0]["scroll"]
        assert spec["steps"] == MAX_SCROLL
        assert spec["ms_per_step"] == SCROLL_STEP_MS_MAX

    @pytest.mark.parametrize("bad", [
        {"scroll": {"steps": 3, "selector": "   "}},
        {"scroll": {"steps": "many"}},
        {"scroll": {"steps": 3, "selector": 12}},
        {"scroll": {"ms_per_step": "fast"}},
        {"scroll": ["3"]},
    ])
    def test_a_scroll_that_cannot_be_run_is_refused_up_front(self, bad):
        # 一个跑不起来的动作值一整个浏览器调用的时间，所以它在发请求之前就要被拒。
        with pytest.raises(ValueError):
            _validate_actions([bad])

    def test_wait_selector_still_accepts_a_bare_selector_as_first_match(self):
        w = _validate_actions([{"wait_selector": ".item"}])[0]["wait_selector"]
        assert w["selector"] == ".item"
        assert w["count"] == 1
        # "attached" is what it always waited for; a lazy node is attached before
        # it is painted, so that default is the one that does not hang.
        assert w["state"] == "attached"

    def test_count_and_state_are_how_you_wait_for_a_loaded_list(self):
        w = _validate_actions([{"wait_selector": {
            "selector": ".quote", "count": 40, "state": "visible"}}])[0]["wait_selector"]
        assert (w["count"], w["state"]) == (40, "visible")

    @pytest.mark.parametrize("bad", [
        {"wait_selector": {"selector": ".x", "count": 0}},
        {"wait_selector": {"selector": ".x", "count": "lots"}},
        {"wait_selector": {"selector": ".x", "state": "shiny"}},
        {"wait_selector": {"count": 3}},
        {"wait_selector": {"selector": ".x", "state": "visible", "timeout_ms": None}},
    ])
    def test_a_wait_that_cannot_be_satisfied_is_refused(self, bad):
        with pytest.raises(ValueError):
            _validate_actions([bad])


class _Clock:
    """A clock that only moves when the code under test waits.

    `_scroll` mixes `time.monotonic()` (its budget) with `page.wait_for_timeout`
    (the polling pace), so with the real clock every one of these tests would
    spend its full 20 s budget asleep. Advancing the clock inside the fake sleep
    keeps the budget logic under test and the suite in milliseconds.
    """

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, ms):
        self.now += ms / 1000.0


@pytest.fixture
def clock(monkeypatch):
    import types

    c = _Clock()
    monkeypatch.setattr(actions_mod, "time", types.SimpleNamespace(monotonic=c.monotonic))
    return c


class _Page:
    """A page whose DOM grows only while it is being scrolled to a moving bottom.

    `grow_for` is the lazy-loader model the old code could not satisfy: the bottom
    recedes each time a chunk lands, so one scroll per step never asks again.
    """

    def __init__(self, clock, growth_per_scroll=1, grow_for=3, height=1000):
        self.clock = clock
        self.top = 0
        self.height = height
        self.nodes = 10
        self.scrolls = 0
        self.measures = 0
        self.slept_ms = 0
        self.growth_per_scroll = growth_per_scroll
        self.grow_for = grow_for

    async def evaluate(self, js, arg=None):
        # `_MEASURE_JS` mentions scrollTop, so key off what only the scroll
        # script returns.
        if "return true" in js:
            self.scrolls += 1
            self.top = self.height
            if self.scrolls <= self.grow_for:
                self.height += 500
                self.nodes += self.growth_per_scroll
            return True
        self.measures += 1
        return [self.top, self.height, self.nodes]

    async def wait_for_timeout(self, ms):
        self.slept_ms += ms
        self.clock.sleep(ms)


class TestTheLoopThatChasesTheBottom:

    @pytest.mark.asyncio
    async def test_a_growing_page_is_scrolled_again_rather_than_once(self, clock):
        page = _Page(clock, grow_for=3)

        await _scroll(page, steps=6, selector=None, ms_per_step=2000)

        assert page.scrolls >= 4, (page.scrolls, "one scroll per step is the old bug")
        assert page.nodes > 10

    @pytest.mark.asyncio
    async def test_a_quiet_page_stops_without_spending_its_budget(self, clock):
        page = _Page(clock, grow_for=0)

        await _scroll(page, steps=5, selector=None, ms_per_step=2000)

        # 5 steps × 2s would be 10s of waiting on a page that already went quiet;
        # the settle check gives up after two identical reads.
        assert page.slept_ms <= 5 * 750, page.slept_ms
        # One re-scroll per step is allowed on top of the step itself: reaching
        # the bottom moves scrollTop, which reads as growth once. Re-scrolling an
        # already-bottomed page is idempotent, so this is not a wasted request.
        assert 5 <= page.scrolls <= 10, page.scrolls

    @pytest.mark.asyncio
    async def test_a_page_that_never_stops_getting_bigger_is_abandoned(self, clock):
        """懒加载的信息流可以无限长。没有总预算的话这就是一次永不返回的调用。"""
        started = clock.now
        page = _Page(clock, grow_for=10_000)

        await _scroll(page, steps=50, selector=None, ms_per_step=SCROLL_STEP_MS_MAX)

        assert clock.now - started <= actions_mod.SCROLL_TOTAL_MS / 1000.0 + 0.5
        assert page.scrolls > 50, "it should have got real work done before stopping"

    @pytest.mark.asyncio
    async def test_a_container_is_scrolled_instead_of_the_window(self, clock):
        seen: list = []

        class _ContainerPage(_Page):
            async def evaluate(self, js, arg=None):
                seen.append(arg)
                return await super().evaluate(js, arg)

        page = _ContainerPage(clock, grow_for=1)
        await _scroll(page, steps=2, selector=".feed", ms_per_step=500)

        assert set(seen) == {".feed"}, set(seen)


@pytest.mark.live
@pytest.mark.asyncio
async def test_the_real_lazy_load_page_lets_more_than_its_first_screen_out():
    """报告就是从这个页面发起的，所以证据也必须来自它。

    只断言「比首屏多」：站方的分页数量会变，而这条要证的是「滚动把懒加载触发了」。
    """
    from dhole_mcp.server import MasterFetchServer

    plain = await MasterFetchServer().smart_fetch(
        "https://quotes.toscrape.com/scroll", extraction_type="html",
        force_fetcher="stealthy", cache_ttl=0, timeout=60000)
    scrolled = await MasterFetchServer().smart_fetch(
        "https://quotes.toscrape.com/scroll", extraction_type="html",
        actions=[{"scroll": {"steps": 6, "ms_per_step": 3000}}],
        force_fetcher="stealthy", cache_ttl=0, timeout=90000)

    def quotes(result) -> int:
        return len(re.findall(r'class="quote"', "".join(result.content or [])))

    assert quotes(plain) > 0, plain.error
    assert quotes(scrolled) > quotes(plain), (
        f"{quotes(plain)} -> {quotes(scrolled)}: scroll did not load more")
