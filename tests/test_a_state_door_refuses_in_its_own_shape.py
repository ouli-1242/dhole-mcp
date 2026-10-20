# -*- coding: utf-8 -*-
"""状态面与存储面的**参数拒绝**不许借抓取的壳（评审 R02-103 · F97）。

量到的（`r181_envelope_field_census.json` + 半径普查 `r183_argument_refusal_doors.json` ·
档案 `sessions/P02-R56-envelope-field-census.jsonl`、`sessions/P02-R57-argument-refusal-doors.jsonl`
· HEAD ad5e70b/d09323a · 服务 pid 15668 · 11 个门名各交一颗陌生键）：

| 门 | 是不是抓取门 | 陌生参数被拒时的格数 | 里面有什么 |
|---|---|---|---|
| 七扇 `web_*` / `feed_read` | 是 | 13 | 说抓取，合理 |
| `sys_state` | **否** | **13** | `content` / `content_ok` / `duration_ms` / `fetched_at` / `fetcher_used` / `url` / `focus_*` |
| `cred_defaults` | **否** | **13** | 同上（它**自己**校验器那条路只交 4 格 —— 两条路两副形状） |
| `sys_guide` | 否 | 13 | 它没有自己的模型（`models.py` 里没 `SysGuideModel`）→ **本把不收，另记一行** |

作者：`server.py:11223` 的 ArgumentRefusal 兜底不分门，一律 `_invalid_request_result` →
`ResponseModel`（quality.py:2577）。一次什么都没读的被拒回执，因此像刚读完一页 —— 同一把尺下
近邻两扇的成功/拒绝只差 `error` 一颗，所以这不是"两副形状各有道理"，是漂移。

契约（本文件钉住）：
  * `sys_state` 与 `cred_defaults` 的参数被拒回执只许出现**自己模型里有的**格，
    且必须仍然带 `invalid_request:` 前缀与本门申报句（改形状不许把信封三件改没）；
  * 抓取那一族的 13 格**一格不许少**（反向锁 —— 这是一次收窄，不是一次统一）；
  * 出路/支持列表不许被剪（R02-095 那一族：陌生参数那句要交出 `Supported: [...]` 的整段）。
"""
from __future__ import annotations

import json

import pytest

from dhole_mcp.models import CredDefaultsModel, SysStateModel
from dhole_mcp.server import MasterFetchServer

STATE_DOORS = [
    ('sys_state', set(SysStateModel.model_fields), {}),
    ('cred_defaults', set(CredDefaultsModel.model_fields), {}),
]
FETCH_ONLY = ('content', 'content_ok', 'duration_ms', 'fetched_at', 'fetcher_used',
              'focus_selection', 'focus_terms_absent', 'focus_terms_total', 'url')


async def _face(tool: str, args: dict) -> dict:
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
        d = await _face(door, {**args, 'r183_bogus_key': 1})
        leaked = sorted(k for k in FETCH_ONLY if k in d)
        assert not leaked, f'{door} 的参数被拒回执带着抓取的字段 {leaked}：{sorted(d)}'
        assert set(d) <= own_fields, f'{door} 交了自己模型以外的格：{sorted(set(d) - own_fields)}'

    @pytest.mark.asyncio
    @pytest.mark.parametrize('door,own_fields,args', STATE_DOORS,
                             ids=[d[0] for d in STATE_DOORS])
    async def test_the_envelope_three_still_arrive_in_the_new_shape(self, door, own_fields, args):
        """换形状不许顺手把信封换没：前缀、本门申报、能照着改的支持列表。"""
        d = await _face(door, {**args, 'r183_bogus_key': 1})
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
        d = await _face(door, {**args, 'r183_bogus_key': 1})
        for field in ('url', 'status', 'content_ok', 'next_action', 'error'):
            assert field in d, f'{door} 的抓取信封少了 {field}：{sorted(d)}'
