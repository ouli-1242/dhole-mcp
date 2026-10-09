# -*- coding: utf-8 -*-
"""同一条链里读到的挑战，必须能在同一条链里答掉 —— 跨调用重试答的是上一页的答案。

症状（15 场景真人体测的交互组 + 验证码组，实录见 `review/`）：常驻层的 `web_act` 描述
写着 "Captchas get at most 3 read-and-retry rounds, then hand off"。这句话在**每一次调用
都会重新导航**的实现上是做不到的：读到的挑战属于上一个页面实例，下一次调用导航过去拿到的是
新的一枚，于是"重试"填进去的是上一页的答案。实测靶站 `/captcha` 每次 `GET` 出新的 token
（答案由 token 派生），那一格就是这样翻车的 —— 提交 `72G69` 而站点那枚是 `CJN2Y`。

`{"screenshot": ...}` 救不了这一格：那一张图要到整条链跑完才交得到调用方眼前，链里的下一步
看不见它。所以当挑战**写在 DOM 的文本里**（真实站点上很常见：「请输入下方显示的字符」）时，
缺的不是识别能力，是"读回来的那一段能被同一条链的下一步用"。这一刀加的就是那一件事：

    [{"read": {"selector": "#challenge", "as": "code"}},
     {"fill": {"selector": "#answer", "text": "{=code}"}},
     {"submit": {"selector": "#btn"}}]

判据的形状：

1. **顺序是判据的一部分**。`{=名字}` 只引用跑在它之前的 read —— 指着还没发生的事不是
   等待，是写错了。
2. **引用不是通配的**。裸 `{code}` 是文本，不引用任何东西；引用要写 `{=code}`。反过来
   写了 `{=code}` 而没有那一次读，是**具名拒绝**，不是把六个字符原样填进表单 ——
   后者会伪装成一次成功的 fill，提交出去的是占位符本身。
3. **空读数不是一次成功的读**。元素的文本为空时，那一行要指名另两扇门（框的值走
   `attribute`，图里的挑战走 `screenshot`），而不是交出 `read: ""` 让人猜自己读错了地方。
4. **帽要说，寄存器不砍**。回执里的 `read` 有字符帽，帽生效时 `read_truncated` 与原文
   长度一起上线；而 `{=名字}` 展开的是**全文** —— 截断是说给读这一行的人听的，填回表单
   的那一半不该拿到一枚被砍过的答案。
5. **一次导航一份寄存器**。`page_action` 每次跑都清空，跨调用、跨页面都不带走上一枚值。
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from dhole_mcp import guide, wire
from dhole_mcp.actions import (
    MAX_ACTION_READ_CHARS,
    _VALID_KEYS,
    _validate_actions,
    action_label,
    build_page_action,
)
from dhole_mcp.models import ResponseModel
from dhole_mcp.server import MasterFetchServer

# ─── 假页面：只长这一次判据需要的器官 ────────────────────────────────


class _El:
    def __init__(self, text="", attrs=None, name=""):
        self.text = text
        self.attrs = dict(attrs or {})
        self.name = name
        self.filled: list[str] = []
        self.hovered = 0

    async def inner_text(self, timeout=None):
        return self.text

    async def get_attribute(self, attr):
        return self.attrs.get(attr)

    async def fill(self, value, timeout=None):
        self.filled.append(value)

    async def evaluate(self, js, arg=None):
        return self.name


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

    async def get_attribute(self, attr):
        return await self._el.get_attribute(attr)

    async def fill(self, value, timeout=None):
        await self._el.fill(value)

    async def evaluate(self, js, arg=None):
        return await self._el.evaluate(js)


class _Page:
    def __init__(self, by_selector: dict[str, _El]):
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


CHAIN = [{"read": {"selector": "#challenge", "as": "code"}},
         {"fill": {"selector": "#answer", "text": "{=code}"}}]


# ─── 1 · 校验：形状、名字、顺序 ────────────────────────────────────

def test_read_is_a_registered_action():
    assert "read" in _VALID_KEYS


def test_a_bare_selector_and_the_dict_form_both_normalize():
    (a,) = _validate_actions([{"read": "#challenge"}])
    assert a == {"read": {"selector": "#challenge"}}
    (b,) = _validate_actions([{"read": {"index": 3, "expect": "字符", "attribute": "value"}}])
    assert b == {"read": {"index": 3, "expect": "字符", "attribute": "value"}}


def test_a_target_is_mandatory_and_one_of_selector_or_index():
    with pytest.raises(ValueError, match="exactly one of"):
        _validate_actions([{"read": {"selector": "#a", "index": 1}}])
    with pytest.raises(ValueError, match="read"):
        _validate_actions([{"read": {}}])


@pytest.mark.parametrize("bad", ["Bad Name", "a" * 40, "with space", 7, ""])
def test_as_names_are_refused_by_name(bad):
    with pytest.raises(ValueError, match="read.as"):
        _validate_actions([{"read": {"selector": "#c", "as": bad}}])


@pytest.mark.parametrize("bad", ["onmouseover=x", "", "a b", 7, "x" * 60])
def test_attribute_names_are_refused_by_name(bad):
    with pytest.raises(ValueError, match="read.attribute"):
        _validate_actions([{"read": {"selector": "#c", "attribute": bad}}])


def test_a_reference_to_nothing_is_refused_instead_of_being_filled_verbatim():
    """这一条是整刀的支点：写错的引用若原样填进表单，回执会显示一次成功的 fill。"""
    with pytest.raises(ValueError) as e:
        _validate_actions([{"fill": {"selector": "#a", "text": "{=code}"}}])
    assert "{=code}" in str(e.value)
    assert "no" in str(e.value) and "read" in str(e.value)


def test_a_reference_only_reaches_backwards_not_forwards():
    """先 fill 后 read 是写反了顺序，不是"稍后就绪"。"""
    with pytest.raises(ValueError, match="no earlier action stored"):
        _validate_actions([{"fill": {"selector": "#a", "text": "{=code}"}},
                           {"read": {"selector": "#c", "as": "code"}}])


def test_a_refused_reference_names_what_is_available():
    with pytest.raises(ValueError) as e:
        _validate_actions([{"read": {"selector": "#c", "as": "hint"}},
                           {"fill": {"selector": "#a", "text": "{=code}"}}])
    assert "available" in str(e.value) and "hint" in str(e.value)


def test_a_plain_brace_is_text_not_a_reference():
    """`{code}` 是模板、JSON、说明文字里常见的字面量 —— 引用要写 `{=code}`。"""
    (a,) = _validate_actions([{"fill": {"selector": "#a", "text": "literal {code} here"}}])
    assert a["fill"]["text"] == "literal {code} here"


def test_the_label_says_what_was_read_and_what_it_was_named():
    assert action_label({"read": {"selector": "#c", "as": "code"}}) == "read '#c' as 'code'"
    assert action_label({"read": {"index": 2, "attribute": "value"}}) == "read [2] @value"


# ─── 2 · 执行：读进回执、填进表单 ──────────────────────────────────

def test_a_read_lands_in_the_receipt_and_a_later_fill_uses_it():
    page = _Page({"#challenge": _El(text="  K7Q2Z \n"), "#answer": _El()})
    rows = _run(CHAIN, page)
    assert [r["ok"] for r in rows] == [True, True]
    assert rows[0]["read"] == "K7Q2Z"
    assert rows[0]["read_as"] == "code"
    assert page.els["#answer"].filled == ["K7Q2Z"]


def test_the_value_that_goes_in_is_the_one_this_page_showed():
    """两枚挑战、两次导航：第二条链填的是第二条页面上的那一枚，不是上一页的答案。

    这一条钉的就是那句 "read-and-retry rounds" 做不到的事 —— 它做不到的原因不在重试，
    在每次重试都换一枚；在同一条链里读到并填回，才第一次让"看到的"与"答的"是同一枚。
    """
    first = _Page({"#challenge": _El(text="AAAAA"), "#answer": _El()})
    _run(CHAIN, first)
    second = _Page({"#challenge": _El(text="ZZZZZ"), "#answer": _El()})
    _run(CHAIN, second)
    assert first.els["#answer"].filled == ["AAAAA"]
    assert second.els["#answer"].filled == ["ZZZZZ"]


def test_text_around_the_reference_survives_the_substitution():
    page = _Page({"#challenge": _El(text="K7Q2Z"), "#answer": _El()})
    _run([{"read": {"selector": "#challenge", "as": "code"}},
          {"fill": {"selector": "#answer", "text": "prefix-{=code}-suffix"}}], page)
    assert page.els["#answer"].filled == ["prefix-K7Q2Z-suffix"]


def test_an_attribute_read_reaches_the_fill_too():
    """框的值不是元素的文本 —— 读它要 `attribute`，这一格把那句话演一遍。"""
    page = _Page({"#tok": _El(text="", attrs={"value": "TK-9"}), "#answer": _El()})
    rows = _run([{"read": {"selector": "#tok", "attribute": "value", "as": "t"}},
                 {"fill": {"selector": "#answer", "text": "{=t}"}}], page)
    assert rows[0]["read"] == "TK-9"
    assert page.els["#answer"].filled == ["TK-9"]


# ─── 3 · 读数不在时：那一行怎么说 ─────────────────────────────────

def test_an_empty_text_read_fails_the_step_and_names_the_other_two_doors():
    page = _Page({"#challenge": _El(text="   "), "#answer": _El()})
    rows = _run(CHAIN, page)
    assert rows[0]["ok"] is False
    err = rows[0]["error"]
    assert "attribute" in err and "value" in err
    assert "screenshot" in err


def test_an_empty_text_read_fills_nothing_and_says_the_reference_did_not_resolve():
    """读失败之后，引用它的那一步不许伪装成功 —— 填进表单的不能是 `{=code}` 那六个字符。"""
    page = _Page({"#challenge": _El(text=""), "#answer": _El()})
    rows = _run(CHAIN, page)
    assert rows[1]["ok"] is False
    assert "metadata.actions" in rows[1]["error"]
    assert page.els["#answer"].filled == []


def test_an_absent_attribute_reports_as_absent_not_as_empty_page_text():
    page = _Page({"#tok": _El(text="无关的正文", attrs={}), "#answer": _El()})
    rows = _run([{"read": {"selector": "#tok", "attribute": "value", "as": "t"}}], page)
    assert rows[0]["ok"] is False
    assert "'value'" in rows[0]["error"]
    assert "screenshot" not in rows[0]["error"]


def test_a_selector_that_matches_nothing_is_the_usual_loud_failure():
    page = _Page({"#answer": _El()})
    rows = _run(CHAIN, page)
    assert rows[0]["ok"] is False
    assert "no element" in rows[0]["error"]


# ─── 4 · 帽：说给读的人听，但不砍给表单 ────────────────────────────

def test_a_long_read_is_capped_in_the_receipt_and_says_so_with_the_original_length():
    long = "甲" * (MAX_ACTION_READ_CHARS + 37)
    page = _Page({"#challenge": _El(text=long), "#answer": _El()})
    rows = _run(CHAIN, page)
    assert rows[0]["read"] == long[:MAX_ACTION_READ_CHARS]
    assert rows[0]["read_truncated"] is True
    assert rows[0]["read_chars_total"] == len(long.strip())


def test_the_cap_announces_itself_only_when_it_bites():
    page = _Page({"#challenge": _El(text="SHORT"), "#answer": _El()})
    rows = _run(CHAIN, page)
    assert "read_truncated" not in rows[0] and "read_chars_total" not in rows[0]


def test_the_register_holds_the_whole_value_not_the_capped_head():
    """回执给的是帽内的前一段，填回表单的必须是全文：截断是显示的事，不是答案的事。"""
    long = "X" * (MAX_ACTION_READ_CHARS + 500)
    page = _Page({"#challenge": _El(text=long), "#answer": _El()})
    _run(CHAIN, page)
    assert page.els["#answer"].filled == [long]


def test_a_substitution_that_would_outgrow_the_field_is_refused_by_name():
    page = _Page({"#challenge": _El(text="X" * 4_600), "#answer": _El()})
    rows = _run([{"read": {"selector": "#challenge", "as": "code"}},
                 {"fill": {"selector": "#answer", "text": ("P" * 600) + "{=code}"}}], page)
    assert rows[1]["ok"] is False
    assert "5,000" in rows[1]["error"]
    assert page.els["#answer"].filled == []


# ─── 5 · 回执上的新键要活着走到线上，并且写在手册里 ─────────────────

def test_the_new_receipt_keys_survive_the_wire_dump():
    """`metadata.actions` 的行是 dict，逐键透传 —— 这条量的是"真的透传"，不是应当如此。"""
    page = _Page({"#challenge": _El(text="K7Q2Z"), "#answer": _El()})
    resp = ResponseModel(url="https://example.com", status=200, content=[])
    pa = build_page_action(CHAIN)
    asyncio.run(pa(page))
    from dhole_mcp.quality import _report_action_outcomes
    _report_action_outcomes(resp, pa)
    dumped = wire._wire_dump(resp)
    row = dumped["metadata"]["actions"][0]
    assert row["read"] == "K7Q2Z" and row["read_as"] == "code"


def test_the_read_action_is_named_where_a_caller_decides_what_to_send():
    """常驻层那两份（动作清单 + 工具描述）都要说得出这件事。

    清单给名字，描述给"读了之后怎么用"那一对 —— `{=code}` 的记号从前不存在，
    只见过旧 wire 的调用方会像体测里那样，把截图当成"下一步能照着填"的东西。
    """
    td = next(d for d in MasterFetchServer._TOOL_DEFS if d["name"] == "web_act")
    listed = td["inputSchema"]["properties"]["actions"]["description"]
    tool = td["description"]
    assert re.search(r"(?<![\w-])read(?![\w-])", listed), listed
    assert '"read"' in tool and "{=code}" in tool, tool


def test_the_impossible_promise_is_not_back_on_the_wire():
    """两句做不到的话不许回来 —— 它们是本刀撤下来的东西，不是风格问题。

    `read-and-retry rounds`：每次调用都重新导航，第二轮读的是新的一枚，填的是上一轮的
    答案（实测靶站 `/challenge/text` 两次导航的 token 不同，见 `tests/
    test_a_chain_answers_the_challenge_of_the_page_it_is_standing_on.py`）。
    `a CAPTCHA you are about to type`：截图要等整条链跑完才交得到调用方眼前，链里没有
    一步能照着它打字。两句都是**承诺了实现给不了的杠杆**，而调用方按它花的是一整轮
    浏览器时间（体测里那一格烧掉 3 次调用）。
    """
    blob = json.dumps(MasterFetchServer._TOOL_DEFS, ensure_ascii=False)
    for banned in ("read-and-retry", "about to type"):
        assert banned not in blob, f"wire 上又出现那句做不到的承诺: {banned!r}"


def test_the_reference_shape_is_in_a_volume_the_guide_serves():
    """`{=名字}` 与那两扇替代门（`attribute` / `screenshot`）得写在按需取的那一卷里。

    常驻层只带得出动作名（那颗 296 字符的闸在前一刀刚试过），所以这一卷是调用方
    查"怎么把读回来的东西用掉"的地方 —— 它不在，能力就等于只在源码里。
    """
    _header, behavior = guide.read("behavior")
    for needle in (r"\{=code\}", r"(?<![\w.\"])as(?![\w\"])",
                   r"(?<![\w.\"])attribute(?![\w\"])",
                   "for a challenge drawn as an image",
                   "already gone on the next"):
        assert re.search(needle, behavior), needle


def test_the_receipt_keys_are_documented_where_a_reader_looks_up_a_field():
    """回执上的新键进 fields.md —— 那才是查"这一行为什么这么写"的地方。"""
    from dhole_mcp import guide
    fields = (guide._ROOT / "references" / "fields.md").read_text(encoding="utf-8")
    for needle in ("read_truncated", "read_chars_total", "read_as"):
        assert needle in fields, needle
