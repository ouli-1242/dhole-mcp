# -*- coding: utf-8 -*-
"""一句守卫拒绝属于哪一族，只有一个裁判（评审 R02-117 · F100）。

量在 r196（六扇 × 永久边界三种拼法 · 37 格）与 r200（六颗主机名 · `r200_family_dispatch.json`）：

| 打进去的 URL | 守卫那句 | 出路族（今天） | 该是 |
|---|---|---|---|
| `http://localhost:9/x` | `URL targets internal service: localhost` | private（提 `allow_private`） | **private ✓**（loopback 真有门） |
| `http://0.0.0.0/x` | `… internal/private IP (0.0.0.0 in 0.0.0.0/8)` | private | **private ✓**（可被点名放行） |
| `http://metadata/x` | `URL targets internal cloud metadata endpoint: metadata` | permanent | **permanent ✓** |
| `http://100.100.100.200/x` / `http://[fd00:ec2::254]/x` | 同上 | permanent | **permanent ✓** |
| `http://metadata.google.internal/x` | `URL targets internal service: metadata.google.internal` | **private** | **permanent ✗** |

作者不是那四处装配点，是 `security.py:766` 那张前缀表：`URL targets internal service:` 整颗登记在
私网族里，而这句话由 `security.py:1048` 为 `_BLOCKED_HOSTNAMES` **整张名单**发出 —— 名单里既有
`localhost`（真有门）也有 `metadata.google.internal`（永不开门，守卫自己写着 "it never opens cloud
metadata"）。所以**把那颗前缀搬去永久族是错修**：会把 `localhost` 那一半修坏。

正解是一个裁判：从消息里取出那颗主机名，按元数据/重绑定名单判族，四处装配点都读它的判决。
契约（本文件钉住）：
  * `url_refusal_family()` 对上面六条各给一族，且 `localhost` 与 `0.0.0.0` 留在 private；
  * 线上两面：元数据名交 `PERMANENT_URL_RECOVERY` 且**不许再提 `allow_private`**；
    `localhost` 交私网那句并继续指出那道门（正控制 —— 这条不许被"修好了"顺手删掉）；
  * `feed_read` 那一族的表读同一个判决（F96 那张表不再自己扫前缀）；
  * 反向锁：装配点不许留着自己扫 `*_REFUSAL_PREFIXES` 的第二位裁判（源码形状钉）。
"""
from __future__ import annotations

import inspect
import json

import pytest

from dhole_mcp import security
from dhole_mcp.server import MasterFetchServer

GCE = 'http://metadata.google.internal/x'
LOCAL = 'http://localhost:9/x'
META_BARE = 'http://metadata/x'
REBIND = 'http://x.nip.io/y'
PRIVATE_IP = 'http://127.0.0.1:9/ok'


async def _face(tool: str, args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    return json.loads(''.join(getattr(b, 'text', '') for b in res.content))


class TestTheClassifierIsTheOnlyJudge:

    @pytest.mark.parametrize('msg,family', [
        ('URL targets internal service: metadata.google.internal', 'permanent'),
        ('URL targets internal service: localhost', 'private'),
        ('URL targets internal service: 0.0.0.0', 'private'),
        ('URL targets internal cloud metadata endpoint: metadata', 'permanent'),
        ('URL uses DNS rebinding service: 1.2.3.4.nip.io', 'permanent'),
        ('URL targets internal/private IP (127.0.0.1 in 127.0.0.0/8)', 'private'),
        ('URL hostname x.com resolves to internal/private IP (10.0.0.1). whatever', 'resolver'),
        ('Timeout exceeds maximum of 120000ms (2 minutes)', 'timeout'),
        ("URL scheme 'ftp' is not allowed", 'shape'),
        ('URL must be a non-empty string', 'shape'),
    ])
    def test_each_sentence_gets_one_family(self, msg, family):
        assert security.url_refusal_family(msg) == family, msg

    def test_the_localhost_half_does_not_move(self):
        """这颗是本把的护栏：一句话跨两族，搬前缀会连 `localhost` 一起搬走。

        `localhost` 的出路**必须**继续指出 `allow_private` 那道门 —— 那一支真有门。
        """
        assert security.url_refusal_family('URL targets internal service: localhost') == 'private'
        assert 'allow_private' in security.PRIVATE_ADDRESS_RECOVERY


class TestTheWireReadsTheSameVerdict:

    @pytest.mark.asyncio
    @pytest.mark.parametrize('door,args', [
        ('web_read', {'url': GCE}),
        ('feed_read', {'urls': [GCE]}),
        ('web_discover', {'url': GCE}),
        ('web_crawl', {'url': GCE}),
    ])
    async def test_the_metadata_name_gets_the_boundary_way_out(self, door, args):
        d = await _face(door, args)
        na = d.get('next_action') or ''
        assert security.PERMANENT_URL_RECOVERY[:44] in na, (
            f'{door} 还在把元数据名派去私网那句：{na[:150]}')
        assert 'allow_private' not in na, (
            f'{door} 在教一道不存在的门（守卫自己写着永不开元数据）：{na[:150]}')

    @pytest.mark.asyncio
    @pytest.mark.parametrize('door,args', [
        ('web_read', {'url': LOCAL}),
        ('feed_read', {'urls': [LOCAL]}),
    ])
    async def test_localhost_keeps_the_door_it_really_has(self, door, args):
        """正控制：`localhost` 那一半一个字不许变 —— 它的出路真的有门。"""
        d = await _face(door, args)
        na = d.get('next_action') or ''
        assert security.PRIVATE_ADDRESS_RECOVERY[:44] in na, na[:150]
        assert 'allow_private' in na, na[:150]

    @pytest.mark.asyncio
    async def test_the_permanent_names_stay_permanent(self):
        for args in ({'url': META_BARE}, {'url': REBIND}):
            d = await _face('web_read', args)
            assert security.PERMANENT_URL_RECOVERY[:44] in (d.get('next_action') or ''), args


class TestNoSecondJudgeSurvives:

    def test_the_dispatch_sites_read_the_verdict_not_the_prefix_lists(self):
        """源码形状：装配点自己扫前缀名单的那两位旧裁判要退休（句子本体仍住守卫侧）。"""
        src = inspect.getsource(MasterFetchServer._dispatch)
        assert 'PRIVATE_ADDRESS_REFUSAL_PREFIXES' not in src, (
            '门侧又在扫私网前缀表 —— 那正是 `localhost` 与元数据名被撞成一族的形状')
        assert 'url_refusal_family' in src, '门侧不再读那颗判决'

    def test_the_crawl_engine_reads_the_verdict_too(self):
        from dhole_mcp import crawl
        src = inspect.getsource(crawl)
        seg = src[src.index('def _crawl_url_guard_face'):] if 'def _crawl_url_guard_face' in src else src
        assert 'PRIVATE_ADDRESS_REFUSAL_PREFIXES' not in seg or 'url_refusal_family' in seg
