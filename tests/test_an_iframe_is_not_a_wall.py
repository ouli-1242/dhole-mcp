"""iframe 里的元素不再是墙外的世界。

现场（2026-10-24，可提升清单 1）：快照与动作寻址只枚举主框——企业后台、旧
门户的可交互元素大量活在 iframe 里，agent 对它们又瞎又哑。两条修法：

  * 快照的枚举跟进子框：主框的行号照旧从 0 起，子框元素**全局编号连续**地跟在
    后面，每行多一格 `frame`（帧的 URL）；索引 → 帧的映射记在
    `page._dhole_frame_map`，`resolve_index` 是唯一读者——快照行号对框内元素
    照样可用，「用户不用懂」的合同不在框边上破。
  * 动作目标多一颗 `frame`：帧的 name 精确匹配、URL 前缀、或 iframe 元素自己的
    CSS 三种拼法，跨域照样可达（frame_locator 过 CDP，不受同源策略约束）。

主框行为零改动：没有子框的页面，快照行不带第七格、不设映射，与从前逐字同形。
"""

from __future__ import annotations

import asyncio

import pytest

from dhole_mcp.actions import (
    INTERACTIVE_SELECTOR,
    _target_locator,
    _validate_actions,
    build_page_action,
    resolve_index,
    snapshot_elements,
)


class _FakeFrame:
    def __init__(self, name: str, url: str, rows, total):
        self.name = name
        self.url = url
        self._rows = rows
        self._total = total
        self.located: list = []

    async def evaluate(self, js, arg=None):
        if "els.length" in js:
            return [self._rows, self._total]
        raise AssertionError(f"unexpected frame evaluate: {js[:60]}")

    def locator(self, sel):
        self.located.append(sel)
        return _FakeLoc(self)


class _FakeLoc:
    def __init__(self, owner):
        self._owner = owner

    @property
    def first(self):
        return self

    def nth(self, i):
        self._owner.nths = getattr(self._owner, "nths", [])
        self._owner.nths.append(i)
        return self

    async def evaluate(self, js):
        return "inner save button"


class _FakePage:
    def __init__(self, rows, total, children=()):
        self._rows = rows
        self._total = total
        self.frames = [self] + list(children)
        self.main_frame = self
        self.located: list = []

    async def evaluate(self, js, arg=None):
        if "els.length" in js:
            return [self._rows, self._total]
        if "querySelectorAll(sel).length" in js:
            return self._total
        raise AssertionError(f"unexpected page evaluate: {js[:60]}")

    def locator(self, sel):
        self.located.append(sel)
        return _FakeLoc(self)

    def frame_locator(self, sel):
        self.frame_located = sel
        return _FakeFL()


class _FakeFL:
    def __init__(self):
        self.got = None

    def locator(self, sel):
        self.got = sel
        return _FakeLoc(self)


ROW_MAIN = [0, "button", "button", "outer", False, ""]
ROW_INNER_1 = [0, "input", "textbox", "note", False, "text"]
ROW_INNER_2 = [1, "button", "button", "save", False, ""]


# ── 1 · 快照：子框跟进来，编号连续 ──────────────────────────────────────

def test_snapshot_enumerates_child_frames_with_a_frame_column():
    inner = _FakeFrame("app", "http://127.0.0.1:8399/frame/inner",
                       [ROW_INNER_1, ROW_INNER_2], 2)
    page = _FakePage([ROW_MAIN], 1, children=[inner])
    rows, total, truncated = asyncio.run(snapshot_elements(page))
    assert total == 3 and truncated is False
    assert [r[0] for r in rows] == [0, 1, 2]
    # 主框行不带第七格；子框行带着帧的 URL
    assert len(rows[0]) == 6
    assert rows[1][6] == "http://127.0.0.1:8399/frame/inner"
    assert rows[2][6] == "http://127.0.0.1:8399/frame/inner"


def test_snapshot_builds_the_index_to_frame_map():
    inner = _FakeFrame("app", "http://x/frame/inner", [ROW_INNER_1, ROW_INNER_2], 2)
    page = _FakePage([ROW_MAIN], 1, children=[inner])
    asyncio.run(snapshot_elements(page))
    fmap = page._dhole_frame_map
    assert fmap[1] == (inner, 0) and fmap[2] == (inner, 1)


def test_a_page_without_frames_stays_exactly_as_before():
    page = _FakePage([ROW_MAIN], 1)
    rows, total, truncated = asyncio.run(snapshot_elements(page))
    assert rows == [[0, "button", "button", "outer", False, ""]]
    assert total == 1
    assert not hasattr(page, "_dhole_frame_map")


def test_about_blank_frames_are_skipped():
    blank = _FakeFrame("", "about:blank", [ROW_INNER_1], 1)
    page = _FakePage([ROW_MAIN], 1, children=[blank])
    rows, total, _ = asyncio.run(snapshot_elements(page))
    assert total == 1 and len(rows) == 1


# ── 2 · 索引解析：框内行号认领自己的帧 ──────────────────────────────────

def test_resolve_index_hands_frame_rows_to_their_frame():
    inner = _FakeFrame("app", "http://x/frame/inner", [ROW_INNER_1], 1)
    page = _FakePage([ROW_MAIN], 1, children=[inner])
    asyncio.run(snapshot_elements(page))
    loc = asyncio.run(resolve_index(page, 1))
    assert inner.located == [INTERACTIVE_SELECTOR]


def test_a_stale_frame_index_still_fails_loudly():
    page = _FakePage([ROW_MAIN], 1)      # 没有快照过、没有映射
    with pytest.raises(ValueError, match="beyond"):
        asyncio.run(resolve_index(page, 5))


# ── 3 · frame 寻址：name / URL 前缀 / CSS 三种拼法 ──────────────────────

def test_frame_addressed_by_name():
    inner = _FakeFrame("app", "http://x/frame/inner", [], 0)
    page = _FakePage([], 0, children=[inner])
    loc = asyncio.run(_target_locator(
        page, {"selector": "#save", "frame": "app"}))
    assert inner.located == ["#save"]


def test_frame_addressed_by_url_prefix():
    inner = _FakeFrame("", "http://127.0.0.1:8399/frame/inner", [], 0)
    page = _FakePage([], 0, children=[inner])
    asyncio.run(_target_locator(
        page, {"selector": "#save",
               "frame": "http://127.0.0.1:8399/frame/inner"}))
    assert inner.located == ["#save"]


def test_frame_addressed_by_css_falls_through_to_frame_locator():
    page = _FakePage([], 0)              # 没有可按名/URL 认领的帧
    loc = asyncio.run(_target_locator(
        page, {"selector": "#save", "frame": "#app"}))
    assert page.frame_located == "#app"


def test_frame_shapes_are_validated():
    with pytest.raises(ValueError, match="frame"):
        build_page_action([{"click": {"selector": "#save", "frame": ""}}])
    with pytest.raises(ValueError, match="frame"):
        build_page_action([{"click": {"selector": "#save", "frame": 3}}])


def test_validation_accepts_frame_with_selector_and_snapshot_receipt_keeps_shape():
    out = _validate_actions([{"fill": {"selector": "#note", "frame": "app",
                                          "text": "hello"}}])
    assert out[0]["fill"]["frame"] == "app"
    assert out[0]["fill"]["text"] == "hello"
