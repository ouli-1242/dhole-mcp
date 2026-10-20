# -*- coding: utf-8 -*-
"""门表是扫出来的，不是手抄的 —— 每扇在册的门都过同一把尺（评审 B-76/B-77）

现场（`review/.runtime/p1/r257_door_roster_census.json` · HEAD fa5bc4b · 零外网 · in-process）：
这条轴上的判据此前只存在于**一次性普查**里（r215/r216/r218 数行首位置，r246/r250 数 diagnosis，
r257 数三张登记表）。普查的毛病不在判据，在名单：F110 那把尺用的是 `JSON_DOORS` 六扇手抄名单，
而线上有 11 扇 —— 于是"补齐了拒绝把手"这句话当时只对 6/11 成立，剩下的 5 扇没人量过。
本轮 r257 就是这么量出两颗漏网的：`cred_defaults` / `sys_state` 的拒绝行首申报齐全，
`diagnosis` 整格缺席（它们穿自己的 `SysStateModel` / `CredDefaultsModel`，那两副模型压根没有这一格）。

契约（本文件钉住；名单一律从登记表派生，新增一扇就自动进尺，不增一行代码）：
  1. 尺具自己的名单 = `server._TOOL_DEFS` 全量（判据不许自己抄一份）。**登记表在不在**那一轴
     归 `tests/test_every_door_is_asked_of_every_roster.py`（B-75 那把，F112 给它补了参数面那张表）——
     这把只问**脸上**的东西，两份判据不重叠，同一件事不许有两位裁判各数一遍；
  2. 每扇门的拒绝，`next_action` 行首那半句就是装配器给这扇算出的那句（`nothing_went_out(tool)`）；
     散文门（`_PROSE_DOORS`）说散文，申报住在第二行；
  3. 每扇 JSON 门的拒绝交出 `diagnosis`，`code` 由判据表给（不许由 `_LEADING_CODE_RE` 兜底发明）、
     `retryable` false、`recover_with` 非空 —— 手册（`references/fields.md:67`）教的分支把手
     不许只对一部分设计过的失败存在（R02-129 修了 6 扇手抄名单里的，R02-130 的 state-door 半边
     在这一把补齐到 10/10 扇）；
  4. 归属锁（F93）：哨兵换掉判据表唯一的读者 `quality.classify_error_text` ⇒ 全部被扫的门跟着变
     （锁在 `ResponseModel` 那一族的适配器 `_diagnose` 上只盖得住 9/11 扇）。任何一扇自己手写
     一颗 code 的修法在这一格会红；
  5. 在册的两扇状态门的**自家判决句**也在表里（`Nothing matches …` / `… is show-only …`）：
     大写开头的那句兜底给 ''，首词是类名的那句兜底会**发明**一颗 'models'（真机 r258）；
  6. 正控制：加了格子不等于每发都发 —— 干净 census 与一次正文读取都仍然没有 `diagnosis`；
     散文门 `sys_guide` 按设计没有这一格，不许被"顺手补齐"。
"""

from __future__ import annotations

import json

import pytest

from dhole_mcp import context as CTX
from dhole_mcp import quality as Q
from dhole_mcp import server as SRV
from dhole_mcp.server import MasterFetchServer

#: 一颗谁都不收的参数名 ⇒ 每扇都停在参数层 ⇒ 同一张拒绝脸（`invalid_request`）。
_UNKNOWN = "totally_not_an_argument"
#: 缺必填位会先撞"缺参数"那一面，量到的就不是陌生参数名这一面 —— 所以按门补一颗锚点。
_ANCHORS = (("url", "https://example.invalid/x"),
            ("file_path", ""),          # 由 fixture 填
            ("query", "b-76-b-77"),
            ("topic", "call"),
            ("action", "click"),
            ("instructions", "b-76-b-77"))

ALL_DOORS = [td["name"] for td in MasterFetchServer._TOOL_DEFS]
JSON_DOORS = [d for d in ALL_DOORS if d not in SRV._PROSE_DOORS]
PROSE_DOORS = [d for d in ALL_DOORS if d in SRV._PROSE_DOORS]
#: 判据表在册的 token（兜底发明的词一律不算"有作者"）。
TABLE_CODES = {e[1] for e in Q._DIAGNOSIS_PREFIXES} | {e[1] for e in Q._DIAGNOSIS_CONTAINS}


@pytest.fixture(scope="module")
def doc_path(tmp_path_factory):
    p = tmp_path_factory.mktemp("door-roster") / "body.txt"
    p.write_text("AAA1 BBB2 CCC3 DDD4\n" + "v" * 60, encoding="utf-8")
    return str(p)


async def _call(tool: str, args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    text = "".join(getattr(b, "text", "") for b in res.content)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_prose": text[:900]}


def _refusal_args(tool: str, doc_path: str) -> dict:
    accepted = SRV._TOP_LEVEL_ARGS.get(tool, frozenset())
    assert _UNKNOWN not in accepted, f"{tool} 竟然收了 {_UNKNOWN} —— 这张脸不统一了"
    for name, value in _ANCHORS:
        if name not in accepted:
            continue
        args = {name: doc_path if name == "file_path" else value, _UNKNOWN: 1}
        return args
    return {_UNKNOWN: 1}


def _first_sentence(text: str) -> str:
    out, seen = "", False
    for ch in text:
        out += ch
        if ch == ".":
            seen = True
            break
    return out if seen else text


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ALL_DOORS)
async def test_the_uniform_refusal_reaches_the_param_layer(tool, doc_path):
    """尺具自己先站住：每扇都要能被同一颗参数名拒绝，否则下面几条量的是别的东西。"""
    payload = await _call(tool, _refusal_args(tool, doc_path))
    prose = payload.get("_prose", "")
    err = str(payload.get("error") or prose)
    assert "invalid_request" in err or tool in SRV._PROSE_DOORS, (tool, err[:120])
    assert "Unsupported argument" in err, (tool, err[:120])


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ALL_DOORS)
async def test_every_registered_door_heads_its_own_declaration(tool, doc_path):
    """判据 2（R02-089 + R02-111 那条轴的门表版）：行首那半句由装配器给，逐扇对上。"""
    payload = await _call(tool, _refusal_args(tool, doc_path))
    expect = CTX.nothing_went_out(tool)
    if tool in SRV._PROSE_DOORS:
        lines = str(payload.get("_prose") or "").splitlines()
        assert len(lines) >= 2, (tool, lines)
        head = _first_sentence(lines[1])
    else:
        head = _first_sentence(str(payload.get("next_action") or ""))
    assert head == expect, (tool, f"行首是 {head!r}，装配器给这扇算的是 {expect!r}")


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", JSON_DOORS)
async def test_every_json_door_hands_over_a_table_authored_code(tool, doc_path):
    """判据 3（R02-129 → R02-130）：把手不许只对 6/11 扇存在 —— 名单从登记表派生。"""
    payload = await _call(tool, _refusal_args(tool, doc_path))
    dia = payload.get("diagnosis")
    assert isinstance(dia, dict), f"{tool} 的把手整格缺席：{sorted(payload)}"
    assert dia.get("code") in TABLE_CODES, (tool, dia)
    assert dia.get("retryable") is False, (tool, dia)
    assert str(dia.get("recover_with") or "").strip(), (tool, dia)


@pytest.mark.asyncio
async def test_the_codes_come_from_the_table_for_every_door(doc_path, monkeypatch):
    """判据 4（归属锁 F93）：哨兵换掉表本身 ⇒ 全部 JSON 门跟着变。

    这里换的是 `classify_error_text`（判据表唯一的读者，`_diagnose` 也问它）而不是
    `_diagnose`（ResponseModel 那一族的适配器）—— 状态门不穿 ResponseModel，锁在适配器上
    就等于只锁了 9/11 扇。
    """
    monkeypatch.setattr(Q, "classify_error_text", lambda err: ("SENTINEL", False, "from the table"))
    for tool in JSON_DOORS:
        payload = await _call(tool, _refusal_args(tool, doc_path))
        dia = payload.get("diagnosis") or {}
        assert dia.get("code") == "SENTINEL", (tool, dia)
        assert str(dia.get("recover_with") or "").startswith("from the table"), (tool, dia)


#: 两扇状态门**自家**的拒绝脸（r258 量到的那几张：都不是参数层的 `invalid_request`，
#: 而是这扇自己的判决住在方法体里）。名单按门派生不现实（脸要有人写），但每张都要过同一把尺。
STATE_DOOR_FACES = [
    ("sys_state", {"clear": ["no_such_class"]}),
    ("sys_state", {"clear": ["session:nope"]}),
    ("sys_state", {"clear": ["cdp:host=nope"]}),
    ("sys_state", {"clear": ["models"]}),
    ("sys_state", {"show": ["nope"]}),
    ("cred_defaults", {"unset": ["no_such_key"]}),
    ("cred_defaults", {"set": {"no_such_key": "AAA1"}}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", STATE_DOOR_FACES)
async def test_a_state_doors_own_verdict_hands_over_a_table_authored_code(tool, args):
    """把手不许只住在参数层：这扇自己写的判决句也要有在册的 token（判据 3 的另一半）。"""
    payload = await _call(tool, args)
    assert str(payload.get("error") or ""), (tool, args, sorted(payload))
    dia = payload.get("diagnosis")
    assert isinstance(dia, dict), f"{tool} 的自家判决没交出把手：{sorted(payload)}"
    assert dia.get("code") in TABLE_CODES, (tool, args, dia)
    assert dia.get("retryable") is False, (tool, dia)
    assert str(dia.get("recover_with") or "").strip(), (tool, dia)


def test_the_state_doors_own_sentences_are_in_the_table():
    """表锁（与 F110 那格同法）：两句自家话各有在册 token，不靠首词兜底发明。

    `Nothing matches ...` 大写开头 ⇒ 小写兜底规则给 ''；`models is show-only ...` 首词是
    类名 ⇒ 兜底会**发明**一颗 'models'（真机 r258）。两句都是我们自己写的，都该在册。
    """
    for sentence, want in (("Nothing matches 'no_such_class': a token is one of cache",
                            "unknown_state_token"),
                           ("models is show-only: this tool only reports them",
                            "show_only_class")):
        code, retryable, recover = Q.classify_error_text(sentence)
        assert code == want, (sentence[:40], code)
        assert retryable is False and str(recover).strip(), (sentence[:40], code, recover)


@pytest.mark.asyncio
async def test_a_state_door_clean_success_carries_no_handle():
    """正控制（判据 3 的另一面）：格子加了，不许变成每发都发 —— 干净 census 仍然没有把手。"""
    payload = await _call("sys_state", {})
    assert not payload.get("error"), payload.get("error")
    assert "diagnosis" not in payload, sorted(payload)
    payload = await _call("cred_defaults", {"show": True})
    assert not payload.get("error"), payload.get("error")
    assert "diagnosis" not in payload, sorted(payload)


def test_the_sweep_roster_is_the_wire_roster():
    """尺具自己的前提：名单 = `_TOOL_DEFS` 全量，一格不多一格不少（判据不许自己抄名单）。

    登记表**在不在**那一轴由 `tests/test_every_door_is_asked_of_every_roster.py` 问（B-75 那把：
    `_DOOR_KIND` 在册 + 非抓取门有自己的形状 + 表外默认支留着兜未来的生门）。这把只问**脸上**
    的东西（行首那句话、把手那格），两份判据不重叠 —— 同一件事不许有两位裁判各数一遍。
    """
    assert ALL_DOORS == [td["name"] for td in MasterFetchServer._TOOL_DEFS]
    assert len(ALL_DOORS) == len(set(ALL_DOORS)) > 8, ALL_DOORS
    assert set(JSON_DOORS) | set(PROSE_DOORS) == set(ALL_DOORS)
    assert set(SRV._STATE_DOOR_MODELS) <= set(ALL_DOORS)
    assert set(SRV._PROSE_DOORS) <= set(ALL_DOORS)
    for door in ALL_DOORS:
        # `sys_state` 那句与默认支**同字**是刻意的（R02-116：登记不是改口），所以这里按在册问，
        # 不按字串不等问。
        assert door in CTX._DOOR_KIND, door


@pytest.mark.asyncio
async def test_a_clean_success_still_carries_no_handle(doc_path):
    """正控制：把手不是每发都发 —— ABSENT = 干净成功（models.py:143）要照旧成立。"""
    payload = await _call("doc_parse", {"file_path": doc_path})
    assert payload.get("content_ok") is True, payload
    assert "diagnosis" not in payload, sorted(payload)
