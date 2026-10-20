# -*- coding: utf-8 -*-
"""`web_read` 的拒绝要把本门的申报放在第一句（2026-10-18 · R02-F104 · R02-122）

现场（`review/.runtime/p1/r216_web_read_clause_faces.json` · 线上逐发 · HEAD be8153f ·
server pid 10408 · 零外网）：对普查圈出的 6 处装配点各发一发，到了拒绝面的 5 发里 ——

  * **句尾才有**：`nested_fields=` 缺 `nested_selector=` 那一支，交回的第一句是
    「Add nested_selector='<css>' naming the elements to follow on THIS page, or drop …」，
    「Nothing was fetched.」缀在整段最后；
  * **根本没有**：数字越界那一支（`offset=-6`），第一句是「Pass an integer for that argument
    and call again - no request was made.」—— 手抄的那半句替门话说话，而本门的申报没出门；
  * 另有两支（focus_mode、schema）行首就是申报 —— 它们**不是**这一把的靶子，
    本文件把它们当正控制钉住，免得有人把整扇一起改写。

判据的来历（不是新立的规矩）：R02-111 已判「动词对但缀在句尾同样算缺陷」，F83 已立「申报由
`context.nothing_went_out()` 按门取词，不许手抄」。这一把只是把 `web_read` 剩下的两处接上同一条规矩。

契约（本文件钉住）：
  * 两处受害面的 `next_action` **第一句**必须是 `nothing_went_out('web_read')` 现算的那句
    （判据由作者现算，不手数 —— R02-102 的规矩）；
  * 原文里被别的尺钉着的指导话一句不许少：「nested_selector=」那支要给续投的写法，
    数字那支要给 `max_content_chars` 的区间与 `offset` 的取法（`tests/test_bug_report2_regressions.py`
    钉的就是后者那半句）；
  * 两支的 `error` 仍要点名自己被拒的那一颗；
  * 源码级锁：这两处的装配里不许再出现字面量「Nothing was fetched.」——
    改回手抄就会红（F93 的通则按源码钉，不靠文案钉）。
"""

from __future__ import annotations

import json

import pytest

from dhole_mcp.context import nothing_went_out
from dhole_mcp.server import MasterFetchServer

CLAUSE = nothing_went_out("web_read")
ORIGIN = "http://127.0.0.1:9/x"


@pytest.fixture(autouse=True)
def _loopback_is_allowed():
    from dhole_mcp import identity
    identity.set_value("allow_private", True)
    yield
    identity.set_value("allow_private", False)


async def _read(args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool("web_read", args)
    return json.loads("".join(getattr(b, "text", "") for b in res.content))


# 现场的两处受害面（r216 的 server.py:6955 与 server.py:7261 两支）
VICTIMS = [
    ("nested_fields 缺 nested_selector",
     {"url": ORIGIN, "nested_fields": [{"name": "n", "selector": "h1"}]},
     ("nested_selector",)),
    ("数字参数越界", {"url": ORIGIN, "offset": -6},
     ("max_content_chars takes 500-200000",)),
]

VICTIMS_BY_ARGS = [(label, args) for label, args, _ in VICTIMS]


@pytest.mark.asyncio
@pytest.mark.parametrize("label,args,keeps", VICTIMS)
async def test_the_refusal_opens_with_this_doors_own_declaration(label, args, keeps):
    """出门的第一句是本门申报，而原来写好的指导话一句不少。

    修之前这两支的第一句分别是「Add nested_selector=…」与「Pass an integer …」——
    读者只读第一句（用量日志与归因脚本都从行首取）就听不见「这一发什么都没发出去」。
    """
    payload = await _read(args)
    error = str(payload.get("error") or "")
    na = str(payload.get("next_action") or "")
    assert error.startswith("invalid_request"), (label, error[:160])
    assert na.startswith(CLAUSE), (label, f"第一句不是本门申报: {na[:160]}")
    # 申报得**自成一节**：紧跟其后的是句点+空格，而不是被并进长句的一半
    # （`na.split(". ")[0]` 会吃掉句点，所以比的是去掉句点的那份）。
    assert na.split(". ")[0] == CLAUSE.rstrip("."), (label, na[:120])
    for frag in keeps:
        assert frag in na, f"{label} 的指导话少了（{frag}）: {na[:200]}"


@pytest.mark.asyncio
@pytest.mark.parametrize("label,args", [
    ("focus_mode 值不认", {"url": ORIGIN, "focus_mode": "strict"}),
    ("schema 无可投影字段", {"url": ORIGIN,
                             "schema": {"records": "not-a-dict"}}),
])
async def test_the_two_faces_that_already_led_with_it_stay_that_way(label, args):
    """正控制：现场量到那两支行首本来就有申报 —— 这一把不许把它们改坏。

    把整扇一起改写是本类修法最常见的过界；这两格钉的是「改该改的两处，别碰已经对的」。
    """
    payload = await _read(args)
    na = str(payload.get("next_action") or "")
    assert str(payload.get("error") or "").startswith("invalid_request"), payload
    assert na.startswith(CLAUSE), (label, na[:160])


@pytest.mark.asyncio
@pytest.mark.parametrize("label,args", VICTIMS_BY_ARGS)
async def test_the_declaration_is_taken_from_the_assembler_not_copied(
        label, args, monkeypatch):
    """把申报**接上取词的那位作者**（F83 的规矩），而不是抄一句一样的话。

    句子对不对，上一格已逐字判过；这一格判的是**归属**：临时把 `server._nothing_went_out`
    换成哨兵，若这一支出门的第一句跟着变，说明它真的在取词；若句子是抄在原地的，哨兵不会出现
    —— 那正是这一族每次复发的形状（F93 的通则：按作者钉，不按文案钉，文案会改而抄写会漂）。
    """
    from dhole_mcp import server as S

    sentinel = "SENTINEL-DECLARATION."
    original = S._nothing_went_out
    monkeypatch.setattr(S, "_nothing_went_out", lambda *a, **k: sentinel)
    try:
        payload = await _read(args)
    finally:
        S._nothing_went_out = original
    na = str(payload.get("next_action") or "")
    assert str(payload.get("error") or "").startswith("invalid_request"), payload
    assert na.startswith(sentinel), (
        f"{label} 的申报不是取来的（第一句仍是抄写的那句）: {na[:160]}")
