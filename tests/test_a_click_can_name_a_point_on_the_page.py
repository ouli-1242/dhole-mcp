"""A click can name a point on the page, not only an element (视觉坐标操作).

现场：矩阵里「视觉坐标级操作」一栏 dhole 是 ⚠️（只有 OCR 辅助的截图，没有坐标
点击）。canvas / 地图 / 自绘控件没有可选中的 DOM，截图能看见却点不到——这一刀补
的是那半条腿，不是把 dhole 变成视觉平台。

安全侧照旧：坐标点击若落在提交型控件上，与 selector click 过**同一道**提交闸。
坐标只接受页面像素的整数对；拿不准的形状（与 selector 混写、负数、布尔）在校验期
就拒绝，不值一次浏览器冷启动。
"""

from __future__ import annotations

import asyncio

import pytest

from dhole_mcp import actions
from dhole_mcp.actions import _validate_actions, action_label, build_page_action


class _Mouse:
    def __init__(self):
        self.events: list[tuple] = []

    async def click(self, x, y, **kw):
        self.events.append(("click", x, y))

    async def move(self, x, y, **kw):
        self.events.append(("move", x, y, kw.get("steps")))

    async def down(self):
        self.events.append(("down",))

    async def up(self):
        self.events.append(("up",))


class _Page:
    """只实现坐标路径要用的那几样；元素路径一棵 locator 都不提供。"""

    def __init__(self, at_submit: bool = False):
        self.mouse = _Mouse()
        self.at_submit = at_submit
        self._listeners: list = []

    def on(self, event, handler):
        self._listeners.append((event, handler))

    def remove_listener(self, event, handler):
        self._listeners = [x for x in self._listeners if x != (event, handler)]

    async def evaluate(self, script, arg=None):
        if "elementFromPoint" in script:
            return "submit" if self.at_submit else None
        return [0, 0, 0]          # _DOM_MARK_JS：DOM 稳定，无需真等

    async def wait_for_timeout(self, ms):
        pass


def _click(at, **extra):
    return {"click": {"at": at, **extra}}


# ── 1 · 校验：坐标是一对页面像素，不许与元素寻址混写 ───────────────────

def test_a_point_is_a_click_target():
    assert _validate_actions([_click([12, 34])]) == [{"click": {"at": [12, 34]}}]


def test_a_point_click_can_carry_confirm():
    assert _validate_actions([_click([1, 2], confirm=True)]) == [
        {"click": {"at": [1, 2], "confirm": True}}]


@pytest.mark.parametrize("bad", [
    {"click": {"at": [1]}},
    {"click": {"at": [-1, 2]}},
    {"click": {"at": [True, 2]}},
    {"click": {"at": [1.5, 2]}},
    {"click": {"at": "1,2"}},
    {"click": {"at": [1, 2], "selector": "#a"}},
    {"click": {"at": [1, 2], "index": 0}},
    {"click": {"at": [1, 2], "expect": "登录"}},
])
def test_a_point_that_is_not_two_ints_is_refused_up_front(bad):
    with pytest.raises(ValueError):
        _validate_actions([bad])


def test_both_ends_of_a_drag_can_be_points():
    assert _validate_actions([{"drag": {"from_xy": [10, 20],
                                        "to_xy": [30, 40]}}]) == [
        {"drag": {"from_xy": [10, 20], "to_xy": [30, 40]}}]


@pytest.mark.parametrize("bad", [
    {"drag": {"from_xy": [1, 2]}},
    {"drag": {"from_xy": [1, 2], "to_xy": [3]}},
    {"drag": {"from_xy": [1, 2], "to_xy": [3, 4], "selector": "#src"}},
    {"drag": {"from_xy": [1, -2], "to_xy": [3, 4]}},
])
def test_a_coordinate_drag_needs_two_clean_points(bad):
    with pytest.raises(ValueError):
        _validate_actions([bad])


def test_the_receipt_names_the_points():
    click = _validate_actions([_click([12, 34])])[0]
    drag = _validate_actions([{"drag": {"from_xy": [10, 20],
                                        "to_xy": [30, 40]}}])[0]
    assert action_label(click) == "click at (12, 34)"
    assert action_label(drag) == "drag (10, 20) -> (30, 40)"


# ── 2 · 执行：鼠标真的动了 ────────────────────────────────────────────

def test_a_point_click_uses_the_mouse(monkeypatch):
    monkeypatch.setattr(actions, "CLICK_SETTLE_MIN_MS", 0)
    monkeypatch.setattr(actions, "CLICK_SETTLE_MS", 50)
    page = _Page()
    pa = build_page_action([_click([12, 34])])
    asyncio.run(pa(page))
    assert page.mouse.events == [("click", 12, 34)]
    assert pa.outcomes[0]["ok"] is True, pa.outcomes


def test_a_point_on_a_submit_control_is_gated(monkeypatch):
    async def no_human(*a, **k):
        return None

    monkeypatch.setattr(actions, "out_of_band_confirm", no_human)
    page = _Page(at_submit=True)
    pa = build_page_action([_click([12, 34])])
    asyncio.run(pa(page))
    row = pa.outcomes[0]
    assert row["pending_approval"] is True, row
    assert row["ok"] is False
    assert page.mouse.events == [], "被闸拦下的坐标点击一个鼠标事件都不许发"
    assert "confirm" in row["error"], row["error"]


def test_a_two_point_drag_uses_the_mouse(monkeypatch):
    async def no_settle(page):
        return None

    monkeypatch.setattr(actions, "_settle_quiet", no_settle)
    page = _Page()
    pa = build_page_action([{"drag": {"from_xy": [10, 20],
                                      "to_xy": [30, 40]}}])
    asyncio.run(pa(page))
    assert page.mouse.events == [
        ("move", 10, 20, None),
        ("down",),
        ("move", 30, 40, 10),
        ("up",),
    ], page.mouse.events
    assert pa.outcomes[0]["ok"] is True, pa.outcomes
