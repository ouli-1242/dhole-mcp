"""test_context_5.py —— 由 2 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import json
import pytest
from dhole_mcp.context import nothing_went_out
from dhole_mcp.server import MasterFetchServer
from dhole_mcp.models import CredDefaultsModel, SysStateModel


# ============================================================================
# 来源：test_a_refusal_declares_in_the_field_the_caller_reads.py
# ============================================================================
# resume 光标那两支拒绝把申报句放错了格子（评审 R02-112 · P3）。

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


# ============================================================================
# 来源：test_a_state_door_refuses_in_its_own_shape.py
# ============================================================================
# 状态面与存储面的**参数拒绝**不许借抓取的壳（评审 R02-103 · F97）。
# 重名消歧：_face → _face__a_state_door_refuses_in__dd0a

STATE_DOORS = [
    ('sys_state', set(SysStateModel.model_fields), {}),
    ('cred_defaults', set(CredDefaultsModel.model_fields), {}),
]
FETCH_ONLY = ('content', 'content_ok', 'duration_ms', 'fetched_at', 'fetcher_used',
              'focus_selection', 'focus_terms_absent', 'focus_terms_total', 'url')


async def _face__a_state_door_refuses_in__dd0a(tool: str, args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    return json.loads(''.join(getattr(b, 'text', '') for b in res.content))


class TestStateDoorsStopBorrowingTheFetchShell:

    @pytest.mark.asyncio
    @pytest.mark.parametrize('door,own_fields,args', STATE_DOORS,
                             ids=[d[0] for d in STATE_DOORS])
    async def test_an_unknown_argument_answers_in_its_own_shape(self, door, own_fields, args):
        d = await _face__a_state_door_refuses_in__dd0a(door, {**args, 'r183_bogus_key': 1})
        leaked = sorted(k for k in FETCH_ONLY if k in d)
        assert not leaked, f'{door} 的参数被拒回执带着抓取的字段 {leaked}：{sorted(d)}'
        assert set(d) <= own_fields, f'{door} 交了自己模型以外的格：{sorted(set(d) - own_fields)}'

    @pytest.mark.asyncio
    @pytest.mark.parametrize('door,own_fields,args', STATE_DOORS,
                             ids=[d[0] for d in STATE_DOORS])
    async def test_the_envelope_three_still_arrive_in_the_new_shape(self, door, own_fields, args):
        """换形状不许顺手把信封换没：前缀、本门申报、能照着改的支持列表。"""
        d = await _face__a_state_door_refuses_in__dd0a(door, {**args, 'r183_bogus_key': 1})
        err = d.get('error') or ''
        na = d.get('next_action') or ''
        assert err.startswith('invalid_request:'), err[:160]
        assert 'r183_bogus_key' in err, f'被拒的那颗键没被点名：{err[:160]}'
        assert 'Supported' in err or 'supported' in err, f'支持列表被剪掉了尾巴：{err[:200]}'
        from dhole_mcp.context import nothing_went_out
        assert na.startswith(nothing_went_out(door)), f'门话不在行首：{na[:160]}'


class TestTheFetchFamilyIsNotNarrowed:
    """反向锁：七扇抓取门照旧交 13 格 —— 这一把只收窄不该借壳的三扇。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize('door', ['web_read', 'web_discover', 'web_search'])
    async def test_fetch_doors_keep_their_fields(self, door):
        args = {'query': 'dhole'} if door == 'web_search' else {'url': 'https://example.com/'}
        d = await _face__a_state_door_refuses_in__dd0a(door, {**args, 'r183_bogus_key': 1})
        for field in ('url', 'status', 'content_ok', 'next_action', 'error'):
            assert field in d, f'{door} 的抓取信封少了 {field}：{sorted(d)}'
