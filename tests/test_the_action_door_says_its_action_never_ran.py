# -*- coding: utf-8 -*-
"""动作那一扇门被形状顶回来时，要说"这一发动作没跑过"（R02-087 · P3）。

真机三格（`review/.runtime/p1/r92_act_shape.json` · HEAD 3b34cf1 · 本地靶站 /login）：

    A1 action 0 'click'.selector must be a non-empty CSS selector
    A2 action 0 has unknown key 'teleport'; valid: [...]
    A3 action 0 'fill' must be {selector, text} or {index, text}
       三格同句：Nothing was fetched. The action named in `error` has the wrong shape; …

`web_act` 的 wire 面 required=['url','actions']，描述第一行是 "Act on one page … then read
back what it became"。那一扇既不抓也不写：它跑一段动作。三格各 10-17ms（隐藏浏览器不可能这么快
返回，靶站也零行）—— 实话是"动作没跑过"，而句子里给的是"没抓到"。

这是 F77 那条规矩漏下的第三种门。那次立的助手 `_nothing_went_out()` 只分了两岔（会走缓存的那扇
fetched / 否则 sent），漏的原因是写死的那一处在**模块常量**里：`_ACTION_REFUSAL_WAY_OUT` 是常量，
所以它压根没经过任何分岔 —— 常量形状本身就是一种漏分岔的来路（本次也按这条改：把常量改成按门取词
的函数）。

判据三面对称：动作门的三格各说 "Nothing was run."（正面）· 写门与读门各自的词不许被顺手带跑
（反向锁）· 那句话不许再有第二个作者（一处硬编码就红）。
"""

from __future__ import annotations

import asyncio
import inspect
import json

import pytest

from dhole_mcp.server import MasterFetchServer

ACT_URL = "https://action-door.invalid/"
BAD_SHAPES = [
    ("selector 是对象", [{"click": {"selector": {"nested": 1}}}]),
    ("键名不存在", [{"teleport": {"selector": "#x"}}]),
    ("fill 收 text 不收 value", [{"fill": {"selector": "#x", "value": "v"}}]),
]


def _act(actions) -> str:
    """读这一格交回的话。`_dispatch` 对动作形状**抛** `ArgumentRefusal`（不是交回带 error 的
    载荷 —— 把它翻成载荷的是 MCP 那一层），所以两种形状都要读：抛出的那颗句子与 next_action
    就在异常对象上。F75 那把记过一次同族（判据读错了交付形状），这是第二次。
    """
    from dhole_mcp.params import ArgumentRefusal
    srv = MasterFetchServer()
    try:
        out = asyncio.run(srv._dispatch("web_act", {"url": ACT_URL, "actions": actions}))
        text = out[0][0].text
    except ArgumentRefusal as e:
        return str(e) + " " + (getattr(e, "next_action", "") or "")
    try:
        o = json.loads(text)
    except Exception:
        return text
    return (o.get("next_action") or "") + " " + (o.get("error") or "")


class TestTheActionDoorSaysItNeverRan:

    @pytest.mark.parametrize("why,actions", BAD_SHAPES)
    def test_a_refused_action_says_nothing_ran(self, why, actions):
        text = _act(actions)
        assert "Nothing was run." in text, f"{why}：{text[:220]}"
        for wrong in ("Nothing was fetched.", "Nothing was sent."):
            assert wrong not in text, f"{why}：动作门上说了别扇的话 {wrong}：{text[:220]}"

    def test_the_shape_advice_it_travels_with_stays(self):
        """换了申报口径，不许把那条真出路一起弄丢（sys_guide 那一指是本句的全部用处）。"""
        text = _act(BAD_SHAPES[0][1])
        assert "sys_guide(topic='behavior'" in text, text[:240]
        assert "Page interaction" in text, text[:240]


class TestTheOtherDoorsKeepTheirWords:
    """反向锁：第三种词不许把前两扇带跑 —— 这三格是 F77 那把的成果，不是本把的靶子。"""

    def test_the_write_door_still_says_sent(self):
        srv = MasterFetchServer()
        out = asyncio.run(srv._dispatch("web_request", {
            "url": "https://write-door.invalid/", "method": "HEAD",
            "headers": {"Host": "elsewhere.invalid"}}))
        text = out[0][0].text
        assert "Nothing was sent." in text, text[:220]
        assert "Nothing was run." not in text, text[:220]

    def test_the_read_door_still_says_fetched(self):
        srv = MasterFetchServer()
        out = asyncio.run(srv._dispatch("web_read", {
            "url": "https://read-door.invalid/", "headers": {"Host": "x.invalid"}}))
        text = out[0][0].text
        assert "Nothing was fetched." in text, text[:220]
        assert "Nothing was run." not in text, text[:220]


class TestTheSentenceHasOneAuthor:

    def test_the_action_way_out_is_not_a_hardcoded_constant_anymore(self):
        """漏分岔的来路之一就是常量：句子必须从助手取词。"""
        import dhole_mcp.server as m
        assert not isinstance(getattr(m, "_ACTION_REFUSAL_WAY_OUT", None), str), (
            "还是模块常量 —— 常量没法按门取词，这就是这一句在动作门上说 fetched 的来路")
        fn = m._action_refusal_way_out
        # 只看**代码里的字面量**，不看 docstring —— 那一处 docstring 在引用被修的原文
        # （F77 的同一把尺：判据不许把"解释这句话从哪来"当成"手抄了这句话"）。
        consts = [c for c in fn.__code__.co_consts
                  if isinstance(c, str) and c != fn.__doc__]
        for c in consts:
            assert "Nothing was" not in c, f"函数里手抄了句子口径：{c[:60]!r}"
        src = inspect.getsource(fn)
        assert "_nothing_went_out(" in src

    def test_the_helper_names_three_doors_not_two(self):
        from dhole_mcp.server import _nothing_went_out
        src = inspect.getsource(_nothing_went_out)
        assert '"actions"' in src, "动作门的分岔判据没在助手里"
        assert "door" in inspect.getsource(_nothing_went_out).splitlines()[0], (
            "动作车道上 _TOOL 是 web_read（web_act 在 web_read 的实现里跑），"
            "所以门名要能由调用点交进来")
        assert _nothing_went_out() in ("Nothing was fetched.", "Nothing was sent.",
                                      "Nothing was run.")
