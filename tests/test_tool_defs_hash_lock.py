"""M10（G25 评审）：工具描述的哈希锁。

`tools/list` 是宿主免审批与调用决策的唯一依据——它是这个服务器的脸。脸变了
（改一个字、加一颗工具、动一条描述），要么是代码变了、要么是构建链路里有人
动了手脚；两种情况都必须是有意识的：测试红了，改代码或改锁，二选一。

锁文件的**写者**是一扇命令：`dhole wire lock --write --reason "…"`（差额与理由一起进
comment）。**不许手抄** —— 阶段 7 的作者手抄时丢过开头的 "3"，这一条注释就是那次的
纪念碑，而那条规矩现在由命令顶住而不是只由这句话顶着。
"""

from __future__ import annotations

import json
import pathlib

from dhole_mcp.server import DHOLE_INSTRUCTIONS, MasterFetchServer
from dhole_mcp.wire import minimal_face, tool_defs_digest, wire_face_digest

LOCK = pathlib.Path(__file__).resolve().parent / "tool_defs_lock.json"


def test_the_tool_defs_digest_matches_the_lock():
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    # 算术只有一个作者（`wire.tool_defs_digest`），写锁的人只有一个（`dhole wire lock
    # --write`）—— 这一处只当读者。从前这里自己算一遍是第三份作者，而三份作者里最容易
    # 漂的是序列化口径，口径一漂，比对就失去意义（口径本身由下面那条黄金值钉）。
    digest = tool_defs_digest(MasterFetchServer._TOOL_DEFS)
    assert digest == lock["sha256"], (
        "tools/list 的字节变了而锁没动（或反过来）。两种都可能是对的，但必须"
        "二选一地说出来：改了代码就跑 `dhole wire lock --write --reason \"…\"`（差额与"
        "理由一起进 comment）；没改代码就查依赖/构建链路里谁动了描述。")
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

    doctor 的 wire face digest 行（`updater._append_wire_cost`）报的是这条进程实际
    serve 的脸；锁里的 `sha256_wire_face` 存默认面（全部工具的 minimal 投影 ＋
    DHOLE_INSTRUCTIONS）的同款指纹。从前"各算各的"是刻意的双记账，而它买到的独立性
    抵不上它付出去的代价：三份算术里只要有一份换了序列化口径，比对就只是**看起来**
    在对。现在口径只有一处（`wire.wire_face_digest`），独立性改由下面那条黄金值守 ——
    钉的是算法本身，与今天serve 的数据无关。
    """
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    digest = wire_face_digest(MasterFetchServer._TOOL_DEFS, DHOLE_INSTRUCTIONS)
    assert digest == lock["sha256_wire_face"], (
        "默认连接期面孔变了而锁没动（或反过来）—— 面孔的基线与 doctor 的 "
        "wire face digest 行对不上，升级比对就失去意义")


def test_the_digest_arithmetic_is_pinned_by_a_golden_value():
    """一把算术要有一个与今天 serve 的数据**无关**的黄金值，否则换口径没人知道。

    这一条补的是上面两处改成"只当读者"之后失去的那份独立性：数据一样时，读者与作者
    永远一致，哪怕作者自己换了序列化口径。所以这里喂一份固定的假工具表，里面刻意
    放着口径的三个易错位 —— 非 ASCII（`ensure_ascii=False`）、键的顺序（`sort_keys`）、
    以及一颗**没有 annotations** 的工具（投影不许替它补一个空字典：宿主读的是"这颗没
    声明"，补出来就是一张没人 serve 的脸）。
    """
    gold = [
        {"name": "alpha", "description": "does a thing · 一句中文",
         "inputSchema": {"type": "object",
                         "properties": {"x": {"type": "string",
                                              "description": "one"}}},
         "annotations": {"readOnlyHint": True}},
        {"name": "beta", "description": "does another",
         "inputSchema": {"type": "object", "required": ["y"], "properties": {}}},
    ]
    assert sorted(minimal_face(gold)[1]) == ["description", "inputSchema", "name"]
    assert tool_defs_digest(gold) == (
        "be011dda94f26815693eb6a7fa2c928a6229b3cd20cdf4d7f2398ad68af27a8a")
    assert wire_face_digest(gold, "instructions 文本\n") == (
        "5ee444be93fa57654cbc2d1fc7e4da965b193d771553af2d5e8aa55213cb70ad")
