# -*- coding: utf-8 -*-
"""链里没有一步能跑过调用者点名的那笔预算 —— 每一步的截止跟着**剩下的**走。

症状（15 场景真人体测，场景 10，实录见 `review/`）：靶站 `/captcha` 上一步元素截图，
`timeout=60000`，实测 **61,529ms** 之后交回 0 字节。期 1.2 已经让被掐的那一步留下自己的
一行，那一刀治的是"看不见跑到第几步"；这一刀治的是**为什么会到那一步**：

链里每一颗 playwright 等待拿的都是一个跟预算无关的字面量（click / fill / hover / drag /
press / inner_text / wait_for 各 10 秒，settle 再 4 秒，scroll 一整段 20 秒）。8 步的链最坏
要花 80 秒，而调用方点的是 60 秒 —— 于是唯一真正会掐的东西是外面那道 backstop
（`asked + 1.5s`），它一掐就走 `CancelledError`，而那条路上**信封交不出去**：61.5 秒之后
拿到的是"什么都没抽到"，哪怕前 6 步真的落在页面上。

判据的形状是"每**要执行**的一步自己结预算之内的账，预算之外那 1.5 秒留给兜底交账"：

1. 收窄是取小，不是改写：`min(自身预算, 剩余 − 交账宽限)`。
2. 没点名预算的调用（CLI、直连 `dispatch`、单测）**一个字都不变** —— 那颗 10 秒今天就是
   行为，动它等于改了所有不在工具入口上的调用。
3. 余额已经不够一步时不许造一个 1 毫秒的等待冒充"站点慢"：那种形状下真话只有一句
   "预算用完"，而它由兜底说（1.2 那一条已经给它留了行）。
4. 交账宽限只有一个作者：`context.BACKSTOP_GRACE_S`。三处读者（兜底、建议句、这一步的
   截止）各抄一份就是下一次漂移的开始。
5. 显式的 `{"wait": ms}` 不在射程里 —— 那是调用者自己点的秒数，收窄它等于改一句他已经
   说出口的指令；那种调用越过预算由兜底结，结的时候有 1.2 那一行。
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from dhole_mcp import context as ctx
from dhole_mcp import quality
from dhole_mcp.actions import (
    BACKSTOP_GRACE_S, CLICK_SETTLE_MS, STEP_MIN_MS,
    STEP_WAIT_MS, _step_deadline_ms, build_page_action)
from dhole_mcp.context import call_deadline

ACTIONS_SRC = (Path(__file__).resolve().parents[1] / "src" / "dhole_mcp"
               / "actions.py").read_text(encoding="utf-8")

GRACE_MS = int(BACKSTOP_GRACE_S * 1000)


@pytest.fixture
def clock(monkeypatch):
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


def test_a_budget_bigger_than_the_step_shortens_nothing(clock):
    with call_deadline(60):
        assert _step_deadline_ms() == STEP_WAIT_MS, "宽裕的预算不该改变任何一步"


def test_a_tight_budget_shortens_the_step_to_what_is_left(clock):
    with call_deadline(5):
        assert _step_deadline_ms() == 5_000 - GRACE_MS, (
            "剩 5 秒的调用里一步等 10 秒，就是让它去撞外面那道兜底")


def test_the_step_finishes_strictly_inside_the_budget_it_was_given(clock):
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


def test_an_exhausted_budget_is_not_answered_with_a_fake_one_millisecond_wait(clock):
    """不够一步时交回原值：真话只有一句"预算用完"，而那句话由兜底来说（附 1.2 那一行）。

    反过来说，1 毫秒的等待一定会超时，而那条超时在回执里长得和"站点没响应"一模一样 ——
    那才是这一族最贵的谎。
    """
    with call_deadline(1.0):
        assert _step_deadline_ms() == STEP_WAIT_MS
    with call_deadline(2.4):
        # cap = 2400 − 1500 = 900 < STEP_MIN_MS：这一步已经没有意义，交回原值让兜底说真话
        assert _step_deadline_ms() == STEP_WAIT_MS, "不够一步也不许冒充站点慢"


def test_the_floor_is_named_because_a_step_of_zero_is_a_different_bug(clock):
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


class _Page:
    """hover 之后链里要的是 `_settle_quiet`：两次 poll 的 DOM 记号 + 一小步 sleep。"""

    def __init__(self):
        self.sink = []

    def locator(self, sel):
        return _RecordingLocator(self.sink)

    async def evaluate(self, script, arg=None):
        return ("https://example.test/p", 10, 100)

    async def wait_for_timeout(self, ms):
        await asyncio.sleep(0)


def test_the_page_is_asked_for_the_capped_time_not_the_literal(clock):
    page = _Page()
    with call_deadline(5):
        asyncio.run(build_page_action([{"hover": {"selector": "#x"}}])(page))
    assert [c for c in page.sink if c[0] == "hover"] == [("hover", 5_000 - GRACE_MS)], (
        "算术对了而页面拿到的还是那颗字面量 10_000 —— 这一刀就只活在函数里")


def test_the_same_step_takes_the_full_default_when_nothing_was_named():
    page = _Page()
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
