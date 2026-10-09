"""连接面上的工具参数说明只有一个作者：`_TOOL_DEFS`。签名里不许再有第二份（D-32）。

症状（实测 `.scratch/liveverify/shadow_desc_report.txt`：把 11 颗工具的签名与 `_TOOL_DEFS`
逐参数比对）：115 个参数位里**逐字相同的只有 3 个**，59 个两份文本各说一套（例：
`feed_read.since` 签名 597 字符 / 连接面 276；`cred_defaults.set` 签名 20 / 连接面 190），
49 个参数只有表里有描述（`web_search.before` 这类裸注解），4 个只有签名里有（`web_read`
的 `actions` / `method` / `body` / `content_type` —— 那是 web_act / web_request 的面，
web_read 的连接面故意不收）。

后果不是难看，是**改了没人知道**：棘轮 `CONNECT_CEILING` 量的是
`json.dumps({"tools": _TOOL_DEFS}) + DHOLE_INSTRUCTIONS`，两半锁与手册价目表与 README 都
从这同一把尺出发，而真机 `tools/list` 交回的与 `_TOOL_DEFS` 逐字相同
（`test_the_dhole_tools_price_is_the_wire_itself.py` 真起一台 stdio 对过）。所以改签名里
那一份：客户端看不见、棘轮不量、整套判据一条都不红 —— 上一刀就把准确话差点写进了这份
没人看的文本。

这一批把 66 处 `Annotated[X, Field(description=…)]` 全部摘成 `X`（`Annotated` 与
pydantic `Field` 两颗 import 随之退出 server.py）。下面钉的是它不许回来，以及"摘影子
没有把连接面的话一起摘掉"。
"""

from __future__ import annotations

import ast
import inspect
import json
import pathlib

from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer

SERVER_SRC = pathlib.Path(inspect.getsourcefile(server_mod)).read_text(encoding="utf-8")
TREE = ast.parse(SERVER_SRC)

TOOL_NAMES = [d["name"] for d in MasterFetchServer._TOOL_DEFS]


def _names(kind) -> list:
    out = []
    for node in ast.walk(TREE):
        if isinstance(node, kind) and isinstance(getattr(node, "value", None), ast.Name):
            out.append(node.value.id)
    return out


class TestTheShadowIsGone:

    def test_the_module_carries_no_field_metadata_at_all(self):
        """`Field(...)` 调用与 `Annotated[...]` 下标一个都不许留在 server.py。"""
        fields = [n for n in _names(ast.Call) if n == "Field"]
        subscripts = [n for n in _names(ast.Subscript) if n == "Annotated"]
        assert not fields and not subscripts, (
            f"签名里又长出了第二份客户端文本（Field {len(fields)} 处 / Annotated "
            f"{len(subscripts)} 处）。发上 wire 的那一份唯一的作者是 "
            "`MasterFetchServer._TOOL_DEFS` —— 改那里。")

    def test_the_two_imports_left_with_the_shadow(self):
        src = SERVER_SRC
        for line in ("from pydantic import Field", "from typing import Annotated"):
            assert line not in src, f"{line} 是影子时代的残留：描述摘掉后它没人用了"

    def test_signatures_carry_types_and_defaults_only(self):
        """摘描述不许顺手改到类型或默认值 —— 那两处都是客户端看得见的。"""
        for name in TOOL_NAMES:
            method = getattr(MasterFetchServer, name)
            for pname, param in inspect.signature(method).parameters.items():
                text = str(param.annotation)
                assert "description" not in text and "Field" not in text, \
                    f"{name}.{pname} 的注解里还带着描述: {text[:80]}"
        read = inspect.signature(MasterFetchServer.web_read).parameters
        assert read["cache_ttl"].default is None
        assert read["extraction_type"].default == "markdown"
        assert read["offset"].default == 0


class TestTheWireKeepsItsOwnWords:
    """摘掉影子之后，连接面那一份话一句都不能少 —— 这才是"一处作者"的另一半。"""

    def test_every_advertised_parameter_is_described(self):
        defs = MasterFetchServer._enabled_tool_defs()
        total = 0
        for tool in defs:
            for pname, schema in (tool["inputSchema"].get("properties") or {}).items():
                total += 1
                assert (schema.get("description") or "").strip(), \
                    f"{tool['name']}.{pname} 上了 wire 却没有一句话"
        assert total >= 100, f"参数位只剩 {total} 个 —— 表被人剪短了，不是影子被摘了"

    def test_the_served_definitions_are_the_table_itself(self):
        """`_enabled_tool_defs()` 只是过滤，不是第二处写作。"""
        table = {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}
        for tool in MasterFetchServer._enabled_tool_defs():
            assert tool is table[tool["name"]], (
                f"{tool['name']} 交出的不是表里那一份 —— 又多了一个作者")

    def test_the_wire_price_did_not_move(self):
        """棘轮的那把尺与这里的口径同源：删描述不该改变连接面字节。"""
        from tests.test_wire_moves_land_in_the_manual import CONNECT_CEILING

        total = (len(json.dumps({"tools": MasterFetchServer._TOOL_DEFS},
                                ensure_ascii=False, separators=(",", ":")))
                 + len(server_mod.DHOLE_INSTRUCTIONS))
        assert total == CONNECT_CEILING, (
            f"连接面量到 {total}，棘轮写着 {CONNECT_CEILING} —— "
            "摘影子不该动这个数，动了就是顺手改了表")
