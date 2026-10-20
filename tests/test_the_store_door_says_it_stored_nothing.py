# -*- coding: utf-8 -*-
"""存储门自己说的话（评审 R02-102）：`cred_defaults` 的每一种拒绝都说同一句本门话，且说一遍。

量到的形状（真机 `review/.runtime/p1/r121_sys_doors.json` · 档案
`review/round-02/sessions/P02-R23-sys-doors.jsonl` · HEAD 71c0ad3 · pid 3268 · 离线）：

  C4 `cred_defaults(set={"bogus": 1})` → `error` = `Unknown key(s) bogus: …`（**无**
     `invalid_request:` 前缀），`next_action` = `Nothing was stored or forgotten.`
  C6 `cred_defaults(delete=["proxy"])`（陌生参数名，落到装配层）→
     `next_action` = `Nothing was fetched or changed. The name in `error` is not one this
     tool takes …`
  同一扇两种申报（`clause_variants_seen` 这一格就列着这两句）。

这正是 F83 留在判据里的那条反向守卫所等的东西：那一把把 `cred_defaults` 留在 `_DOOR_KIND`
表外的理由是「本轮没有一条真回执证明现在这句害到它」—— 回执现在有了，而且**不是新词**：
`Nothing was stored or forgotten.` 是这一扇自己已有的拒绝里的原话（`server.py` 里五处字面量）。
所以这一把做三件事：

  ①这一扇进门话簿（类别 `store`，词就是它自己那句）；
  ②五处字面量改成向同一位作者取词 —— 否则"进表"只是又造一位作者；
  ③这一扇的门口拒绝补上 `invalid_request:` 前缀（R02-097 立的契约，本门是漏网的那一扇）。

`sys_state` / `sys_guide` 两扇仍留在表外，反向守卫跟着收窄到这两扇（本轮没有它们的受害证据；
R02-101/103 量到的是它们的**形状**问题，不是动词问题）。
"""
from __future__ import annotations

import inspect
import json
import re

import pytest

from dhole_mcp.context import nothing_went_out, tool_scope
from dhole_mcp.server import MasterFetchServer

CLAUSE = "Nothing was stored or forgotten."
OLD_DEFAULT = "Nothing was fetched or changed."
RE_CLAUSE = re.compile(r"[Nn]othing was [a-zA-Z ,\-]+?\.")

# 五种门口拒绝：全部在写库之前返回，不碰磁盘、不碰网络。
REFUSALS = [
    pytest.param({"set": {"bogus": 1}}, id="陌生键"),
    pytest.param({"set": {"proxy": "http://x"}, "unset": ["proxy"]}, id="同一颗既存又忘"),
    pytest.param({"set": {"auth": {"user": "u", "pass": "p"}}}, id="凭据没给 host"),
    pytest.param({"set": {"proxy": "http://x", "host": "api.example.com"}},
                 id="给了 host 却没给需要 host 的键"),
]


async def _face(args: dict) -> dict:
    """走**真握手**：陌生参数名那一颗是在 `call_tool` 里被 `ArgumentRefusal` 转成信封的，
    直接调 `_dispatch` 会拿到异常而不是回执 —— 而这一行要判的正是线上读到的那句话。
    """
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool("cred_defaults", args)
    d = json.loads("".join(getattr(b, "text", "") for b in res.content))
    assert d.get("error"), f"这一形状本该被拒，却回了没有 error 的包: {d}"
    return d


class TestTheStoreDoorSpeaksOneSentence:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("args", REFUSALS)
    async def test_a_refusal_from_the_store_door_is_named_as_input(self, args):
        d = await _face(args)
        assert d["error"].startswith("invalid_request:"), d["error"][:180]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("args", REFUSALS)
    async def test_it_says_its_own_sentence_not_the_fetch_door_s(self, args):
        na = (await _face(args)).get("next_action") or ""
        assert na.startswith(CLAUSE), na[:200]
        assert OLD_DEFAULT not in na, f"这一扇还在借用别的门的动词: {na[:200]}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("args", REFUSALS)
    async def test_the_sentence_is_spoken_once(self, args):
        """两遍等于两位作者：字面量搬进装配点后，旧那句若没删干净会拼成两句。"""
        na = (await _face(args)).get("next_action") or ""
        assert len(RE_CLAUSE.findall(na)) == 1, na[:220]

    @pytest.mark.asyncio
    async def test_the_unknown_argument_name_agrees_with_the_key_level_refusal(self):  # noqa: D
        """C6 那一格（陌生参数名，装配层拒）必须与门内那几格说同一句。

        修前它落 `context` 的表外默认支说 `fetched or changed`，而同一扇的门内五处说
        `stored or forgotten` —— 一扇两种申报就是 R02-102 的读数本身。
        """
        d = await _face({"delete": ["proxy"]})
        na = d.get("next_action") or ""
        assert na.startswith(CLAUSE), na[:200]
        assert "fetched or changed" not in na, na[:200]


class TestTheWordsHaveOneAuthor:
    def test_the_door_is_declared_by_class_not_by_default(self):
        assert nothing_went_out("cred_defaults") == CLAUSE

    def test_the_body_of_the_door_no_longer_writes_the_sentence(self):
        """这一扇的函数体里不许再出现门话字面量 —— 词只许向 `context` 那一位取。

        只判 `cred_defaults` 这一颗方法：仓库里其余按门已经写对的句子不在这把刀的半径里
        （F86 的同一取舍，写在那把尺的 docstring 里）。
        """
        src = inspect.getsource(MasterFetchServer.cred_defaults)
        hits = [ln.strip()[:90] for ln in src.splitlines()
                if re.search(r"Nothing was (stored|fetched|sent|run)", ln)
                and not ln.strip().startswith("#")]
        assert hits == [], f"这一扇里还有人在抄门话: {hits}"

    def test_the_undeclared_pair_keeps_the_old_sentence(self):
        """【原判据搬家 · F99】这颗守卫跟着证据走完了它该走的全程：表外那两扇如今都有主了。

        原句是"剩下两扇仍没有受害证据，不替它们发明新词"。F98 给 `sys_state` 收了证据
        （句子逐字未变，只是不再由默认支代答），F99 给 `sys_guide` 收了更硬的一面：它的
        陌生参数拒绝从前穿一副 13 格的抓取壳，而它连成功面都是散文（真机
        `r192_sys_guide_faces.json` 六面）—— 一扇两种语言，这就是"发明新词"所缺的那份证据。

        期待强度没有降，只是换了对象：**没被认领的门名仍须落默认支**。这条要是红了，就等于
        有人把默认支掏空了 —— 那正是 F83 那把刀最初要防的方向。
        """
        from dhole_mcp.context import _DOOR_KIND
        assert _DOOR_KIND.get("sys_state") == "state", _DOOR_KIND
        assert _DOOR_KIND.get("sys_guide") == "guide", _DOOR_KIND
        with tool_scope("no_such_door_yet"):
            assert nothing_went_out() == OLD_DEFAULT

    def test_cred_defaults_is_no_longer_in_the_undeclared_list(self):
        from dhole_mcp.context import _DOOR_KIND
        assert _DOOR_KIND.get("cred_defaults") == "store", _DOOR_KIND


async def _face_ok(args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool("cred_defaults", args)
    return json.loads("".join(getattr(b, "text", "") for b in res.content))


class TestNothingElseMoved:
    """成功那一面一字不改：这一扇的 `message` 说的是库里现在有什么，不是申报句。"""

    @pytest.mark.asyncio
    async def test_an_empty_store_still_answers_in_its_own_words(self):
        d = await _face_ok({"show": True})
        assert not d.get("error"), d
        assert "Nothing is stored" in (d.get("message") or ""), d.get("message")
        assert d.get("error", "").startswith("invalid_request:") is False
