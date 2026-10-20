# -*- coding: utf-8 -*-
"""没读过这颗文件的每一面，行首都得说「这一发什么也没发生」—— 而且不许说「读过」（R02-124）

现场（`review/.runtime/p1/r241_pre_read_radius.json` · HEAD fce6e05 · pid 9556 · 零外网）：
`_parse_one_file` 在真去读内容那一句 `try:` 之前有 5 处抬手，其中两处的句子落进
`quality._agent_hints` 那条 `else`，而那条 `else` 同时装着两种事实：

  * `Unsupported file type '.mp3'`（存在的文件，扩展名不认识）→ 行首**申报缺席**，
    出路直接从 `parse` 的 `way_forward` 通到收件人：`If the file is plain text under
    another extension, copy or rename it to .txt (or .md) and parse that.`
  * `File too large (51.0 MB)`（存在、大小超限）→ 更糟：它没有 `way_forward`，于是落进
    那句兜底的话 —— **`the file was found and read; what failed is its contents`**。
    这一发一个字节都没读过：`os.path.getsize` 之后就在读之前拒了，那句话是假话。

F105 已经立过这条门的规矩（读之前被拒的每一面，行首由装配取一句），也立过它的边界
（读过而内容坏的面**不许**有申报 —— 那是假话的反方向）。这一把把两半一起钉：

契约：
  1. 扩展名面与超大面的 `next_action` 第一句 == 本门装配来的 `nothing_went_out()`（按作者钉，
     把 `server._nothing_went_out` 换成哨兵 ⇒ 第一句要跟着变）；
  2. 这两面**不许**再说「文件被读过」：`found and read` 这类话在它们的出路里一处都不许出现；
  3. 指引话一句不少：扩展名面仍要说「当纯文本就改名成 .txt」，超大面要说清这是**门不是旋钮**
     （没有哪颗参数能把 50 MB 这道帽顶开）；
  4. 邻座不动：读过而内容坏的三面（坏 JSON / 坏 YAML / 真是 CFB 的文档）照旧说
     「found and read」，且行首**不许**出现申报 —— F105 那颗边界格子的同类。
"""

from __future__ import annotations

import json
import os

import pytest

from dhole_mcp.context import nothing_went_out, tool_scope
from dhole_mcp.server import MasterFetchServer

with tool_scope("doc_parse"):
    CLAUSE = nothing_went_out()

READ_CLAIM_MARKERS = ("found and read", "what failed is its contents")
#: `_parse_one_file` 的那顶帽（读源码取，别手抄数字：帽一改，手抄的尺就变假绿）
from dhole_mcp import parse as _PARSE  # noqa: E402

OVER_BYTES = _PARSE.MAX_PARSE_FILE_SIZE + 1024 * 1024
CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


@pytest.fixture(scope="module")
def mp3_path(tmp_path_factory):
    p = tmp_path_factory.mktemp("unread-faces") / "sound.mp3"
    p.write_text("AAA1 BBB2 CCC3\n" + "m" * 40, encoding="utf-8")
    return str(p)


@pytest.fixture(scope="module")
def big_path(tmp_path_factory):
    d = tmp_path_factory.mktemp("unread-faces-big")
    p = d / "huge.txt"
    with open(p, "wb") as fh:
        fh.truncate(OVER_BYTES)
    return str(p)


@pytest.fixture(scope="module")
def cfb_path(tmp_path_factory):
    p = tmp_path_factory.mktemp("unread-faces-cfb") / "legacy.docx"
    p.write_bytes(CFB_MAGIC + b"\x00" * 600)
    return str(p)


@pytest.fixture(scope="module")
def bad_json_path(tmp_path_factory):
    p = tmp_path_factory.mktemp("unread-faces-read") / "broken.json"
    p.write_text('{ oops not json,,,', encoding="utf-8")
    return str(p)


async def _call(args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool("doc_parse", args)
    return json.loads("".join(getattr(b, "text", "") for b in res.content))


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture_name", ["mp3_path", "big_path"])
async def test_a_face_that_never_read_the_body_opens_with_the_declaration(
        fixture_name, request):
    """两族「读之前就拒」的脸：行首必须是装配取来的那一句。

    修之前扩展名那发行首是 `If the file is plain text…`（出路没有申报位），
    超大那发行首是 `the file was found and read…`（一句假话）。
    """
    path = request.getfixturevalue(fixture_name)
    payload = await _call({"file_path": path})
    err = str(payload.get("error") or "")
    na = str(payload.get("next_action") or "")
    assert err, payload
    assert na, f"{fixture_name} 的拒绝没有出路: {err[:120]}"
    assert na.startswith(CLAUSE), (fixture_name, f"第一句不是装配来的那一句: {na[:150]}")
    assert na.split(". ")[0] == CLAUSE.rstrip("."), (fixture_name, na[:120])


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture_name", ["mp3_path", "big_path"])
async def test_a_face_that_never_read_the_body_never_claims_a_read(fixture_name, request):
    """这两面不许在任何一处说「文件被读过」—— 那是这一行里最贵的假话。"""
    path = request.getfixturevalue(fixture_name)
    payload = await _call({"file_path": path})
    na = str(payload.get("next_action") or "").lower()
    for marker in READ_CLAIM_MARKERS:
        assert marker not in na, (fixture_name, f"出路还说着读过: {na[:170]}")


@pytest.mark.asyncio
async def test_the_guidance_survives_the_new_head(mp3_path, big_path):
    """换的是行首那半句申报，不是整段重写：两族的指引话都得还在，且要说得出**什么能改答案**。"""
    p1 = await _call({"file_path": mp3_path})
    na1 = str(p1.get("next_action") or "")
    assert "plain text" in na1.lower() and ".txt" in na1, na1[:240]

    p2 = await _call({"file_path": big_path})
    na2 = str(p2.get("next_action") or "")
    assert "split" in na2.lower(), f"超大面要说得出真正能改答案的那条路: {na2[:240]}"
    assert "pages" in na2 and "offset" in na2, (
        f"要点名这两颗改不了答案的参数（兜底句从前正是没点名才骗了人）: {na2[:240]}")
    assert "knob" in na2.lower() or "no argument" in na2.lower(), (
        f"超大面要说清这是门不是旋钮: {na2[:240]}")


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture_name", ["bad_json_path", "cfb_path"])
async def test_a_face_that_did_read_the_body_still_says_so(fixture_name, request):
    """邻座（F105 边界格子的同类）：读过而内容坏的两面照旧说读过，且行首不许有申报。"""
    path = request.getfixturevalue(fixture_name)
    payload = await _call({"file_path": path})
    err = str(payload.get("error") or "")
    na = str(payload.get("next_action") or "")
    assert err, payload
    assert not na.startswith(CLAUSE), (fixture_name, f"读过的那一面被贴上了申报: {na[:150]}")
    assert "nothing was" not in na.lower()[:60], (fixture_name, na[:150])


@pytest.mark.asyncio
async def test_the_head_is_taken_from_the_assembler(mp3_path, monkeypatch):
    """归属锁（F93）：哨兵换上去，两面的第一句都要跟着变（手抄的不会）。

    两颗格子故意分给两位装配者：扩展名那面的申报由**门**取（它读得到 `parse` 交的事实），
    路径不存在那面的申报由 `quality` 取（F105 那一把定的位置）。两个绑定点都要换 ——
    换错一位就会绿得没有意义（本轮 r232 刚为这件事付过一次账）。
    """
    from dhole_mcp import context as CTX
    from dhole_mcp import quality as Q
    from dhole_mcp import server as S

    sentinel = "SENTINEL-DECLARATION."
    original_ctx, original_q, original_s = (CTX.nothing_went_out,
                                            Q.nothing_went_out, S._nothing_went_out)
    monkeypatch.setattr(CTX, "nothing_went_out", lambda *a, **k: sentinel)
    monkeypatch.setattr(Q, "nothing_went_out", lambda *a, **k: sentinel)
    monkeypatch.setattr(S, "_nothing_went_out", lambda *a, **k: sentinel)
    try:
        p1 = await _call({"file_path": mp3_path})
        p2 = await _call({"file_path": os.path.join(
            os.path.dirname(mp3_path), "nope-not-here.txt")})
    finally:
        CTX.nothing_went_out, Q.nothing_went_out, S._nothing_went_out = (
            original_ctx, original_q, original_s)
    assert str(p1.get("next_action") or "").startswith(sentinel), str(p1)[:200]
    assert str(p2.get("next_action") or "").startswith(sentinel), (
        f"路径不存在那一支的申报不是取来的: {str(p2)[:200]}")


def test_parse_never_hand_copies_the_declaration():
    """源码锁：`parse` 只交事实（哪一处没读书），句子由装配取 —— 手抄就是第二位作者。"""
    import inspect

    src = inspect.getsource(_PARSE)
    for word in ("Nothing was fetched.", "Nothing was read."):
        assert word not in src, f"parse 里手抄了申报（{word}）"
