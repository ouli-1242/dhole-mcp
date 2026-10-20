# -*- coding: utf-8 -*-
"""同一颗 `offset` 在两扇门上必须遇见同一位裁判（2026-10-18 · R02-F103 · R02-121）

起因是取值守卫普查（`review/.runtime/p1/r212_guard_sweep.json` · 20 格）当场挖出的那一格：

  * `web_read(url=本机 fixture, offset=-6)` ⇒ `invalid request · offset must be between
    0 and 2147483648, got -6.` —— 拒，干脆。
  * `doc_parse(<113B 的 txt>, offset=-6)` ⇒ status 200、正文只剩末尾 6 字、`summary` 仍写
    「200 OK · 113B markdown」、`content_ok` true、`next_action` 空 —— 前面 107 个字被跳过，
    整封没有一处说这件事。Python 的负数切片从末尾数，而这一扇把那颗值原样交给了切片。
  * `offset=-100000` 更坏：正文空、summary 写「truncated」、出路是
    「page truncated. … or **offset=-100000** to continue paginating」—— 把刚产出空正文的那颗
    值原样教回去，照着做就永远停在同一封空答案上（R02-093 那句"永远不可执行的建议"换了个门重生）。

裁判本来就现成，而且这仓**早已知道**这个陷阱：判据住在 `params._coerce_int_arg(lo=0, hi=2**31,
clamp=False)`（`server.py:6771` / `7257` 都在叫它），`params.py:30` 与 `:122` 的注释白纸黑字
写着"负数切片会把『最后 5 个字』当成『从第 0 个开始』"。这一把不发明判据，只是把现成的裁判
叫到 `doc_parse` 门口（`doc_parse` 的 offset 从前只进了类型归一名册 `params.py:560`，没带上下界）。

契约（本文件钉住）：
  * 负数在门口**具名拒绝**：`error` 以 `invalid_request:` 开头、点名 `offset`、给出区间、
    `summary` 与 `next_action` 都在，且**不交正文**；
  * 拒绝里不许出现"把那颗值再发一遍"的建议；
  * 正数照旧是合法分页位置：`0` 全文、`6` 从第 6 个字起、越过末尾的正数拿到诚实的
    `[No more content.]`（这一格是反向锁 —— 修的时候把区间判据写成"只许 0 与小的正数"
    或干脆钳位，这里就红）；
  * 同一颗在 `web_read` 与 `doc_parse` 上说的是**同一句**（同一位裁判，不是各抄一份）；
  * 路径那一族不受本把影响（缺文件的句子照旧，别为新增的检查改它的顺序）。
"""

from __future__ import annotations

import json

import pytest

from dhole_mcp.server import MasterFetchServer

BODY = "AAA1 BBB2 CCC3 DDD4 EEE5 FFF6\n" + "x" * 40 + "\n" + "y" * 40


@pytest.fixture(scope="module")
def doc_path(tmp_path_factory):
    p = tmp_path_factory.mktemp("offset-fixture") / "body.txt"
    p.write_text(BODY, encoding="utf-8")
    return str(p)


async def _call(tool: str, args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    return json.loads("".join(getattr(b, "text", "") for b in res.content))


NEGATIVE = [-1, -6, -(len(BODY) + 1)]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", NEGATIVE)
async def test_a_negative_offset_is_refused_by_name_and_delivers_no_body(
        bad, doc_path):
    """负数不是一种分页位置：拒、点名、给区间，且不把文档尾部当答案交出去。

    修之前这三格全是 status 200：`-6` 交末尾 6 字、`-(len+1)` 交空正文再加一句"把同一个负数
    再发一遍"的续读建议。
    """
    payload = await _call("doc_parse", {"file_path": doc_path, "offset": bad})
    error = str(payload.get("error") or "")
    assert error.startswith("invalid_request"), (
        f"offset={bad} 从门口过去了（没有被拒）: {error[:160]}")
    assert "offset" in error, f"那句拒绝没点名是哪颗参数: {error}"
    assert "between 0" in error, f"那句拒绝没给出合法区间: {error}"
    assert payload.get("summary"), payload
    assert payload.get("next_action"), f"拒绝只说了哪错了，没说出路: {error}"
    assert not payload.get("content"), (
        f"offset={bad} 被拒了却还把正文交了出去: {str(payload.get('content'))[:60]!r}")


@pytest.mark.asyncio
async def test_the_refusal_does_not_send_the_caller_back_to_the_rejected_value(doc_path):
    """出路不许是"把刚被拒的那颗值原样再发一遍"（R02-093 的那条规矩，换个门也不许破）。

    修之前这一封写的是 `… or offset=-100000 to continue paginating` —— 照着做只会拿到
    同一封空正文，而 `summary` 还一直说 `truncated`（听起来"再发就有"）。
    """
    bad = -(len(BODY) + 1)
    payload = await _call("doc_parse", {"file_path": doc_path, "offset": bad})
    na = str(payload.get("next_action") or "")
    assert f"offset={bad}" not in na, na
    assert "continue paginating" not in na, na
    assert "truncated" not in str(payload.get("summary") or ""), payload


@pytest.mark.asyncio
async def test_positive_offsets_still_page_exactly_where_they_said(doc_path):
    """正数照旧合法，而且**位置算得对** —— 这一格是反向锁。

    把判据写成"钳到 0"会把 `-6` 变成"从第 0 个开始"（那是把『最后 6 个字』说成『全文』，
    `params.py:30` 的注释警告的正是这件事）；把上限写小会把越过末尾的正数一起打死。
    """
    whole = await _call("doc_parse", {"file_path": doc_path})
    start = await _call("doc_parse", {"file_path": doc_path, "offset": 0})
    sixth = await _call("doc_parse", {"file_path": doc_path, "offset": 6})
    past = await _call("doc_parse", {"file_path": doc_path, "offset": len(BODY) + 5000})

    for payload in (whole, start):
        assert not str(payload.get("error") or ""), payload
    text = (sixth.get("content") or "")
    first = str(next(iter(text)) if not isinstance(text, list) else text[0])
    assert "BB2" in first[:40], f"offset=6 没有从第 6 个字起: {first[:60]!r}"

    past_text = json.dumps(past, ensure_ascii=False)
    assert "No more content" in past_text, (
        f"越过末尾的正数该有诚实的那句，而不是被新判据打死: {past_text[:180]}")
    assert not str(past.get("error") or "").startswith("invalid_request"), past


@pytest.mark.asyncio
async def test_the_two_doors_quote_the_same_judge(doc_path):
    """同一颗参数在两扇门上说的是**同一句**（同一位裁判，不各抄一份）。

    `web_read` 那一句早就在了；这一格钉的是"抄来的句子会漂"这一族：哪天有人给某一扇
    另写一句区间话，这里就红。

    URL 要给**拼法正确**的那一颗：现场量到 `web_read` 的顺序是先问地址再问这颗数字
    （`offset=-6` 配一颗坏 URL 时答的是 URL 那一族），拿坏 URL 比句子只会比到另一族的话。
    地址指向回环并临时开豁免（与仓里其余尺同一惯例）—— 这一发本就不该出网：区间门在
    任何一发之前，若它跑到了网上，交回来的会是网络错而不是这句拒绝，那一格照样红。
    """
    from dhole_mcp import identity
    identity.set_value("allow_private", True)
    try:
        local = await _call("doc_parse", {"file_path": doc_path, "offset": -6})
        remote = await _call("web_read", {"url": "http://127.0.0.1:9/x",
                                          "offset": -6})
    finally:
        identity.set_value("allow_private", False)
    a = str(local.get("error") or "")
    b = str(remote.get("error") or "")
    for frag in ("offset", "must be between 0"):
        assert frag in a and frag in b, (a, b)


@pytest.mark.asyncio
async def test_a_missing_still_owns_its_own_sentence(tmp_path):
    """本把新增的检查不许抢走路那一族的句子（顺序没变，句子也没被改写）。"""
    payload = await _call("doc_parse", {"file_path": str(tmp_path / "nope.txt")})
    error = str(payload.get("error") or "")
    assert "File not found" in error, payload
    assert "offset" not in error, f"缺文件的那封把错算到 offset 头上: {error}"
