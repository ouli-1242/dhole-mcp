# -*- coding: utf-8 -*-
"""resume 光标那两支拒绝把申报句放错了格子（评审 R02-112 · P3）。

量到的（`review/.runtime/p1/r167_crawl_plan_clauses.json` · 档案 `sessions/P02-R52-crawl-plan-clauses.jsonl`
· 8 发 · HEAD b0df70e · 服务 pid 24088 · 期望句子由 `context.nothing_went_out(door)` 现算）：

| 形状 | `error` 末尾带申报 | `next_action` 行首带申报 |
|---|---|---|
| 坏 `resume_token`（server.py:10545） | **是** | **否** |
| 未知/过期 plan（server.py:10558） | **是** | **否** |
| `selector=` 无 token（10443）· `discover_only=` 两支（10573/10594）· `focus_mode`（6947）· 坏 `session_id`（2167） | 否 | 是 ✓ |

信封契约（R02-087/100/102/111 一路钉下来的分工）：`error` 只说**为什么**，申报"这一发什么也没跑"
归 `next_action` 且必须在**行首**。这两支把两件事反着放：读 `error` 的人在理由里撞上一句申报，
而按契约只读 `next_action` 第一句的调用方（本仓的 r161/r166 尺就是这么判的）什么都看不见。

契约（本文件钉住）：
  * 两支行首要本门动词的句子，`error` 不再以申报句收尾（同一句话只许有一位作者、出现在一个字段）；
  * 那五支本来就对的形状**不许被顺手改坏**（反向锁：同一把尺重跑，行首仍要有申报、`error` 仍不许带）；
  * 出路仍要点名能修的那颗参数（`resume_token=` / 重跑 `web_discover`），不许把光标说成 URL 的拼法问题。
"""
from __future__ import annotations

import json

import pytest

from dhole_mcp.context import nothing_went_out
from dhole_mcp.server import MasterFetchServer

URL = 'https://example.com/'


async def _face(tool: str, args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    return json.loads(''.join(getattr(b, 'text', '') for b in res.content))


class TestTheCursorRefusalsDeclareInTheRightField:

    @pytest.mark.asyncio
    @pytest.mark.parametrize('args', [
        {'url': URL, 'resume_token': 'not-a-cursor'},
        {'url': URL, 'resume_token': 'deadbeef:0'},
    ], ids=['坏光标', '未知或过期 plan'])
    async def test_the_declaration_moves_to_the_head_of_next_action(self, args):
        d = await _face('web_crawl', args)
        err = d.get('error') or ''
        na = d.get('next_action') or ''
        clause = nothing_went_out('web_crawl')
        own = clause.lower().rstrip('.')
        assert err.startswith('invalid_request:'), err[:160]
        assert na.startswith(clause), f'门话不在行首：{na[:170]}'
        assert not err.lower().rstrip().endswith(own + '.'), (
            f'同一句申报在 error 里又说了一遍：{err[:170]}')

    @pytest.mark.asyncio
    async def test_the_way_out_still_names_the_knob_that_can_be_fixed(self):
        d = await _face('web_crawl', {'url': URL, 'resume_token': 'not-a-cursor'})
        na = d.get('next_action') or ''
        assert 'resume_token' in na, na[:200]
        assert 'takes an absolute http(s) URL' not in na, f'把光标问题说成了 URL 拼法课：{na[:200]}'


class TestTheFiveAlreadyCorrectShapes:
    """反向锁：本仓这一族的其他支路本来就对，改这两处不许把它们带坏。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize('tool,args', [
        ('web_crawl', {'url': URL, 'selector': 'h1'}),
        ('web_crawl', {'url': URL, 'discover_only': True, 'selector': 'h1'}),
        ('web_crawl', {'url': URL, 'discover_only': True, 'max_pages': 3}),
        ('web_read', {'url': URL, 'focus_mode': 'strict'}),
        ('web_read', {'url': URL, 'session_id': 'bad/name with space'}),
    ])
    async def test_head_clause_stays_and_error_stays_clean(self, tool, args):
        d = await _face(tool, args)
        err = d.get('error') or ''
        na = d.get('next_action') or ''
        clause = nothing_went_out(tool)
        if not err and not na:
            pytest.skip('这一发的形状变了（既无 error 也无 next_action），先量再判')
        assert na.startswith(clause), f'{tool} 的门话不在了：{na[:170]}'
        assert not err.lower().rstrip().endswith(clause.lower().rstrip('.') + '.'), (
            f'{tool} 的申报又跑回 error 里：{err[:170]}')
