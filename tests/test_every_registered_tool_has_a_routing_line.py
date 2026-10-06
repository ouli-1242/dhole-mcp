"""注册了却没有路的目的地 —— web_discover 在连接期路由段查无此人。

症状（2026-10-06 F-A-J1 复现）：_INSTRUCTIONS_ROUTING 共 10 个键，11 颗注册
工具里唯独 web_discover 没有；「只想知道站点有什么 URL」在 initialize
instructions 里无门可循，它只活在 tools/list 自己的 1,553 字符 schema 与
SKILL.md。指令装配器注释自证「Every tool with a routing line here is a
destination」—— 注册面与路由面必须一一对应，这条不变式此前没有守卫。
"""

from __future__ import annotations

from dhole_mcp.server import _INSTRUCTIONS_ROUTING, _TOP_LEVEL_ARGS


def test_every_registered_tool_has_a_routing_line():
    unrooted = sorted(set(_TOP_LEVEL_ARGS) - set(_INSTRUCTIONS_ROUTING))
    assert not unrooted, (
        f"{unrooted} 注册了却没有连接期路由行 —— 只读 instructions 的 agent "
        "不知道它们存在，会用别的工具硬凑。给 _INSTRUCTIONS_ROUTING 补键，"
        "或把这颗工具下线。")


def test_no_routing_line_points_at_an_unregistered_tool():
    """反向：路由行指向表外名字 = instructions 在给不存在的目的地指路。"""
    ghost = sorted(set(_INSTRUCTIONS_ROUTING) - set(_TOP_LEVEL_ARGS))
    assert not ghost, f"{ghost} 有路由行但不在注册面 —— 指路指到了幽灵"
