# -*- coding: utf-8 -*-
r"""受限目录那道门报的是**哪间屋子**，不是这台机器的门牌（2026-10-18 · R02-F107 · R02-126）

现场（`review/.runtime/p1/r227_server_resolved_paths.py` · 14 面 × 四顶帽子的针 · 走真接线 · 零外网）：
`doc_parse(file_path=r"C:\\Users\\<user>\\.ssh\\id_ed25519")` 交回

    error = Access denied: path is in a restricted system directory (c:\\users\\<user>\\.ssh)

—— 收件人那一面（会进 transcript 的那一格）拿到的是**这台机器的家目录在哪**。同一封回执里
`url` 那格走的是 `_masked_file_uri()`（F25/D11 立的作者），而守卫报出的那一颗 `blocked` 没过任何人：
名单在源码里写的是 `os.path.expanduser("~/.ssh")`，展开之后的绝对形状被直接拼进了句子。
14 面里只有这两格到场（`.dhole` 与 `.ssh` 在这台机器上真存在，所以它们的守卫才会被走到），
`next_action` / `diagnosis` / `summary` 都干净；写侧 `actions._resolve_save_to` 抬的
`ValueError` 是同一颗病（`refusing to write … it is in a restricted system directory ({blocked})`）。

判据不新立：F106 那条收口判决说「这台机器的自我信息只许由服务器自己交出去，而它交出的每一格都遮过」——
这里的 `blocked` 正是服务器自己交出去的（不是调用方打的原文：调用方写的是 `~/.ssh` 那种形状）。
而 `server.py:1718` 早就写过本仓的偏好：**别把绝对路径拼进句子再靠遮蔽器折回去**。所以这一把不是
在装配点套一层 `mask_machine_paths`，而是**报出名单被作者写下的那一形**（`~/.ssh`），
展开只在比较那一步做。读数上也更可用：`(~\.ssh)` 与手册里那份名单逐字对得上，而
`(c:\users\<user>\.ssh)` 只是把这台的布局念了一遍。

**门不许被削弱**（§12）：这一把动的只有「报出来的形状」。名单一个字不动、比较照旧用展开后的绝对形、
等于它或紧跟一颗分隔符仍拒 —— `tests/test_a_sibling_directory_is_not_inside_the_gate.py` 那 15 格
是本把的邻座，一字未改。不在家目录里的那些条目（`c:\\windows`、`/etc`）**今天怎么报以后还怎么报**：
没有可折的根，就不许顺手改口（本文件第 3 格钉住这条）。
"""

from __future__ import annotations

import json
import os
import re

import pytest

from dhole_mcp import actions as actions_mod
from dhole_mcp.server import MasterFetchServer, _blocked_path_prefix

HOME = os.path.expanduser("~")
CWD = os.getcwd()
USER = (os.environ.get("USERNAME") or os.path.basename(HOME)).strip()


def _hats(x: str) -> tuple:
    """四顶帽子：真反斜杠 / 正斜杠 / 全小写 / 百分号编码（B-80 那一课）。"""
    enc = x.replace("\\", "%5C").replace("/", "%2F").replace(":", "%3A")
    return (x, x.replace("\\", "/"), x.lower(), enc, enc.lower())


NEEDLES = tuple(v for x in (HOME, CWD, USER) for v in _hats(x) if v)


def _raw_root_fields(node, path=""):
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            out += _raw_root_fields(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += _raw_root_fields(v, f"{path}[{i}]")
    elif isinstance(node, str):
        if any(n in node for n in NEEDLES):
            out.append(path)
    return out


async def _call(tool: str, args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    return json.loads("".join(getattr(b, "text", "") for b in res.content))


@pytest.mark.parametrize("rel,room", [
    (r".ssh\id_ed25519", ".ssh"),
    (r".dhole\cache.db", ".dhole"),
    (r".dhole\usage.jsonl", ".dhole"),
])
@pytest.mark.asyncio
async def test_the_guard_names_the_room_not_this_home(rel, room):
    r"""门照旧拒，但报出来的是名单作者写的那一形：`~\\<room>`，不是这台的家目录。

    格子**不必在这台机器上存在**：门走在存在性检查之前（2026-10-20 CI 实测 —— runner
    上没有 `~\.ssh`，这一发落进 `File not found` 而不是守卫。那一版把"这间屋子被禁"的
    判决交给了这台机器的目录形状：同一颗路径在一台装过 ssh 的机器上拒、在一台干净的
    机器上"没找到"，而"这台没有这个文件"本身就是只有服务器知道的答案）。写侧
    `_resolve_save_to` 一直无条件过名单，读侧补齐同一纪律；r227 第一版那颗不存在的
    `~\.dhole\profiles\p.txt` 当年判不动，今天判得动。
    """
    payload = await _call("doc_parse", {"file_path": os.path.join(HOME, rel)})
    err = str(payload.get("error") or "")
    assert err.startswith("Access denied: path is in a restricted system directory"), err
    assert room in err, err
    assert "~" in err, f"没有报出作者写的那一形: {err[:120]}"
    assert _raw_root_fields(payload) == [], (
        f"这些字段还带着这台机器的根: {_raw_root_fields(payload)}")


def test_the_matcher_itself_returns_the_authored_shape():
    """作者级锁（F93 的按作者钉）：`_blocked_path_prefix` 交回的就是可贴进句子的那一形。"""
    got = _blocked_path_prefix(os.path.realpath(os.path.join(HOME, r".ssh\id_ed25519")))
    assert got, "门被削弱了：家目录下的 .ssh 不再被挡"
    assert _raw_root_fields([got]) == [], got
    assert got.startswith("~"), got


def test_the_writer_side_guard_says_the_same_thing():
    """写侧同一颗病：`_resolve_save_to` 抬的 ValueError 也不许念出这台的门牌。

    句子里**允许**出现调用方自己写的那个目的地（`refusing to write '<他给的路径>'` —— F106 那条
    判决的另一半：请求身份是他说过的话），不许出现的是**服务器自己展开**的那颗名单条目。
    """
    with pytest.raises(ValueError) as got:
        actions_mod._resolve_save_to(os.path.join(HOME, r".ssh\shot.png"), "png")
    msg = str(got.value)
    assert "restricted system directory" in msg, msg
    room = re.search(r"restricted system directory \((.*)\)", msg)
    assert room, msg
    assert ".ssh" in room.group(1), room.group(1)
    assert not any(n in room.group(1) for n in NEEDLES), room.group(1)


@pytest.mark.asyncio
async def test_a_room_outside_this_home_is_still_named_verbatim(tmp_path):
    """不顺手改口：不在家目录里的那些条目，今天怎么报以后还怎么报（没有根可折就不动文案）。"""
    payload = await _call("doc_parse", {
        "file_path": os.path.join(os.environ.get("SystemRoot") or r"C:\Windows",
                                  "System32", "drivers", "etc", "hosts")})
    err = str(payload.get("error") or "")
    assert err.startswith("Access denied: path is in a restricted system directory"), err
    assert "windows" in err.lower(), err
    assert "~" not in err, f"不在家目录里的条目被安了一个家目录符号: {err[:120]}"


@pytest.mark.asyncio
async def test_an_ordinary_path_still_reads(tmp_path):
    """邻座：门没被改宽也没被改窄 —— 名单外的普通文件照旧读得出内容。"""
    p = tmp_path / "room.txt"
    p.write_text("AAA1 BBB2 CCC3 DDD4 EEE5\n" + "z" * 40, encoding="utf-8")
    payload = await _call("doc_parse", {"file_path": str(p)})
    assert not payload.get("error"), payload.get("error")
    assert payload.get("content_ok") is True
    assert "AAA1" in "".join(payload.get("content") or [])
