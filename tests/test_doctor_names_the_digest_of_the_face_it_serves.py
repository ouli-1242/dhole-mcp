"""doctor 只报了字数，没报指纹 —— 升级后宿主无从核对 served 的脸还是评审过的那张。

症状（2026-10-06 F-C1-1 复现）：tools/list 的内容有 tests/tool_defs_lock.json
把门，但那只锁在仓库里、只对开发者生效；doctor 的 connect cost 行只报字数
（31,050 → 31,026 那类），字数相同内容不同（一次措辞改写）照样过。

判决：在 updater.py 已有且已构造好线上载荷的 connect cost 检查里，加一行
sha256 指纹（覆盖 minimal defs ＋ instructions，即这条进程实际 serve 的脸），
锁文件加 sha256_wire_face 字段存**默认面**的同款指纹 —— 宿主从仓库对应 tag
的锁文件取基线，升级后比对。

验法：真实 doctor 运行要网络（live probe），属真实环境停机点 —— 停在单测层：
直接调 `_append_wire_cost`，断言新行携带 64 位十六进制指纹，并在测试里用同
一算术从 server 的公开输入独立复算对上（复算逻辑故意不共享，指纹的意义就在
于两边各算各的）。
"""

from __future__ import annotations

import hashlib
import json
import re

from dhole_mcp import server as _srv
from dhole_mcp import updater


def _expected_digest(defs, instructions) -> str:
    minimal = [{"name": td["name"], "description": td["description"],
                "inputSchema": td["inputSchema"],
                **({"annotations": td["annotations"]}
                   if "annotations" in td else {})} for td in defs]
    payload = {"tools": minimal, "instructions": instructions}
    return hashlib.sha256(json.dumps(
        payload, separators=(",", ":"), ensure_ascii=False,
        sort_keys=True).encode("utf-8")).hexdigest()


def test_doctor_names_the_digest_of_the_face_it_serves():
    checks = []
    updater._append_wire_cost(checks)
    rows = [c for c in checks if c[0] == "wire face digest"]
    assert rows, "doctor 没有报面对指纹行 —— 字数对不上内容改写，升级后无从察觉"
    leading = re.match(r"[0-9a-f]{64}\b", rows[0][2])
    assert leading, f"指纹行的开头不是 64 位十六进制: {rows[0][2][:80]}"
    assert len(leading.group(0)) == 64


def test_the_digest_is_of_the_face_this_process_serves():
    checks = []
    updater._append_wire_cost(checks)
    reported = re.match(r"[0-9a-f]{64}", [c for c in checks if c[0] == "wire face digest"][0][2]).group(0)
    expected = _expected_digest(
        _srv.MasterFetchServer._enabled_tool_defs(), _srv.ACTIVE_INSTRUCTIONS)
    assert reported == expected, (
        "doctor 报的指纹与这条进程实际 serve 的脸对不上 —— 指纹变成了装饰")


def test_the_row_says_what_baseline_to_compare_against():
    checks = []
    updater._append_wire_cost(checks)
    row = [c for c in checks if c[0] == "wire face digest"][0]
    assert "tool_defs_lock" in row[2], (
        "指纹不指基线就不是核对，只是又一串十六进制")
