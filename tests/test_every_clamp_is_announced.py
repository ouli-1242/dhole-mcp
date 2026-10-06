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
import re

import pytest

from dhole_mcp import actions as actions_mod
from dhole_mcp import crawl as crawl_mod
from dhole_mcp import nested as nested_mod
from dhole_mcp import params as params_mod
from dhole_mcp import pdf_extractor as pdf_mod
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
