# -*- coding: utf-8 -*-
"""随 focus 词变的那个总数，必须自己说它是被过滤过的那一路；切口不许落在词中间。

两条实测（`review/2026-10-10-用户体验模拟报告.md`）：

1. 一个 URL 的 `total_extracted_chars` 交出过六个值 —— 30994 / 12819 / 19843 / 45312 /
   9829 / 2396。那不是站点在变，是**分页算的是 focus 过滤之后那一路的长度**，而这句话
   今天只在"删掉了结构化块"（表格/代码）时才出口。于是 `[No more content.]` 骗过两次：
   换一个 focus 词，第 3 页可以是最后一页，也可以是中间一页 —— 而这两种的修法相反
   （前者该停手，后者该回到 `offset=0` 重翻）。
2. 分页那一切是裸切片 `full_text[offset:offset + max_chars]`，把一段中文正文从"值"字
   中间剪断过。仓库里**早就有一颗函数在管这件事**：`_summary_label` 为了不把标签剪成
   "…can compile (S"，排好了"先句末、再子句末、再词末"的顺序 —— 只是那份排序没被分页读。

判据的形状：

1. 数在动的时候，**哪一路**写在响应里（`pagination_stream` / `page_total_chars` /
   `pagination_basis`），不需要调用方猜。
2. 没动的时候不占字节（默认值被压实机制删掉）—— 绝大多数调用不带 focus，那一路就是全文。
3. 吸附**挪界不删字**：退回去的那几个字随 `next_offset` 出现在下一页，且 `chars_snapped_back`
   把挪了多少说出口；最后一片永远不吸附（没有下一页可去）。
4. 边界规则只有一个作者：摘要标签与分页读同一颗 `snap_to_boundary`。
"""

from __future__ import annotations

import pytest

from dhole_mcp import wire
from dhole_mcp.context import _FOCUS
from dhole_mcp.models import ResponseModel
from dhole_mcp.focus import snap_to_boundary
from dhole_mcp.quality import _summary_label
from dhole_mcp.server import MIN_CHUNK_CHARS, _apply_chunking

COOKIE = "第{i}段讲 cookie 的存活时间，配置项叫 DHOLE_COOKIE_TTL_S，默认三千秒。"
OTHER = "第{i}段是别的话题：图表、排版、字体回退与一些无关的说明，用来把这一页填满一点。"


def _page(hits: int = 6, filler: int = 18) -> ResponseModel:
    paras = []
    for i in range(hits):
        paras.append(COOKIE.format(i=i))
        for k in range(filler // hits):
            paras.append(OTHER.format(i=f"{i}{k}"))
    return ResponseModel(url="https://e.test/p", status=200,
                         content=["\n\n".join(paras)], extracted_type="markdown")


def _chunked(result: ResponseModel, *, focus: str | None, max_chars: int = 4_000,
             offset: int = 0) -> ResponseModel:
    token = _FOCUS.set(focus)
    try:
        return _apply_chunking(result.model_copy(), max_chars=max_chars, offset=offset)
    finally:
        _FOCUS.reset(token)


# ─── 1 · 会变的那个数要说它是哪一路 ──────────────────────────────

def test_a_focus_cut_page_reports_which_stream_it_counted():
    out = _chunked(_page(), focus="cookie 存活时间")
    assert out.pagination_stream == "focused", out.pagination_stream
    assert out.page_total_chars == len("\n\n".join(_page().content)), (
        "`page_total_chars` 应当是同一页**未过滤**的长度，不是另一个 guess")
    assert out.total_extracted_chars < out.page_total_chars, (
        "这一路没被切短，那「哪一路」就没内容可说")
    assert "focus-filtered" in out.pagination_basis, out.pagination_basis


def test_two_different_focus_words_on_one_page_both_say_so():
    """六个值的来路：换 focus 词就是换一路。两发都自己认领，读的人不必猜页面变没变。"""
    a = _chunked(_page(), focus="cookie 存活时间")
    b = _chunked(_page(), focus="字体回退")
    assert a.total_extracted_chars != b.total_extracted_chars, (
        "夹具失灵：两个 focus 词应当切出两路不同的长度，这一条就没有对照")
    for one in (a, b):
        assert one.pagination_stream == "focused"
        assert one.page_total_chars == a.page_total_chars == b.page_total_chars, (
            "同一页的未过滤长度随 focus 词变了 —— 那才叫真的在骗人")


def test_an_unfocused_read_says_nothing_about_a_stream():
    """默认值就是"这一路是全文"，一句人人都知道的话不占字节。"""
    out = _chunked(_page(), focus=None)
    assert out.pagination_stream == "full", out.pagination_stream
    assert out.page_total_chars == 0 and out.pagination_basis == ""
    dumped = wire._wire_dump(out)
    for name in ("pagination_stream", "page_total_chars", "pagination_basis"):
        assert name not in dumped, f"{name} 上线了：干净响应在付费"


def test_the_no_more_content_branch_carries_the_basis_too():
    """`[No more content.]` 骗人的正是那一支：翻到"没有更多"时更得说清是哪一路。"""
    out = _chunked(_page(), focus="cookie 存活时间", max_chars=60, offset=10 ** 6)
    assert out.content == ["[No more content.]"], out.content
    assert out.pagination_stream == "focused", out.pagination_stream
    assert out.page_total_chars > out.total_extracted_chars


# ─── 2 · 切口落在界上，而字一个都不丢 ────────────────────────────

def test_a_window_ends_at_a_clause_not_mid_word():
    """裸切片实测剪断过中文正文的"值"字 —— 这一窗必须退回到最近的界。

    R01-024 之前这一条要先 `split("\\n\\n[Truncated")[0]` 把旁注剪掉才拿到正文；
    现在 `content[0]` 就是正文，剪的那一步没了，断言反而更硬。
    """
    out = _chunked(_page(hits=4, filler=40), focus=None, max_chars=100)
    body = out.content[0]
    assert body[-1] not in "，。；：！？" , f"界上不该把标点留在窗口尾：{body[-6:]!r}"
    assert out.next_offset == len(body), (
        "next_offset 与切回去的长度不一致 —— 那就是真丢了字")
    assert out.chars_snapped_back == 100 - len(body), (
        f"吸附了 {out.chars_snapped_back}，界在 {len(body)}：两个数说的是同一件事吗")


def test_the_snapped_characters_arrive_on_the_next_page():
    """吸附挪界不删字：这一窗 + 下一窗，接起来仍然是那一路的全文。"""
    page = _page(hits=4, filler=40)
    first = _chunked(page, focus=None, max_chars=100)
    kept = len(first.content[0])
    second = _chunked(page, focus=None, max_chars=100, offset=first.next_offset)
    whole = "\n\n".join(page.content)
    assert first.next_offset == kept
    assert whole[:kept] + whole[kept:] == whole
    assert second.content[0].startswith(whole[kept:kept + 20]), (
        "下一页不是从这一页停下的地方开始的 —— 那几个字确实丢了")


def test_the_last_window_never_snaps():
    """最后一片没有"下一页"可以去：吸附它就是把字扔掉。"""
    page = _page(hits=2, filler=4)
    whole = "\n\n".join(page.content)
    out = _chunked(page, focus=None, max_chars=max(1, len(whole) - MIN_CHUNK_CHARS + 1))
    assert out.is_truncated is False, out.content[0][-60:]
    assert out.chars_snapped_back == 0
    assert out.content[0] == whole, "最后一片该是剩下的全部，一个字都不该少"


def test_a_cut_that_already_lands_on_a_boundary_pays_no_note():
    head = "一句话说完了。" * 10
    assert snap_to_boundary(head) in (len(head), head.rfind("。"))
    assert snap_to_boundary("abc def") == 3, "词末是兜底，不是首选"


def test_no_boundary_at_all_returns_the_whole_window():
    """没有标点也没有空格时原样交回 —— "找不到界"不等于"可以剪半截"。"""
    run = "没有标点也没有空格的一长串中文正文用来测试兜底那一支" * 3
    assert snap_to_boundary(run) == len(run)


# ─── 3 · 一处作者：标签与分页读同一颗规则 ────────────────────────

def test_the_summary_label_and_the_page_share_one_rule():
    """同一串文本，标签剪在哪里与分页剪在哪里必须是同一颗函数说了算。"""
    msg = ("这份文档讲 cookie 的存活时间，配置项叫 DHOLE_COOKIE_TTL_S，"
           "默认值是三千秒，可以被会话级参数覆盖掉。后面还有很多字，用来把它顶过帽。")
    cap = 40
    label = _summary_label(msg, cap=cap)
    at = snap_to_boundary(msg[:cap])
    assert label == msg[:at].rstrip(), (label, msg[:at])
    assert not label.endswith(("，", "。", " ", "：")), label[-4:]


def test_a_chinese_label_no_longer_ends_on_a_colon_or_a_dangling_open_paren():
    """标签那一族的实测形状（"…Valid values:"、"…can compile (S"）现在也管中文标点。"""
    label = _summary_label("有效值包括：markdown、text、article、html 与 structured "
                           "五种，其中 html 跳过阅读器直接交回源码正文。", cap=20)
    assert not label.endswith("："), label
    assert "、" not in label[-1:], label


def test_the_new_fields_have_a_single_producer_in_chunking():
    """三颗字段只在 `_apply_chunking` 里被写：别处再算一遍就是第二个作者。"""
    import inspect
    from dhole_mcp import server
    src = inspect.getsource(server)
    assert src.count('"pagination_stream"') == 1, src.count('"pagination_stream"')
    assert src.count('"chars_snapped_back"') == 1


def test_a_wire_row_keeps_its_pagination_truth_when_it_is_not_the_default():
    """压实机制按"等于自己的默认值"删字段：'full' 被删、'focused' 必须活下来。"""
    out = _chunked(_page(), focus="cookie 存活时间")
    dumped = wire._wire_dump(out)
    assert dumped["pagination_stream"] == "focused"
    assert dumped["page_total_chars"] == out.page_total_chars
    assert dumped["pagination_basis"]


@pytest.mark.parametrize("focus,expected_stream",
                         [("cookie 存活时间", "focused"), (None, "full")])
def test_the_stream_field_survives_every_chunking_branch(focus, expected_stream):
    """两条出口（翻过头 / 正常一片）都得带着这一颗：model_copy 漏一处就静默消失过。"""
    page = _page()
    big = _chunked(page, focus=focus, max_chars=4_000)
    assert big.pagination_stream == expected_stream
    past = _chunked(page, focus=focus, max_chars=60, offset=10 ** 6)
    assert past.pagination_stream == ("focused" if focus else "full")
