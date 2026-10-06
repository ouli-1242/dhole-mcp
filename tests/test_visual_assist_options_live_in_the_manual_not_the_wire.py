"""wire 上的枚举与 how-to 是同一事实的第二个作者 —— 手册已承重，wire 留契约。

症状（2026-10-06 F-C1-J2 逐句量取）：web_act 描述里的 dict-form 枚举
（decode_qr/full_page/save_to/image_type/quality 一句 247 字符）在
references/tools.md 与 examples.md 已有更完整的版本 —— sys_guide(topic=
'behavior') 即达；doc_parse 的 charset 恢复 how-to 与运行时错误的 next_action
＋encoding= 参数描述三重承重。放置规则只说一份，这是没被同一把尺量过的三处
之二（第三处 web_crawl 的 Caps 句与 P11 回收互斥，本刀不动它）。

判据：wire 只留契约与指针 —— web_act 保留 VISUAL ASSIST 存在性＋验证码用途，
枚举换 sys_guide 指针；doc_parse 保留 charset 探测契约（metadata.encoding
names the charset used），恢复 how-to 撤到 fields.md 的 metadata.encoding 行
（已在）。本刀碰 _TOOL_DEFS → 刷锁 → measure 对账。
"""

from __future__ import annotations

import pathlib

from dhole_mcp.server import MasterFetchServer

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_TOOLS_MD = (_ROOT / "src" / "dhole_mcp" / "skills" / "dhole-web"
             / "references" / "tools.md").read_text(encoding="utf-8")
_FIELDS_MD = (_ROOT / "src" / "dhole_mcp" / "skills" / "dhole-web"
              / "references" / "fields.md").read_text(encoding="utf-8")


def _desc(tool: str) -> str:
    return next(td for td in MasterFetchServer._TOOL_DEFS
                if td["name"] == tool)["description"]


def test_the_visual_assist_options_live_in_the_manual_not_the_wire():
    act = _desc("web_act")
    assert "VISUAL ASSIST" in act, "存在性＋验证码用途的首句要留在 wire"
    assert "The dict form adds" not in act, "dict-form 枚举句仍在线上"
    for option in ("full_page", "save_to", "image_type", "'jpeg' only"):
        assert option not in act, f"枚举项 {option} 仍在线上 —— 手册已逐项承重"
    assert "sys_guide(topic='behavior')" in act, "枚举的替换句要指得到手册"
    for option in ("decode_qr", "full_page", "save_to", "image_type", "quality"):
        assert option in _TOOLS_MD, f"手册缺 {option} —— wire 撤了手册就得接住"


def test_the_charset_recovery_howto_lives_in_the_manual_not_the_wire():
    parse = _desc("doc_parse")
    assert "metadata.encoding names the charset used" in parse, (
        "charset 探测契约句要留在 wire")
    assert "next_action says to re-call with encoding=" not in parse, (
        "恢复 how-to 仍在线上 —— 运行时错误与 fields.md 已双重承重")
    assert "metadata.encoding" in _FIELDS_MD and "encoding=" in _FIELDS_MD, (
        "手册的 fields 卷要接住恢复 how-to")


def test_the_caps_sentence_is_untouched_for_p11():
    """互斥注记：web_crawl 的 Caps 句与 P11 回收的是同一句，本刀不碰。"""
    crawl = _desc("web_crawl")
    assert "Whichever one bites is named in the summary" in crawl, (
        "Caps 句被本刀动了 —— 它是 P11 的账，两案只能计一次")
