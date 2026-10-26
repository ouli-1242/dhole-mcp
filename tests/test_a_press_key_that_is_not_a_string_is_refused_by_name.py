"""动作 press.key 传来非字符串时应当场具名拒绝，而不是 str() 后交给驱动、再把失败怪到 selector。

实测（round-18 P11 A10 / P15）：`{"press": {"selector": "#note", "key": ["Enter"]}}` 在
`#note` 确实存在的页面上返回
    the selector matched no element (or none became actionable) before the timeout:
    Locator.press: Unknown key: "['Enter']"
前一句是假的 —— selector 命中了，页面上也有那颗元素；真正错的是 key 的类型。
"""

from __future__ import annotations

import pytest

from dhole_mcp.actions import _validate_actions


@pytest.mark.parametrize("bad_key", [["Enter"], 13, {"code": 13}, True, None])
def test_a_non_string_press_key_is_refused_and_names_the_key_not_the_selector(bad_key):
    with pytest.raises(ValueError) as e:
        _validate_actions([{"press": {"selector": "#note", "key": bad_key}}])
    msg = str(e.value)
    assert "press.key" in msg, msg
    assert "key string" in msg, msg
    assert "selector matched no element" not in msg, msg


def test_the_bare_press_form_still_takes_a_key_string():
    (a,) = _validate_actions([{"press": "Enter"}])
    assert a == {"press": {"selector": None, "key": "Enter"}}


def test_the_dict_press_form_still_takes_a_key_string():
    (a,) = _validate_actions([{"press": {"selector": "#note", "key": "Enter"}}])
    assert a == {"press": {"selector": "#note", "key": "Enter"}}
