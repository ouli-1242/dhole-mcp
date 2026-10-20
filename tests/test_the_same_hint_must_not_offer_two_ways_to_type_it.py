"""同一句出路里出现两种花括号，等于给了两个互相矛盾的抄写目标。

现场（2026-10-14 真实使用模拟 · P02-09 沈序（跨境电商风控运营 L2）· S2 提交闸 · R02-028 · P2 · 组 C）：

    web_act(…, pin=true) 里被拦下的那一步
      → error 前半：「… re-run the same actions with {"confirm": true} …」
      → error 后半：「… re-run just this submit step with {{"confirm": true}} on the same
        pinned session …」

同一封响应里两种写法。前半是**能抄的形状**（JSON 对象），后半抄进参数是非法 JSON。而不可逆
那一步的出路文案，正是调用方逐字抄的那一段 —— 他抄哪个都可能，两个都不是"猜"出来的，回执
自己写的。

根由一行：前半那半句在 f-string 里（`{{"confirm": true}}` 渲染成 `{"confirm": true}`），
后半那一段是**普通字符串拼接**，不是 f-string —— 转义没人处理，`{{` 原样上线。同一句话的
两种命运，取决于它有没有 `f` 前缀。

同族第二处（本文件一并量）：`_substitute_refs` 那句"填进去会超长"的报错写着
`{{=name}} is substituted`，而那一段同样不在 f-string 的处理范围内 —— 产品自己的引用形状是
`{=name}`（`_REF_RE` 就这么定义），报出来的却是一对花括号包着的 `{{=name}}`，把人往一个
不存在的语法上带。

契约（本文件钉住）：
  * 交回给调用方的任何一句话里，不许出现 `{{` 或 `}}` —— 那是模板的转义残留，不是内容；
  * 两条出路（重跑这一步 / 去掉 pin）与 pin 的事实申报一位不少，`Nothing was submitted.` 也
    不许在修句子时掉下去；
  * 引用形状只有一种写法：`{=name}`，报错里指它时不许多出一对花括号；
  * 反面锁：`human is False`（问到人了但被拒）那一支**仍然不给** `confirm` 这条路 ——
    「there is no parameter that skips the ask」那句不许被本刀的"统一形状"顺手抹平；
  * 那一句 `{"confirm": true}` 只有一位作者（src 里定义一次，两处引用同一串字）。
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from dhole_mcp import actions
from dhole_mcp.actions import _screenshot_shape_error, _submit_gate_row, _substitute_refs

SNIPPET = '{"confirm": true}'


def _gate_row(monkeypatch, *, pinned: bool, answer=None):
    async def _ask(*a, **k):
        return answer
    monkeypatch.setattr(actions, "out_of_band_confirm", _ask)
    return asyncio.run(_submit_gate_row('submit "Subscribe"', False, "on this click",
                                        pinned=pinned))["error"]


# ─── 1 · 一句话不许有两种抄法 ───


def test_the_pinned_approval_hint_renders_the_same_shape_twice(monkeypatch):
    err = _gate_row(monkeypatch, pinned=True)
    assert "{{" not in err and "}}" not in err, err
    assert SNIPPET in err, err


def test_the_plain_approval_hint_was_already_right(monkeypatch):
    """未驻留那一支是**对照组**：它早就渲染对了，本刀不许把它改坏。"""
    err = _gate_row(monkeypatch, pinned=False)
    assert SNIPPET in err and "{{" not in err, err


def test_the_pinned_branch_keeps_both_ways_and_the_verdict(monkeypatch):
    """修句子不许丢内容：pin 的事实、两条出路、没提交这三样都要在。"""
    err = _gate_row(monkeypatch, pinned=True)
    assert "pin=true" in err, err
    assert "Nothing was submitted." in err, err
    assert "fresh" in err.lower() or "only this" in err.lower() or "just this submit step" in err, err


def test_a_declined_ask_still_offers_no_confirm_back_door(monkeypatch):
    """反面锁：问到人了而人说不 —— `confirm` 救不回来，这句话不许被统一形状抹平。"""
    err = _gate_row(monkeypatch, pinned=True, answer=False)
    assert SNIPPET not in err, err
    assert "no parameter that skips the ask" in err, err


# ─── 2 · 引用形状只有一种写法 ───


def test_the_fill_length_error_names_the_reference_shape_once():
    long_value = "x" * (actions.MAX_FILL_TEXT_CHARS + 10)
    with pytest.raises(ValueError) as exc:
        _substitute_refs("{=code}", {"code": long_value}, "fill.text")
    msg = str(exc.value)
    assert "{{" not in msg and "}}" not in msg, msg
    assert "{=code}" in msg, msg


def test_the_missing_reference_error_keeps_the_shape_readable():
    with pytest.raises(ValueError) as exc:
        _substitute_refs("{=code} {=title}", {}, "fill.text")
    msg = str(exc.value)
    assert "{{" not in msg and "}}" not in msg, msg
    assert "{=code}" in msg and "{=title}" in msg, msg


def test_the_validator_rejects_a_shape_the_wire_never_shows():
    """`_screenshot_shape_error` 列的是可抄的形状：同样不许出现转义残留。"""
    msg = str(_screenshot_shape_error(3))
    assert "{{" not in msg and "}}" not in msg, msg


# ─── 3 · 一句作者 ───


def test_the_confirm_snippet_has_one_author():
    """那串可抄的 JSON 在**出厂文案**里只能定义一次（注释里提到它不算作者）。

    量的是 AST 里的字符串常量，不是源码文本：`_CONFIRM_SNIPPET = '{"confirm": true}'` 是一位
    作者，两处出路引用它的名字；解释性注释里再提一次不是第二个作者。
    """
    import ast

    tree = ast.parse(inspect.getsource(actions))
    authored = [node.value for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
                and SNIPPET in node.value]
    assert len(authored) == 1, f"那串字现在有 {len(authored)} 位作者：{authored}"
    assert authored[0] == SNIPPET, authored
