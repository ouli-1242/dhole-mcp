# -*- coding: utf-8 -*-
"""`sys_state` 的门话要有作者，两句都算（评审 R02-116 · F98）。

量在 `r188_door_table_census.json` + `r189_sys_state_clause_authors.json`（档案
`sessions/P02-R59-door-table-census.jsonl` · HEAD 2c80c91 · 零外网 · in-memory 与线上同一处理器）：

| 面 | 行首那句 | 作者 |
|---|---|---|
| `clear=["nosuch_class"]` | `Nothing was erased.` | server.py:9108 的字面量 |
| 陌生参数 / `show=7` / `confirm='one'` | `Nothing was fetched or changed.` | **没人** —— 表外默认支 `_UNDECLARED_DOOR_WAY_OUT`（context.py:144） |
| `clear=["sessions"]`（待确认） | 无申报，直接教重按 | 另一族（pending_approval），本把不动 |

`_DOOR_KIND` 是这张表的在册名单（八扇 + store）。F97 之后 `sys_state` 的**形状**归了自己的模型，
**句子**仍在表外 —— 而默认支的意义是"这扇门没人认领"。同一族的 `cred_defaults` 早就按 R02-102
进过表，判决原文是"进表用的就是它自己那句，不是本轮发明的新词"。

契约（本文件钉住 · 内容一字不改，改的只有作者的归属）：
  * `sys_state` 在 `_DOOR_KIND` 里**在册**，且在册之后调用方听到的那句话**与今天相同**
    （登记不许变成改口 —— 这是"两种句子都有道理"那个判决的可测面）；
  * erasure 面那句 `Nothing was erased.` 由 `context` 装配，server 里不再焊着那句申报；
  * 其余三处 `Nothing was erased` 都**不在申报位**（一处在 `message` 字段，两处在正文/错误文案）
    —— 这一把不合并它们（反向锁钉住"不许顺手全替换"）；
  * 别门的句子一颗不许跟着变（`cred_defaults` 仍 store、`web_read` 仍 read）。
"""
from __future__ import annotations

import inspect

import pytest

from dhole_mcp import context
from dhole_mcp import server as server_mod

BOGUS = 'r190_bogus_key'


async def _face(tool: str, args: dict) -> dict:
    import json

    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport
    from dhole_mcp.server import MasterFetchServer

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    return json.loads(''.join(getattr(b, 'text', '') for b in res.content))


class TestTheDoorIsClaimed:

    def test_sys_state_is_in_the_roster(self):
        """在册 = 这扇的句子有人写过。默认支只该兜"没人认领的门"。"""
        assert 'sys_state' in context._DOOR_KIND, (
            'sys_state 的门话仍由 `_UNDECLARED_DOOR_WAY_OUT` 代答 —— 它的形状在 F97 已经归了自己，'
            '句子不能再住在"没有作者"那一支')

    def test_registering_did_not_change_what_the_caller_hears(self):
        """登记不是改口：这句话与表外默认支**逐字相同**，变的只是它有了作者。"""
        assert context.nothing_went_out('sys_state') == 'Nothing was fetched or changed.', (
            '在册之后调用方听到的句子变了 —— 这一把的判决是"两句都留着、内容一字不改"')

    @pytest.mark.asyncio
    @pytest.mark.parametrize('args', [{BOGUS: 1}, {'show': 7}, {'confirm': 'one'}])
    async def test_the_parameter_faces_lead_with_its_own_declared_sentence(self, args):
        d = await _face('sys_state', args)
        assert (d.get('next_action') or '').startswith(context.nothing_went_out('sys_state')), d


class TestTheErasureFaceKeepsItsSentenceWithAnAuthor:

    @pytest.mark.asyncio
    async def test_a_bad_state_class_still_says_it_erased_nothing(self):
        d = await _face('sys_state', {'clear': ['nosuch_class']})
        na = d.get('next_action') or ''
        assert na.startswith(context.nothing_erased()), (
            f'erasure 面的申报不再由作者装配：{na[:120]}')
        assert 'clear= takes a state class' in na, f'出路被顺手剪了：{na[:160]}'

    def test_the_declaration_is_no_longer_welded_in_server(self):
        """源码形状：申报位交给 `context`，server 里那句字面量不许还留着。"""
        src = inspect.getsource(server_mod.MasterFetchServer)
        assert 'Nothing was erased. clear=' not in src, (
            'erasure 面的申报又焊回文案里了 —— 那一处就是"同一扇两种作者"的现场')
        assert 'nothing_erased' in src, 'erasure 面不再从作者那儿取话了'

    def test_the_message_field_is_not_mass_replaced(self):
        """反向锁：其余三处 `Nothing was erased` 住的不是申报位，这一把不许顺手合并。

        第一版这一格数的是**带引号的整颗字面量** `"Nothing was erased."`，而三处里只有一处
        是那个形状（`message = "Nothing was erased."`）；另两处一个嵌在 config 那句正文里、
        一个是 `Nothing was erased by this call.` —— 于是判据自己报红（A 层，尺子的错）。
        改成数子串：**三处正文都还在**（少一处就是有人把它们一起"归给作者"了）。
        """
        src = inspect.getsource(server_mod.MasterFetchServer)
        assert src.count('Nothing was erased') >= 3, (
            f'正文里那三处只剩 {src.count("Nothing was erased")} 处 —— 它们说的是"这一发没擦掉什么"，'
            '属正文/错误文案，不属申报位')
        assert 'message = "Nothing was erased."' in src, (
            '`message` 那颗正文被换掉了：回执正文与申报句是两个格子，这一把只动后者')


class TestTheOtherDoorsAreNotTouched:

    @pytest.mark.parametrize('door,expected', [
        ('cred_defaults', 'Nothing was stored or forgotten.'),
        ('web_read', 'Nothing was fetched.'),
        ('web_act', 'Nothing was run.'),
        ('web_search', 'Nothing was searched - no engine was asked.'),
    ])
    def test_a_neighbour_sentence_does_not_move(self, door, expected):
        assert context.nothing_went_out(door) == expected, door

    def test_sys_guide_moved_on_purpose(self):
        """【原判据搬家 · F99】这一颗从前在上面的名单里，钉的是 `sys_guide` 听默认支。

        F98 把"不许由没人认领的默认支代答"立成通则，F99 就是拿它办 `sys_guide`：它从此有
        自己的句子。所以"别门不许动"的名单里它不再算别门 —— 留着那条断言就等于让上一把的
        反向锁否决这一把的修法。钉的方向不变，只是这扇换了阵营。
        """
        assert context.nothing_went_out('sys_guide') == 'Nothing was served from the handbook.'
