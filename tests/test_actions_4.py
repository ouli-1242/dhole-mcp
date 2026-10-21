"""test_actions_4.py —— 由 5 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import asyncio
import pytest
from dhole_mcp.actions import build_page_action
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _over_budget_result, _report_action_outcomes
import re
from dhole_mcp import actions as actions_mod
from dhole_mcp.actions import MAX_SCROLL, SCROLL_STEP_MS_MAX, _scroll, _validate_actions
from pathlib import Path
from types import SimpleNamespace
from dhole_mcp import context as ctx
from dhole_mcp import quality
from dhole_mcp.actions import BACKSTOP_GRACE_S, CLICK_SETTLE_MS, STEP_MIN_MS, STEP_WAIT_MS, _step_deadline_ms
from dhole_mcp.context import call_deadline
from dhole_mcp.actions import _resolve_save_to, _write_screenshot
from dhole_mcp.server import MasterFetchServer


# ============================================================================
# 来源：test_a_step_killed_by_the_budget_leaves_its_own_row.py
# ============================================================================
# 被预算掐断的那一步必须留下自己的一行 —— 不然"跑到第几步"这件事根本没处查。

# ─── 一页：解析到第二个选择器时，执行中的链被掐死 ───────────────────


class _Ok:
    """一步能成的动作只需要这些器官（`read` 走 inner_text）。"""

    @property
    def first(self):
        return self

    async def inner_text(self, timeout=None):
        return "正文"


class _Page:
    """`locator()` 是 `_target_locator` 踏进页面后的第一个调用，所以取消从这里抛
    最省事 —— 不必猜 click 内部还调了哪几颗。"""

    def locator(self, sel):
        if sel == "#boom":
            # 真机上这一步不是"失败"，是被取消：await 正在等的时候任务被掐。
            raise asyncio.CancelledError()
        return _Ok()


def _run(chain, page):
    """跑到被掐为止，把 CancelledError 接回来交给调用方断言。"""
    pa = build_page_action(chain)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(pa(page))
    return pa


def _cut(stage="actions (stealthy) tier"):
    return _over_budget_result("http://127.0.0.1:8399/captcha", 60000, 61529,
                               stage, "")


# ─── 1 · 被掐的那一步留下自己的行 ──────────────────────────────────


def test_a_step_killed_by_the_budget_leaves_its_own_row():
    pa = _run([{"read": "#p"}, {"click": "#boom"}], _Page())
    rows = pa.outcomes
    assert len(rows) == 2, "第一步成了就该有一行，第二步被掐也该有一行"
    assert rows[0]["ok"] is True
    assert rows[1]["ok"] is False
    assert rows[1]["cut_by_budget"] is True


def test_the_cut_row_says_which_step_of_how_many():
    pa = _run([{"read": "#p"}, {"click": "#boom"}], _Page())
    row = pa.outcomes[1]
    assert row["step"] == "2/2", row
    assert "click '#boom'" in row["action"]


def test_the_cut_row_says_the_maybe_done_half_rather_than_a_clean_failure():
    """一步被掐 ≠ 一步没跑：它的效果可能已经落了一半在页面上，这必须说出口。"""
    pa = _run([{"click": "#boom"}], _Page())
    assert "never finished" in pa.outcomes[0]["error"]
    assert "half-done" in pa.outcomes[0]["error"]


def test_the_chain_still_reports_how_many_steps_it_was_asked_to_walk():
    pa = _run([{"read": "#p"}, {"click": "#boom"}], _Page())
    assert pa.total_steps == 2
    assert len(pa.outcomes) == 2, "被掐时只走到第 2 步，行数不该假装是 3"


# ─── 2 · 取消照原样交回，不接成假成功 ──────────────────────────────


def test_cancellation_is_handed_back_not_swallowed():
    """这一刀只加一行，不改截止时间：外面那道 backstop 才是建超时响应的人。"""
    pa = build_page_action([{"click": "#boom"}])
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(pa(_Page()))


def test_a_step_that_fails_normally_still_takes_the_old_branch():
    """反面守卫：普通失败（元素找不到）不许被写成"被预算掐了"。"""
    class _NoSuchPage:
        def locator(self, sel):
            raise RuntimeError("waiting for locator")

    pa = build_page_action([{"click": "#absent"}])
    asyncio.run(pa(_NoSuchPage()))
    row = pa.outcomes[0]
    assert row["ok"] is False and "cut_by_budget" not in row


# ─── 3 · 断在第几步要上到 summary ─────────────────────────────────


def test_the_summary_says_where_the_chain_stopped():
    r = _cut()
    r.summary = "out of time · 0B markdown"
    pa = _run([{"read": "#p"}, {"click": "#boom"}], _Page())
    _report_action_outcomes(r, pa)
    assert "chain cut by the budget at step 2/2" in r.summary, r.summary
    assert "1 step(s) landed before it" in r.summary, r.summary


def test_a_single_step_chain_cut_reports_zero_landed_not_a_claim_of_progress():
    """体测里那一格就是这条：唯一一步被掐，0 步落地。"""
    r = _cut()
    r.summary = "out of time · 0B markdown"
    pa = _run([{"click": "#boom"}], _Page())
    _report_action_outcomes(r, pa)
    assert "at step 1/1" in r.summary, r.summary
    assert "0 step(s) landed" in r.summary, r.summary
    assert "already ran" not in r.summary, "没有一步跑成，不许说跑成了"


def test_the_cut_receipt_reaches_the_response_metadata():
    """回执存在不等于看得见：这一行得真的进 metadata.actions。"""
    r = _cut()
    pa = _run([{"read": "#p"}, {"click": "#boom"}], _Page())
    _report_action_outcomes(r, pa)
    rows = r.metadata["actions"]
    assert rows[1]["cut_by_budget"] is True and rows[1]["step"] == "2/2"


# ─── 4 · 已经落地的步骤不许被一句模板话抹掉 ────────────────────────


def test_a_partial_chain_is_not_described_as_never_having_happened():
    """`fill` 成了、`read` 失败 —— 那句 "these interactions never happened" 是假的。"""
    r = ResponseModel(url="http://127.0.0.1:8399/checkout", status=200, content=["x"])
    r.summary = "200 OK"
    r.next_action = ""  # 只有没人给过建议时，那一句才会被写出口
    pa = type("PA", (), {"outcomes": [
        {"action": "fill '#qty'", "ok": True},
        {"action": "read '#qty'", "ok": False, "error": "read found no text"}],
        "total_steps": 2})()
    _report_action_outcomes(r, pa)
    # 要禁的是那句**整条链**的 blanket 话（"these interactions never happened"）；
    # "失败的那一步没发生过"这一句是真话，也是 `test_extended_report_regressions.py`
    # 从另一边钉着的 —— 两个方向共用一句判据时，needle 必须精确到那一支说的是哪些步骤。
    assert "these interactions never happened" not in r.next_action, r.next_action
    assert "the failed one never happened" in r.next_action, r.next_action
    assert "DID take effect" in r.next_action
    assert "fill '#qty'" in r.next_action


def test_a_chain_where_nothing_ran_keeps_the_clean_slate_sentence():
    """反面守卫：一步都没落地的时候，那句"页面还是它们之前的样子"是真话。"""
    r = ResponseModel(url="http://127.0.0.1:8399/checkout", status=200, content=["x"])
    r.summary = "200 OK"
    r.next_action = ""
    pa = type("PA", (), {"outcomes": [
        {"action": "click '#nope'", "ok": False, "error": "no element"}],
        "total_steps": 1})()
    _report_action_outcomes(r, pa)
    assert "never happened" in r.next_action, r.next_action


def test_a_finished_chain_is_never_described_as_cut():
    r = ResponseModel(url="http://127.0.0.1:8399/checkout", status=200, content=["x"])
    r.summary = "200 OK · markdown"
    pa = type("PA", (), {"outcomes": [{"action": "wait 500", "ok": True}],
                         "total_steps": 1})()
    _report_action_outcomes(r, pa)
    assert "chain cut" not in r.summary, r.summary


# ─── 5 · 新键得写在人查字段的地方 ─────────────────────────────────


def test_the_cut_keys_are_documented_where_a_field_is_looked_up():
    from dhole_mcp import guide
    fields = (guide._ROOT / "references" / "fields.md").read_text(encoding="utf-8")
    for needle in ("cut_by_budget", "step"):
        assert needle in fields, needle


# ============================================================================
# 来源：test_scroll_g11.py
# ============================================================================
# G11 回归：`scroll` 要真的把懒加载滚出来。
# 重名消歧：_Page → _Page__scroll_g11

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


class _Page__scroll_g11:
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
        page = _Page__scroll_g11(clock, grow_for=3)

        await _scroll(page, steps=6, selector=None, ms_per_step=2000)

        assert page.scrolls >= 4, (page.scrolls, "one scroll per step is the old bug")
        assert page.nodes > 10

    @pytest.mark.asyncio
    async def test_a_quiet_page_stops_without_spending_its_budget(self, clock):
        page = _Page__scroll_g11(clock, grow_for=0)

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
        page = _Page__scroll_g11(clock, grow_for=10_000)

        await _scroll(page, steps=50, selector=None, ms_per_step=SCROLL_STEP_MS_MAX)

        assert clock.now - started <= actions_mod.SCROLL_TOTAL_MS / 1000.0 + 0.5
        assert page.scrolls > 50, "it should have got real work done before stopping"

    @pytest.mark.asyncio
    async def test_a_container_is_scrolled_instead_of_the_window(self, clock):
        seen: list = []

        class _ContainerPage(_Page__scroll_g11):
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

    plain = await MasterFetchServer().web_read(
        "https://quotes.toscrape.com/scroll", extraction_type="html",
        force_fetcher="stealthy", cache_ttl=0, timeout=60000)
    scrolled = await MasterFetchServer().web_read(
        "https://quotes.toscrape.com/scroll", extraction_type="html",
        actions=[{"scroll": {"steps": 6, "ms_per_step": 3000}}],
        force_fetcher="stealthy", cache_ttl=0, timeout=90000)

    def quotes(result) -> int:
        return len(re.findall(r'class="quote"', "".join(result.content or [])))

    assert quotes(plain) > 0, plain.error
    assert quotes(scrolled) > quotes(plain), (
        f"{quotes(plain)} -> {quotes(scrolled)}: scroll did not load more")


# ============================================================================
# 来源：test_no_single_action_can_outrun_the_budget_the_caller_named.py
# ============================================================================
# 链里没有一步能跑过调用者点名的那笔预算 —— 每一步的截止跟着**剩下的**走。
# 重名消歧：_Page → _Page__no_single_action_can_out_f3b2、clock → clock__no_single_action_can_out_f3b2

ACTIONS_SRC = (Path(__file__).resolve().parents[1] / "src" / "dhole_mcp"
               / "actions.py").read_text(encoding="utf-8")

GRACE_MS = int(BACKSTOP_GRACE_S * 1000)


@pytest.fixture
def clock__no_single_action_can_out_f3b2(monkeypatch):
    """把 `context` 读的那颗时钟钉住。

    不是为了省事：墙钟每走一毫秒，`int(left * 1000)` 就少一位，于是"剩 5 秒应当给 3500"
    这一类断言会红在算术之外的那一位上。钉住之后红了才是接错线。
    """
    now = [1_000.0]
    monkeypatch.setattr(ctx, "time", SimpleNamespace(time=lambda: now[0]))
    return now


# ─── 1 · 算术：取小，不是改写 ────────────────────────────────────

def test_no_budget_named_means_the_number_that_already_shipped():
    """不在预算作用域里时原样交回 —— CLI、直连 dispatch 与单测今天就是这样跑的。"""
    assert ctx._CALL_DEADLINE.get() is None, "上一条用例漏了摘那颗 ContextVar"
    assert _step_deadline_ms() == STEP_WAIT_MS
    assert _step_deadline_ms(4_000) == 4_000
    assert _step_deadline_ms(CLICK_SETTLE_MS) == CLICK_SETTLE_MS


def test_a_budget_bigger_than_the_step_shortens_nothing(clock__no_single_action_can_out_f3b2):
    with call_deadline(60):
        assert _step_deadline_ms() == STEP_WAIT_MS, "宽裕的预算不该改变任何一步"


def test_a_tight_budget_shortens_the_step_to_what_is_left(clock__no_single_action_can_out_f3b2):
    with call_deadline(5):
        assert _step_deadline_ms() == 5_000 - GRACE_MS, (
            "剩 5 秒的调用里一步等 10 秒，就是让它去撞外面那道兜底")


def test_the_step_finishes_strictly_inside_the_budget_it_was_given(clock__no_single_action_can_out_f3b2):
    """这一步 + 交账宽限 必须正好落在余额之内 —— 否则"收窄"两个字没有内容。"""
    for seconds in (2.0, 3.0, 5.0, 8.0, 20.0, 45.0):
        with call_deadline(seconds):
            step_ms = _step_deadline_ms()
        left_ms = int(seconds * 1000)
        if step_ms < STEP_WAIT_MS:      # 真的被收窄的那些格
            assert step_ms + GRACE_MS == left_ms, (seconds, step_ms)
            assert step_ms >= STEP_MIN_MS, (seconds, step_ms)
        else:                           # 没被收窄的那些格也要说清是哪一种
            assert (left_ms - GRACE_MS >= STEP_WAIT_MS          # 余额宽裕
                    or left_ms - GRACE_MS < STEP_MIN_MS), (seconds, step_ms)
            # 后一种"不够一步"交回原值是刻意的：见 exhausted-budget 那条的理由


def test_an_exhausted_budget_is_not_answered_with_a_fake_one_millisecond_wait(clock__no_single_action_can_out_f3b2):
    """不够一步时交回原值：真话只有一句"预算用完"，而那句话由兜底来说（附 1.2 那一行）。

    反过来说，1 毫秒的等待一定会超时，而那条超时在回执里长得和"站点没响应"一模一样 ——
    那才是这一族最贵的谎。
    """
    with call_deadline(1.0):
        assert _step_deadline_ms() == STEP_WAIT_MS
    with call_deadline(2.4):
        # cap = 2400 − 1500 = 900 < STEP_MIN_MS：这一步已经没有意义，交回原值让兜底说真话
        assert _step_deadline_ms() == STEP_WAIT_MS, "不够一步也不许冒充站点慢"


def test_the_floor_is_named_because_a_step_of_zero_is_a_different_bug(clock__no_single_action_can_out_f3b2):
    assert STEP_MIN_MS < STEP_WAIT_MS and STEP_MIN_MS > 0
    with call_deadline((STEP_MIN_MS + GRACE_MS) / 1000.0 + 2):
        # 剩 4.5 秒：cap = 4500 − 1500 = 3000 ≥ STEP_MIN_MS，收窄照走
        assert _step_deadline_ms() == 3_000
    with call_deadline((STEP_MIN_MS + GRACE_MS) / 1000.0):
        # 剩 2.5 秒：cap 正好等于 STEP_MIN_MS —— 边界在"够"的那一侧
        assert _step_deadline_ms() == STEP_MIN_MS


# ─── 2 · 接线：那个数真的交到 playwright 手上 ──────────────────────

class _RecordingLocator:
    def __init__(self, sink):
        self._sink = sink

    @property
    def first(self):
        return self

    async def hover(self, timeout=None):
        self._sink.append(("hover", timeout))

    async def fill(self, value, timeout=None):
        self._sink.append(("fill", timeout))

    async def inner_text(self, timeout=None):
        self._sink.append(("inner_text", timeout))
        return "正文"

    async def wait_for(self, state=None, timeout=None):
        self._sink.append(("wait_for", timeout))


class _Page__no_single_action_can_out_f3b2:
    """hover 之后链里要的是 `_settle_quiet`：两次 poll 的 DOM 记号 + 一小步 sleep。"""

    def __init__(self):
        self.sink = []

    def locator(self, sel):
        return _RecordingLocator(self.sink)

    async def evaluate(self, script, arg=None):
        return ("https://example.test/p", 10, 100)

    async def wait_for_timeout(self, ms):
        await asyncio.sleep(0)


def test_the_page_is_asked_for_the_capped_time_not_the_literal(clock__no_single_action_can_out_f3b2):
    page = _Page__no_single_action_can_out_f3b2()
    with call_deadline(5):
        asyncio.run(build_page_action([{"hover": {"selector": "#x"}}])(page))
    assert [c for c in page.sink if c[0] == "hover"] == [("hover", 5_000 - GRACE_MS)], (
        "算术对了而页面拿到的还是那颗字面量 10_000 —— 这一刀就只活在函数里")


def test_the_same_step_takes_the_full_default_when_nothing_was_named():
    page = _Page__no_single_action_can_out_f3b2()
    asyncio.run(build_page_action([{"hover": {"selector": "#x"}}])(page))
    assert [c for c in page.sink if c[0] == "hover"] == [("hover", STEP_WAIT_MS)]


# ─── 3 · 静态：不许留第二颗字面量，也不许留第二个作者 ────────────────

def test_no_action_waits_on_a_literal_default_anymore():
    for shape in ("timeout=10_000", '"timeout": 10_000'):
        assert shape not in ACTIONS_SRC, (
            f"链里还有一颗 {shape}：那颗等待不看预算，正是场景 10 的机制")


def test_every_waiting_site_goes_through_the_one_helper():
    """数一数字面量原来落在几处，今天就该有几处走同一把尺 —— 少一处是漏接，不报错。"""
    sites = len(re.findall(r"_step_deadline_ms\(", ACTIONS_SRC))
    # 8 颗原字面量（click/fill/hover/drag/press×2/wait_for/inner_text）＋ 截图那颗
    # ＋ wait_selector 两处 ＋ click 的 settle ＋ scroll 的总预算 － 定义与默认参本身
    assert sites >= 13, f"接线的站点只剩 {sites} 处：某一族动作又自由了"


def test_the_scroll_and_click_settle_are_on_the_same_clock():
    assert "_step_deadline_ms(SCROLL_TOTAL_MS)" in ACTIONS_SRC, (
        "scroll 那 20 秒的一段不看预算：它是最容易越界的一族（一步 2 秒 × 20 秒）")
    assert "_step_deadline_ms(CLICK_SETTLE_MS)" in ACTIONS_SRC, (
        "click 之后那 4 秒是**第二笔**等待：click 花掉一截之后它拿的应当是剩下的")


def test_the_grace_has_one_author_and_three_readers():
    assert quality._BACKSTOP_GRACE_S is BACKSTOP_GRACE_S, (
        "交账宽限被抄成了第二颗数 —— 那一步的截止与兜底就会各自决定什么时候算超时")
    from dhole_mcp import server
    assert server._BACKSTOP_GRACE_S is BACKSTOP_GRACE_S


def test_an_explicit_wait_is_the_callers_own_number_and_stays_that_way():
    """故意的边界（判据 5）：`{"wait": ms}` 不收窄。

    收窄一条已经说出口的指令，比让它撞兜底更坏 —— 撞上的那条路有 1.2 那一行写着
    "这一步没跑完"，而悄悄把 30 秒改成 3 秒没有任何一句真话可说。
    """
    assert 'await page.wait_for_timeout(a["wait"])' in ACTIONS_SRC


# ============================================================================
# 来源：test_screenshot_save_g10.py
# ============================================================================
# G10 回归：截图落盘 —— 看不见图片的调用方也要拿得到那张图。

# A1：save_to 的家从 web_screenshot 搬到了 web_act 的截图动作，helper 随迁。

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class TestThePathIsCheckedBeforeTheBrowserStarts:

    def test_parents_are_created(self, tmp_path):
        target = tmp_path / "deep" / "nested" / "shot.png"
        assert not target.parent.exists()
        path = _write_screenshot(PNG, _resolve_save_to(str(target), "png"))
        assert target.read_bytes() == PNG
        assert path == str(target.resolve())

    def test_the_returned_path_is_absolute(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = _write_screenshot(PNG, _resolve_save_to("shot.png", "png"))
        assert path == str(tmp_path / "shot.png")
        assert (tmp_path / "shot.png").exists()

    def test_a_home_shortcut_is_expanded_rather_than_written_literally(self):
        """只解析、不落盘（这里没有 tmp_path 可断言）：真正的等价物是「返回的路径
        不再带 ~，而且落在展开后的家目录下」。"""
        import os
        from pathlib import Path

        target = _resolve_save_to("~/dhole-g10-probe.png", "png")
        assert not str(target).startswith("~")
        assert Path(os.path.expanduser("~/dhole-g10-probe.png")) == target
        assert not target.exists(), "resolving must not create anything"

    @pytest.mark.parametrize("bad", ["", "   ", "./"])
    def test_a_path_that_is_not_a_file_target_is_refused(self, bad, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError):
            _resolve_save_to(bad, "png")

    def test_a_directory_is_refused_rather_than_written_into(self, tmp_path):
        with pytest.raises(ValueError) as e:
            _resolve_save_to(str(tmp_path), "png")
        assert "directory" in str(e.value)


class TestRetakingAShotDoesNotEatADocument:

    def test_an_existing_image_is_overwritten_because_that_is_the_point(self, tmp_path):
        """同一个名字重拍是正常用法（改完样式再来一张）。拦下来只会逼人换个
        越来越长的文件名。"""
        target = tmp_path / "shot.png"
        target.write_bytes(b"old bytes")
        _write_screenshot(PNG, _resolve_save_to(str(target), "png"))
        assert target.read_bytes() == PNG

    def test_a_file_that_is_not_named_like_an_image_is_left_alone(self, tmp_path):
        notes = tmp_path / "notes.txt"
        notes.write_text("the only copy", encoding="utf-8")

        with pytest.raises(ValueError) as e:
            _resolve_save_to(str(notes), "png")

        assert "refusing to overwrite" in str(e.value)
        assert notes.read_text(encoding="utf-8") == "the only copy"
        assert ".png" in str(e.value), "报错要说清改成什么才通过"

    def test_a_new_file_that_is_not_named_like_an_image_is_refused_too(self, tmp_path):
        """后缀守卫不以「文件已存在」为前提（G25 评审）：描述说 a non-image file
        name is refused，那就无论存在与否都拒——否则 save_to 是一扇无门的
        「任意新路径写字节」原语，往 Startup 目录塞 .bat 的路是敞开的。"""
        target = tmp_path / "startup" / "task.bat"

        with pytest.raises(ValueError) as e:
            _resolve_save_to(str(target), "png")

        assert "refusing to create" in str(e.value)
        assert "not named like an image" in str(e.value)
        assert ".png" in str(e.value), "报错要说清改成什么才通过"
        assert not target.parent.exists(), "校验不许顺手建目录"

    def test_a_jpeg_named_target_may_hold_jpeg_bytes(self, tmp_path):
        """image_type=jpeg 写 .jpg 是同一个动作，不该被后缀守卫拦下。"""
        shot = tmp_path / "a.jpg"
        _write_screenshot(b"\xff\xd8\xff", _resolve_save_to(str(shot), "jpeg"))
        assert shot.read_bytes() == b"\xff\xd8\xff"

    def test_an_unwritable_target_is_a_value_error_not_a_traceback(self, tmp_path):
        """父目录建不了要成为可读的错误，而不是 OSError 冒到工具层——这里的形状
        是「一个叫 blocker 的文件挡在了中间目录的位置上」。"""
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")

        with pytest.raises(ValueError) as e:
            _write_screenshot(PNG, _resolve_save_to(str(blocker / "s.png"), "png"))

        assert "could not write" in str(e.value)


class TestTheAnnotationsTellTheTruth:
    """注解是宿主决定「要不要先问用户」的依据：readOnlyHint=True 的工具会被
    自动放行，而 save_to 写盘、anti_detect 答 Cloudflare 挑战都不是只读
    （G25 评审：注解撒谎等于给写原语开免审通道）。"""

    def test_web_act_is_not_read_only_on_the_wire(self):
        """A1：截图能力住进 web_act；"save_to 写盘、anti_detect 答挑战都不是
        只读"的注解诚实现在由 web_act 这扇门继续承担。"""
        defs = {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}
        ann = defs["web_act"]["annotations"]
        assert ann["readOnlyHint"] is False
        assert ann["idempotentHint"] is False
        assert ann["openWorldHint"] is True
        assert "web_screenshot" not in defs


class TestScreenshotUsesTheKnobItDocuments:

    def test_a_bad_save_to_refuses_at_action_validation(self, tmp_path):
        """A1：校验在动作归一化层发生 —— 那是浏览器冷启动之前。"""
        with pytest.raises(ValueError) as e:
            build_page_action([{"screenshot": {"save_to": str(tmp_path)}}])
        assert "directory" in str(e.value)
        with pytest.raises(ValueError) as e:
            build_page_action([{"screenshot": {"save_to": str(tmp_path / "a.png")}}])
        assert "selector" in str(e.value), "既不落盘也不裁元素的截图没有意义"

    @pytest.mark.asyncio
    async def test_the_saved_path_lands_in_the_action_receipt(self, tmp_path):
        """文本段是非多模态客户端唯一能读的那一半：A1 之后路径进的是动作回执行
        （metadata.actions → summary/next_action 的原料），字节一律不搭 transcript。"""
        target = tmp_path / "page.png"
        pa = build_page_action([{"screenshot": {"save_to": str(target),
                                                "full_page": True}}])

        class _Page:
            url = "https://example.com/"

            async def screenshot(self, **kwargs):
                assert kwargs.get("full_page") is True
                return PNG

        await pa(_Page())

        (row,) = pa.outcomes
        assert row["ok"] is True
        assert "image_b64" not in row
        assert target.name in row["saved"]
        assert row["saved_bytes"] == len(PNG)
        assert target.read_bytes() == PNG


@pytest.mark.live
@pytest.mark.asyncio
async def test_a_real_page_lands_on_disk_as_a_real_png(tmp_path):
    """真浏览器、真页面：证明的不是「write_bytes 被调了」（上面已经证了），而是
    落下来的东西是一个能被打开的图片文件，且路径出现在文本段里。"""
    from dhole_mcp.browser import check_browser_available

    if not check_browser_available():
        pytest.skip("no browser deps for the live tier")

    try:
        out = await MasterFetchServer().web_screenshot(
            "https://example.com/", save_to=str(tmp_path / "real.png"), timeout=60000)
    except RuntimeError as e:
        # patchright 装了但 chromium 没装（`python -m patchright install chromium`
        # 是 [all] 之外的一步）——那是环境缺件，不是这个功能坏了。
        if "executable" in str(e).lower() or "install" in str(e).lower():
            pytest.skip(f"no browser binary: {str(e)[:120]}")
        raise

    text = [c for c in out if getattr(c, "type", "") == "text"][0].text
    written = tmp_path / "real.png"
    assert written.exists(), text
    assert written.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    assert written.stat().st_size > 1000, "a 1KB screenshot is a blank frame"
    assert str(written.resolve()) in text


# ============================================================================
# 来源：test_the_screenshot_validator_gives_one_rule_to_four_shapes.py
# ============================================================================
# R2刀3（P2-8）：`{"screenshot": ...}` 的三条校验自相矛盾，同一件事有三种说法。

LEGAL = [
    ("h1", {"selector": "h1", "image_type": "png", "decode_qr": False}),
    (2, {"index": 2, "image_type": "png", "decode_qr": False}),
    ({"selector": "h1"}, {"selector": "h1", "image_type": "png",
                          "decode_qr": False}),
    ({"index": 2}, {"index": 2, "image_type": "png", "decode_qr": False}),
    ({"full_page": True}, {"full_page": True, "image_type": "png",
                           "decode_qr": False}),
]


def _err(val):
    with pytest.raises(ValueError) as e:
        _validate_actions([{"screenshot": val}])
    return str(e.value)


class TestTheAcceptedShapes:
    """每一种合法形状各一条：说出规则就得接住规则自己列的每一种写法。"""

    @pytest.mark.parametrize("val,expected", LEGAL)
    def test_each_shape_is_accepted(self, val, expected):
        (action,) = _validate_actions([{"screenshot": val}])
        assert action["screenshot"] == expected

    def test_row_zero_is_a_row_not_an_absence(self):
        """0 是 snapshot 的第一行，不是"没有目标"：falsy 不许把它吃掉。"""
        (action,) = _validate_actions([{"screenshot": 0}])
        assert action["screenshot"]["index"] == 0, action

    def test_full_page_needs_no_target(self):
        """第 3 条读数本来就是对的：整页截图没有元素可言，不该被要求指一个元素。"""
        (action,) = _validate_actions([{"screenshot": {"full_page": True}}])
        assert action["screenshot"]["full_page"] is True


class TestTheRefusalsSayOneThing:
    @pytest.mark.parametrize("val", [True, 3.5, "  ", [1],
                                     {"decode_qr": True}, {}, {"foo": 1}])
    def test_refused(self, val):
        with pytest.raises(ValueError):
            _validate_actions([{"screenshot": val}])

    def test_bare_true_and_a_targetless_dict_get_the_same_sentence(self):
        """同一类输入只有一种答案：两条拒绝路径必须念同一句话。"""
        assert _err(True) == _err({"decode_qr": True})

    def test_the_sentence_lists_every_shape_the_validator_accepts(self):
        text = _err(True)
        for needle in ("selector", "index", "full_page"):
            assert needle in text, text
        # 刀9-6c 之后这句还许得出裸行号 —— 列形状的那句漏一种，就等于教人再撞一次
        assert "bare snapshot row" in text, text

    def test_no_python_brace_artifact(self):
        """`{{` 是字符串拼接漏出的伪影，不是给调用方的语法。"""
        for val in (True, {"decode_qr": True}, 3.5):
            assert "{{" not in _err(val) and "}}" not in _err(val)

    def test_the_rule_names_the_optional_knobs_too(self):
        """旧句子里唯一有用的信息（可选旋钮名单）不许在收敛时被顺手删掉。"""
        text = _err(True)
        for needle in ("image_type", "decode_qr", "save_to"):
            assert needle in text, text

    def test_the_action_index_survives(self):
        with pytest.raises(ValueError) as e:
            _validate_actions([{"click": "a"}, {"screenshot": True}])
        assert "action 1" in str(e.value)


class TestTheBoundariesThatWereAlreadyRight:
    """收敛报错不许顺手改掉既有裁决。"""

    def test_full_page_with_a_target_is_still_refused(self):
        with pytest.raises(ValueError, match="cannot combine"):
            _validate_actions([{"screenshot": {"full_page": True,
                                               "selector": "h1"}}])

    def test_selector_and_index_together_is_still_refused(self):
        with pytest.raises(ValueError):
            _validate_actions([{"screenshot": {"selector": "h1", "index": 2}}])

    def test_the_knobs_are_still_checked(self):
        for bad in ({"selector": "h1", "decode_qr": "yes"},
                    {"selector": "h1", "image_type": "webp"},
                    {"selector": "h1", "quality": 900},
                    {"selector": "h1", "quality": 50}):
            with pytest.raises(ValueError):
                _validate_actions([{"screenshot": bad}])

    def test_the_other_actions_keep_their_own_wording(self):
        """click/fill 的「selector 或 index」那句不属于这颗刀，改动不许波及它们。"""
        with pytest.raises(ValueError, match="needs exactly one of"):
            _validate_actions([{"click": {}}])


class TestTheRefusalNamesTheAdjacentDoor:
    """2026-10-08 operator 判决：裸 `{"screenshot": true}` **维持拒绝**，报错要点名隔壁。

    两条改判路线都先量过，都不干净：

      * 改判成"整页"——实测 `rfc-editor.org/rfc/rfc9110.html` 的整页捕获在内部 10s
        上限里就 `Page.screenshot: Timeout 10000ms exceeded`（长页直接失败）；短页能出图，
        而 `MAX_ACTION_SHOT_BYTES` 自己的注释写着"这么大的一张就是弄错了 —— 它花的是
        多模态客户端的上下文"。
      * 改判成"视口"——那是一项**新能力**：要在描述里加字（连接面 30,750 只降不升，
        眼下没有抵项），并且"没有目标"从此有两种意思。

    病根不在判决，在**相邻的两扇门长得一模一样**：`{"screenshot": ...}` 与
    `{"snapshot": true}` 同一副语法，后者收裸布尔（它不需要目标，列的就是整页的元素），
    前者不收。于是这个错会反复出现。所以这一刀只动文本：那句话要说清裸 true 是从哪儿
    学来的，并把两种"我其实想要的是这个"各写一遍给调用方照抄。
    """

    def test_the_refusal_names_the_sibling_that_takes_a_bare_true(self):
        text = _err(True)
        assert '"snapshot": true' in text, (
            f"裸 true 是从 snapshot 学来的，那句话却不说隔壁长什么样: {text}")

    def test_the_refusal_says_why_a_bare_boolean_cannot_work(self):
        """不只列形状，还要说出**为什么**裸布尔在这一颗上做不到 —— 拍照得有个对象。"""
        text = _err(True)
        assert '"full_page": true' in text, text
        assert "something to look at" in text, (
            f"那句要点出这颗动作的规矩从哪来，不然读起来像任意的: {text}")

    def test_the_targetless_dict_still_gets_the_very_same_sentence(self):
        """R2刀3 的既有裁决不许分叉：裸 true 与无目标 dict 念同一句话。"""
        assert _err(True) == _err({"decode_qr": True})

    def test_the_knob_list_stays_where_the_manual_lock_reads_it(self):
        """刀10 的锁从"Optional knobs:"这一段取名单：新句子往后加，这段不许动。"""
        import re
        text = _err(True)
        found = re.search(r"Optional knobs: (.+?)\.", text)
        assert found, text
        assert found.group(1) == "image_type, quality, decode_qr, save_to", found.group(1)
        assert text.index("Optional knobs") < text.index('"snapshot"'), (
            "名单在前、隔壁在后 —— 打乱顺序会把上面那条锁读成别的东西")


class TestTheManualSaysTheSameThing:
    """同一句规矩在运行时报错与手册里必须是同一个说法 —— 报错是事后解释，手册是事前拦住。"""

    def test_the_page_interaction_section_names_the_bare_boolean_rule(self):
        from dhole_mcp import guide
        body = guide.read("behavior", "Page interaction")[1]
        assert "bare boolean" in body, (
            "报错那半句刚立的规矩（只有 snapshot 收裸布尔）手册里没有，"
            "下一次还是照样写错，只是换个地方学到")
        assert '{"screenshot": true}' in body, body[:400]
