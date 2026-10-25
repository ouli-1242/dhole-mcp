"""test_actions_5.py —— 由 5 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import re
import pytest
from dhole_mcp import guide
from dhole_mcp.actions import _VALID_KEYS, _validate_actions, action_payload_keys
from dhole_mcp.server import MasterFetchServer
import ast
import json
from pathlib import Path
from dhole_mcp.actions import _screenshot_shape_error, action_label
from dhole_mcp.actions import MAX_ELEMENT_INDEX
import asyncio
import inspect
from dhole_mcp import actions
from dhole_mcp.actions import _submit_gate_row, _substitute_refs
import os
from dhole_mcp.actions import _resolve_save_to


# ============================================================================
# 来源：test_the_action_key_table_has_one_author.py
# ============================================================================
# 动作的形状表只有一个作者：校验器自己的正文。

TABLE = action_payload_keys()

#: 每颗键一枚**与默认值不同**的值。相同值会让 §2 的差分探针看不见效果 —— 第一版正是
#: 这样把 `press.key`、`screenshot.image_type` 这些"传了等于没传"的假阴性放过去的。
PROBE_VALUE = {
    "selector": "#another-one", "index": 4, "expect": "另一个名字",
    "text": "另一段字", "key": "Tab", "to": "#dropzone",
    "attribute": "href", "property": "checked", "as": "别的名字",
    "confirm": True, "steps": 5, "ms_per_step": 700, "count": 6,
    "state": "hidden", "timeout_ms": 4000, "full_page": True, "decode_qr": True,
    "image_type": "jpeg", "quality": 50, "save_to": "somewhere.png",
    "frame": "#frame-a", "option": "另一个选项",
}
#: 载荷的起点：一颗元素动作总得有个目标，而两颗动作少了这一颗键就根本不成形。
SEED = {"press": {"key": "Enter"}, "fill": {"text": "hi"}, "drag": {"to": "#b"},
        "select": {"option": "b"}, "pdf": {"save_to": "x.pdf"}}


# ─── 1 · 覆盖面 ───────────────────────────────────────────────────

def test_every_action_key_has_a_row_in_the_table():
    """表与动作名单必须同颗 —— 少一格就是新动作没进尺子。"""
    assert set(TABLE) == set(_VALID_KEYS), (set(TABLE) ^ set(_VALID_KEYS))


def test_only_the_two_deliberately_knobless_actions_are_empty():
    """`wait` 只有一个数、`snapshot` 没有可调的东西、`navigate` 的值（back/
    forward 二选一）就是全部载荷，这三格空是**判决**不是遗漏。

    反过来也钉住：给它们加一颗键，这一条会红，而那是一次显式决定 —— 与 §2 一起读，
    意思是"新形状要么被 honors，要么不许出现"。
    """
    empty = {k for k, v in TABLE.items() if not v}
    assert empty == {"wait", "snapshot", "navigate"}, empty


def test_the_table_is_still_derived_not_recopied():
    """静态守卫：`actions.py` 里那张表不许长出第二份字面量抄本。

    `action_payload_keys()` 的正文里不许出现 `"click": {…}` 这种成套的动作键；推导一次、
    读取多次，是本仓库对"一张表三个家"的一贯答复。
    """
    import inspect

    src = inspect.getsource(action_payload_keys)
    assert "ast.parse(inspect.getsource(_validate_actions))" in src
    for action in sorted(_VALID_KEYS):
        assert not re.search(rf'"{action}":\s*{{', src), action


# ─── 2 · 在册的键都真的被读到 ─────────────────────────────────────

@pytest.mark.parametrize("action", sorted(TABLE))
def test_a_listed_payload_key_changes_the_verdict_or_the_normalized_row(action):
    for key in sorted(TABLE[action]):
        if key == "selector":
            continue   # 起点本身就是 selector，换它不改变形状，只换寻址

        def build(with_key: bool):
            payload = {"selector": "#a", **SEED.get(action, {})}
            if with_key:
                payload[key] = PROBE_VALUE[key]
            return _validate_actions([{action: payload}])

        try:
            plain = build(False)
        except ValueError:
            pytest.skip(f"{action} 的基本形状需要别的种子（{key} 不在这一格的可比范围）")
        try:
            with_key = build(True)
        except ValueError:
            continue   # 具名拒绝 = 这颗键被读到了，§2 允许的第二种答案
        assert with_key != plain, (
            f"{action}.{key} 在册却被静默忽略：归一化产物与不传它一字不差")


def test_the_probe_seed_covers_every_action_in_the_table():
    """§2 不许悄悄跳过整颗动作：每一格都要被上面那条走到（skip 只许是键级的特例）。"""
    # `wait` / `snapshot` / `navigate` 不传 dict（navigate 的值就是全部载荷），
    # 三颗都在 §1 单独钉着，这里数其余的动作。
    probed = {a for a, keys in TABLE.items() if keys}
    assert probed == set(TABLE) - {"wait", "snapshot", "navigate"}, probed


def test_an_unknown_payload_key_is_not_in_the_table():
    """尺子的另一边：一张"什么都能进"的表等于没有表。

    `zz_noise` 这类名字被校验器怎么对待不由本文件判（那是 D6 那一族的事），本文件只钉
    "推导出来的表不许因为它也变宽" —— 表若开始照单收，第 1 份作者与这份表就又是两件事。
    """
    for action in sorted(TABLE):
        assert "zz_noise" not in TABLE[action], action
    assert "value" not in TABLE["fill"], "fill 的载荷叫 text；`value` 是被猜错的那一颗"


# ─── 3 · 常驻层点名的必填键 ───────────────────────────────────────

def test_the_three_required_keys_the_wire_names_are_read_by_the_validator():
    """wire 那句必填键的话不许说出表里没有的名字。

    常驻层是第 2 份作者：它只有在这一句与第 1 份一致时才是真话。这一条读的是当天
    serve 的那颗描述，不是一句抄在测试里的字符串。两种写法都算说过：老的
    "fill needs text" 和 292 帽压出来的紧缩形 "fill=text"（upload/dialog 进场时
    描述超帽，needs 那句换成等号，账没变：等号两侧同样是动作与它必填的键）。
    """
    wire = next(td for td in MasterFetchServer._TOOL_DEFS
                if td["name"] == "web_act")["inputSchema"]["properties"]
    text = wire["actions"]["description"]
    named = []
    for a, b, c, d in re.findall(r"(\w+) needs (\w+)|(\w+)=(\w+)", text):
        named.append((a, b) if a else (c, d))
    assert named, f"常驻层那句必填键的话读不到形状：{text!r}"
    for action, key in named:
        assert action in TABLE, (action, key)
        assert key in TABLE[action], (action, key, sorted(TABLE[action]))


# ─── 4 · 语料把其余的键都写了 ────────────────────────────────────

def test_the_manual_lists_every_payload_key_the_validator_reads():
    """可选项住在按需付的那一卷（`test_wire_moves_land_in_the_manual.py` 的裁决）。

    常驻层只放三颗必填，其余全靠这一节；卷里少一颗，那张表就有一个名字谁都没写过 ——
    调用方的下一步是猜，而猜的代价本刀开头就量过（三条 0 秒拒绝、约 90 秒）。
    """
    tools = (guide._ROOT / "references" / "tools.md").read_text(encoding="utf-8")
    universe = set().union(*TABLE.values())
    missing = [k for k in sorted(universe)
               if not re.search(rf"(?<![\w-]){re.escape(k)}(?![\w-])", tools)]
    assert not missing, f"tools.md 没写出这些键：{missing}"


# ============================================================================
# 来源：test_a_named_way_out_answers_what_it_points_at.py
# ============================================================================
# 一句出路指向的那一节，要真答得出那句出路问的问题（2026-10-08 真实使用模拟 · 刀10）

SECTION = "Page interaction"


def _section_body() -> str:
    return guide.read("behavior", SECTION)[1]


def _mentions(text: str, key: str) -> bool:
    """按词边界找键名（与 test_every_wire_parameter_is_findable_in_the_manual 同法）：
    `wait` 不该被 `wait_selector` 里的一段算成已解释。"""
    return re.search(rf"(?<![\w.]){re.escape(key)}(?![\w])", text) is not None


class TestTheWayOutAnswersWhatItPromises:

    def test_every_action_key_the_validator_takes_is_named_in_the_section(self):
        """键表从校验器自己嘴里拿（它拒绝陌生键时会念全），不在测试里重抄一份。"""
        with pytest.raises(ValueError) as got:
            _validate_actions([{"bogus": {}}])
        keys = ast.literal_eval(re.search(r"valid: (\[.*\])", str(got.value)).group(1))
        assert len(keys) >= 8, keys
        body = _section_body()
        missing = [k for k in keys if not _mentions(body, k)]
        assert not missing, (
            f"出路那一节认不出这些动作键（第 {SECTION} 节）: {missing}")

    def test_every_screenshot_knob_the_refusal_offers_is_explained_in_the_section(self):
        """报错句里给出的每个可选项，出路指向的那一节都得写。句子与手册不许各执一词。"""
        refusal = str(_screenshot_shape_error(0).args[0])
        knobs = re.search(r"Optional knobs: (.+?)\.", refusal).group(1)
        wanted = [k.strip() for k in knobs.split(",")] + ["full_page"]
        body = _section_body()
        missing = [k for k in wanted if not _mentions(body, k)]
        assert not missing, (
            f"拒绝句把这些交给调用方，出路那一节却不提: {missing}（拒绝句：{refusal}）")

    def test_the_section_does_not_claim_only_element_captures_exist(self):
        """反向锁的靶子是原文那句 "**Element screenshots** are always available" ——
        它把可用范围说小了，而这一节正是拒绝句送调用方来的地方。整页写法的正面断言在
        上一条；这条钉的是那句框小的话不许回来（措辞若再改，改这条，别让它假绿）。"""
        body = _section_body()
        assert not re.search(r"(?i)\belement screenshots are always available", body), (
            "'只有元素截图可用' 又回来了：照着出路走进这一节的人，会被告知他刚被拒绝"
            "的那种形状不存在")


class TestAReceiptNamesWhatRan:

    def test_a_whole_page_capture_is_not_labelled_none(self):
        # 走校验器，拿线上真正会出现在 metadata.actions 里的那个归一化形状。
        action = _validate_actions([{"screenshot": {"full_page": True}}])[0]
        label = action_label(action)
        assert "None" not in label, f"回执在念一个不存在的目标: {label}"
        assert "page" in label.lower(), f"回执没说出这是整页: {label}"

    def test_a_whole_page_capture_with_knobs_is_still_named(self):
        action = _validate_actions([{"screenshot": {"full_page": True, "decode_qr": True,
                                                    "image_type": "jpeg"}}])[0]
        label = action_label(action)
        assert "None" not in label, label
        assert "decode_qr" in label, label

    @pytest.mark.parametrize("spec,expect", [
        ({"screenshot": ".captcha"}, "captcha"),
        ({"screenshot": {"index": 3}}, "[3]"),
    ], ids=["selector", "index"])
    def test_an_element_capture_still_names_its_target(self, spec, expect):
        label = action_label(_validate_actions([spec])[0])
        assert expect in label and "None" not in label, label

    def test_no_legal_action_ever_leaves_a_none_in_its_receipt(self):
        """把 `submit None` 那次的教训铺满整张动作表：任何合法形状都不该念出 None。"""
        shapes = [{"click": ".btn"}, {"fill": {"selector": "#q", "text": "x"}},
                  {"press": {"key": "Enter"}}, {"hover": ".menu"},
                  {"drag": {"selector": ".a", "to": ".b"}}, {"scroll": 2},
                  {"wait": 100}, {"wait_selector": ".item"}, {"snapshot": True},
                  {"submit": {"selector": "form"}}, {"screenshot": {"full_page": True}},
                  {"screenshot": {"selector": "img", "save_to": "C:/tmp/x.png"}}]
        actions = _validate_actions(shapes)
        assert len(actions) == len(shapes)
        bad = [(a, action_label(a)) for a in actions if "None" in action_label(a)]
        assert not bad, f"回执里念出了不存在的东西: {bad}"


class TestTheManualSendsValidText:

    @pytest.mark.parametrize("path", [p for p in
                                      sorted(guide._ROOT.rglob("*.md"))],
                             ids=lambda p: p.name)
    def test_braces_balance_in_every_manual_page(self, path):
        text = path.read_text(encoding="utf-8")
        assert text.count("{") == text.count("}"), (
            f"{path.name} 花括号不平（{text.count('{')} 对 {text.count('}')}）—— "
            "手册里的 JSON 例子是调用方直接抄的，抄不得半只括号")

    def test_the_confirm_example_is_parseable(self):
        """出路那句让调用方"重跑同一条链并带上 {"confirm": true}"，这一块必须解析得了。"""
        text = (guide._ROOT / "references" / "tools.md").read_text(encoding="utf-8")
        fragments = re.findall(r"\{[\"']confirm[\"']\s*:\s*true\}?", text)
        assert fragments, "语料改了这块的话，这条锁跟着改"
        for f in fragments:
            assert json.loads(f) == {"confirm": True}, f


class TestTheScratchDirectoryStaysOutOfTheRepo:

    def test_dotscratch_is_ignored(self):
        gitignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(
            encoding="utf-8")
        lines = {ln.strip() for ln in gitignore.splitlines() if ln.strip()
                 and not ln.startswith("#")}
        assert {".scratch/", "/.scratch/", ".scratch"} & lines, (
            "现场脚本的目录要在 git status 里隐身：这一仓的 review/ 与 .scratch/ "
            "都是本地台账，不该有机会被顺手提交")


# ============================================================================
# 来源：test_a_snapshot_row_number_can_be_written_as_the_number_it_shows.py
# ============================================================================
# F8：从快照那一列读来的行号，可以原样写回去 —— 一个裸整数就是一个目标。

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

        assert _target_payload_keys() == {"selector", "index", "expect", "frame"}, \
            sorted(_target_payload_keys())


# ============================================================================
# 来源：test_the_same_hint_must_not_offer_two_ways_to_type_it.py
# ============================================================================
# 同一句出路里出现两种花括号，等于给了两个互相矛盾的抄写目标。

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


# ============================================================================
# 来源：test_save_to_into_the_restricted_rooms_is_refused.py
# ============================================================================
# 截图可以落进受限目录 —— 写侧原语对读侧黑名单一无所知。

@pytest.fixture
def sandbox_home(tmp_path, monkeypatch):
    def fake_expanduser(p):
        # Path.expanduser() 以裸 '~' 调用，os.path.expanduser('~/x') 以全串调用
        if p == "~":
            return str(tmp_path)
        if p.startswith("~/"):
            return str(tmp_path / p[2:])
        return p
    monkeypatch.setattr(os.path, "expanduser", fake_expanduser)
    return tmp_path


@pytest.mark.parametrize("target", [
    "C:\\Windows\\System32\\config\\evil.png",
    "C:\\Program Files\\Vendor\\evil.png",
    "C:\\ProgramData\\ssh\\drop.png",
])
def test_system_rooms_refuse_the_screenshot(target):
    with pytest.raises(ValueError, match="restricted"):
        _resolve_save_to(target, "png")


@pytest.mark.parametrize("target", ["~/.ssh/drop.png", "~/.dhole/drop.png"])
def test_user_credential_rooms_refuse_the_screenshot(sandbox_home, target):
    with pytest.raises(ValueError, match="restricted"):
        _resolve_save_to(target, "png")


def test_a_normal_temp_path_still_resolves(tmp_path):
    got = _resolve_save_to(str(tmp_path / "shot.png"), "png")
    assert got.name == "shot.png"


def test_the_refusal_fires_before_any_browser_exists(sandbox_home):
    """拒绝发生在验证层（浏览器冷启动前），错误文本不来自浏览器层。"""
    with pytest.raises(ValueError) as ei:
        _resolve_save_to("C:\\Windows\\System32\\config\\evil.png", "png")
    assert "browser" not in str(ei.value).lower()
