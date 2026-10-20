# -*- coding: utf-8 -*-
"""共用的陌生参数闸要说本门的动词，而且要说在行首（评审 R02-110 · R02-111）。

量到的（`review/.runtime/p1/r162_shared_guard_verb.json` · 14 发 · HEAD 86a17d6 · pid 23568 ·
门话由 `context.nothing_went_out()` 自己算，不手数）：

  | 门 · 撞到的作者 | 该说 | 实际说 | 门话在行首？ |
  |---|---|---|---|
  | `web_read` · `options=` 袋子（server.py:4494） | Nothing was **fetched** | Nothing was **sent** | 否 |
  | `web_read` · `actions=` 搬过家的键（server.py:4479） | Nothing was **fetched** | Nothing was **sent** | 否 |
  | `web_act` · 缺 `actions`（server.py:6548） | Nothing was **run** | Nothing was **sent** | 否 |
  | `web_request` · `method=GET` / 缺 `method`（6443 / 6436） | Nothing was sent | 同 | **否**（缀在句尾） |
  | 十一扇的「陌生键」通用支（4496 兜底） | 本门动词 | 本门动词 ✓ | 是 ✓ |

所以这一族不是全坏 —— 是**三处把写门的动词焊死在共用/单门文案里**，另两处动词对但位置不对。
信封契约（R02-087 / R02-100 / R02-102 一路钉下来的）要求：门话由 `context.nothing_went_out()`
这一位作者装配，并且落在 `next_action` 的**行首** —— 只读第一句的调用方（本仓评审自己的读数
`r161` 就是靠行首判的）不该听见别的门的话。

契约（本文件钉住）：
  * 同一颗 `options=` 袋子的拒绝，在 `web_read` / `feed_read` / `web_act` 三扇上各说**本门**的动词，
    且行首就是那句话；
  * 搬过家的键（`actions=` 出现在读门上）同理 —— 指路可以给别门，**申报**必须是本门；
  * `web_act` 缺 `actions` 说 "run" 不说 "sent"；`web_request` 的两句自己的形状拒绝行首是
    "Nothing was sent."；
  * 反向锁：不许有第二处把某扇的动词写死进**共用**文案（源码形状钉：`_reject_unknown_args` 里
    不得再出现字面量 `Nothing was sent.`）。
"""
from __future__ import annotations

import inspect
import json

import pytest

from dhole_mcp import context, server as server_mod
from dhole_mcp.server import MasterFetchServer

OK_URL = 'https://example.com/'
FOREIGN = ('fetched', 'sent', 'run', 'searched', 'stored or forgotten')


async def _face(tool: str, args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    return json.loads(''.join(getattr(b, 'text', '') for b in res.content))


def _clause_for(door: str) -> str:
    # 期望句子由**那一位作者**算出来：本文件不许手数动词（手数的判据会和文案一起漂）
    return context.nothing_went_out(door)


class TestTheSharedGuardSpeaksItsOwnDoor:

    @pytest.mark.asyncio
    @pytest.mark.parametrize('door,args', [
        ('web_read', {'url': OK_URL, 'options': {'timeout': 5}}),
        ('feed_read', {'urls': [OK_URL], 'options': {'timeout': 5}}),
        ('web_act', {'url': OK_URL, 'actions': [{'read': {}}], 'options': {'x': 1}}),
        ('web_crawl', {'url': OK_URL, 'options': {'depth': 2}}),
    ])
    async def test_the_options_bag_refusal_leads_with_this_door(self, door, args):
        d = await _face(door, args)
        na = d.get('next_action') or ''
        assert d.get('error', '').startswith('invalid_request:'), d.get('error')
        assert na.startswith(_clause_for(door)), f'{door} 的袋子拒绝行首不是本门的话：{na[:120]}'
        # 比对要**去掉句点**：`nothing_went_out()` 交的是整句（带 .），而下面按片段找动词。
        # 第一版没剥句点，于是每扇把自己门的动词也当成了外来动词 —— 三格假红，产品那边其实是对的。
        own = _clause_for(door).lower().rstrip('.')
        wrong = [v for v in FOREIGN if f'nothing was {v}' in na.lower()
                 and f'nothing was {v}' != own]
        assert not wrong, f'{door} 听见了别门的动词 {wrong}：{na[:160]}'

    @pytest.mark.asyncio
    @pytest.mark.parametrize('door,args', [
        ('web_read', {'url': OK_URL, 'actions': [{'read': {}}]}),
        ('web_read', {'url': OK_URL, 'body': 'x'}),
        ('feed_read', {'urls': [OK_URL], 'method': 'POST'}),
    ])
    async def test_a_moved_key_is_pointed_elsewhere_but_declares_here(self, door, args):
        d = await _face(door, args)
        na = d.get('next_action') or ''
        assert _clause_for(door) in na, f'{door} 的搬家键没说本门的话：{na[:160]}'
        assert na.startswith(_clause_for(door)), f'{door} 的门话不在行首：{na[:160]}'

    @pytest.mark.asyncio
    async def test_the_action_door_names_its_own_verb(self):
        """`web_act` 缺 `actions`：行动门不能替写门申报（server.py:6548 的实锤）。"""
        d = await _face('web_act', {'url': OK_URL})
        na = d.get('next_action') or ''
        assert na.startswith(_clause_for('web_act')), na[:180]
        assert 'nothing was sent' not in na.lower(), f'行动门说着写门的话：{na[:180]}'

    @pytest.mark.asyncio
    @pytest.mark.parametrize('args', [{'url': OK_URL, 'method': 'GET'}, {'url': OK_URL}])
    async def test_the_write_door_leads_with_its_own_clause(self, args):
        """`web_request` 两句自己的形状拒绝：动词本来就对，位置要从句尾搬到行首。"""
        d = await _face('web_request', args)
        na = d.get('next_action') or ''
        assert na.startswith(_clause_for('web_request')), f'门话缀在尾，只读首句的人看不见：{na[:180]}'


class TestTheRulerWasNotLoosened:
    """改文案不许把别处已有的对称拆掉。"""

    @pytest.mark.asyncio
    async def test_a_plain_unknown_key_still_speaks_its_own_door(self):
        for door, args in (('web_read', {'url': OK_URL, 'bogus_key': 1}),
                           ('web_search', {'query': 'dhole', 'bogus_key': 1}),
                           ('cred_defaults', {'bogus_key': 1}),
                           ('sys_state', {'bogus_key': 1})):
            d = await _face(door, args)
            na = d.get('next_action') or ''
            assert na.startswith(_clause_for(door)), (door, na[:140])

    def test_no_door_verb_is_welded_into_the_shared_guard(self):
        """源码形状：共用的陌生参数闸里不许再出现写死的某门动词。

        `_reject_unknown_args` 是**模块级**函数（server.py:4456），不是 MasterFetchServer 的方法 ——
        第一版按方法去取源码，AttributeError 就是那颗判据自己在猜形状。
        """
        src = inspect.getsource(server_mod._reject_unknown_args)
        for welded in ('Nothing was sent.', 'Nothing was fetched.', 'Nothing was run.'):
            assert welded not in src, f'共用闸里又焊进了 {welded!r} —— 那是又一位作者'
        assert 'nothing_went_out' in src, '共用的陌生参数闸不再从本门作者那儿取话了'
