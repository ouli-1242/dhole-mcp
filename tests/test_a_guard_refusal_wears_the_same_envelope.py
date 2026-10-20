# -*- coding: utf-8 -*-
"""SSRF 解析器守卫的那句拒绝，六张脸里有五张不穿信封（评审 R02-113 · P3）。

量到的（`review/.runtime/p1/r172_guard_family_in_process.json` · 档案
`sessions/P02-R54-guard-family-in-process.jsonl` · HEAD a01a84b · 六扇同一前提）：

| 门 | `invalid_request:` 前缀 | 门话在 `next_action` 行首 | 出路（守卫那句 recovery） |
|---|---|---|---|
| `web_read` / `web_discover` / `web_crawl` / `web_request` / `web_act` | **无** | **无** | 有 |
| `feed_read` | 有 | 有 | **无**（换成通用的 `urls=[…]` 提示） |

句子本体来自一位作者：`server._resolver_guard_result`（4380，由 7098 那处 needle 分支调用，
而 discover/crawl/request/act 都经 `web_read` 的实现跑），它的 `next_action` 是
「No argument on this call changes it - this process **fetches** on a resolver verdict it took
earlier. …; **nothing was fetched**.」—— 于是写门与行动门（`verbs_found=['fetched']` 那两格）
被同一份共用文案推去说读门的话。这与 R02-100/110/111 是同一个形状：**装配点共用、动词写死**。

**前提怎么造的（诚实归因）**：这台机器的解析器判 fake-ip，守卫那句在线上门口不可达（见 backlog B-58
的注），所以这一族是把进程记着的判词摆到 public 一侧量的 —— 第一具量具（r165）把 `probe_fake_ip`
覆写成读全局，而 `security._reprobe_before_tunnel_refusal` 会先把那颗全局清成 None 再调用探测，
于是造出一条 TypeError **假崩溃**（归 B 层，没为它开产品行）。r172 改成值覆写，六张脸才可比。尺子里再把解析答案钉成一颗固定隧道地址（同一个理由：判据的前提不许挂在会变的机器状态上）。
这一族证明的是"守卫的话到了各扇手上长什么样"，不证明"这台机器会自己走到这一步"。

契约（本文件钉住）：
  * 守卫那一族的 `error` 一律带 `invalid_request:` 前缀；
  * `next_action` 行首是本门的申报句（由 `context.nothing_went_out(door)` 现算），
    共用文案里不得再出现别门的动词或 "nothing was fetched" 的尾签；
  * 出路仍要说清那条唯一的真路（重启宿主 / `dhole --doctor`）—— 收紧信封不许把出路收紧掉；
  * **SSRF 判决本身一字不动**：同一颗地址仍然必须被拒（`status=0` + 守卫句在），
    这一把只改它怎么说。
"""
from __future__ import annotations

import json

import pytest

import dhole_mcp.security as sec
from dhole_mcp.context import nothing_went_out

TARGET = 'http://example.com/r113'
VERBS = ('fetched', 'sent', 'run', 'searched', 'stored')


class _PublicVerdict:
    """把守卫的**两个输入**钉死：进程判词 public + 解析答案是一颗隧道段地址。

    第一版只钉判词，留 `_resolves_to_internal` 去问这台机器的 DNS —— 那一跑红得没道理：
    同一颗 `example.com` 在 r172（06:07Z）答 `fd00::9d`，两分钟后这格答的是可路由地址，
    于是门放行、真发了一发 404。判据的**前提**不能挂在会变的机器状态上（这把尺量的是信封，
    不是这台机器此刻的解析器）。所以这里把解析答案也钉成一颗固定隧道地址：门仍然自己走
    `validate_url` 的那条支路（判决与拒绝都是真的产品代码），只有它的两个输入是受控的。
    """

    ANSWER = '198.18.0.99'

    def __enter__(self):
        self._cache = sec._fake_ip_cache
        self._probe = sec.probe_fake_ip
        self._resolve = sec._resolves_to_internal
        # `_DNS_CHECK_CACHE` 也要一起收：`validate_url` 会把解析判决按主机缓存几分钟，
        # 覆写期间留下的那颗"内网"会漏给后面真的问 DNS 的测试（合跑时把
        # `test_audit_fixes.py` 两格带红，单跑却绿 —— 顺序依赖就是这么来的）。
        self._dns_cache = dict(sec._DNS_CHECK_CACHE)
        sec._fake_ip_cache = ('public', '', 'forced by ruler')
        sec.probe_fake_ip = lambda *a, **k: ('public', '', 'forced by ruler')
        sec._resolves_to_internal = lambda hostname: self.ANSWER
        return self

    def __exit__(self, *exc):
        sec._fake_ip_cache = self._cache
        sec.probe_fake_ip = self._probe
        sec._resolves_to_internal = self._resolve
        sec._DNS_CHECK_CACHE.clear()
        sec._DNS_CHECK_CACHE.update(self._dns_cache)
        return False


async def _face(tool: str, args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    from dhole_mcp.server import MasterFetchServer
    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    return json.loads(''.join(getattr(b, 'text', '') for b in res.content))


DOORS = [('web_read', {'url': TARGET}),
         ('web_discover', {'url': TARGET}),
         ('web_crawl', {'url': TARGET}),
         ('web_request', {'url': TARGET, 'method': 'POST', 'body': 'x', 'confirm': True}),
         ('web_act', {'url': TARGET, 'actions': [{'read': {}}]})]


class TestTheGuardSpeaksInTheEnvelope:

    @pytest.mark.asyncio
    @pytest.mark.parametrize('tool,args', DOORS, ids=[d[0] for d in DOORS])
    async def test_the_guard_refusal_wears_the_envelope(self, tool, args):
        with _PublicVerdict():
            d = await _face(tool, args)
        err = d.get('error') or ''
        na = d.get('next_action') or ''
        clause = nothing_went_out(tool)
        assert sec.RESOLVER_GUARD_NEEDLE in err, f'守卫那一族没在这扇说话：{err[:160]}'
        assert err.startswith('invalid_request:'), f'缺前缀，按前缀分类的分支读不到：{err[:160]}'
        assert na.startswith(clause), f'{tool} 的门话不在行首：{na[:170]}'
        wrong = [v for v in VERBS if ('nothing was ' + v) in na.lower()
                 and ('nothing was ' + v) != clause.lower().rstrip('.')]
        assert not wrong, f'{tool} 的申报说了别门的动词 {wrong}：{na[:170]}'
        assert 'fake-ip' in na or 'dhole --doctor' in na, f'出路被收紧掉了：{na[:170]}'

    @pytest.mark.asyncio
    async def test_the_gate_still_refuses_after_the_wording_change(self):
        """收紧信封不许松动闸门：同一颗地址仍然必须被拒、内容一格不许有。"""
        with _PublicVerdict():
            d = await _face('web_read', {'url': TARGET})
        assert d.get('status') == 0, json.dumps(d, ensure_ascii=False)[:200]
        assert not (d.get('content') or []), '守卫放行了 —— 这一族只许改说法'
        diag = d.get('diagnosis') or {}
        assert diag.get('code') == 'resolver_guard', diag
        assert diag.get('retryable') is False, diag


class TestTheFeedDoorKeepsTheWayOut:
    """`feed_read` 的出路表（server.py:11610-11620）只登记了两族，其余掉进通用形状提示。

    半径量在 `r178_feed_way_out_table.json`（档案 `sessions/P02-R55-feed-way-out-table.jsonl` · 五族）：
    私网与超时两族**有**专属出路（表内），守卫族与云元数据族被
    `Pass urls=[…] with 1-50 absolute http(s) RSS/Atom feed URLs.` 顶掉（表外）；
    URL 形状族落在那个提示里是**对的**（那一发的出路本来就是 urls= 的形状）。
    前缀与本门申报五族都在 —— 缺的只是第三件。这一族就是 R02-096（出路在教一件本来就对的事）
    在同一张表上的复发。
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize('urls,needle,way_out', [
        (['http://example.com/f'], 'resolver guard', 'fake-ip'),
        (['http://169.254.169.254/latest/meta-data/'], 'permanent boundary', 'design boundary'),
    ], ids=['守卫族', '云元数据族'])
    async def test_the_way_out_survives_the_feed_envelope(self, urls, needle, way_out):
        body = _PublicVerdict() if needle == 'resolver guard' else None
        if body:
            body.__enter__()
        try:
            d = await _face('feed_read', {'urls': urls})
        finally:
            if body:
                body.__exit__()
        na = d.get('next_action') or ''
        err = d.get('error') or ''
        assert err.startswith('invalid_request:'), err[:150]
        assert na.startswith(nothing_went_out('feed_read')), na[:150]
        assert 'Pass urls=[...]' not in na, (
            f'{needle} 的出路被通用形状提示顶掉了：{na[:180]}')
        assert way_out in na, f'出路没说出那一族真正的路：{na[:180]}'

    @pytest.mark.asyncio
    async def test_a_badly_shaped_url_list_keeps_the_shape_hint(self):
        """反向锁：形状族落在那个提示里是对的，修表不许把它也"特殊化"。"""
        d = await _face('feed_read', {'urls': ['not a url at all']})
        na = d.get('next_action') or ''
        assert 'Pass urls=[...]' in na, na[:180]
