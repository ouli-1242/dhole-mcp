"""README 的 DHOLE_TOOLS 子集数是「tools/list 实测 ＋ 全量 instructions」的手工口径 —— 与 wire 实际行为不符。

症状（2026-10-06 复算实测）：README 写 8 颗 24,038 / 9 颗 25,592；用 measure 同款
算法（子集 tools/list ＋ _compose_instructions(子集)）复算：8 颗 ＝ 23,678、
9 颗 ＝ 25,232。24,038 − 子集 tools/list ＝ 2,486 ＝ 全量 instructions —— README
的尺没跟 _compose_instructions 对子集裁路由行的行为（server.py 里被裁工具的
路由行会一起退出 instructions），把永远不会出现的支付当成了账。

判据：README 的 8/9 颗数字与按实跑口径（_compose_instructions）复算的结果一致；
11 颗全量与测量脚本逐位一致。

报告给 X，实际取 Y（记录）：报告的原方案是"改为 23,678/25,232 或注明保守口径"，
这里两个都做了——数字用实跑口径，并在 README 同句注明"被裁的工具连自己的路由
行一起退出 instructions"，让下一个手工复算的人不再踩同一把错尺。
"""

from __future__ import annotations

import json
from pathlib import Path

from dhole_mcp.server import DHOLE_INSTRUCTIONS, MasterFetchServer, _compose_instructions

README = Path(__file__).resolve().parent.parent / "README.md"

SUB8 = ["web_read", "web_search", "web_request", "web_crawl",
        "doc_parse", "feed_read", "sys_state", "sys_guide"]


def _subset_connect(sel: list[str]) -> int:
    """measure 同款算法，但 instructions 按 _compose_instructions(子集) 实跑。"""
    defs = {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}
    table = len(json.dumps({"tools": [defs[n] for n in sel]},
                           ensure_ascii=False, separators=(",", ":")))
    return table + len(_compose_instructions(set(sel)))


def test_readme_subset_numbers_match_composed_instructions():
    text = README.read_text(encoding="utf-8")
    sub8 = _subset_connect(SUB8)
    sub9 = _subset_connect(SUB8 + ["web_discover"])
    assert f"{sub8:,}" in text, f"README 的 8 颗子集数应与实跑口径一致（实测 {sub8:,}）"
    assert f"{sub9:,}" in text, f"README 的 9 颗子集数应与实跑口径一致（实测 {sub9:,}）"


def test_the_full_surface_number_stays_the_measured_one():
    """全量口径两种算法同值（没有工具被裁），README 总数必须等于它。"""
    text = README.read_text(encoding="utf-8")
    defs = {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}
    total = (len(json.dumps({"tools": list(defs.values())},
                            ensure_ascii=False, separators=(",", ":")))
             + len(DHOLE_INSTRUCTIONS))
    assert f"{total:,}" in text, f"README 连接期总数应与实测一致（{total:,}）"
