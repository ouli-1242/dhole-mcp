"""R01-010（并入 R01-014）· 掐掉别人的那一层不许替别人出主意。

现场（`sessions/P02-s2b-pdf.json` → `sessions/P02-s2c-pdf-retry.json`，一份在线大 PDF）：
第一发默认 30s，被调用预算的**背板**（`_within_call_budget`）掐掉，回的是通用建议 ——

    "Raise timeout - this call already had 30000ms and dhole accepts up to 120000ms
     (90000ms more)"

用户照做了。第二发跑了 33051ms，同一份响应里换成另一句话：

    "one HTTP attempt already has the whole 30s it can ever get, so a bigger timeout=
     buys parsing room, not a faster download"

也就是说第一封建议拧的那颗旋钮，第二封自己承认拧不动：一份文档只有**一次**尝试，一次
HTTP 尝试封顶 30s（`_http_tier_budget` 的 document 分支 + `bulk_get` 的算式），抬
`timeout=` 改变不了决定字节到不到得了的那个数。33s 是照建议做的代价，不是网络的钱。

在受控靶站上量成可重复的（`review/.runtime/p4/probe_r01_010.py`：一台按 4096B/s 吐
163840B 的应用/PDF 服务，一次连接必须跑过 40s）：

```
call1 默认 30s     → duration 32425 / next_action "Raise timeout … 120000ms"，无尺寸、无 30s
call2 timeout=60000 → duration 33013 / next_action "one HTTP attempt already has the whole 30s"
```

根因读码（不是"文案写得不好"）：背板自己写着它**晚到是设计**，而它建的那封响应
`diagnosis` 传的是空串 —— 被它掐掉的那一层早就量到的东西（这是一份文档、它有
160.0KB、一次尝试钳在 30s）一个字都没带过来。R01-014（"尺寸警报只在失败之后才出现"）
是同一根因的第二半：那一层的尺寸预检定在下载**之前**，可它的结果跟着那次取消一起丢了。

这一刀补的是那条缝，不是那句文案：一层把它学到的事实登进这次调用的账本（与
`_BudgetCredit` 同一机制、同一作用域），背板在自己那封响应里把账本交给**同一个作者**
（`_document_budget_advice` 与新抽出的 `_document_timeout_lever`）写建议；散文与
机器可读孪生引用同一句杠杆，不许两只手互相否决（R01-044 立的根因）。

三条边界是刻意的：

1. **门禁一个字不动**：30s 一次尝试的钳、文档只一次尝试、`timeout_applied` 的语义、
   `MAX_RESPONSE_BYTES` 的拒收都不改。本刀只让背板说出别人量过的事。
2. **没有账本就一切照旧**：非文档调用（背板从没被登记过）仍走通用建议 —— 那句对浏览器
   档与 HTML 是真有效的杠杆，删掉它才是另一种说谎。
3. **不在这里改竞速**：背板晚到是设计（见它自己的 docstring），修它谁赢等于把"每一档
   自己收尾"的保证拆掉。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import dhole_mcp.server as server_mod
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _is_over_budget

URL = "https://docs.example.com/manual.pdf"
SIZE_BYTES = 163_840
SIZE_HUMAN = "160.0KB"          # `_human_bytes` 印出来的样子
CAP_S = server_mod._HTTP_ATTEMPT_CAP_MS // 1000

_TIER_BASE = dict(
    extraction_type="markdown", css_selector=None, cache_ttl=0, offset=0,
    headless=True, real_chrome=False, proxy=None, solve_cloudflare=False,
    block_webrtc=False, hide_canvas=False, extra_headers=None,
    useragent=None, cookies=None,
)


# ─── 1 · 那一条缝：层学到的事实必须能到达背板的那句话 ─────────────────

def _srv_with_a_hanging_download(monkeypatch, *, size=SIZE_BYTES, size_note=""):
    """真的升级阶梯 + 假网络：尺寸预检定在下载之前，下载那一发永远不回来。

    `_document_size` 摘的是模块名（`_auto_escalate` 在函数体里按名字取它），摘错地方
    这条用例就会去拨真网络；`get` 挂死则是靶站上那 40s 下载的等价物。
    """

    async def _size(url, proxy, headers, cookies, useragent, budget_s=3.0):
        return size, size_note

    async def _hang(*a, **k):
        await asyncio.sleep(30)

    async def _no_session(**kw):
        return "auto-under-test"

    async def _installed(real_chrome=False):
        return SimpleNamespace(ok=True, detail="", waited_s=0.0)

    monkeypatch.setattr(server_mod, "_document_size", _size)
    monkeypatch.setattr(server_mod, "ensure_chromium_async", _installed)
    monkeypatch.setattr(server_mod, "_browser_deps_available", lambda: True)
    srv = server_mod.MasterFetchServer(cache_ttl=0)
    srv.get = _hang
    srv.stealthy_fetch = _hang
    srv._ensure_auto_session = _no_session
    return srv


async def _cut_midway(srv, *, backstop_ms=1500, tier_ms=5000):
    """让背板赢下这场竞速 —— 真实缺陷里它本来就赢（这是设计，见其 docstring）。

    阶梯自己那份预算给得比背板宽，谁赢就不再取决于毫秒级的调度顺序；被测的是
    **背板那封响应有没有拿到阶梯登进去的事实**。
    """
    return await srv._within_call_budget(
        srv._auto_escalate(URL, timeout=tier_ms, **_TIER_BASE),
        URL, backstop_ms, "fetch tiers")


@pytest.mark.asyncio
async def test_the_size_measured_before_the_download_survives_the_cut(monkeypatch):
    """R01-014 那一半：尺寸在失败**之前**就该在回执里，因为它早就量好了。"""
    srv = _srv_with_a_hanging_download(monkeypatch)
    out = await _cut_midway(srv)

    assert SIZE_HUMAN in out.next_action, out.next_action
    assert "document download" in out.next_action, out.next_action


@pytest.mark.asyncio
async def test_the_cut_stays_the_terminal_answer_it_has_always_been(monkeypatch):
    """反向守卫：换建议不许动那条机器可读的边 —— 每一档都靠 "timeout: the " 收尾。"""
    srv = _srv_with_a_hanging_download(monkeypatch)
    out = await _cut_midway(srv)

    assert out.error.startswith("timeout: the "), out.error
    assert _is_over_budget(out)
    assert out.status == 0 and out.content == [], out


@pytest.mark.asyncio
async def test_the_twin_and_the_prose_recommend_the_same_move(monkeypatch):
    """两只手不许各说一套：信封里那颗也得带着"一次尝试钳 30s"这颗事实。

    修前这里读到的是通用前缀表给的 `recover_with="raise timeout, or switch source"`，
    而同一封的 next_action 说的是另一件事 —— R01-044 归的那条根因在建议位的形状。
    """
    srv = _srv_with_a_hanging_download(monkeypatch)
    out = await _cut_midway(srv)

    lever = out.diagnosis["recover_with"]
    assert f"caps at {CAP_S}s" in lever, lever
    assert lever in out.next_action, (lever, out.next_action)
    assert out.diagnosis["code"] == "timeout"
    assert out.diagnosis["retryable"] is False, out.diagnosis


@pytest.mark.asyncio
async def test_a_download_whose_size_the_host_never_told_still_says_so(monkeypatch):
    """量不到尺寸是一个事实，不是空白：那句 note 必须原样上回执。"""
    srv = _srv_with_a_hanging_download(monkeypatch, size=None,
                                       size_note="host reported no size (chunked or range refused)")
    out = await _cut_midway(srv)

    assert "document download" in out.next_action, out.next_action
    assert "host reported no size" in out.next_action, out.next_action


@pytest.mark.asyncio
async def test_a_call_that_never_touched_a_document_keeps_the_generic_lever(monkeypatch):
    """反向守卫：HTML 那一发抬 `timeout=` 是真有用的，这刀不删一句有用的话。"""
    async def _html(*a, **k):
        await asyncio.sleep(30)

    srv = _srv_with_a_hanging_download(monkeypatch)
    srv.get = _html
    html_url = "https://news.example.com/story"
    out = await srv._within_call_budget(
        srv._auto_escalate(html_url, timeout=5000, **_TIER_BASE),
        html_url, 1500, "fetch tiers")

    assert "document" not in out.next_action, out.next_action
    assert "Raise timeout" in out.next_action, out.next_action


@pytest.mark.asyncio
async def test_each_concurrent_call_carries_only_its_own_facts(monkeypatch):
    """账本按调用作用域走：一份 PDF 的结论不许串到同一时刻的另一发 HTML 上。"""
    srv = _srv_with_a_hanging_download(monkeypatch)
    html_url = "https://news.example.com/story"

    async def _hang_html(*a, **k):
        await asyncio.sleep(30)

    doc = srv._within_call_budget(
        srv._auto_escalate(URL, timeout=5000, **_TIER_BASE), URL, 1500, "fetch tiers")
    # 同一 TaskGroup 之外并发两发：两把 `_within_call_budget` 各有各的账本
    srv2 = _srv_with_a_hanging_download(monkeypatch)
    srv2.get = _hang_html
    page = srv2._within_call_budget(
        srv2._auto_escalate(html_url, timeout=5000, **_TIER_BASE),
        html_url, 1500, "fetch tiers")
    doc_out, page_out = await asyncio.gather(doc, page)

    assert SIZE_HUMAN in doc_out.next_action, doc_out.next_action
    assert "document" not in page_out.next_action, page_out.next_action


# ─── 2 · 同一根因的第二条出口：那一层自己答的那一发 ──────────────────

# 现场的第二发（`sessions/P02-s2c-pdf-retry.json`）：这一次没有被背板掐，是 HTTP 档
# 自己拿着一次 status=0 的网络错误回来，`next_action` 换成了专项那句（"a bigger
# timeout= buys parsing room"）—— 而信封里那只手原封不动还是通用前缀表的
# "Retry once with a longer timeout"。真 CLI 复验时又量到一次（见 fix 文档）：两封的
# prose 都对了，`diagnosis.recover_with` 还在劝人抬那颗拧不动的旋钮。
_TRANSPORT_TIMEOUT = ("error decoding response body > request or response body error"
                      " > operation timed out")


async def _tier_answers_a_dead_download(monkeypatch, *, timeout_ms=30_000):
    """阶梯自己收尾的那一发：尺寸预检定到了，下载那一发以 status=0 的网络错误回来。"""

    async def _size(url, proxy, headers, cookies, useragent, budget_s=3.0):
        return SIZE_BYTES, ""

    async def _dead(*a, **k):
        return ResponseModel(url=URL, status=0, content=[], error=_TRANSPORT_TIMEOUT,
                             content_type="application/pdf", fetcher_used="http")

    monkeypatch.setattr(server_mod, "_document_size", _size)
    srv = server_mod.MasterFetchServer(cache_ttl=0)
    srv.get = _dead
    return await srv._auto_escalate(URL, timeout=timeout_ms, **_TIER_BASE)


@pytest.mark.asyncio
async def test_the_tier_that_answered_its_own_timeout_agrees_with_its_twin(monkeypatch):
    srv = await _tier_answers_a_dead_download(monkeypatch)

    assert SIZE_HUMAN in srv.next_action, srv.next_action
    assert "raise timeout" not in srv.next_action.lower(), srv.next_action
    # 这一条是复验量出来的：prose 早就修对了，孪生还在替通用表说话
    assert "longer timeout" not in srv.diagnosis["recover_with"].lower(), srv.diagnosis
    assert f"{CAP_S}s" in srv.diagnosis["recover_with"], srv.diagnosis
    assert srv.diagnosis["retryable"] is False, srv.diagnosis
    assert srv.diagnosis["recover_with"] in srv.next_action, (srv.diagnosis, srv.next_action)


@pytest.mark.asyncio
async def test_a_document_under_the_cap_still_hears_the_lever_that_works(monkeypatch):
    """反向守卫：12s 预算那一发，抬到 30s 是真有效的 —— 两只手都要说出来，不是都闭嘴。"""
    srv = await _tier_answers_a_dead_download(monkeypatch, timeout_ms=12_000)

    assert "raise timeout" in srv.next_action.lower(), srv.next_action
    assert f"caps at {CAP_S}s" in srv.next_action, srv.next_action
    assert srv.diagnosis["recover_with"] in srv.next_action, (srv.diagnosis, srv.next_action)


# ─── 3 · 那句杠杆：一个作者，两个分支都要说得出 ──────────────────────

def test_a_document_already_at_the_attempt_cap_is_not_told_to_raise_timeout():
    """现场那一发的形状（预算=30000=钳顶）：这封响应里不许再出现"抬抬"。

    背板那发在单元里等 31.5s 才回来，所以这一条按**同一个作者**量：`_cancelled_tier_advice`
    就是背板在 TimeoutError 分支里调的那一个函数，靶站复验再量整条线。
    """
    facts = server_mod._TierFacts()
    facts.mark_document(SIZE_BYTES, "")
    prose, twin = server_mod._cancelled_tier_advice(URL, facts, 30_000, "fetch tiers")

    assert "raise timeout" not in prose.lower(), prose
    assert f"{CAP_S}s" in prose, prose
    assert "switch source" in prose and "parse" in prose, prose
    # 孪生引用的就是散文里那一句杠杆 —— 一个作者，两只手
    assert twin["recover_with"] in prose, (twin, prose)
    assert twin["retryable"] is False


def test_a_document_under_the_cap_is_still_told_the_lever_works():
    """另一半：12s 预算里抬上去是真有效的 —— 不许把这刀做成"永远劝人换源"。"""
    facts = server_mod._TierFacts()
    facts.mark_document(SIZE_BYTES, "")
    prose, twin = server_mod._cancelled_tier_advice(URL, facts, 12_000, "fetch tiers")

    assert "raise timeout" in prose.lower(), prose
    assert f"caps at {CAP_S}s" in prose, prose
    assert twin["recover_with"] in prose, (twin, prose)


def test_a_non_document_call_asks_for_no_advice_at_all():
    """背板的兜底不许变成第二个建议作者：没登记过事实就交白卷，通用那句照旧。"""
    prose, twin = server_mod._cancelled_tier_advice(
        "https://news.example.com/story", server_mod._TierFacts(), 30_000, "fetch tiers")
    assert prose == "" and twin is None


# ─── 4 · 建议的形状：句首不许是小写 ─────────────────────────────────

def test_an_advice_that_opens_a_field_does_not_open_with_a_lower_case_word():
    """建议是由"分句"拼出来的，而分句从前是为 "; " 之后写的 —— 搬到句首就成了小写句首。

    真 CLI 复验量到的两封（`r010-verify.json`）：

    ```
    Budget exhausted after 32033ms at the fetch tiers. the fetch tiers on a document
    download; size probe failed (TimeoutError); …
    the HTTP tier on a document download; this host has taken 33s …   ← next_action 的第一个词
    ```

    后者在这一刀之前就在 wire 上（P02-s2c 那一发的 `next_action` 正是以小写的 "the" 开
    头），前者是背板这条新接上的路把它带到 head 之后。句号之后一个小写词，读者会当成
    上一句没写完 —— 与本刀同一族的文案账，一起收。
    """
    from dhole_mcp.quality import _over_budget_result

    facts = server_mod._TierFacts()
    facts.mark_document(SIZE_BYTES, "")
    for budget in (30_000, 12_000):
        prose, _twin = server_mod._cancelled_tier_advice(URL, facts, budget, "fetch tiers")
        assert prose[:1].isupper(), prose
        out = _over_budget_result(URL, budget, budget + 2033, "fetch tiers", "", prose)
        # head 之后那一句的句首 —— 不按 ". " 切句子，建议里有 "e.g." 那样的缩写，
        # 切出来的"句子"不是句子（这条守卫量的是字段边界，不是全文分句）。
        head_end = out.next_action.index(". ") + 2
        assert out.next_action[head_end:head_end + 1].isupper(), out.next_action


def test_the_generic_advice_opens_its_field_with_a_capital_too():
    """通用那一句的"已经到顶"分支同样是分句搬句首 —— 同一个规矩，不留第二个例外。"""
    from dhole_mcp.quality import _budget_advice
    from dhole_mcp.security import TIMEOUT_MAX_MS

    advice = _budget_advice("fetch tiers", "", TIMEOUT_MAX_MS)
    assert "nothing left to raise" in advice, advice
    assert advice[:1].isupper(), advice
