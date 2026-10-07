"""R2刀3（P2-8）：`{"screenshot": ...}` 的三条校验自相矛盾，同一件事有三种说法。

评审现场的三个读数：
  1. `{"screenshot": true}` → 拒。文本说「must be a CSS selector string or
     {{selector, decode_qr, ...}}」——花括号是 Python 字符串拼接漏出的伪影，
     而它给出的形状清单也不全（没有 full_page）。
  2. `{"screenshot": {"decode_qr": true}}`（dict，没有目标）→ 拒，但换了一句
     「needs exactly one of 'selector' (CSS) or 'index'」——这句是 click/fill 共用的，
     它不提 full_page 也是一种合法形状。
  3. `{"screenshot": {"full_page": true}}`（同样没有 selector/index）→ **成功**。
三条放一起读不出规则，纯视口截图在语法上做不到。

修法：一条规则一句报错 —— selector 串 / dict(selector) / dict(index) /
dict(full_page=true) 四种形状合法；其余一律同一句话，句里列全四种，且不带花括号伪影。
bare `true` 的语义（要不要实现成视口截图）是待 operator 判决项，这里不改它的判决，
只让它的报错与事实一致。
"""

import pytest

from dhole_mcp.actions import _validate_actions

LEGAL = [
    ("h1", {"selector": "h1", "image_type": "png", "decode_qr": False}),
    ({"selector": "h1"}, {"selector": "h1", "image_type": "png",
                          "decode_qr": False}),
    ({"index": 2}, {"index": 2, "image_type": "png", "decode_qr": False}),
    ({"full_page": True}, {"full_page": True, "image_type": "png",
                           "decode_qr": False}),
]


def _err(val):
    with pytest.raises(ValueError) as e:
        _validate_actions([{"screenshot": val}])
    return str(e.value)


class TestTheFourLegalShapes:
    """四种形状各一条：说出规则就得接住规则自己列的每一种写法。"""

    @pytest.mark.parametrize("val,expected", LEGAL)
    def test_each_shape_is_accepted(self, val, expected):
        (action,) = _validate_actions([{"screenshot": val}])
        assert action["screenshot"] == expected

    def test_full_page_needs_no_target(self):
        """第 3 条读数本来就是对的：整页截图没有元素可言，不该被要求指一个元素。"""
        (action,) = _validate_actions([{"screenshot": {"full_page": True}}])
        assert action["screenshot"]["full_page"] is True


class TestTheRefusalsSayOneThing:
    @pytest.mark.parametrize("val", [True, 1, 3.5, "  ", [1],
                                     {"decode_qr": True}, {}, {"foo": 1}])
    def test_refused(self, val):
        with pytest.raises(ValueError):
            _validate_actions([{"screenshot": val}])

    def test_bare_true_and_a_targetless_dict_get_the_same_sentence(self):
        """同一类输入只有一种答案：两条拒绝路径必须念同一句话。"""
        assert _err(True) == _err({"decode_qr": True})

    def test_the_sentence_lists_every_shape_the_validator_accepts(self):
        text = _err(True)
        for needle in ("selector", "index", "full_page"):
            assert needle in text, text

    def test_no_python_brace_artifact(self):
        """`{{` 是字符串拼接漏出的伪影，不是给调用方的语法。"""
        for val in (True, {"decode_qr": True}, 3):
            assert "{{" not in _err(val) and "}}" not in _err(val)

    def test_the_rule_names_the_optional_knobs_too(self):
        """旧句子里唯一有用的信息（可选旋钮名单）不许在收敛时被顺手删掉。"""
        text = _err(True)
        for needle in ("image_type", "decode_qr", "save_to"):
            assert needle in text, text

    def test_the_action_index_survives(self):
        with pytest.raises(ValueError) as e:
            _validate_actions([{"click": "a"}, {"screenshot": True}])
        assert "action 1" in str(e.value)


class TestTheBoundariesThatWereAlreadyRight:
    """收敛报错不许顺手改掉既有裁决。"""

    def test_full_page_with_a_target_is_still_refused(self):
        with pytest.raises(ValueError, match="cannot combine"):
            _validate_actions([{"screenshot": {"full_page": True,
                                               "selector": "h1"}}])

    def test_selector_and_index_together_is_still_refused(self):
        with pytest.raises(ValueError):
            _validate_actions([{"screenshot": {"selector": "h1", "index": 2}}])

    def test_the_knobs_are_still_checked(self):
        for bad in ({"selector": "h1", "decode_qr": "yes"},
                    {"selector": "h1", "image_type": "webp"},
                    {"selector": "h1", "quality": 900},
                    {"selector": "h1", "quality": 50}):
            with pytest.raises(ValueError):
                _validate_actions([{"screenshot": bad}])

    def test_the_other_actions_keep_their_own_wording(self):
        """click/fill 的「selector 或 index」那句不属于这颗刀，改动不许波及它们。"""
        with pytest.raises(ValueError, match="needs exactly one of"):
            _validate_actions([{"click": {}}])
