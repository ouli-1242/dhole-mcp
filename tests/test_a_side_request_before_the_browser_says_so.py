# -*- coding: utf-8 -*-
"""R02-F63 · 强制浏览器档之前那一发探活是**多发请求**，它必须在事前说一声。

现场（本机回环，真实调用，`review/round-02/sessions/P02-R5-F63-pre.jsonl`）：
`web_read(url='http://127.0.0.1:8400/…', force_fetcher='stealthy')` 在同一个 URL 上
留下 **两** 发请求 —— 一发 dhole 自己按 primp 预设直连的（`server.py:7947
_private_health_probe`，直连、3 秒、不重试、不合成头、不跟重定向），一发真浏览器
导航（Chromium/156，带 dhole 的 google `referer`）。回执里 `escalation_path` 只有
`direct:stealthy`，`summary` 里一个字都没有：调用方自己的服务日志上多了一条他没点名
的请求，而墙上说只有一档跑过。

这一刀只加一句申报，**行为一寸不改**：
1. 探活照发（它是设计：私网死端口不该再被浏览器拨一遍 —— 
   `test_a_dead_local_port_is_not_dialed_again_by_a_browser.py` 钉着那条判决），
2. 它带的那些参数一个不动（直连 / 3 秒 / 不重试 / 不合成头 / 不跟跳），
3. 唯一变化是这一发在 `summary` 里留名 —— 把那一行摘掉之后必须与今天逐字节相同。

不在这一刀里的两件事，各自记账：探发**能不能**带上一发写操作 —— 实测不能
（`web_request` 没有 `force_fetcher=` 这颗参数，且私网写被 `server.py:6275` 那道
"method=POST is a write" 的门先挡住），所以这不是"同一写做两遍"而是"多发一次读"；
浏览器档那 17 颗头（含编造的 google `referer`）在私网目标上照发 —— 那是另一把尺，
R02-077。
"""
import asyncio


from dhole_mcp import context as CT
from dhole_mcp.models import ResponseModel
from dhole_mcp.server import MasterFetchServer

_SENTENCE = "extra direct request"


def _probe(url="http://127.0.0.1:8400/page", status=200, **kw):
    """跑一次作者点：只替 `get`（真发请求的那只手），别的都留原样。

    `MasterFetchServer.get` 是 **staticmethod**，所以这里两处都要按 staticmethod 办：
    替身自己不接 `self`，还原时还回 `__dict__` 里那颗描述符本身（把纯函数赋回类属性
    会把它变成普通方法，之后每一次 `self.get(url)` 都会把实例当成 url —— 这一格付过
    这个代价：它让全量红了 46 格，见 review/backlog.md 的 B-30）。
    """
    calls = []

    async def fake_get(u, **kwargs):
        calls.append(kwargs)
        return ResponseModel(url=u, status=status, content=["body"],
                             fetcher_used="http", **kw)

    orig = MasterFetchServer.__dict__["get"]
    MasterFetchServer.get = staticmethod(fake_get)
    try:
        with CT._arg_notes_scope() as notes:
            server = MasterFetchServer()
            got = asyncio.run(server._private_health_probe(url, {}, None, None, 0.0))
    finally:
        MasterFetchServer.get = orig
    return got, notes, calls


# ── 格 0：挂具不许把钩子留在进程里 ──────────────────────────────────────

class TestTheFixtureHandsTheHookBack:

    def test_get_is_still_a_staticmethod_after_the_probe(self):
        _probe()
        assert isinstance(MasterFetchServer.__dict__["get"], staticmethod), (
            "还原成纯函数就等于把 `get` 变成方法：后面的每一发都会把实例当 url")



# ── 格 1：活着的那一发要留名 ────────────────────────────────────────────

class TestTheSideRequestNamesItself:

    def test_an_alive_probe_says_that_it_dialed_too(self):
        got, notes, calls = _probe()
        assert got is None, "200 = 活着，浏览器照跑（这条判决不许动）"
        joined = " ".join(notes).lower()
        assert _SENTENCE in joined, (
            f"目标自己的日志上多了一条请求，回执里却没人说：{notes}")

    def test_the_sentence_says_who_and_how_many(self):
        joined = " ".join(_probe()[1]).lower()
        for needle in ("probe", "browser", "1"):
            assert needle in joined, joined


# ── 格 2：判死的那一发也只说一次 ─────────────────────────────────────────

class TestTheVerdictSideStillSaysItOnce:

    def test_a_dead_probe_is_announced_once_not_twice(self):
        got, notes, calls = _probe(status=0)
        assert got is not None, "status=0 = 死了，探测结果就是答案（这条也不许动）"
        hits = [n for n in notes if _SENTENCE in n.lower()]
        assert len(hits) == 1, hits


# ── 格 3：申报不许改动行为 ───────────────────────────────────────────────

class TestTheAnnouncementChangesNoBehaviour:

    def test_the_probe_still_dials_direct_once_and_plain(self):
        got, notes, calls = _probe()
        assert len(calls) == 1, calls
        one = calls[0]
        assert one["stealthy_headers"] is False, one
        assert one["retries"] == 0, one
        assert one["follow_redirects"] is False, one
        assert one["proxy"] is None, one

    def test_the_envelope_other_than_the_note_is_untouched(self):
        got, notes, calls = _probe(status=503)
        assert got.status == 503 and not got.error, got
        assert "extra direct" not in (got.error or "").lower()
        assert len([n for n in notes if _SENTENCE in n.lower()]) == 1
