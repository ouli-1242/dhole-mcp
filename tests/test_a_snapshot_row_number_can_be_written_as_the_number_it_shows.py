"""F8：从快照那一列读来的行号，可以原样写回去 —— 一个裸整数就是一个目标。

缺陷的原文是一句人的话："又格式错了？"（2026-10-11 全真模拟 S10，同一类形状错连撞四次：
hover 裸数字✗、drag 落点✗、screenshot 空 obj✗、confirm 位置✗）。心智模型是这样长成的：
`{"snapshot": true}` 交回来的那一列**本身就写着 0/1/2/3**，而照着那一列做事的人先写下的
必然是裸数 —— 结果要的是 `{"click": {"index": 3}}`。这一层花括号不是语义，是排版。

改法把形状补齐，而不是把拒绝改得更响：

* **接**：`click` / `hover` / `read` / `drag`（源与落点两端）/ `submit` / `screenshot`
  的裸整数 = `{"index": N}`，归一化后与 dict 写法**逐字节同形**，所以执行器只有一条路；
* **不接**：`fill`（值里必须有 `text`，一个数带不动它）、`press`（它的裸值是**键名**，
  `{"press": 5}` 会读成"按下名为 5 的键"）、`drag` 顶层（一个数说不清拖的是哪一端）、
  `snapshot`（本来就收裸布尔，收裸整数只会多出第二种意思）、`wait`/`scroll`（不是目标）；
* **区间与布尔两位不换作者**：简写只把形状递进 `_act_target` 已有的那一支，
  0..MAX_ELEMENT_INDEX 与 `isinstance(bool)` 的裁决仍在那一处，报错仍是那一句。

线面一寸未动：`action_payload_keys()` 只从 `_act_target` 的 `val.get("字面量")` 里挖键名，
新增的是一个**字典字面量**而不是一个新键，派生表与 wire 描述逐字节不变 —— 这一点由
`test_an_agent_that_only_reads_the_wire_can_name_an_actions_payload.py` 与
`test_the_action_key_table_has_one_author.py` 在本刀之后继续绿来作证（它们不需要改口）。
"""

from __future__ import annotations

import pytest

from dhole_mcp.actions import MAX_ELEMENT_INDEX, _validate_actions, action_payload_keys


def _one(action: dict) -> dict:
    (only,) = _validate_actions([action])
    return only


class TestTheNumberIsTheRow:
    """每一种"目标"动作各一条：裸数与 {"index": N} 交出的归一化产物必须相同。"""

    @pytest.mark.parametrize("key", ["click", "hover", "read"])
    def test_the_single_target_actions(self, key):
        assert _one({key: 3}) == _one({key: {"index": 3}})
        assert _one({key: 3}) == {key: {"index": 3}}

    def test_submit_keeps_the_confirm_default_while_adopting_the_number(self):
        """简写只替掉寻址那一层：confirm 缺席仍是缺席，破坏性操作的暂停门不松动。"""
        assert _one({"submit": 4}) == {"submit": {"index": 4, "confirm": False}}
        assert _one({"submit": {"index": 4, "confirm": True}}) == \
            {"submit": {"index": 4, "confirm": True}}

    def test_both_drag_ends_take_the_number(self):
        assert _one({"drag": {"index": 3, "to": 7}}) == \
            {"drag": {"index": 3, "to": {"index": 7}}}

    def test_screenshot_number_is_the_element_crop_of_that_row(self):
        assert _one({"screenshot": 2})["screenshot"]["index"] == 2

    def test_row_zero_is_a_row(self):
        """0 是列表的第一行，不许被任何 `if val:` 读成"没写"。"""
        assert _one({"click": 0}) == {"click": {"index": 0}}
        assert _one({"hover": 0}) == {"hover": {"index": 0}}

    def test_the_number_still_carries_the_expect_name_check(self):
        """`expect` 那一半没跟着简写走：它需要 dict 才能与行号同格出现。"""
        assert _one({"click": {"index": 3, "expect": "提交"}}) == \
            {"click": {"index": 3, "expect": "提交"}}


class TestTheRangeHasNotGainedASecondAuthor:
    """简写接的是形状，不是宽免：越界与非整数仍由 `_act_target` 那一支裁决，同一句话。"""

    @pytest.mark.parametrize("bad", [-1, MAX_ELEMENT_INDEX + 1, 10 ** 9])
    def test_an_out_of_range_number_fails_with_the_index_sentence(self, bad):
        with pytest.raises(ValueError) as e:
            _validate_actions([{"click": bad}])
        text = str(e.value)
        assert f"0..{MAX_ELEMENT_INDEX}" in text, text
        assert "snapshot" in text, text
        # 与 dict 写法撞的是同一句话：一个规则两个说法就是 F8 那四次里的第一次
        with pytest.raises(ValueError) as e2:
            _validate_actions([{"click": {"index": bad}}])
        assert text == str(e2.value), (text, str(e2.value))

    @pytest.mark.parametrize("bad", [True, False, 2.5, [3], None,
                                     {"index": 3, "selector": "#a"}])
    def test_the_neighbouring_shapes_stay_refused(self, bad):
        """裸布尔、小数、列表、None 都不是行号 —— 它们是各自的形状错误。"""
        with pytest.raises(ValueError):
            _validate_actions([{"click": bad}])

    def test_a_string_number_stays_a_selector(self):
        """"3" 不是行号 3：串那一支一直是 CSS 选择器，简写不许把它掰成数字。"""
        assert _one({"hover": "3"}) == {"hover": {"selector": "3"}}


class TestWhatDeliberatelyDoesNotTakeTheNumber:

    def test_press_bare_value_is_a_key_name_not_a_row(self):
        """press 的裸位早就被键名占了：在这里收整数等于把"按下 5"读成"对第 5 行按什么"。"""
        with pytest.raises(ValueError) as e:
            _validate_actions([{"press": 5}])
        assert "key string" in str(e.value), str(e.value)
        # 想要第 5 行？dict 那一支本来就有 index
        assert _one({"press": {"index": 5, "key": "Enter"}}) == \
            {"press": {"index": 5, "key": "Enter"}}

    def test_fill_needs_the_text_a_number_cannot_carry(self):
        with pytest.raises(ValueError) as e:
            _validate_actions([{"fill": 2}])
        assert "text" in str(e.value), str(e.value)

    def test_drag_top_level_stays_two_endpoints(self):
        """`{"drag": 4}` 读不出"拖哪一端、落到哪"，所以 drag 不设顶层简写。"""
        with pytest.raises(ValueError) as e:
            _validate_actions([{"drag": 4}])
        assert "to" in str(e.value), str(e.value)

    def test_snapshot_still_takes_only_bare_true(self):
        with pytest.raises(ValueError):
            _validate_actions([{"snapshot": 0}])
        assert _one({"snapshot": True}) == {"snapshot": True}


class TestTheWireTableDoesNotGainAKey:
    """简写买的是形状，不是新键 —— 派生表若多出名字，线面就要跟着重新算账。"""

    def test_index_is_the_key_the_shorthand_lands_on_and_it_was_already_there(self):
        table = action_payload_keys()
        for key in ("click", "hover", "read", "submit", "drag", "screenshot"):
            assert "index" in table[key], (key, sorted(table[key]))
        assert "confirm" in table["click"] and "expect" in table["click"], table["click"]

    def test_the_shorthand_adds_no_literal_key_anywhere(self):
        from dhole_mcp.actions import _target_payload_keys

        assert _target_payload_keys() == {"selector", "index", "expect"}, \
            sorted(_target_payload_keys())
