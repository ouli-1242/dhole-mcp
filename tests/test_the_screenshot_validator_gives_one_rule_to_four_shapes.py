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

修法：一条规则一句报错 —— selector 串 / 裸行号 / dict(selector) / dict(index) /
dict(full_page=true) 五种形状合法；其余一律同一句话，句里列全合法形状，且不带花括号
伪影。bare `true` 的语义曾是待 operator 判决项；2026-10-08 判决**维持拒绝**（两条改判
路线的实测理由见 `TestTheRefusalNamesTheAdjacentDoor`）。这一刀不改那个判决，只让报错与
事实一致，并在句尾点名那张长得一样的隔壁门。裸**整数**不是裸布尔的另一种拼法：它指着
snapshot 那一行，有明确的"拍什么"，所以 刀9-6c（F8）把它列为第五种合法形状 —— 与
click/hover/drag/read/submit 同一句规矩（一个数就是从那一列读来的行号）。
"""

import pytest

from dhole_mcp.actions import _validate_actions

LEGAL = [
    ("h1", {"selector": "h1", "image_type": "png", "decode_qr": False}),
    (2, {"index": 2, "image_type": "png", "decode_qr": False}),
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


class TestTheAcceptedShapes:
    """每一种合法形状各一条：说出规则就得接住规则自己列的每一种写法。"""

    @pytest.mark.parametrize("val,expected", LEGAL)
    def test_each_shape_is_accepted(self, val, expected):
        (action,) = _validate_actions([{"screenshot": val}])
        assert action["screenshot"] == expected

    def test_row_zero_is_a_row_not_an_absence(self):
        """0 是 snapshot 的第一行，不是"没有目标"：falsy 不许把它吃掉。"""
        (action,) = _validate_actions([{"screenshot": 0}])
        assert action["screenshot"]["index"] == 0, action

    def test_full_page_needs_no_target(self):
        """第 3 条读数本来就是对的：整页截图没有元素可言，不该被要求指一个元素。"""
        (action,) = _validate_actions([{"screenshot": {"full_page": True}}])
        assert action["screenshot"]["full_page"] is True


class TestTheRefusalsSayOneThing:
    @pytest.mark.parametrize("val", [True, 3.5, "  ", [1],
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
        # 刀9-6c 之后这句还许得出裸行号 —— 列形状的那句漏一种，就等于教人再撞一次
        assert "bare snapshot row" in text, text

    def test_no_python_brace_artifact(self):
        """`{{` 是字符串拼接漏出的伪影，不是给调用方的语法。"""
        for val in (True, {"decode_qr": True}, 3.5):
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


class TestTheRefusalNamesTheAdjacentDoor:
    """2026-10-08 operator 判决：裸 `{"screenshot": true}` **维持拒绝**，报错要点名隔壁。

    两条改判路线都先量过，都不干净：

      * 改判成"整页"——实测 `rfc-editor.org/rfc/rfc9110.html` 的整页捕获在内部 10s
        上限里就 `Page.screenshot: Timeout 10000ms exceeded`（长页直接失败）；短页能出图，
        而 `MAX_ACTION_SHOT_BYTES` 自己的注释写着"这么大的一张就是弄错了 —— 它花的是
        多模态客户端的上下文"。
      * 改判成"视口"——那是一项**新能力**：要在描述里加字（连接面 30,750 只降不升，
        眼下没有抵项），并且"没有目标"从此有两种意思。

    病根不在判决，在**相邻的两扇门长得一模一样**：`{"screenshot": ...}` 与
    `{"snapshot": true}` 同一副语法，后者收裸布尔（它不需要目标，列的就是整页的元素），
    前者不收。于是这个错会反复出现。所以这一刀只动文本：那句话要说清裸 true 是从哪儿
    学来的，并把两种"我其实想要的是这个"各写一遍给调用方照抄。
    """

    def test_the_refusal_names_the_sibling_that_takes_a_bare_true(self):
        text = _err(True)
        assert '"snapshot": true' in text, (
            f"裸 true 是从 snapshot 学来的，那句话却不说隔壁长什么样: {text}")

    def test_the_refusal_says_why_a_bare_boolean_cannot_work(self):
        """不只列形状，还要说出**为什么**裸布尔在这一颗上做不到 —— 拍照得有个对象。"""
        text = _err(True)
        assert '"full_page": true' in text, text
        assert "something to look at" in text, (
            f"那句要点出这颗动作的规矩从哪来，不然读起来像任意的: {text}")

    def test_the_targetless_dict_still_gets_the_very_same_sentence(self):
        """R2刀3 的既有裁决不许分叉：裸 true 与无目标 dict 念同一句话。"""
        assert _err(True) == _err({"decode_qr": True})

    def test_the_knob_list_stays_where_the_manual_lock_reads_it(self):
        """刀10 的锁从"Optional knobs:"这一段取名单：新句子往后加，这段不许动。"""
        import re
        text = _err(True)
        found = re.search(r"Optional knobs: (.+?)\.", text)
        assert found, text
        assert found.group(1) == "image_type, quality, decode_qr, save_to", found.group(1)
        assert text.index("Optional knobs") < text.index('"snapshot"'), (
            "名单在前、隔壁在后 —— 打乱顺序会把上面那条锁读成别的东西")


class TestTheManualSaysTheSameThing:
    """同一句规矩在运行时报错与手册里必须是同一个说法 —— 报错是事后解释，手册是事前拦住。"""

    def test_the_page_interaction_section_names_the_bare_boolean_rule(self):
        from dhole_mcp import guide
        body = guide.read("behavior", "Page interaction")[1]
        assert "bare boolean" in body, (
            "报错那半句刚立的规矩（只有 snapshot 收裸布尔）手册里没有，"
            "下一次还是照样写错，只是换个地方学到")
        assert '{"screenshot": true}' in body, body[:400]
