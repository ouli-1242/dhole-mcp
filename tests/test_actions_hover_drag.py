"""hover / drag 两个动作：寻址语法与 click 同族，收尾等的是页内安静。

这两动作补齐 web_act 与 playwright-mcp 对比时唯一明确的动作缺口（悬停展开的
菜单、可拖拽的排序/滑块）。三件事各有各的钉子：

1. **寻址同族**：hover 与 click 一样接受字符串简写或 {"selector"|"index",
   "expect"?}；drag 是双端点动作，没有落点的 drag 不是没写完，是没意义，
   所以字符串简写在这里**不设**——值必须是带 "to" 的 dict，两端各走同一套
   `_act_target`。快照索引对两端都可用。
2. **收尾等页内安静**：hover/drag 的后果几乎总是页内变化（菜单展开、列表
   重排），不是导航——所以收尾与 click 的"安静轮询"同一问法但没有"换页"分支，
   预算更短（QUIET_SETTLE_MS）。悬停展开的菜单是同步渲染的，没有网络往返可等。
3. **收执标签**不泄漏值、点名两端（drag 的收执要能看出从哪拖到哪）。
"""

from __future__ import annotations

import asyncio

import pytest

from dhole_mcp import actions as actions_mod
from dhole_mcp.actions import (
    _settle_quiet,
    _validate_actions,
    action_label,
    build_page_action,
)


# ─── 校验：什么形状被接受，什么被当场拒绝 ─────────────────────────────

class TestHoverShapes:
    def test_a_bare_selector_is_the_shorthand(self):
        assert _validate_actions([{"hover": ".menu > li"}]) == [
            {"hover": {"selector": ".menu > li"}}]

    def test_snapshot_index_and_expect_work_like_click(self):
        spec = _validate_actions(
            [{"hover": {"index": 12, "expect": "文件"}}])[0]["hover"]
        assert spec == {"index": 12, "expect": "文件"}

    def test_a_bare_row_number_hovers_that_row(self):
        """刀9-6c（F8）：`{"hover": 12}` 从前是一句拒绝，现在就是 `{"index": 12}`。

        这一格原本钉的是"裸数字不是合法形状"，而它钉住的正是用户连撞四次的那一类
        （2026-10-11 全真模拟 S10："又格式错了？"）—— 所以它按新语义改口，不是删掉。
        """
        assert _validate_actions([{"hover": 12}]) == [{"hover": {"index": 12}}]
        assert _validate_actions([{"hover": 0}]) == [{"hover": {"index": 0}}]

    @pytest.mark.parametrize("bad", [
        {"hover": "   "},
        {"hover": {}},
        {"hover": {"selector": "#a", "index": 1}},
        {"hover": {"index": 1, "expect": 9}},
        {"hover": 12.5},
        {"hover": True},
    ])
    def test_a_hover_that_cannot_be_addressed_is_refused_up_front(self, bad):
        # 一个跑不起来的动作值一整个浏览器调用的时间（校验在前，浏览器冷启动在后）。
        with pytest.raises(ValueError):
            _validate_actions([bad])


class TestDragShapes:
    def test_two_css_endpoints(self):
        spec = _validate_actions(
            [{"drag": {"selector": "#src", "to": "#dst"}}])[0]["drag"]
        assert spec == {"selector": "#src", "to": {"selector": "#dst"}}

    def test_both_ends_can_be_snapshot_indexes(self):
        spec = _validate_actions(
            [{"drag": {"index": 3, "expect": "滑块", "to": {"index": 7}}}])[0]["drag"]
        assert spec == {"index": 3, "expect": "滑块",
                        "to": {"index": 7}}

    def test_the_to_side_accepts_a_bare_selector_string(self):
        spec = _validate_actions(
            [{"drag": {"index": 3, "to": ".trash"}}])[0]["drag"]
        assert spec["to"] == {"selector": ".trash"}

    def test_the_to_side_accepts_a_bare_row_number(self):
        """两端同一套寻址语法（上面那句注释就是这么写的），落点也接裸行号。"""
        spec = _validate_actions(
            [{"drag": {"index": 3, "to": 7}}])[0]["drag"]
        assert spec == {"index": 3, "to": {"index": 7}}, spec

    @pytest.mark.parametrize("bad", [
        {"drag": "#src"},                       # 没有落点 = 没意义，不是简写
        {"drag": {"selector": "#src"}},          # 同上，dict 形态也不放行
        {"drag": {"to": "#dst"}},                # 没有源
        {"drag": {"selector": "#a", "to": {}}},
        {"drag": {"selector": "#a", "to": 4.5}},
        {"drag": 12},                            # 一个裸数说不清是哪一端，drag 不设简写
    ])
    def test_a_drag_that_cannot_be_run_is_refused_up_front(self, bad):
        with pytest.raises(ValueError):
            _validate_actions([bad])

    def test_drag_is_in_the_valid_key_announcement(self):
        """报错清单是 agent 的自我纠错入口：新键必须在里面。"""
        with pytest.raises(ValueError, match="hover"):
            _validate_actions([{"nope": True}])


# ─── 执行器：伪造 page，记录交互调用 ──────────────────────────────────

class _FakeLocator:
    def __init__(self, page, selector, log):
        self._page, self._sel, self._log = page, selector, log

    @property
    def first(self):
        # `_target_locator` 对选择器寻址走 `page.locator(sel).first`。
        return self

    async def hover(self, **kw):
        self._log.append(("hover", self._sel, kw.get("timeout")))

    async def drag_to(self, target, **kw):
        self._log.append(("drag_to", self._sel, target._sel, kw.get("timeout")))


class _FakePage:
    """locator() 按选择器发伪定位器；DOM 快照恒定 → 收尾立即判安静。"""

    def __init__(self, log):
        self._log = log

    def locator(self, selector):
        return _FakeLocator(self, selector, self._log)

    async def wait_for_timeout(self, ms):
        pass

    async def evaluate(self, *_a, **_kw):
        return ["stable"]


@pytest.fixture
def _fast_settle(monkeypatch):
    """收尾轮询是真实计时器；单测里把预算缩到两拍以内。"""
    monkeypatch.setattr(actions_mod, "QUIET_SETTLE_MS", 50)
    monkeypatch.setattr(actions_mod, "QUIET_SETTLE_MIN_MS", 0)


def _run(page, actions):
    pa = build_page_action(actions)
    return asyncio.run(pa(page)), pa.outcomes


class TestExecution:
    def test_hover_resolves_the_selector_and_settles(self, _fast_settle):
        log: list = []
        outcomes, rows = _run(_FakePage(log), [{"hover": ".menu > li"}])
        assert log == [("hover", ".menu > li", 10_000)]
        assert rows == [{"action": "hover '.menu > li'", "ok": True}]

    def test_hover_by_snapshot_index(self, monkeypatch, _fast_settle):
        log: list = []
        page = _FakePage(log)
        idx_loc = _FakeLocator(page, "resolved-row-12", log)

        async def fake_resolve(p, i):
            assert (p, i) == (page, 12)
            return idx_loc

        monkeypatch.setattr(actions_mod, "resolve_index", fake_resolve)
        outcomes, rows = _run(page, [{"hover": {"index": 12}}])
        assert log == [("hover", "resolved-row-12", 10_000)]
        assert rows[0]["ok"] is True

    def test_drag_records_both_endpoints_in_order(self, _fast_settle):
        log: list = []
        outcomes, rows = _run(
            _FakePage(log),
            [{"drag": {"selector": "#card-1", "to": "#card-3"}}])
        assert log == [("drag_to", "#card-1", "#card-3", 10_000)]
        assert rows == [{"action": "drag '#card-1' -> '#card-3'", "ok": True}]

    def test_drag_by_indexes_with_expect_on_the_source(self, monkeypatch, _fast_settle):
        log: list = []
        page = _FakePage(log)

        async def fake_resolve(p, i):
            return _FakeLocator(p, f"row-{i}", log)

        async def fake_expect(p, i, expect):
            assert (i, expect) == (3, "滑块")

        monkeypatch.setattr(actions_mod, "resolve_index", fake_resolve)
        monkeypatch.setattr(actions_mod, "_check_expect", fake_expect)
        outcomes, rows = _run(
            page, [{"drag": {"index": 3, "expect": "滑块", "to": {"index": 7}}}])
        assert log == [("drag_to", "row-3", "row-7", 10_000)]
        assert rows[0]["ok"] is True

    def test_a_failed_drag_is_a_row_not_a_crash(self, _fast_settle):
        """一条失败的动作不.abort 整条链——收执行说清哪端没找到。"""

        class _Boom(_FakeLocator):
            async def drag_to(self, target, **kw):
                raise TimeoutError("waiting for locator")

        class _DeadPage(_FakePage):
            def locator(self, selector):
                return _Boom(self, selector, self._log)

        log: list = []
        _, rows = _run(_DeadPage(log),
                       [{"drag": {"selector": "#a", "to": "#b"}}])
        assert rows[0]["ok"] is False
        assert "waiting for locator" in rows[0]["error"]


class TestReceiptLabels:
    def test_hover_label_names_the_target(self):
        assert action_label({"hover": {"selector": ".x"}}) == "hover '.x'"
        assert action_label({"hover": {"index": 5}}) == "hover [5]"

    def test_drag_label_names_both_ends(self):
        a = _validate_actions(
            [{"drag": {"index": 3, "to": {"selector": ".trash"}}}])[0]
        assert action_label(a) == "drag [3] -> '.trash'"


class _FakeClock:
    """替身时钟：每次 monotonic() 推进一拍。actions.time 是全局 time 模块，
    直接 monkeypatch 它会让替身调到自己（递归爆炸），所以换整个对象。"""

    def __init__(self):
        self.t = 0.0

    def monotonic(self) -> float:
        self.t += 0.4
        return self.t


class TestQuietSettle:
    def test_settle_exits_after_two_quiet_polls(self, monkeypatch):
        """安静两拍（且过了最短时限）就出来，不等满预算。"""
        monkeypatch.setattr(actions_mod, "QUIET_SETTLE_MS", 10_000)
        monkeypatch.setattr(actions_mod, "QUIET_SETTLE_MIN_MS", 0)
        monkeypatch.setattr(actions_mod, "time", _FakeClock())
        polls = {"n": 0}

        class _P:
            async def wait_for_timeout(self, ms):
                polls["n"] += 1

            async def evaluate(self, *_a, **_kw):
                return ["stable"]  # DOM 恒定 = 安静

        asyncio.run(_settle_quiet(_P()))
        assert polls["n"] == 2  # 两拍安静即出，没有等满 10 秒

    def test_settle_keeps_waiting_while_the_dom_grows(self, monkeypatch):
        """菜单还在展开（DOM 一直变）就继续等，直到预算到顶。"""
        monkeypatch.setattr(actions_mod, "QUIET_SETTLE_MS", 2_000)
        monkeypatch.setattr(actions_mod, "time", _FakeClock())
        polls = {"n": 0}

        class _P:
            async def wait_for_timeout(self, ms):
                polls["n"] += 1

            async def evaluate(self, *_a, **_kw):
                polls["n"] += 1
                return ["growing", polls["n"]]  # 每拍都在变 = 菜单还没展开完

        asyncio.run(_settle_quiet(_P()))
        assert polls["n"] > 2  # 越过了"两拍安静"的出口，预算到顶才出来
