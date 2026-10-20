# -*- coding: utf-8 -*-
"""读一个填过的框，必须读出页面上现在那个值 —— 不是标签上那一个。

症状（15 场景真人体测，场景 12，实录见 `review/`）：靶站 `/checkout` 上只有两个字段，
`item` 默认"机械键盘"、`qty` 默认 **"1"**。要提交的是 qty=3。于是需要一次回读来确认
"我填进去的 3 到底在不在页面上"，因为下一步是**不可逆的写库**（服务端往 orders.log
追加一行，撤不回来）。

`{"read": {"selector": "#qty", "attribute": "value"}}` —— 这是 dhole 自己在空读数提示里
教的那一条门（`actions.py` 旧行 1490-1493："an input's value needs {"attribute": "value"}"）
—— 交回的是 `"1"`。而 `fill` 的回执是 `ok:true`。最小复现换一枚值（99）重跑同一条链，
照样是 `"1"`：

    fill '#qty' ok:true  →  read '#qty' @value = "1"

真相由服务端旁证给出：真发一次之后 `orders.log` 多出的那一行是 `qty: "3"` —— **fill
一直是对的，是那一读取错了层**。`get_attribute("value")` 读的是标签上的 attribute，
而 fill 改的是 DOM 的 property；`<input>` 上这两个只有在这一行代码之前碰巧相等才看着一致。

所以这不是"读数有点糙"，是**按下提交之前唯一的回读手段会指着一枚旧数说"这是现在的值"**。
实测代价：4 次复验调用、约 44 秒，才敢发一次已经填对了的订单。

同一文件里本来就有一处反证：`snapshot` / `expect` 认的名字走的是活的那一层
（`_NAME_JS` 里那两行 `el.value`）。同一个页面两套口径，人只能挑一个信 —— 这一刀把它们
统一到活的那一侧，并且把读的是哪一层写在回执上（`read_via`），两层的数不一样时把标签
那一层也带回去（`attribute_says`）：**两数不等本身就是"填进去的字此刻在页面上"的凭据**，
一次调用说完，不必再来回四轮。

判据的形状：

1. **问 attribute 也答 property（仅限表单域）** —— 旧写法不能变成一个新的坑；调用方
   已经按那句提示写了 `attribute:"value"` 的，拿回来的必须是活的值，并且要知道自己被换了层。
2. **非表单域不许被换** —— `<a href>` 之类照旧走 `get_attribute`，读到的就是标签上那个。
3. **两层的差要如实上线，相等时不许谎报** —— `attribute_says` 只在不相等时出现。
4. **没有这一层是具名失败，不是空串** —— 把"够不着"读成"值是空的"，是这一族最贵的误读
   （一张空的验证码框和一个压根没有 value 的 `<div>`，在回执里会长得一模一样）。
5. **那句指路的话不能再指着错的门** —— 指错了比不指更糟，因为调用方会照着它信一个假证据。
6. **两个来源同时给是笔误，不是组合** —— 同 `_act_target` 的 selector/index 规矩。
"""

from __future__ import annotations

import asyncio
import re

import pytest

from dhole_mcp import wire
from dhole_mcp.actions import (
    _READ_PROPERTY_JS,
    _TAG_JS,
    _validate_actions,
    action_label,
    build_page_action,
)
from dhole_mcp.models import ResponseModel

# ─── 一页会把 attribute 和 property 分开存的迷你 DOM ─────────────────


class _El:
    """`attrs` 是标签上那一层，`props` 是页面上现在那一层。

    `fill` **只动 props** —— 这一条是整个测试的物理基础。真实的 `<input>` 就是这样：
    写 `el.value = "3"` 不会重写 markup 里的 `value="1"`，所以 outerHTML 与 .value
    可以长期不一致，而只有后者会被提交上去。
    """

    def __init__(self, tag="DIV", text="", attrs=None, props=None):
        self.tag = tag.upper()
        self.text = text
        self.attrs = dict(attrs or {})
        self.props = dict(props or {})
    async def inner_text(self, timeout=None):
        return self.text

    async def get_attribute(self, attr):
        return self.attrs.get(attr)

    async def fill(self, value, timeout=None):
        self.props["value"] = value

    async def evaluate(self, js, arg=None):
        if js is _TAG_JS:
            # 交回的是**那颗 JS 实际会交回的形状**（`toLowerCase()`），不是"一个标签名"。
            # 第一版这里写的是 `return self.tag`（大写），于是 mocks 与真机在同一个字符串
            # 上给出两种答案，`_VALUEISH_TAGS` 的大小写错了整整一轮没人测到 ——
            # 靶站复验（`evals/verify_fixes.py` 的 1.1）量到的才是真相。
            return self.tag.lower()
        if js is _READ_PROPERTY_JS:
            if arg not in self.props:
                return None
            return self.props[arg]
        return None


class _Loc:
    def __init__(self, el):
        self._el = el

    @property
    def first(self):
        return self

    async def inner_text(self, timeout=None):
        return await self._el.inner_text()

    async def get_attribute(self, attr):
        return await self._el.get_attribute(attr)

    async def fill(self, value, timeout=None):
        await self._el.fill(value)

    async def evaluate(self, js, arg=None):
        return await self._el.evaluate(js, arg)


class _Page:
    def __init__(self, by_selector):
        self.els = by_selector

    def locator(self, sel):
        if sel not in self.els:
            raise RuntimeError(f"waiting for locator {sel!r}")
        return _Loc(self.els[sel])


def _run(chain, page):
    pa = build_page_action(chain)
    asyncio.run(pa(page))
    return pa.outcomes


def _filled_box():
    """markup 说 1，页面上现在放着 3 —— 就是场景 12 的那一格。"""
    return _Page({"#qty": _El(tag="INPUT", attrs={"value": "1"}, props={"value": "3"})})


# ─── 1 · 那一次 44 秒的犹豫：问 attribute，也得拿到活的值 ────────────


def test_a_filled_box_reads_the_value_the_page_holds_not_the_one_the_markup_shipped():
    rows = _run([{"read": {"selector": "#qty", "attribute": "value"}}], _filled_box())
    assert rows[0]["ok"] is True
    assert rows[0]["read"] == "3", "读回 1 就是那次'不敢发'的起点"


def test_the_receipt_says_which_layer_it_answered_from():
    rows = _run([{"read": {"selector": "#qty", "attribute": "value"}}], _filled_box())
    assert rows[0]["read_via"].startswith("property:value")


def test_the_two_layers_disagreeing_is_itself_the_evidence_the_fill_landed():
    """两数不等 → `attribute_says` 上线。这一行不需要第二次调用就回答了"填上了吗"。"""
    rows = _run([{"read": {"selector": "#qty", "attribute": "value"}}], _filled_box())
    assert rows[0]["attribute_says"] == "1"


def test_a_box_still_matching_its_markup_does_not_claim_a_difference():
    page = _Page({"#qty": _El(tag="INPUT", attrs={"value": "1"}, props={"value": "1"})})
    rows = _run([{"read": {"selector": "#qty", "attribute": "value"}}], page)
    assert rows[0]["read"] == "1"
    assert "attribute_says" not in rows[0], "相等时谎报一层，是制造一条不存在的证据"


# ─── 2 · 显式那一层，与非表单域的老路 ───────────────────────────────


def test_an_explicit_property_read_answers_from_the_live_layer():
    rows = _run([{"read": {"selector": "#qty", "property": "value"}}], _filled_box())
    assert rows[0]["read"] == "3"
    assert rows[0]["read_via"] == "property:value"


def test_an_attribute_read_on_a_link_still_reads_the_markup():
    """`href` 那一族本来就没有两层可分 —— 不许为了修 input 把别的读数都换掉。"""
    page = _Page({"a.next": _El(tag="A", attrs={"href": "/p/2"})})
    rows = _run([{"read": {"selector": "a.next", "attribute": "href"}}], page)
    assert rows[0]["read"] == "/p/2"
    assert rows[0]["read_via"] == "get_attribute:href"


def test_a_textarea_and_a_select_are_treated_as_form_fields_too():
    for tag in ("TEXTAREA", "SELECT"):
        page = _Page({"f": _El(tag=tag, attrs={"value": "旧"}, props={"value": "新"})})
        rows = _run([{"read": {"selector": "f", "attribute": "value"}}], page)
        assert rows[0]["read"] == "新", tag


# ─── 3 · 够不着与读错了地方：两种失败要分开说 ───────────────────────


def test_every_read_mode_names_the_layer_it_answered_from():
    """三种读法都得留出处。少一种，读的人就只能在两层之间猜自己拿到的是哪一层。"""
    page = _Page({"#p": _El(tag="P", text="正文"),
                  "a.n": _El(tag="A", attrs={"href": "/x"}),
                  "#q": _El(tag="INPUT", attrs={"value": "1"}, props={"value": "3"})})
    rows = _run([{"read": {"selector": "#p"}},
                 {"read": {"selector": "a.n", "attribute": "href"}},
                 {"read": {"selector": "#q", "property": "value"}}], page)
    assert [r["read_via"] for r in rows] == ["inner_text", "get_attribute:href",
                                             "property:value"]


def test_a_property_the_element_does_not_have_fails_by_name_not_as_an_empty_read():
    """<div> 没有 value 这一层。交回 "" 会被读成"这框是空的"，那是另一种误判。"""
    page = _Page({"#box": _El(tag="DIV", text="正文", props={})})
    rows = _run([{"read": {"selector": "#box", "property": "value"}}], page)
    assert rows[0]["ok"] is False
    err = rows[0]["error"]
    assert "div" in err.lower() and "form control" in err, err


def test_an_empty_form_field_says_that_is_its_current_value_not_a_missing_read():
    page = _Page({"#qty": _El(tag="INPUT", attrs={"value": ""}, props={"value": ""})})
    rows = _run([{"read": {"selector": "#qty", "attribute": "value"}}], page)
    assert rows[0]["ok"] is False
    assert "current value" in rows[0]["error"], rows[0]["error"]


# ─── 4 · 那句指路的话不许再指着错的门 ───────────────────────────────


def test_the_empty_text_read_now_points_at_the_layer_that_actually_works():
    page = _Page({"#challenge": _El(tag="P", text="   ")})
    rows = _run([{"read": {"selector": "#challenge"}}], page)
    err = rows[0]["error"]
    assert '"property"' in err and "value" in err, err
    assert "screenshot" in err, "图里的挑战那一扇门不能丢"


def test_the_wrong_door_is_not_recommended_as_a_way_to_read_a_field():
    """这条是整刀的守门人：旧提示教人用 attribute 读框的值，而那正是假证据的来源。

    它回来说"这一行只是把'没读到'原样交回给人猜"—— 猜错一次的代价是 4 次调用，
    猜错的时机是不可逆写之前。
    """
    page = _Page({"#challenge": _El(tag="P", text="")})
    rows = _run([{"read": {"selector": "#challenge"}}], page)
    err = rows[0]["error"]
    assert re.search(r"an input'?s value needs \{\"attribute\"", err) is None, err


def test_the_new_hint_matches_what_the_code_will_actually_return():
    """指路句与实现必须同向：照着提示读一个填过的框，拿到的就是活的值。"""
    page = _Page({"#answer": _El(tag="INPUT", attrs={"value": ""}, props={})})
    _run([{"read": {"selector": "#answer", "property": "value"}}], page)  # shape probe
    filled = _filled_box()
    rows = _run([{"read": {"selector": "#qty", "property": "value"}}], filled)
    assert rows[0]["read"] == "3"


# ─── 5 · 校验：两个来源同时给是笔误 ─────────────────────────────────


def test_asked_for_both_layers_is_refused_as_a_typo_not_a_combination():
    with pytest.raises(ValueError, match="not both"):
        _validate_actions([{"read": {"selector": "#q", "attribute": "value",
                                     "property": "value"}}])


@pytest.mark.parametrize("bad", ["href", "textContent", "", "value ", "VALUE", 7])
def test_a_property_outside_the_fillable_three_is_refused_by_name(bad):
    """value/checked/selected 之外没有"fill 会改的 property"这一说 —— 收了就是空头承诺。"""
    with pytest.raises(ValueError, match="read.property"):
        _validate_actions([{"read": {"selector": "#q", "property": bad}}])


def test_the_label_separates_the_two_layers_at_a_glance():
    assert (action_label({"read": {"selector": "#q", "attribute": "value"}})
            == "read '#q' @value")
    assert (action_label({"read": {"selector": "#q", "property": "value"}})
            == "read '#q' @value (property)")


# ─── 6 · 新键要活着走到线上，并且写在人查的地方 ─────────────────────


def test_read_via_and_attribute_says_survive_the_wire_dump():
    page = _filled_box()
    resp = ResponseModel(url="https://example.com", status=200, content=[])
    pa = build_page_action([{"read": {"selector": "#qty", "attribute": "value"}}])
    asyncio.run(pa(page))
    from dhole_mcp.quality import _report_action_outcomes
    _report_action_outcomes(resp, pa)
    row = wire._wire_dump(resp)["metadata"]["actions"][0]
    assert row["read"] == "3"
    assert row["read_via"].startswith("property:") and row["attribute_says"] == "1"


def test_the_two_layers_are_explained_in_the_volume_a_caller_reads():
    """常驻层有字数闸，所以这件事写在按需取的那一卷 —— 不在就等于只在源码里。"""
    from dhole_mcp import guide
    _header, behavior = guide.read("behavior")
    assert re.search(r"(?<![\w.\"])property(?![\w\"])", behavior)
    for needle in ("attribute", "property"):
        assert needle in behavior, needle


def test_the_new_receipt_keys_are_documented_where_a_field_is_looked_up():
    from dhole_mcp import guide
    fields = (guide._ROOT / "references" / "fields.md").read_text(encoding="utf-8")
    for needle in ("read_via", "attribute_says"):
        assert needle in fields, needle

def test_the_tag_probe_is_case_matched_by_the_js_it_ships():
    """`_VALUEISH_TAGS` 的大小写必须由**发出去的那颗 JS** 决定，且那颗 JS 只有一个定义。

    这一条是复验（`evals/verify_fixes.py` 的 1.1）量出来的账，不是想象的守卫：第一版
    在这里写了一个大写集合，又在它上面"再定义"了一遍 `_TAG_JS`（同名、模块后被覆盖），
    于是真机上 `tag in _VALUEISH_TAGS` 恒为假 —— 问 attribute 的那一读照旧回死值，而
    离线 mock 交回的是大写标签名，测不出这个形状。两条一起钉：JS 说小写，集合就得小写；
    同一个名字不许有第二处赋值。
    """
    import inspect

    from dhole_mcp import actions as A

    assert "toLowerCase()" in A._TAG_JS, A._TAG_JS
    assert all(tag == tag.lower() for tag in A._VALUEISH_TAGS), A._VALUEISH_TAGS
    src = inspect.getsource(A)
    defs = [ln for ln in src.splitlines() if ln.startswith("_TAG_JS =")]
    assert len(defs) == 1, f"一颗 JS 有两个作者，后写的那个会静默赢下前一个：{defs}"


def test_a_form_control_is_matched_whatever_case_the_page_gives_back():
    """正向件：`<input>` 无论大小写都要走到活的那一层（判据不该由标记的写法决定）。"""
    for tag in ("INPUT", "input", "Input"):
        page = _Page({"#qty": _El(tag=tag, attrs={"value": "1"}, props={"value": "3"})})
        rows = _run([{"read": {"selector": "#qty", "attribute": "value"}}], page)
        row = rows[0]
        assert row["read"] == "3", (tag, row)
        assert row["read_via"].startswith("property:"), (tag, row)
