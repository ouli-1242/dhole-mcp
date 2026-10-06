"""web_act 自称「只做加性更新」（destructiveHint: False），而 submit 门自己承认不可逆。

症状（2026-10-06 实测，_enabled_tool_defs() 逐颗打印）：

    web_act {'readOnlyHint': False, 'idempotentHint': False,
             'destructiveHint': False, 'openWorldHint': True}

SDK 规范语义（mcp_types ToolAnnotations 原文）："If false, the tool performs only
additive updates … Default: true"。而 submit 门存在本身证明该链产生不可逆的服务端
状态变更（actions.py 的 pending_approval："submitting is an irreversible …"）。
hint 的读者是宿主不是 confirm 门：spec 默认 true，写 False 是主动错报——错的
提示比缺失更贵（宿主把可破坏工具当加性工具对待，注入驱动的破坏性写更难被拦）。

判据：_enabled_tool_defs 的 web_act 无 destructiveHint 键（删键落回默认 true，
省 26 连接期字符）；其余各颗的注解取值不变。
"""

from __future__ import annotations

from dhole_mcp.server import MasterFetchServer


def test_web_act_stops_claiming_destructive_is_false():
    defs = {td["name"]: td for td in MasterFetchServer._enabled_tool_defs()}
    web_act = defs["web_act"]
    assert "destructiveHint" not in (web_act.get("annotations") or {}), (
        "spec 默认 true；写 False 是主动错报，删键落回默认")


def test_the_other_annotations_are_untouched():
    defs = {td["name"]: td for td in MasterFetchServer._enabled_tool_defs()}
    ann = defs["web_act"]["annotations"]
    assert ann == {"readOnlyHint": False, "idempotentHint": False,
                   "openWorldHint": True}
    # web_request 的 destructive=True 是真话（真的能写），不许被顺手带走
    req = defs["web_request"]["annotations"]
    assert req.get("destructiveHint") is True
