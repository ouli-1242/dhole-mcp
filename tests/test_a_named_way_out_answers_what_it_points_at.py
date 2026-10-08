"""一句出路指向的那一节，要真答得出那句出路问的问题（2026-10-08 真实使用模拟 · 刀10）

三处同一族，各自量出来的现场：

1. **报错把调用方送到手册，手册那一节少一半答案**。刀1 给动作形状错的拒绝写的出路是
   `sys_guide(topic='behavior', section='Page interaction')`，那句承诺是"lists every
   action key and what each one takes"。实测那一节（4,084 字符）里 `decode_qr` 在，
   而 `full_page` / `save_to` / `image_type` / `quality` **一个都不在** —— 它们只出现在
   另一节"The remaining tools"。更糟的是那一节写着"**Element screenshots** are always
   available"，读着像整页捕获不存在。被拒的人照着出路走，走到的是否认他那条出路的页面。
2. **回执把不存在的东西当目标念**：整页截图的字典按设计就没有 selector/index，
   `_target_label` 于是交回 `repr(None)`，`metadata.actions` 的标签打成
   `screenshot None`。同一个洞 `submit` 已经补过并留了注释（"the audit trail lost
   the target of the very step it exists to record"），screenshot 这一格漏了。
3. **手册里一个 JSON 例子少一只花括号**：`re-run the same actions chain with
   {"confirm": true` —— 全语料只有 tools.md 花括号数不平（67 对 66），就是这一处。
   调用方的出路是"重跑同一条链并带上这一块"，抄下来的是解析不了的文本，而这正是
   submit 那条不可逆动作的钥匙。

`.gitignore` 忽略 `scratch/` 却不认 `.scratch/`，同族顺手补：这一仓给现场脚本留的
目录不该出现在 `git status` 里。
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from dhole_mcp import guide
from dhole_mcp.actions import _screenshot_shape_error, _validate_actions, action_label

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
