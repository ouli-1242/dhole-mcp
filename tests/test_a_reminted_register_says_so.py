"""R08-003：同一链里 as= 同名重铸，第二枚 read 要把「覆盖」说出去。

现场（round-08 P06）：链 [read #challenge as=dup, read h1 as=dup, fill {=dup}] ——
重铸静默 last-wins，fill 实填的是 h1 的页标题，调用方以为填的是挑战 token。
刷新型重读（同名验值）是合法模式，所以语义保留；缺的是覆盖现场的信号。

修后合同：重铸同名的那枚 read 行带 `register_overwritten` 注记（点名名字 +
说清「往后的 {=名字} 用的是这一枚」）。首铸不加。
"""
from __future__ import annotations

import asyncio

from dhole_mcp.actions import build_page_action


class _FakeLoc:
    def __init__(self, page, sel):
        self._page = page
        self._sel = sel

    @property
    def first(self):
        return self

    async def inner_text(self, **kw):
        return self._page.texts.get(self._sel, "")

    async def fill(self, text, **kw):
        self._page.filled.append(text)

    async def wait_for(self, **kw):
        pass


class _FakePage:
    def __init__(self, texts):
        self.main_frame = object()
        self.frames = [self]
        self.texts = texts
        self.filled: list = []

    def set_default_navigation_timeout(self, ms):
        pass

    def set_default_timeout(self, ms):
        pass

    def locator(self, sel):
        return _FakeLoc(self, sel)

    async def goto(self, url, **kw):
        pass

    def on(self, event, handler):
        pass

    def remove_listener(self, event, handler):
        pass


def test_reminted_register_carries_the_note():
    page = _FakePage({"#challenge": "TOKEN", "h1": "页标题"})
    pa = build_page_action([
        {"read": {"selector": "#challenge", "as": "dup"}},
        {"read": {"selector": "h1", "as": "dup"}},
        {"fill": {"selector": "#answer", "text": "{=dup}"}},
    ])
    asyncio.run(pa(page))
    assert page.filled == ["页标题"], page.filled  # last-wins 本身保留
    row = pa.outcomes[1]
    assert "register_overwritten" in row, row
    assert "'dup'" in row["register_overwritten"]
    assert "TOKEN" in row["register_overwritten"], row  # 说清被覆盖的是什么


def test_first_mint_stays_quiet():
    page = _FakePage({"#challenge": "TOKEN"})
    pa = build_page_action([
        {"read": {"selector": "#challenge", "as": "code"}},
        {"fill": {"selector": "#answer", "text": "{=code}"}},
    ])
    asyncio.run(pa(page))
    assert page.filled == ["TOKEN"]
    assert "register_overwritten" not in pa.outcomes[0]
