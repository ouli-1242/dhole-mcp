"""B5-13d：渲染时机不再是一颗参数（§6 表的最后两行 / 三条新规矩第 1 条）。

13a 把两条轴分开了：伪装进档位，时机留在参数面。这一刀把时机那颗也收掉，理由是
`network_idle` 唯一的用户就是"SPA 抽到空壳"那一种失败，而这种失败现在认得出来 ——
DOM 里的可见文本还没长出来。所以那 5 秒成了**认出壳页才付的钱**，不是每次要么全付
要么不付的开关。`wait`（固定睡 N 毫秒）彻底删、不留替代：没有任何一种真实场景里
"固定睡 N"比"等页面自己说好了"更对，它是拿来掩盖偶发失败的，而掩盖失败正是这批要
治的病。

断言各自钉住一件事：
- 判定是纯函数，读数进、结论出：短文本 + 有脚本才算壳页。JS 那边只报数，不下结论
  —— 阈值散在页面脚本里就等于没人能测它，也没人能改它；
- 付钱的条件：认出壳页才付，认不出（包括读不到）一律不付 —— 一次猜测不该花读者的
  5 秒；
- 付的钱有上限且超时静默 —— 这条是既有实现，删参数不许顺手把它改掉；
- 参数面上两颗都没了，传上来是陌生键而不是被吞（§3-20 的静默忽略只管环境变量）；
- 内部链路里也没有残留的形参：只有调用方还在传、而没人能设，那才叫真删干净。
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from dhole_mcp import browser as browser_mod
from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer as Server

URL = "https://example.com/"


class _Page:
    """playwright page 的最小替身：只记 `_wait_for_stability` 会碰的两件事。"""

    def __init__(self, *, reading=None, evaluate_raises=False, idle_raises=False):
        self.states: list[tuple[str, object]] = []
        self._reading = reading if reading is not None else {"text": 5000, "scripts": 3}
        self._evaluate_raises = evaluate_raises
        self._idle_raises = idle_raises

    async def wait_for_load_state(self, state, timeout=None):
        self.states.append((state, timeout))
        if state == "networkidle" and self._idle_raises:
            raise TimeoutError("timeout 5000ms exceeded")

    async def evaluate(self, script):
        if self._evaluate_raises:
            raise RuntimeError("execution context destroyed")
        return self._reading


def _settle(page) -> bool:
    """跑真的 `_wait_for_stability`，返回它有没有付那 5 秒。"""
    session = object.__new__(browser_mod.BrowserSession)  # 造一台浏览器不是本题
    return asyncio.run(session._wait_for_stability(page))


class TestTheVerdictIsAPureFunction:
    """读数 → 结论，一张表说清什么叫"这页还没长出来"。"""

    def test_short_text_with_a_script_bundle_is_a_shell(self):
        assert browser_mod._looks_unrendered({"text": 40, "scripts": 2}) is True

    def test_a_page_with_real_text_has_rendered(self):
        assert browser_mod._looks_unrendered({"text": 4000, "scripts": 9}) is False

    def test_short_text_without_scripts_is_a_small_page_not_a_shell(self):
        """nginx 的 404 也是几十字符的正文：没有脚本就没法说"脚本还没跑"。"""
        assert browser_mod._looks_unrendered({"text": 30, "scripts": 0}) is False

    def test_the_threshold_is_the_same_number_as_the_extraction_side(self):
        # server 的壳页判定拿"抽取后的文本 < _JS_SHELL_MIN_TEXT_CHARS"做门槛；
        # 浏览器这边认的是"可见文本还没长到那个数"。两个数一错位，就会出现一条轴
        # 认得出、另一条轴白等 5 秒（或该等的不等）的裂缝。
        assert browser_mod._UNRENDERED_MAX_TEXT_CHARS == server_mod._JS_SHELL_MIN_TEXT_CHARS

    @pytest.mark.parametrize("reading", [None, {}, "junk", {"text": "abc", "scripts": 1},
                                         {"text": -1, "scripts": 3}])
    def test_an_unreadable_dom_is_not_a_shell(self, reading):
        assert browser_mod._looks_unrendered(reading) is False


class TestTheSettleIsConditional:

    def test_a_page_that_has_not_rendered_pays_the_idle_wait(self):
        page = _Page(reading={"text": 20, "scripts": 4})
        assert _settle(page) is True
        assert ("load", None) in page.states
        assert ("networkidle", 5000) in page.states, \
            "认出壳页却不补 networkidle —— 这颗参数删了就等于把能力删了"

    def test_a_rendered_page_pays_nothing(self):
        page = _Page(reading={"text": 8000, "scripts": 4})
        assert _settle(page) is False
        assert not [s for s in page.states if s[0] == "networkidle"], \
            "内容早就在了还要静等 5 秒，是把读者的时间当自己的"

    def test_an_unreadable_dom_does_not_cost_5_seconds(self):
        page = _Page(evaluate_raises=True)
        assert _settle(page) is False
        assert not [s for s in page.states if s[0] == "networkidle"]

    def test_the_idle_wait_is_capped_and_a_timeout_is_silent(self):
        page = _Page(reading={"text": 20, "scripts": 4}, idle_raises=True)
        assert _settle(page) is True
        assert page.states.count(("networkidle", 5000)) == 1, \
            "5 秒上限是这颗的全部意义：认出了壳也不能把调用拖成无限等"

    def test_a_load_that_never_fires_still_reaches_the_verdict(self):
        """wait_for_load_state 抛（超时 / 导航已定）不是判定失败的理由。"""
        class _Ugly(_Page):
            async def wait_for_load_state(self, state, timeout=None):
                self.states.append((state, timeout))
                raise RuntimeError("target closed")

        page = _Ugly(reading={"text": 15, "scripts": 1})
        assert _settle(page) is True


class TestTheCallSurfaceLostBothKnobs:

    def _props(self, tool):
        defs = {d["name"]: d for d in Server._TOOL_DEFS}
        return defs[tool]["inputSchema"]["properties"]

    def test_neither_tool_advertises_a_timing_knob(self):
        for tool in ("web_read", "web_screenshot"):
            props = self._props(tool)
            for knob in ("wait", "network_idle"):
                assert knob not in props, f"{tool} 还在教 {knob}"

    def test_web_read_advertises_twenty_four_names(self):
        """35 → 30（13c 身份）→ 28（13d 时机）→ 25（14a 写操作）→ 24（14b 动作链）。

        §3-12 的 ≤20 由 tests/test_wire_shape.py 整块 xfail(strict) 盯着：差的那几颗是
        §3-16 那道门与读法本身，不是这条链要在这儿替别的批次许的愿。
        """
        assert len(server_mod._TOP_LEVEL_ARGS["web_read"]) == 24, \
            sorted(server_mod._TOP_LEVEL_ARGS["web_read"])

    def test_web_screenshot_lost_the_same_two(self):
        assert not {"wait", "network_idle"} & server_mod._TOP_LEVEL_ARGS["web_screenshot"]

    @pytest.mark.parametrize("knob", ["wait", "network_idle"])
    def test_passing_a_retired_timing_knob_is_refused_not_swallowed(self, knob):
        with pytest.raises(ValueError, match="Unsupported argument") as e:
            asyncio.run(Server()._dispatch("web_read", {"url": URL, knob: 5}))
        assert knob in str(e.value)


class TestTheInternalChainHasNoSourceLeft:
    """形参只有调用方能填，而调用方没了 —— 留着就是死码冒充能力。"""

    def test_the_browser_takes_no_timing_knobs(self):
        for fn in (browser_mod.BrowserSession.__init__,
                   browser_mod.BrowserSession.fetch):
            params = inspect.signature(fn).parameters
            for knob in ("wait", "network_idle"):
                assert knob not in params, f"{fn.__qualname__} 还收 {knob}"

    def test_the_server_tiers_take_no_timing_knobs(self):
        for name in ("get", "stealthy_fetch", "bulk_stealthy_fetch",
                     "_force_fetch", "_auto_escalate", "_smart_fetch_bulk",
                     "web_read", "web_screenshot"):
            params = inspect.signature(getattr(Server, name)).parameters
            for knob in ("wait", "network_idle"):
                assert knob not in params, f"{name} 还收 {knob}"
