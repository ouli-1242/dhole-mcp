"""M10（G25 评审）：工具描述的哈希锁。

`tools/list` 是宿主免审批与调用决策的唯一依据——它是这个服务器的脸。脸变了
（改一个字、加一颗工具、动一条描述），要么是代码变了、要么是构建链路里有人
动了手脚；两种情况都必须是有意识的：测试红了，改代码或改锁，二选一。

锁文件由脚本口径生成（见其中 comment 字段的算法），**不许手抄**——阶段 7 的
作者手抄时丢过开头的 "3"，这一条注释就是那次的纪念碑。
"""

from __future__ import annotations

import hashlib
import json
import pathlib

from dhole_mcp.server import DHOLE_INSTRUCTIONS, MasterFetchServer

LOCK = pathlib.Path(__file__).resolve().parent / "tool_defs_lock.json"


def test_the_tool_defs_digest_matches_the_lock():
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    payload = json.dumps(MasterFetchServer._TOOL_DEFS,
                         ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert digest == lock["sha256"], (
        "tools/list 的字节变了而锁没动（或反过来）。两种都可能是对的，但必须"
        "二选一地说出来：改了代码就在 tests/tool_defs_lock.json 里更新 sha256 "
        "并写明理由；没改代码就查依赖/构建链路里谁动了描述。")
    assert len(MasterFetchServer._TOOL_DEFS) == lock["tools"], (
        "工具颗数与锁不一致 —— 同一把锁的两半不许各说各话")


def test_the_lock_file_itself_is_well_formed():
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    assert set(lock) == {"comment", "tools", "sha256", "sha256_wire_face"}
    assert len(lock["sha256"]) == 64 and all(
        c in "0123456789abcdef" for c in lock["sha256"]), "不是 sha256 的形状"
    assert len(lock["comment"]) > 50, "锁没有 comment 就是没有人说得清它为什么变"


def test_the_lock_also_pins_the_default_wire_face():
    """批次4 刀4（F-C1-1）：锁从「工具描述」扩到「整个连接期面孔」。

    doctor 的 wire face digest 行（updater._append_wire_cost）报的是这条进程
    实际 serve 的脸；锁里的 sha256_wire_face 存默认面（全部工具的 minimal
    投影 ＋ DHOLE_INSTRUCTIONS）的同款指纹。算术两边各算各的 —— 这里独立复算。
    """
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    minimal = [{"name": td["name"], "description": td["description"],
                "inputSchema": td["inputSchema"],
                **({"annotations": td["annotations"]}
                   if "annotations" in td else {})}
               for td in MasterFetchServer._TOOL_DEFS]
    payload = {"tools": minimal, "instructions": DHOLE_INSTRUCTIONS}
    digest = hashlib.sha256(json.dumps(
        payload, separators=(",", ":"), ensure_ascii=False,
        sort_keys=True).encode("utf-8")).hexdigest()
    assert digest == lock["sha256_wire_face"], (
        "默认连接期面孔变了而锁没动（或反过来）—— 面孔的基线与 doctor 的 "
        "wire face digest 行对不上，升级比对就失去意义")
