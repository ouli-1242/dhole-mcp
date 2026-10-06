"""web_read 的方法 docstring 还在指路一颗不存在的工具 —— 17.0 已把截图并入 web_act。

症状（2026-10-06 实测）：

    grep "use the screenshot tool" src/dhole_mcp/server.py
    → :4565「- Taking screenshots: use the screenshot tool instead」
    同文件 _REMOVED_TOOL_HINTS 写着「the capture lives on web_act now」

tools/list 不读 __doc__（server.py 有注释明说），所以 wire 不受影响、agent 读
不到这句话——但 docstring 是维护者与读者的一手说明，指向一颗不存在的工具
就是同一事实的两个作者各说各话。这条缝没有守卫：搬走的句子有
test_wire_moves_land_in_the_manual 守着「语料里活着」，方法 docstring 谁也不管。

判据：server.py 每个方法的 __doc__ 都不再点名任何已移除的工具
（web_screenshot / web_resolve / web_recipe / web_watch / cred_vault /
cred_login）；量尺读数不变（docstring 不上线）。
"""

from __future__ import annotations

import ast
from pathlib import Path

from dhole_mcp.server import MasterFetchServer

SERVER = Path(__file__).resolve().parent.parent / "src" / "dhole_mcp" / "server.py"

REMOVED_TOOLS = (
    "web_screenshot", "web_resolve", "web_recipe",
    "web_watch", "cred_vault", "cred_login",
)


def test_no_docstring_names_a_removed_tool():
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    docs = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node) or ""
            docs.append((node.name, doc))
    offenders = [(name, tool) for name, doc in docs
                 for tool in REMOVED_TOOLS if tool in doc]
    assert not offenders, (
        "方法 docstring 点名了已移除的工具: "
        + ", ".join(f"{n} -> {t}" for n, t in offenders[:3]))


def test_the_live_docstring_points_at_the_current_route():
    doc = MasterFetchServer.web_read.__doc__ or ""
    assert "web_act" in doc and "screenshot" in doc
