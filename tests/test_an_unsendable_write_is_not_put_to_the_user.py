# -*- coding: utf-8 -*-
"""一发永远发不出去的写，不许占用用户的授权（评审 R02-118 · F101）。

量在 `r205_confirm_gate_order.py`（40 发 · HEAD f3e06c9 · in-memory 与线上同一处理器 · 零外网）：

| 门 | 族 | 不给 confirm 时第一个失败是谁 | 给 confirm=true |
|---|---|---|---|
| `web_request` × POST/PUT/PATCH/DELETE × {形状 / 元数据名 / 元数据 IP / 重绑定 / 私网} | 全部五族 | **确认闸**（`method=POST is a write… Ask the user, then re-send with confirm=true`） | 这才轮到 URL 守卫 |
| `web_act`（同一批 URL · 同一把尺） | 永久 / 私网 | **URL 守卫自己**（`permanent` / `private` 那句） | 同 |

所以这不是"某个族的运气不好"，是这扇门上 20 格同一个形状：**URL 判定排在写确认之后**，而隔壁那扇
已经是先问 URL。害处具体：调用方被告知"去问用户、带 confirm=true 再发"，问完再发才得知这一发
永远发不出去 —— 花掉一次人的授权，去做一件设计上不可能成功的事。

契约（本文件钉住）：
  * 写 method × 五族 URL，**不给 confirm 时第一个失败就是那一族的句子**（`PERMANENT_URL_RECOVERY` /
    `PRIVATE_ADDRESS_RECOVERY` / 形状那句），且 `next_action` 里**不许再教 confirm=true**；
  * 正控制（门不许被修小）：一颗能发出去的 URL，不给 confirm 仍必须撞确认闸 —— 这一把改的是**顺序**，
    不是钥匙；
  * `confirm=true` + 永久边界照旧被拒（不许因为"先问了 URL"就以为确认过了）；
  * `method` 缺失与 `method=GET` 那两句不许被 URL 守卫插到前面（先说清"这扇门不这么用"，
    再说"这颗地址发不出去"）；
  * 判决一字未动：所有被拒的照旧被拒，零发包。
"""
from __future__ import annotations

import json

import pytest

from dhole_mcp import security

WRITE_METHODS = ['POST', 'PUT', 'PATCH', 'DELETE']
FAMILIES = [
    ('permanent', 'http://metadata.google.internal/x', security.PERMANENT_URL_RECOVERY),
    ('permanent-ip', 'http://169.254.169.254/x', security.PERMANENT_URL_RECOVERY),
    ('rebind', 'http://x.nip.io/y', security.PERMANENT_URL_RECOVERY),
    ('private', 'http://127.0.0.1:9/ok', security.PRIVATE_ADDRESS_RECOVERY),
    ('shape', 'http://', security.URL_SHAPE_RECOVERY),
]
# 能发出去的 URL：这台机器的解析判词是 fake-ip，隧道段被容忍，所以这颗过得了 validate_url，
# 而确认闸在它之前就返回 —— 一根连接都不会付出去（正控制靠这一点成立）。
SENDABLE = 'https://example.com/api/things'


async def _face(args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport
    from dhole_mcp.server import MasterFetchServer

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool('web_request', args)
    return json.loads(''.join(getattr(b, 'text', '') for b in res.content))


class TestTheAskComesAfterThePossibility:

    @pytest.mark.asyncio
    @pytest.mark.parametrize('method', WRITE_METHODS)
    @pytest.mark.parametrize('family,url,needle', FAMILIES)
    async def test_an_unsendable_write_is_refused_before_any_approval_ask(self, method, family,
                                                                          url, needle):
        d = await _face({'url': url, 'method': method, 'body': '{}'})
        na = d.get('next_action') or ''
        err = d.get('error') or ''
        assert needle.split('{')[0][:44] in na, (
            f'{method} 打在 {family} 上时第一个失败不是那一族：{na[:140]}')
        assert 'confirm=true' not in na, (
            f'这一发永远发不出去，却还在教人去问授权：{na[:140]}')
        assert 'is a write' not in err, f'确认闸先说话了：{err[:120]}'
        assert d.get('content_ok') is False and d.get('status') == 0, '判决不许变'


class TheGateStillWorks:
    """正控制：顺序改了，钥匙没改 —— 能发出去的写照样要先问人。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize('method', WRITE_METHODS)
    async def test_a_sendable_write_still_demands_confirm(self, method):
        d = await _face({'url': SENDABLE, 'method': method, 'body': '{}'})
        err = d.get('error') or ''
        na = d.get('next_action') or ''
        assert 'is a write' in err, f'确认闸被修没了：{err[:140]}'
        assert 'confirm=true' in na, na[:160]
        assert d.get('status') == 0 and d.get('content_ok') is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize('url,needle', [
        ('http://metadata.google.internal/x', security.PERMANENT_URL_RECOVERY),
        ('http://127.0.0.1:9/ok', security.PRIVATE_ADDRESS_RECOVERY)])
    async def test_confirm_true_does_not_open_anything(self, url, needle):
        """带了钥匙也一样拒：这一族没有门（私网那颗只在这台机器没开豁免时成立）。"""
        d = await _face({'url': url, 'method': 'POST', 'body': '{}', 'confirm': True})
        na = d.get('next_action') or ''
        assert needle.split('{')[0][:44] in na, na[:140]
        assert d.get('status') == 0, '不许因为 confirm=true 就发出去'


class TheVerbSentencesStayFirst:

    @pytest.mark.asyncio
    @pytest.mark.parametrize('args,needle', [
        ({'url': 'http://metadata.google.internal/x', 'body': '{}'}, 'method is required'),
        ({'url': 'http://metadata.google.internal/x', 'method': 'GET', 'body': '{}'},
         'is a read, not a request')])
    async def test_a_wrong_door_question_is_answered_before_the_address(self, args, needle):
        """先说"这扇门不这么用"，再说"这颗地址发不出去" —— 前者是门的选择，后者是地址的事实。"""
        d = await _face(args)
        assert needle in (d.get('error') or ''), d
