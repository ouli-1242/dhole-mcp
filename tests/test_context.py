"""test_context.py —— 由 2 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
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
from dhole_mcp.context import _ARG_NOTES, _arg_notes_scope, _clamp_note, _note_text
from dhole_mcp.models import ResponseModel
from dhole_mcp.server import MasterFetchServer
from dhole_mcp import feed as feed_mod
from dhole_mcp import wire
from dhole_mcp.quality import _budget_advice, _over_budget_result
from dhole_mcp.security import TIMEOUT_MAX_MS, validate_timeout
from dhole_mcp.server import _HTTP_ATTEMPT_CAP_MS, _document_budget_advice, _feed_timeout_s


# ============================================================================
# 来源：test_every_clamp_is_announced.py
# ============================================================================
# 批次 4：钳过的边界必须每一颗都说出口 —— 在钳它的那一层说。

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


# ============================================================================
# 来源：test_a_timeout_bigger_than_the_per_attempt_cap_says_what_it_bought.py
# ============================================================================
# `timeout=` 说的是一份**整次调用**的毫秒预算，而 HTTP 一档钳的是**每次尝试**的秒 ——
# 这颗参数是唯一一处"声明的范围与实施的单位不是一回事"的钳，而一句提示做不了算术。

PROBE = "http://dhole-c5-probe.invalid/"


class _RecordingSession:
    """假 HTTPSession：记下这一档**实际**拿到的一试次预算，然后照挂死的样子失败。

    失败而不是成功，因为本文件量的两件事都在失败那一边：收据最需要发给那一发真的没拨通
    的调用（一次超时最需要"你给的其实是 30s"），而伪造一整个 primp 应答会把用例变成对
    `_translate_response` 的重复测试。
    """

    seen: list[dict] = []

    def __init__(self, **kwargs):
        _RecordingSession.seen.append({"ctor": kwargs.get("timeout")})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def get(self, url, **kwargs):
        _RecordingSession.seen[-1]["get"] = kwargs.get("timeout")
        raise RuntimeError("timed out reading " + url)


@pytest.fixture
def sessions(monkeypatch):
    import dhole_mcp.fetcher as fetcher_mod

    _RecordingSession.seen = []
    # `bulk_get` 在函数体里 `from dhole_mcp.fetcher import HTTPSession`，所以摘的是源头
    # 那颗名字 —— server 的模块命名空间里根本没有它（摘错地方的话桩根本不生效，而用例
    # 会去拨真网络）。
    monkeypatch.setattr(fetcher_mod, "HTTPSession", _RecordingSession)
    return _RecordingSession.seen


def _timeout_schema(tool_name: str) -> str:
    tool = next(d for d in MasterFetchServer._TOOL_DEFS if d["name"] == tool_name)
    return tool["inputSchema"]["properties"]["timeout"]["description"]


def _cap_in_the_formula() -> int:
    """`bulk_get` 那行算式里的秒数 —— 全仓库唯一的一试次上限**作者**。"""
    src = inspect.getsource(MasterFetchServer.bulk_get)
    m = re.search(r"max\(1, min\(int\(timeout\), (\d+)\)\)", src)
    assert m, "算式换了形状：这条对齐守卫要跟着换，而不是放宽"
    return int(m.group(1))


# ─── 1 · 收据：发出去的数必须就是实施了的那个数 ──────────────────

def test_the_receipt_is_the_figure_the_tier_actually_used(sessions):
    """"钳到 30s"不许只留在散文里，而它发出去的数不许与实施的不是同一个。

    反向：把行上的 `timeout_applied` 抄成 `timeout`（调用方传的那颗）而不是
    `_per_attempt_s`，这一条立刻红 —— 它比的是两处**同一个值**，不是任何一句措辞。
    """
    bulk = asyncio.run(MasterFetchServer.bulk_get([PROBE], timeout=120, retries=0))

    assert sessions, "假的 session 没有被构造，这条用例没在量它声称量的事"
    assert {s["ctor"] for s in sessions} == {30}, sessions
    assert {s["get"] for s in sessions} == {30}, sessions
    assert bulk.results[0].timeout_applied == _HTTP_ATTEMPT_CAP_MS


def test_a_failed_attempt_still_carries_the_receipt(sessions):
    """最需要这颗数的一发，正是没有正文的那一发。

    `bulk_get` 的异常分支自己构造一具 ResponseModel（不走 `_translate_response`），所以
    "抄到每一行"必须在那一支也做得到；抄漏了它，收据就只在成功时上线，而失败才是调用方
    要决定"还抬不抬 timeout"的那一刻。
    """
    bulk = asyncio.run(MasterFetchServer.bulk_get([PROBE], timeout=120, retries=0))

    row = bulk.results[0]
    assert row.status == 0 and not row.content, row
    assert row.timeout_applied == _HTTP_ATTEMPT_CAP_MS


def test_the_sentence_and_the_number_describe_the_same_clamp(sessions):
    """双保险不许各说一套：`summary` 那句话与那颗字段说的是同一次钳制。

    这句话只活在 `summary` 里，而 `summary` 由 `_ARG_NOTES` 的桶组装（一次调用一个桶，
    见 `test_every_clamp_is_announced.py` 第六节），所以这里要自己把桶开起来 —— 真实
    调用里桶由 `_dispatch` 开。
    """
    with _arg_notes_scope():
        bulk = asyncio.run(MasterFetchServer.bulk_get([PROBE], timeout=120, retries=0))

    row = bulk.results[0]
    assert "timeout clamped 120->30 s per attempt" in row.summary, row.summary
    assert row.timeout_applied == 30 * 1000, row.timeout_applied


def test_a_budget_that_was_not_cut_pays_no_receipt(sessions):
    """没咬就不许发：一个与调用方自己传的一模一样的数是噪声。"""
    bulk = asyncio.run(MasterFetchServer.bulk_get([PROBE], timeout=20, retries=0))

    assert bulk.results[0].timeout_applied == 0
    assert sessions[0]["ctor"] == 20, sessions
    payload = wire._wire_dump(bulk.results[0])
    assert "timeout_applied" not in payload, (
        f"默认值上线了（压实表里没这颗？）: {sorted(payload)}")


def test_no_http_attempt_means_no_said_figure():
    """这颗只描述 HTTP 一档的一试次；别的来源没有这个时钟。

    缓存回放的信封里根本没有这一列（`cache.py` 存的是白名单字段），浏览器档不经过
    `bulk_get` —— 两处都留默认值。这条钉的是"缺席=这一发没有 HTTP 尝试被钳"，否则一次
    旧回放的数值可以冒充这一次调用的钳制（与 R4 那颗 `fetched_at` 同一族）。
    """
    row = ResponseModel(url="https://cached.example/", status=200,
                        content=["body"], cached=True, fetcher_used="cache")
    assert row.timeout_applied == 0
    assert "timeout_applied" not in wire._wire_dump(row)


# ─── 2 · 一个数的几个家必须同值 ─────────────────────────────────

def test_the_number_the_prose_names_is_the_number_the_formula_uses():
    """三处读数（算式 / 那颗常量 / 常驻层与字段描述里的句子）必须同值。

    `test_every_clamp_is_announced.py:216` 钉死了算式只有一份；本条钉的是"说话的地方"
    引用同一个数时不许各抄一个版本。把 30 改成 45 而忘了改别处，这里红。
    """
    cap_s = _cap_in_the_formula()
    assert _HTTP_ATTEMPT_CAP_MS == cap_s * 1000, (
        f"算式钳 {cap_s}s，说话的地方用 {_HTTP_ATTEMPT_CAP_MS}ms")

    said_it = []
    for tool in MasterFetchServer._TOOL_DEFS:
        schema = (tool["inputSchema"].get("properties") or {}).get("timeout")
        if not schema or "attempt" not in schema["description"]:
            continue
        said_it.append(tool["name"])
        assert f"caps at {cap_s}s" in schema["description"], (tool["name"], schema)
    assert said_it == ["web_read", "web_request", "web_crawl"], said_it

    field_text = ResponseModel.model_fields["timeout_applied"].description
    assert f"{_HTTP_ATTEMPT_CAP_MS}ms" in field_text, field_text


def test_a_description_promising_a_longer_download_names_the_attempt_cap_too():
    """"max 120000" 加一句"下载"就是一个抬上去也够不着的承诺。

    这条是本刀的形状守卫：一颗 `timeout` 描述既承诺得比一试次上限更多、又在这颗工具上
    谈到下载，就必须把上限一起说完。搬家时删掉那半句（或给 web_request 加上 "download"
    却忘了上限），这里红。
    """
    cap_s = _cap_in_the_formula()
    checked = []
    for tool in MasterFetchServer._TOOL_DEFS:
        schema = (tool["inputSchema"].get("properties") or {}).get("timeout")
        if not schema:
            continue
        text = schema["description"]
        promised = re.search(r"max (\d{5,})", text)
        if not promised or "download" not in text.lower():
            continue
        checked.append(tool["name"])
        assert int(promised.group(1)) / 1000 <= cap_s or "attempt" in text, (
            f"{tool['name']} 的 `timeout` 承诺了 {promised.group(1)}ms 的下载，"
            "却没有说一次尝试到不了那么多")
    assert checked, "没有一颗描述既承诺过大预算又谈到下载：这条守卫没在量任何东西"


def test_crawls_timeout_stops_calling_itself_a_whole_call_budget():
    """同一张表里两句互相矛盾过：`timeout` 自述"whole-call"，`deadline_ms` 说它是每页。

    单位与口径都要说的是**页**，否则抬 `timeout=` 的人以为自己抬的是整轮的上司。
    """
    text = _timeout_schema("web_crawl")
    assert text.lower().startswith("per-page"), text
    assert "whole-call" not in text.lower(), text
    assert "deadline_ms" in text, text


def test_the_feed_default_the_wire_prints_is_the_default_the_code_uses():
    """`feed_read` 括号里印的默认值必须就是签名与换算函数共同的那个数。"""
    default_ms = _feed_timeout_s(None) * 1000
    assert default_ms == 20_000, default_ms
    assert f"default {default_ms}" in _timeout_schema("feed_read"), _timeout_schema("feed_read")


# ─── 3 · 建议不许推荐一件不会改变结果的事 ────────────────────────

def test_a_document_past_the_attempt_cap_is_not_told_to_raise_timeout():
    advice = _document_budget_advice("https://d.test/a.pdf", None, "", 120_000,
                                     "HTTP tier")
    assert "raise timeout" not in advice, advice
    assert f"{_HTTP_ATTEMPT_CAP_MS // 1000}s" in advice, advice
    # 出路还在：换源，或把文件取到本地再交给人解析 —— 两句都要说得出。
    assert "switch source" in advice and "parse" in advice, advice


def test_a_document_under_the_attempt_cap_is_still_told_the_lever_works():
    """另一半：12s 预算里抬 `timeout=` 是真有效的（上限还没碰到），别把有用的话删了。

    建议给的数不许超过一次尝试真正拿得到的那个 —— 否则这句话本身就成了下一颗
    "抬上去也够不着"的承诺。
    """
    advice = _document_budget_advice("https://d.test/a.pdf", None, "", 12_000,
                                     "HTTP tier")
    assert "raise timeout" in advice, advice
    suggested = int(re.search(r"timeout=(\d+)", advice).group(1))
    assert suggested <= _HTTP_ATTEMPT_CAP_MS, advice
    assert f"caps at {_HTTP_ATTEMPT_CAP_MS // 1000}s" in advice, advice


def test_the_generic_advice_never_pitches_the_number_already_in_play():
    """通用那一句（非文档路径）从前只有一个写死的例子：`Raise timeout (e.g.
    timeout=60000)` —— 而它不看调用方已经给了多少。

    现场（15 场景体测，场景 10）：`timeout=60000` 的一发元素截图跑了 61,529ms 被掐，
    建议劝的就是"抬到 60000"。一句劝人去做他刚刚做过的事的话不是建议，是让人怀疑自己
    没读懂参数（实测之后那一格没有再试，直接换了路）。
    """
    advice = _budget_advice("actions (stealthy) tier", "", 60_000)
    assert "timeout=60000" not in advice, advice
    assert "already had 60000ms" in advice, advice
    assert "120000" in advice, advice


def test_the_advice_shows_the_room_rather_than_a_second_guess_at_the_ceiling():
    """剩下的余量直接算出来给人看，人就不必去猜 dhole 还收不收得下。"""
    advice = _budget_advice("actions (stealthy) tier", "", 60_000)
    assert f"{TIMEOUT_MAX_MS - 60_000}ms more" in advice, advice


def test_a_budget_at_the_ceiling_is_told_there_is_nothing_left_to_raise():
    """另一半：已经碰到门的那一发，"再抬抬"是一句做不到的话。"""
    advice = _budget_advice("HTTP tier", "", TIMEOUT_MAX_MS)
    assert "Raise" not in advice, advice
    assert "nothing left to raise" in advice, advice


def test_a_default_sized_budget_still_hears_the_verb():
    """反面守卫：25000（actions 那一档的默认）时候 "Raise timeout" 必须还在 ——
    这一刀是把谎撤掉，不是把一句有用的话删了。"""
    advice = _budget_advice("actions (stealthy) tier", "", 25_000)
    assert advice.startswith("Raise timeout"), advice


def test_the_built_over_budget_answer_carries_what_was_asked():
    """整条线：`_over_budget_result` 传下去的就是它自己那发用的预算，不是别的常数。"""
    r = _over_budget_result("http://127.0.0.1:8399/captcha", 60_000, 61_529,
                            "actions (stealthy) tier", "")
    assert "already had 60000ms" in r.next_action, r.next_action
    assert "timeout=60000" not in r.next_action, r.next_action


def test_the_ceiling_the_advice_names_is_the_ceiling_the_door_enforces():
    """一个数的几个家必须同值：门口拒的那颗、建议里念的那颗、常驻层写的那句。

    与 `_HTTP_ATTEMPT_CAP_MS` 那条对齐守卫同一个理由 —— 数有两处家而没有测试，
    就是漂移的开始。
    """
    from dhole_mcp.security import SecurityError

    assert validate_timeout(TIMEOUT_MAX_MS) == TIMEOUT_MAX_MS
    with pytest.raises(SecurityError, match=str(TIMEOUT_MAX_MS)):
        validate_timeout(TIMEOUT_MAX_MS + 1)
    # 常驻层那颗 `timeout` 描述的口径（"max 120000"）必须与门一致
    named = [t["name"] for t in MasterFetchServer._TOOL_DEFS
             if (t["inputSchema"].get("properties") or {}).get("timeout")
             and f"max {TIMEOUT_MAX_MS}" in
             t["inputSchema"]["properties"]["timeout"]["description"]]
    assert named, "没有任何一颗 `timeout` 描述写着这道门：建议与门口各说一套"


def test_the_cap_and_the_receipt_are_explained_where_an_agent_will_look():
    """两颗话都上了常驻层，解释就必须住在 sys_guide 读得到的卷里。

    常驻层与语料各说一套是这一族反复付过的账（`test_wire_moves_land_in_the_manual`）。
    """
    from dhole_mcp import guide

    corpus = "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))
    for needle in ("timeout_applied", "one HTTP attempt of at most 30s",
                   "one attempt never exceeds 30s"):
        assert needle in corpus, f"{needle!r} 不在语料里：只读手册的 agent 解释不了这颗数"


# ─── 4 · feed_read：同一颗参数在另一条通道上根本不换算 ────────────

def _feeds_captured(monkeypatch) -> list:
    seen: list = []

    async def fake_fetch_feeds(urls, timeout=20, max_items=20, since=""):
        seen.append(timeout)
        return []

    monkeypatch.setattr(feed_mod, "fetch_feeds", fake_fetch_feeds)
    return seen


def test_feed_read_sends_seconds_not_the_milliseconds_it_was_given(monkeypatch):
    """`timeout=30000`（wire 的口径 = 30 秒）从前原样下传，而那一层吃的是**秒**。

    于是 primp 拿到 30000 秒。这条按线路上下传的那颗参数真正的样子量（换算之后的秒）。
    """
    seen = _feeds_captured(monkeypatch)
    asyncio.run(MasterFetchServer().feed_read(urls=["https://d.test/rss.xml"],
                                              timeout=30_000))
    assert seen == [30], seen


def test_feed_read_omitted_timeout_keeps_the_wait_it_always_had(monkeypatch):
    """修单位不许顺手改掉一次真实的等待：不传时仍然等 20s。"""
    seen = _feeds_captured(monkeypatch)
    asyncio.run(MasterFetchServer().feed_read(urls=["https://d.test/rss.xml"]))
    assert seen == [20], seen


def test_a_feed_timeout_above_the_documented_ceiling_is_refused_by_name(monkeypatch):
    """拒必须具名：一颗越界的数不许换算成"更长的等待"然后照跑。"""
    called = _feeds_captured(monkeypatch)
    with pytest.raises(ValueError) as exc:
        asyncio.run(MasterFetchServer().feed_read(urls=["https://d.test/rss.xml"],
                                                  timeout=200_000))
    assert "120000" in str(exc.value), str(exc.value)
    assert not called, "拒绝之前已经发过取回了"


def test_the_feed_conversion_is_second_granularity_and_floors_at_one():
    """换算的颗粒是秒：亚秒的值进 1s，而不是变成 0（0 在那一层是"不设超时"）。"""
    assert _feed_timeout_s(30_000) == 30
    assert _feed_timeout_s(500) == 1
    assert _feed_timeout_s(120_000) == 120
    assert _feed_timeout_s(None) == 20
