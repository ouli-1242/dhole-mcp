"""R01-006：猜错引用语法的那一发，回执要教正确的形状——而不是安静地填字面量。

现场（2026-10-25 · P07 阿凯 · S3 抄写挑战 5 连试 · review/round-01）：
fill text='{CODE}' ——页面上躺着一枚他刚读到的码，他猜的语法被**原样**填进表单
（evals/.runtime/challenge.log：answer=`{CODE}`，expected=`TTUUR`，ok=false），
回执只说 `fill ok`。机制本身一直在：`{"read": ..., "as": "code"}` 之后
`{"fill": ..., "text": "{=code}"}` 一条链就能通关（本轮补测 ok:true）——缺的是
「你猜的形状不对」这句话。同轮还坐实：pending_approval 的重跑提示
`{"confirm": true}` 不说 confirm 长在哪一层（顶层 confirm=true 无效，正形是
动作内的 `{"click": {..., "confirm": true}}`）。

「plain 花括号是文本」是设计且已有测试钉住（test_actions_2 的
test_a_plain_brace_is_text_not_a_reference）——这一刀不改判据，只把**提示**
放进成功 fill 的回执：填进去的东西长得像引用、链里又没有那个名字时，说一句。

契约（本文件钉住）：
  * fill 的 text 含 `{word}` 形状、而该名字不在链的寄存器里 → 这一步的回执带
    `note`，教 `{=name}` 用法并点名现存寄存器；
  * 合法的 `{=name}` 展开与真·字面量（无花括号 / `{word}` 恰是寄存器名）照旧
    不付这笔话；
  * 提交闸的 `{"confirm": true}` 提示必须说出 confirm 所在的层（动作内）。
"""

from __future__ import annotations

import asyncio

from dhole_mcp.actions import build_page_action
from dhole_mcp.actions import _CONFIRM_SNIPPET


# ─── 假页面（与 test_actions_2 同款，只长这一判据需要的器官） ─────────


class _El:
    def __init__(self, text=""):
        self.text = text
        self.filled: list[str] = []

    async def inner_text(self, timeout=None):
        return self.text

    async def fill(self, value, timeout=None):
        self.filled.append(value)


class _Loc:
    def __init__(self, el):
        self._el = el

    @property
    def first(self):
        return self

    def nth(self, i):
        return self

    async def inner_text(self, timeout=None):
        return await self._el.inner_text()

    async def fill(self, value, timeout=None):
        await self._el.fill(value)


class _Page:
    def __init__(self, by_selector):
        self.els = by_selector
        self.missing = "no such element"

    def locator(self, sel):
        if sel not in self.els:
            raise RuntimeError(f"waiting for locator {sel!r}")
        return _Loc(self.els[sel])


def _run(chain, page):
    pa = build_page_action(chain)
    asyncio.run(pa(page))
    return pa.outcomes


PAGE = {"#challenge": _El(text="TTUUR"), "#answer": _El()}


def test_a_brace_word_that_names_no_register_gets_the_shape_hint():
    chain = [{"read": {"selector": "#challenge", "as": "code"}},
             {"fill": {"selector": "#answer", "text": "{CODE}"}}]
    rows = _run(chain, _Page(dict(PAGE)))
    fill_row = rows[1]
    assert fill_row["ok"] is True, fill_row
    note = fill_row.get("note", "")
    assert "{=name}" in note or "{=code}" in note, note
    assert "register" in note.lower(), note
    assert "code" in note, f"现存寄存器要被点名：{note}"


def test_a_legitimate_reference_pays_no_hint():
    chain = [{"read": {"selector": "#challenge", "as": "code"}},
             {"fill": {"selector": "#answer", "text": "{=code}"}}]
    rows = _run(chain, _Page(dict(PAGE)))
    assert rows[1]["ok"] is True, rows[1]
    assert "note" not in rows[1], rows[1]


def test_plain_text_without_braces_pays_no_hint():
    chain = [{"read": {"selector": "#challenge", "as": "code"}},
             {"fill": {"selector": "#answer", "text": "TTUUR"}}]
    rows = _run(chain, _Page(dict(PAGE)))
    assert rows[1]["ok"] is True, rows[1]
    assert "note" not in rows[1], rows[1]


def test_the_confirm_snippet_says_which_layer_it_lives_on():
    snippet = _CONFIRM_SNIPPET
    assert '"confirm": true' in snippet, snippet
    # 层次要说清：confirm 长在动作里，不是调用顶层。两个词都点名。
    assert "click" in snippet or "submit" in snippet, snippet
    assert "action" in snippet.lower() or "step" in snippet.lower(), snippet
