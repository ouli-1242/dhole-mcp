"""批次 4：钳过的边界必须每一颗都说出口 —— 在钳它的那一层说。

2026-10-05 实测里，`web_crawl(max_pages=41)` 换来 40 页、`max_depth=9` 换来 5、
`concurrency=9` 换来 3，信封里一个字都没有；`web_act` 的 `{"wait": 900000}` 变成
30,000ms、PDF 的 `pages='5-999'` 在 12 页的文件上变成 5-12、`nested_max=100` 在一颗
只有 5 颗链接的页面上变成 30 —— 全都不说。同一族里 `web_read` 与 `web_search` 却是
说的（`max_content_chars clamped 1->500 (supported range 500-200000)`），于是"钳了
但没说"变成了一处只在某些工具上成立的缺陷：调用方以为自己点的那个数生效了。

两条房内规矩决定了修法的形状：

* `params.py:45-49` —— 边界要上报，就下沉到**钳制真正发生的那一层**，不许在装饰器
  里重抄一遍上限。所以 crawl/pdf/actions/nested 各自就地写，而不是一张上游表。
* 一句话只能有一个作者。`context._clamp_note` 是那句 `X clamped a->b (supported
  range L-H)` 的唯一持有者：web_read 走 ContextVar，crawl 走自己的 summary
  （它是 CrawlResponseModel，不经过 `_with_agent_hints`），两条通道读同一颗纯函数。
* 而且只能有一个落点：那颗字段是 `summary`。三颗工具都这么说，所以只扫一个字段的
  调用方不会漏掉任何一颗 —— 搜索侧曾经写在 `fetch_hint`（2026-10-09 审计 D5）。

判据：

1. 爬取的三颗（max_pages / max_depth / concurrency）越界时逐颗进 summary，措辞与
   web_read 同形；都在界内时一个字都不出现。
2. PDF 的 `pages=` 被**文档长度**钳时说出来（"of 12 page(s)"），整段越界仍然是
   `no_pages_in_range` 那句错误而不是悄悄少给。
3. 动作的 `wait` ms / `scroll` 步数 / `scroll.ms_per_step` 越界逐颗说。
4. `nested_max` 被封顶时说，不管这一页的链接够不够多 —— 省没省是站点的事，
   钳没钳是我们这边的事实。
5. HTTP 一档的每次尝试封顶 30s 与 wire 上"最多 120000ms"不是一回事，所以那一句
   要说出口；而且两个读同一颗值的地方不许各写一遍算式。
6. 上报的通道本身：一次调用一个桶（`_dispatch` 开），桶不在的时候 `_note_text`
   是静默的 no-op —— 它绝不许把上一次调用的话说进下一次响应里。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re

import pytest

from dhole_mcp import actions as actions_mod
from dhole_mcp import crawl as crawl_mod
from dhole_mcp import nested as nested_mod
from dhole_mcp import params as params_mod
from dhole_mcp import pdf_extractor as pdf_mod
from dhole_mcp import server as server_mod
from dhole_mcp.context import (
    _ARG_NOTES, _arg_notes_scope, _clamp_note, _note_text,
)
from dhole_mcp.models import ResponseModel
from dhole_mcp.server import MasterFetchServer

SEED = "https://clamps.example.com/"
# 那句格式的唯一作者产出的形状；本文件用它比较两条通道的措辞。被钳的值可以是负数
# （`max_pages=-5` 是一颗真在实测里出现过的写法），所以数字允许带符号。
_NOTE_RE = re.compile(r"^[a-z_.']+ clamped -?\d+->-?\d+ \(supported range \d+-\d+\)$")


def _site(pages: dict[str, str] | None = None) -> object:
    class _Fake:
        def __init__(self):
            self.fetched: list[str] = []

        async def web_read(self, url, **kwargs):
            self.fetched.append(url)
            return ResponseModel(url=url, status=200,
                                 content=[pages.get(url, "<p>leaf</p>")],
                                 content_type="text/html", fetcher_used="http",
                                 content_ok=True)
    return _Fake()


def _linked(links: list[str]) -> str:
    return ("<html><body>"
            + "".join(f'<a href="{h}">t</a>' for h in links) + "</body></html>")


# ─── 1 · 爬取的三颗 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_max_pages_above_the_ceiling_is_announced_and_so_is_zero():
    for asked, applied in ((41, 40), (0, 1), (-5, 1)):
        out = await crawl_mod.web_crawl(
            _site({SEED: _linked(["/a", "/b"])}), SEED,
            max_pages=asked, max_depth=1, cache_ttl=0)
        expect = _clamp_note("max_pages", asked, applied, lo=1, hi=crawl_mod.MAX_PAGES_CEILING)
        assert expect in out.summary, (asked, out.summary)
        assert _NOTE_RE.match(expect), expect


@pytest.mark.asyncio
async def test_max_depth_and_concurrency_are_two_separate_sentences():
    """两颗各自一句 —— 挤成一句就分不出是哪颗被钳了，也就没法改。"""
    out = await crawl_mod.web_crawl(
        _site({SEED: _linked(["/a", "/b"])}), SEED,
        max_pages=2, max_depth=9, concurrency=9, cache_ttl=0)
    assert "max_depth clamped 9->5 (supported range 0-5)" in out.summary, out.summary
    assert "concurrency clamped 9->5 (supported range 1-5)" in out.summary, out.summary


@pytest.mark.asyncio
async def test_a_crawl_inside_every_bound_says_nothing_about_clamps():
    out = await crawl_mod.web_crawl(
        _site({SEED: _linked(["/a", "/b"])}), SEED,
        max_pages=2, max_depth=1, concurrency=2,
        max_content_chars_per=4000, cache_ttl=0)
    assert "clamped" not in out.summary, out.summary


@pytest.mark.asyncio
async def test_the_per_page_cap_keeps_the_sentence_it_already_had():
    """第四颗早就在说了 —— 这一条守的是搬迁把它改写过（同一族里最容易顺手改的一句）。"""
    out = await crawl_mod.web_crawl(
        _site({SEED: _linked(["/a"])}), SEED, max_pages=1,
        max_content_chars_per=400, cache_ttl=0)
    assert ("max_content_chars_per clamped 400->500 (supported range 500-20000)"
            in out.summary), out.summary


def test_crawl_and_web_read_word_the_same_event_identically():
    """两条通道、一个作者：同一颗钳制在两颗工具上必须是同一形状的一句话。

    这句不是风格问题。实测里"为什么这次只给我 500 字"在 web_read 有答案、在
    web_crawl 没有，调用方只能把两件事当两件。
    """
    a = _clamp_note("max_content_chars", 1, 500, lo=500, hi=200_000)
    b = _clamp_note("max_content_chars_per", 1, 500, lo=500, hi=200_000)
    assert a == "max_content_chars clamped 1->500 (supported range 500-200000)"
    assert b.split(" clamped ", 1)[1] == a.split(" clamped ", 1)[1], (a, b)
    src = inspect.getsource(crawl_mod)
    assert 'f"max_content_chars_per clamped' not in src, (
        "crawl 里还留着一句手抄的措辞：那句必须读 _clamp_note")


# ─── 2 · PDF 的 pages= 由文档长度钳 ─────────────────────────────

def test_a_page_range_that_runs_past_the_document_is_said_out_loud():
    with _arg_notes_scope() as notes:
        got = pdf_mod._parse_pages("5-999", total=12)
    assert got == list(range(5, 13))
    assert notes == ["pages clamped '5-999' -> '5-12' of 12 page(s) "
                     "(supported range 1-12)"], notes


def test_a_single_page_past_the_end_is_dropped_in_words_not_in_silence():
    with _arg_notes_scope() as notes:
        got = pdf_mod._parse_pages("3,99", total=12)
    assert got == [3]
    assert notes and "pages clamped '3,99' -> '3' of 12 page(s)" in notes[0], notes


def test_a_page_spec_inside_the_document_reports_nothing():
    with _arg_notes_scope() as notes:
        assert pdf_mod._parse_pages("1-12", total=12) == list(range(1, 13))
        assert pdf_mod._parse_pages(None, total=12) == list(range(1, 13))
    assert notes == [], notes


# ─── 3 · 动作面的两颗 ────────────────────────────────────────────

def test_an_overlong_wait_is_reported_with_the_cap_that_bit():
    with _arg_notes_scope() as notes:
        out = actions_mod._validate_actions([{"wait": 900_000}])
    assert out[0]["wait"] == actions_mod.MAX_WAIT_MS
    assert notes == [f"action 0 'wait' clamped 900000->{actions_mod.MAX_WAIT_MS} ms "
                     f"(supported range 0-{actions_mod.MAX_WAIT_MS})"], notes


def test_scroll_steps_and_their_step_ms_are_each_named():
    with _arg_notes_scope() as notes:
        out = actions_mod._validate_actions([{"scroll": {"steps": 500, "ms_per_step": 90_000}}])
    assert out[0]["scroll"]["steps"] == actions_mod.MAX_SCROLL
    assert out[0]["scroll"]["ms_per_step"] == actions_mod.SCROLL_STEP_MS_MAX
    assert any("steps" in n for n in notes), notes
    assert any("ms_per_step" in n for n in notes), notes


def test_a_digit_string_wait_is_clamped_with_the_same_sentence():
    """字符串客户端（{"wait": "900000"}）曾经是最容易静默过去的一种写法。"""
    with _arg_notes_scope() as notes:
        actions_mod._validate_actions([{"wait": "900000"}])
    assert notes and "clamped 900000->" in notes[0], notes


def test_a_well_behaved_action_chain_reports_no_clamp():
    with _arg_notes_scope() as notes:
        actions_mod._validate_actions([{"wait": 500}, {"scroll": 2}])
    assert notes == [], notes


# ─── 4 · nested_max 说两次不如说一次到位 ────────────────────────

@pytest.mark.parametrize("asked,applied", [(100, 30), (31, 30)])
def test_nested_max_above_the_ceiling_is_named_even_when_no_link_was_saved(asked, applied):
    """这一页只有 5 颗链接时，`nested_budget` 那句"省掉了 N 颗"永远不会出现 ——
    而 100 确实被钳成了 30。省没省是站点的事，钳没钳是我们这边的事实。"""
    with _arg_notes_scope() as notes:
        assert nested_mod.nested_cap(asked) == applied
    assert notes == [f"nested_max clamped {asked}->{applied} "
                     f"(supported range 0-{nested_mod.MAX_NESTED_ITEMS})"], notes


def test_nested_max_unset_or_in_range_says_nothing():
    with _arg_notes_scope() as notes:
        assert nested_mod.nested_cap(None) == nested_mod.DEFAULT_NESTED_ITEMS
        assert nested_mod.nested_cap(4) == 4
        assert nested_mod.nested_cap(0) == 0
    assert notes == [], notes


# ─── 5 · HTTP 一档的每次尝试 ─────────────────────────────────────

def test_the_per_attempt_timeout_cap_is_computed_in_one_place():
    """两个读点（HTTPSession 构造与 session.get）曾经各写一遍同一条算式 —— 那正是
    "改了上报的那一处、忘了另一处"的产地。算式住在 `bulk_get`（HTTP 一档真正发包的
    那颗），`get` 只是它的一颗门面。
    """
    src = inspect.getsource(MasterFetchServer.bulk_get)
    assert src.count("max(1, min(int(timeout), 30))") == 1, "算式又变成两份了"
    assert "timeout clamped" in src, "封顶不再说出口"


def test_the_http_tier_says_so_when_its_own_attempt_cap_bit():
    """行为而不是形状：那一句要真的在这一颗调用里说得出口。

    用一颗永远解析不到的主机名（RFC 6761 把 `.invalid` 保留成"永不解析"，所以离线
    环境同样立刻失败）—— 这一条测的不是抓到了什么，而是**失败之前有没有先把钳制
    说出口**；上报点就坐在连接之前，所以 status=0 的那次也带着这句话。
    """
    with _arg_notes_scope() as notes:
        asyncio.run(MasterFetchServer.bulk_get(
            ["http://dhole-clamp-probe.invalid/"], timeout=120, retries=0))
    assert any(n.startswith("timeout clamped 120->30") for n in notes), notes


# ─── 6 · 通道的形状 ──────────────────────────────────────────────

def test_a_note_written_with_no_bucket_open_is_dropped_not_leaked():
    """静默是这条通道唯一的失败方式：没有桶就不写，而不是把话留给下一次调用。

    这条守的是 `_ARG_NOTES` 的默认值仍然是 None，以及 `_note_text` 不会顺手
    set 出一个跨调用活着的列表 —— 一台长跑的服务器里，那会把上一颗 PDF 的
    "of 12 page(s)" 印在下一颗无关的响应上。
    """
    assert _ARG_NOTES.get() is None
    with _arg_notes_scope() as notes:
        _note_text("inside")
    assert notes == ["inside"]
    _note_text("outside")
    assert _ARG_NOTES.get() is None, "桶没有被复位"


def test_every_tool_call_opens_one_bucket():
    """桶开在两种传输共同经过的那一个 handler 上，所以没有哪颗工具是聋的。

    开在 `_dispatch` 上也算"一处"，但那颗方法体是四条源码形状守卫共同的读取对象
    （"分发器不许把已删的参数名再 smuggle 回来"），把它的 body 搬进另一颗方法
    就等于悄悄让那四条各看一半。尺子跟着形状走：读 handler。
    批次8 刀1 把 handler 随 Server 构造一起抽进 `_mcp_server()`（真 SDK in-memory
    回路要拿到同一个对象）；桶的形状没动，守卫跟着搬，不是放宽。
    """
    src = inspect.getsource(MasterFetchServer._mcp_server)
    assert "_arg_notes_scope()" in src, "工具面又回到只有一种工具有桶的状态"
    # 而且桶必须包住整次调用（含 finalization），不然话记下了、summary 已经走了。
    handler = src.split("async def call_tool", 1)[1]
    scope_at = handler.index("_arg_notes_scope()")
    dispatch_at = handler.index("self._dispatch(")
    assert scope_at < dispatch_at, "桶开在分发之后，那些话没有 summary 可进"


@pytest.mark.asyncio
async def test_web_read_announces_a_cap_above_the_ceiling(monkeypatch):
    """R04-029 的原参数（web_read, 999999）在 2026-10-20 复现：summary 里已经有
    `max_content_chars clamped 999999->200000`，所以「静默接受」不成立，产品不改。
    这条把那次读数钉住 —— 将来若有人把夹取改成拒绝，或者把注记丢掉，这里会红。"""
    _stub_one_tier(monkeypatch)
    out = await MasterFetchServer(cache_ttl=0).web_read(
        "https://ttl.example.com/", cache_ttl=0, max_content_chars=999_999)
    assert ("max_content_chars clamped 999999->200000 (supported range 500-200000)"
            in out.summary), out.summary


@pytest.mark.parametrize("tool,arg,asked,applied,lo,hi", [
    ("web_read", "max_content_chars", 1, 500, 500, 200_000),
    ("web_read", "max_links", 0, 1, 1, 100),
])
def test_the_two_announced_clamps_that_already_worked_still_word_it_the_same_way(
    tool, arg, asked, applied, lo, hi):
    """房内样板不许被这批改坏：它们已经是对的，本批只是让其余的追上它们。"""
    assert _clamp_note(arg, asked, applied, lo=lo, hi=hi) == (
        f"{arg} clamped {asked}->{applied} (supported range {lo}-{hi})")


def test_the_boundary_is_not_re_stated_above_the_layer_that_announces_it():
    """`params.py:45-49` 那条意图的形状守卫。

    边界转换层只准归一化类型：在那里顺手夹一次范围，就等于在每一句上报之上再做一次
    无人上报的钳制 —— 本批要消灭的正是那一件。
    """
    src = inspect.getsource(params_mod._coerce_one_arg)
    assert "lo=" not in src and "hi=" not in src, (
        "归一化层开始夹范围了：那里的钳制没人上报")


# ─── 7 · cache_ttl：负数不是时长（2026-10-09 审计 D5/D10）────────
#
# 这一族里唯一一颗**静默**的钳位。其余每一颗越界都在 summary 里点名，而
# `cache_ttl=-1` 被当成 0（读也不读、写也不写）之后一个字都没有 —— 偏偏它是唯一
# 一颗与"我到底有没有拿到新内容"直接相关的参数。三颗工具都有它，所以那句措辞
# 也只有一个作者（`context._cache_ttl_note`），否则又是"同一件事两种说法"。
#
# 2026-10-09 审计 D5 收口：上面那句"其余每一颗都在 summary 里点名"当时并不全真 ——
# 搜索侧那颗在 `fetch_hint`。现在两个轴都对齐了：措辞一个作者（context），落点一个
# 字段（summary）。反向那一半（fetch_hint 不许吃回参数注记）也有守卫。

def test_a_negative_cache_ttl_is_worded_once_for_all_three_tools():
    from dhole_mcp.context import _cache_ttl_note

    assert _cache_ttl_note(-1) == (
        "cache_ttl=-1 is not a duration; using 0 - this call reads no cache, "
        "and the copy it fetches is stored like any other read")
    # 字符串客户端（{"cache_ttl": "-1"}）必须落到同一句上。
    assert _cache_ttl_note("-1") == _cache_ttl_note(-1)


@pytest.mark.parametrize("fine", [None, 0, 1, 3600, "0", "3600"])
def test_a_cache_ttl_that_is_a_duration_says_nothing(fine):
    """不许变成噪声：只有真的不是时长时才说话。"""
    from dhole_mcp.context import _cache_ttl_note

    assert _cache_ttl_note(fine) == ""


def test_the_sentence_carries_the_half_of_zero_the_wire_never_states():
    """`0` 的那一半是"这一发不读缓存" —— 而它**从来不是**"也不写缓存"。

    旧句子写的正是后者，因为旧实施把两件事合在了一颗参数上；R02-032 把写侧还给普通读
    之后，那句承诺就成了假话，所以句子与实施一起改（同一把刀，两个落点）。这里锁的是
    新边界：说清读侧，不许再许诺一次它已经不再做的"不写"。
    """
    from dhole_mcp.context import _cache_ttl_note

    note = _cache_ttl_note(-1)
    assert "reads no cache" in note, note
    assert "writes none" not in note, f"句子还停在旧实施上：{note}"


def _stub_one_tier(monkeypatch):
    """把一档取数换成一具现成正文，好让 summary 真的被组装出来。"""
    result = ResponseModel(url="https://ttl.example.com/", status=200,
                           content=["<p>body</p>"], content_ok=True)

    async def fake(self, *a, **kw):
        return server_mod._apply_chunking(
            result, max_chars=server_mod.DEFAULT_MAX_CONTENT_CHARS)

    monkeypatch.setattr(MasterFetchServer, "_auto_escalate", fake)


@pytest.mark.asyncio
async def test_web_read_names_a_clamped_cache_ttl_in_its_summary(monkeypatch):
    _stub_one_tier(monkeypatch)
    out = await MasterFetchServer(cache_ttl=0).web_read(
        "https://ttl.example.com/", cache_ttl=-1)
    assert "cache_ttl=-1 is not a duration" in (out.summary or ""), out.summary


@pytest.mark.asyncio
async def test_web_read_does_not_invent_a_note_for_a_real_ttl(monkeypatch):
    _stub_one_tier(monkeypatch)
    out = await MasterFetchServer(cache_ttl=0).web_read(
        "https://ttl.example.com/", cache_ttl=0)
    assert "cache_ttl=" not in (out.summary or ""), out.summary


@pytest.mark.asyncio
async def test_web_crawl_names_a_clamped_cache_ttl_in_its_summary():
    out = await crawl_mod.web_crawl(
        _site({SEED: _linked(["/a"])}), SEED, max_pages=1, cache_ttl=-1)
    assert "cache_ttl=-1 is not a duration" in out.summary, out.summary


def test_web_search_reads_the_same_sentence_author():
    """搜索那条路读同一颗措辞作者，落同一个字段（2026-10-09 审计 D5 收口）。

    这里读形状而不是跑一次真搜索：`web_search` 的合法入参一定会发网络请求，而这条
    判据要的是"有没有接上那句话"，不是"引擎答了什么"。手抄一句同义的话同样是失败
    —— 那正是这条纪律（一句话一个作者）要防的形状。字段那一半由
    `test_web_search_puts_the_clamp_where_the_other_two_tools_put_theirs` 跑真的。
    """
    from dhole_mcp import search as search_mod

    src = inspect.getsource(search_mod.web_search)
    assert "_cache_ttl_note(" in src, "搜索那条路又回到静默钳位了"
    assert "is not a duration" not in src, (
        "search.py 手抄了那句措辞：它必须读 context._cache_ttl_note")


@pytest.mark.asyncio
async def test_web_search_puts_the_clamp_where_the_other_two_tools_put_theirs(monkeypatch):
    """一颗参数被钳，三颗工具必须说在**同一个字段**里。

    搜索那条路曾经把它追加到 `fetch_hint`。那颗字段在 wire 上的自述是
    *"How many high/med/low results + which to web_read first"* —— 说的是**结果**，
    不是调用方传进来的参数；把"我钳了你的 max_results"写进去，等于让那颗字段说它
    自己声明不说的事。`web_read`（经 `_with_agent_hints`）与 `web_crawl` 都写在
    `summary`，于是只扫 `summary` 的调用方在三颗工具里有两颗看得见、一颗看不见 ——
    2026-10-09 审计 D5 的第一次探针就是这么把「说在别处」读成了「没说」。
    """
    from dhole_mcp import search as search_mod
    from dhole_mcp.search_engines import EngineReport, RawResult

    async def fake_multi_search(query, max_results, **kwargs):
        # 标题与摘录都带着查询词，否则它们会被"一个查询词都没命中"那道过滤器
        # 整批丢掉，于是这条用例量到的 summary 只剩钳制那一句 —— 而它要守的正是
        # "正常的状态行 + 那颗注记"两者同时在。
        rows = [RawResult(title=f"DNS resolution note {i}", url=f"https://e{i}.example/",
                          snippet="DNS resolution", source="bing", position=i + 1)
                for i in range(3)]
        return rows, [EngineReport(name="bing", ok=True)]

    async def no_cache(*a, **k):
        return None

    async def no_set(*a, **k):
        return None

    async def fake_ensure():
        return None

    monkeypatch.setattr(search_mod, "multi_search", fake_multi_search)
    monkeypatch.setattr(search_mod, "get_cached", no_cache)
    monkeypatch.setattr(search_mod, "set_cached", no_set)
    monkeypatch.setattr(search_mod, "ensure_reranker", fake_ensure)
    monkeypatch.setattr(search_mod, "neural_rerank",
                        lambda q, r, **kw: [(x, 1.0) for x in r])

    out = await search_mod.web_search(None, "DNS resolution", 100)
    assert out.results, "夹具被过滤器吃光了，这条用例就没在量它声称量的事"
    assert "max_results=100 is outside the supported 1-50 range" in out.summary, out.summary
    # 反向那一半：这颗字段不许再把参数注记吃回来。
    assert "max_results=" not in out.fetch_hint, out.fetch_hint


# ─── 8 · 钳还是拒：判据写下来了，而且两半都真的照它走 ──────────────
#
# 2026-10-09 审计 C16 记的是"同类参数的验证策略三分裂：拒绝 / 钳制并说明 / 静默强转
# 三者混用"。上面七节把"钳制并说明"与"静默强转已消灭"钉住了，但**分界本身**（一颗
# 越界的数什么时候该钳、什么时候该拒）当时没有任何一处写下来 —— 只能从代码里反读。
# 分界就在 `params._coerce_int_arg(..., clamp=)`，所以判据也写在那里。

def test_the_reject_or_clamp_rule_is_written_where_the_decision_is_made():
    """判据必须住在做决定的那一层，而且必须把第三种策略明说不合法。

    这条守的不是措辞好看：一条没有写下来的判据，下一个人加参数时只能靠猜，
    而"猜"正是这一族缺陷唯一的产地。
    """
    src = inspect.getsource(params_mod)
    assert "越界：钳还是拒" in src, "判据从 params 的文档里消失了"
    assert "有没有唯一一种合理的读法" in src, "判据那句话被改成了别的意思"
    assert "没有合法用法" in src, (
        "第三种策略（静默强转）必须被明说不合法 —— 否则它随时可以回来")


@pytest.mark.parametrize("tool,args,needle", [
    ("web_read", {"url": "https://rule.example/", "offset": -5},
     "offset must be between 0 and 2147483648, got -5."),
    ("web_search", {"query": "x", "page": 99}, "Invalid page: 99 (0-10)"),
])
def test_a_value_whose_reading_would_change_is_refused_not_clamped(tool, args, needle):
    """判据的另一半，跑真的：这两颗的越界值**没有**唯一读法，所以必须拒绝。

    `offset=-5` 钳成 0 会把"最后 5 个字"当成"从第 0 个字开始"发回去；`page=99`
    钳成 10 会把"第 99 页"当成"第 10 页"。两者都会以一个普通 200 交出**另一个问题
    的答案** —— 那比报错坏得多。拒绝发生在发请求之前，所以这条用例不碰网络。
    """
    _content, structured = asyncio.run(MasterFetchServer()._dispatch(tool, args))
    assert needle in structured["error"], structured["error"]
    assert structured["next_action"], "拒绝必须自带出路"
    # 两条路互斥：说了"我拒绝了"就不能同时说"我钳好了"—— 否则调用方无从判断哪句
    # 算数。这是本用例真正的判据：`clamped` / `is outside` 是钳制那一族的措辞。
    blob = json.dumps(structured)
    assert "clamped" not in blob and "is outside" not in blob, blob
