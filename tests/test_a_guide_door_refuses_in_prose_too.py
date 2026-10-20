# -*- coding: utf-8 -*-
"""`sys_guide` 的被拒要说它自己的话，不再借抓取的壳（评审 R02-115 · F99）。

量在 `r192_sys_guide_faces.json`（六面 · HEAD 0a9ca08 · in-memory 与线上同一处理器 · 零外网）：

| 面 | 交回来的形状 | 开头 |
|---|---|---|
| 裸问 / `topic='tools'` | **散文**（1,145 / 102,754 字符） | `sys_guide: topics` / `topic='behavior' from references/tools.md` |
| `topic='nosuch_topic'` | **散文**（1,159 字符） | `sys_guide: topics  unknown topic 'nosuch_topic'.` |
| 陌生参数 `r192_bogus=1` | **JSON 13 格**（`content` / `content_ok` / `duration_ms` / `fetched_at` / `fetcher_used` …） | `{"status":0,…` |
| `topic=7`（错类型） | 同上，JSON 13 格 | 同上 |

所以这不是"它比别门少几格"，是**同一扇门里两种语言**：问不到答案时它用散文把能给的都给了（列出
全部 topic 与别名），而参数写错时它交一副刚抓完网页的 JSON —— 里面 `content=[]`、`url=""`，
说的全是别的事。`models.py` 里没有 `SysGuideModel`（r188 的普查 `no_own_model` 名单），因为
这扇按设计**没有** JSON 回执；刀11 那把扫面尺也正是按这件事给它开的例外
（"sys_guide 是唯一按设计回纯文本的工具，只受『不许念内部话』约束"）。

契约（本文件钉住 · 判决见 `review/decisions.md` 2026-10-18 那条）：
  * 参数被拒的两面改说**散文**，且沿用这扇已有的散文抬头 `sys_guide:` —— 与 unknown-topic 那一面
    同一种语言（本文件把它钉成对照，不是并排的两套规矩）；
  * 信封三件在散文里都必须在：`invalid_request:` 前缀 · 被拒的键点名 · 照着改的支持列表 ·
    以及**本门申报句在出路那一行的行首**（R02-087 那一族，位置契约与 JSON 门同一条）；
  * 门话不许再落表外默认支：`sys_guide` 在 `_DOOR_KIND` 在册，且句子不是"没人写过的那句"；
  * 反向锁：抓取那一族照旧交 JSON 13 格（`web_read`），unknown-topic 那一面照旧是散文且**一字不改**
    （这一把只把两种语言并成一种，不许多改别的面）。
"""
from __future__ import annotations

import json

import pytest

from dhole_mcp import context

BOGUS = 'r192_bogus_key'


async def _raw(tool: str, args: dict) -> str:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport
    from dhole_mcp.server import MasterFetchServer

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    return ''.join(getattr(b, 'text', '') for b in res.content)


class TestTheGuideDoorStopsBorrowingTheFetchShell:

    @pytest.mark.asyncio
    @pytest.mark.parametrize('args', [{BOGUS: 1}, {'topic': 7}])
    async def test_an_argument_refusal_arrives_in_prose(self, args):
        text = await _raw('sys_guide', args)
        with pytest.raises(json.JSONDecodeError):
            json.loads(text)
        for cell in ('content_ok', 'duration_ms', 'fetcher_used', '"url"'):
            assert cell not in text, f'被拒回执还带着抓取壳的 {cell}：{text[:160]}'

    @pytest.mark.asyncio
    async def test_the_unknown_argument_prose_carries_the_supported_roster(self):
        """陌生参数那一面：能照着改的支持列表必须在（R02-095 那一族的契约）。"""
        text = await _raw('sys_guide', {BOGUS: 1})
        assert 'invalid_request:' in text, text[:160]
        assert BOGUS in text, f'被拒的那颗键没被点名：{text[:200]}'
        assert 'Supported' in text, f'支持列表被剪：{text[:240]}'

    @pytest.mark.asyncio
    async def test_the_wrong_type_prose_names_the_shape_instead(self):
        # 【判据自己先写窄了一颗 · F99】这一族从前也要求过 "Supported"，而类型错的句子
        # 本来就不该列名册：它要说的是这颗该写成什么（真机 r193：
        # `topic must be a string, got int (7). Pass topic as text.`）。
        # 要求名册是我把陌生参数那一面的出路套到另一面上 —— 改的是判据，不是产品。
        text = await _raw('sys_guide', {'topic': 7})
        assert 'invalid_request:' in text, text[:160]
        assert 'topic' in text, f'没点名是哪颗参数：{text[:200]}'
        assert 'must be a string' in text, f'形状那一句不见了：{text[:220]}'

    @pytest.mark.asyncio
    async def test_the_declaration_leads_the_way_out_line(self):
        """行首契约对散文门同样成立：只读第一句的人不该听见别的门的话。"""
        text = await _raw('sys_guide', {BOGUS: 1})
        clause = context.nothing_went_out('sys_guide')
        lines = [ln for ln in text.splitlines() if ln.strip()]
        assert any(ln.startswith(clause) for ln in lines), (
            f'本门申报不在任何一行的行首：{lines[:4]}')

    def test_the_door_is_claimed_not_defaulted(self):
        """F98 那条通则的续：在册，且句子不是"没人写过的那句"。"""
        assert 'sys_guide' in context._DOOR_KIND, '仍由 `_UNDECLARED_DOOR_WAY_OUT` 代答'
        assert context.nothing_went_out('sys_guide') != context._UNDECLARED_DOOR_WAY_OUT


class TestTheRestOfTheDoorIsNotRewritten:

    @pytest.mark.asyncio
    async def test_the_unknown_topic_face_is_untouched(self):
        """这一面本来就是散文，也是这把改形的**样板**：一字不许跟着动。"""
        text = await _raw('sys_guide', {'topic': 'nosuch_topic'})
        assert text.startswith('sys_guide: topics'), text[:120]
        assert "unknown topic 'nosuch_topic'" in text
        assert text.index('aliases') > 0, '那份 topic 名单是这扇给自己开的路，不许缩'

    @pytest.mark.asyncio
    async def test_an_argument_refusal_shares_the_prose_header(self):
        """两种失败并成一种语言：抬头与 unknown-topic 那一面同一个。"""
        text = await _raw('sys_guide', {BOGUS: 1})
        assert text.startswith('sys_guide:'), f'抬头不是这扇自己的那种：{text[:100]}'

    @pytest.mark.asyncio
    async def test_the_fetch_family_still_answers_in_json(self):
        d = json.loads(await _raw('web_read', {'url': 'https://example.com/', BOGUS: 1}))
        for field in ('url', 'status', 'content_ok', 'next_action', 'error'):
            assert field in d, f'抓取那一族被顺手收窄了：{sorted(d)}'
