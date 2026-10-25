"""R07-001：寄存器具名拒绝要教「寄存器只活一条链 + pinned 页重读同值」。

现场（round-07 P02）：pin=true 的两个调用想接续 read→fill——寄存器不跨调用
（设计：手册写明 text 挑战在同一链内解、pin 解决的是 IMAGE 案的人在中途），错误
句只说「no earlier action stored that name」。填补：这句拒绝在本就是教学形状的
基础上，把「寄存器只活一条链、pin 保的是页面不是寄存器、重读即同值」补齐——
L3 照手册做跨调用链时不必再猜。
"""
import pytest

from dhole_mcp.actions import _validate_actions


def test_register_miss_teaches_scope_and_the_pinned_way():
    with pytest.raises(ValueError) as e:
        _validate_actions([
            {"fill": {"selector": "#answer", "text": "{=code}"}},
        ])
    msg = str(e.value)
    assert "{=code}" in msg and "no earlier action" in msg
    assert "one call" in msg or "this chain" in msg, msg
    assert "read" in msg and "as=" in msg
    assert "pin" in msg, msg


def test_register_miss_still_names_available_registers():
    with pytest.raises(ValueError) as e:
        _validate_actions([
            {"read": {"selector": "#a", "as": "other"}},
            {"fill": {"selector": "#answer", "text": "{=code}"}},
        ])
    assert "'other'" in str(e.value)
