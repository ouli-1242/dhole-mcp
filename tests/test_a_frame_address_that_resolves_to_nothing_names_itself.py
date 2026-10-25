"""R08-001/R08-005：frame 地址三种拼法全不中时，错误要点名 frame 本身。

现场（round-08 P04/P09）：
  * frame="/frame/inner"（照浏览器地址栏抄的相对路径）——名字不中、URL 前缀不中
    （前缀必须含 scheme），落进 CSS 分支炸出 Playwright 的
    `Unexpected token "/" while parsing css selector`——错误里没有 "frame" 字样。
  * frame="#nosuch"——CSS 合法但 0 个 <iframe> 匹配，等到 10s 超时后错误是
    selector-miss 句 + iframe 出路，矛头指 selector，真问题（frame 引用没解析到）
    一字不提。

修后合同：CSS 分支先数顶层文档里的 <iframe>；0 个 → 即刻具名拒绝，教三拼法
（名字 / **绝对** URL 前缀——从快照 frame 行抄 / <iframe> 的 CSS selector）。
名字与绝对 URL 前缀命中时一个字不加。
"""
from __future__ import annotations

import asyncio

import pytest

from dhole_mcp.actions import _frame_locator


class _FakeLoc:
    def __init__(self, n=1):
        self._n = n

    async def count(self):
        return self._n

    @property
    def first(self):
        return self

    async def inner_text(self, **kw):
        return "x"

    async def fill(self, text, **kw):
        pass


class _FakeFrame:
    def __init__(self, name, url):
        self.name = name
        self.url = url
        self.is_main_frame = False

    def locator(self, sel):
        return _FakeLoc()


class _FakePage:
    def __init__(self, counts=None, parse_error_on=None):
        self.main_frame = object()
        self.frames = [_FakeFrame("app", "http://127.0.0.1:8399/frame/inner")]
        self._counts = counts or {}
        self._parse_error_on = parse_error_on or ()

    def locator(self, sel):
        if sel in self._parse_error_on:
            raise ValueError(
                f'Unexpected token "/" while parsing css selector "{sel}". '
                "Did you mean to CSS.escape it?")
        return _FakeLoc(self._counts.get(sel, 0))

    def frame_locator(self, frame):  # 正形才该走到；错误路径不许到这
        raise AssertionError(f"frame_locator reached with {frame!r}")


def test_relative_url_prefix_names_the_frame_not_the_css():
    with pytest.raises(ValueError) as ei:
        asyncio.run(_frame_locator(
            _FakePage(parse_error_on=("/frame/inner",)), "/frame/inner", "#note"))
    msg = str(ei.value)
    assert "'/frame/inner'" in msg, msg
    assert "frame" in msg.lower()
    assert "absolute URL prefix" in msg, msg


def test_frame_css_matching_nothing_names_the_frame():
    with pytest.raises(ValueError) as ei:
        asyncio.run(_frame_locator(_FakePage(counts={"#nosuch": 0}), "#nosuch", "#note"))
    msg = str(ei.value)
    assert "matched 0 <iframe>" in msg, msg
    assert "'#nosuch'" in msg
    assert "app" in msg and "/frame/inner" in msg, msg  # 在场的 iframe 要报出来
    assert "absolute URL prefix" in msg


def test_name_hit_still_resolves_without_noise():
    loc = asyncio.run(_frame_locator(_FakePage(), "app", "#note"))
    assert loc is not None


def test_hint_text_says_absolute_url_prefix():
    # R04-F1 提示句的两处现场：相对前缀照抄会炸，提示该说「绝对」。
    from dhole_mcp import actions
    src = open(actions.__file__, encoding="utf-8").read()
    assert src.count("absolute URL prefix") >= 3, "提示句与两处错误句都该带 absolute 限定"
