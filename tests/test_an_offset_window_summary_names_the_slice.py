"""summary 的那颗字数必须说清它是**这一窗**，不是整页（E3 / R01-011）。

现场（round-01 R01-011）：`web_read max_content_chars=800 offset=500` 交回一窗
正文，summary 却写着整页的 "7.0KB markdown"。数字本身没错，错的是它回答的问句：
调用方扫这一行是在问"这一发给了我多少、还差多少"，而那一格报的是页面总量 ——
据它判断"拿全了"，下一发就不再续读。

`total_extracted_chars` 的语义不动：它一直是"这一路正文共多长"（续读的坐标），
报告与分页字段都靠它。变的是 summary：窗口比总量短时，写成 "窗口 of 总量"。
"""

from __future__ import annotations

from dhole_mcp.context import _FOCUS
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _format_size
from dhole_mcp.server import _apply_chunking

PAGE_CHARS = 6_000


def _page(chars: int = PAGE_CHARS) -> ResponseModel:
    return ResponseModel(url="https://e.test/long", status=200,
                         content=["word " * (chars // 5)], extracted_type="markdown")


def _chunked(page: ResponseModel, *, max_chars: int = 800, offset: int = 0,
             focus: str | None = None) -> ResponseModel:
    token = _FOCUS.set(focus)
    try:
        return _apply_chunking(page.model_copy(), max_chars=max_chars, offset=offset)
    finally:
        _FOCUS.reset(token)


def _window_of_total(out: ResponseModel) -> str:
    window = sum(len(c) for c in out.content)
    return (f"{_format_size(window)} of "
            f"{_format_size(out.total_extracted_chars)} markdown")


def test_the_first_window_says_how_much_of_the_page_it_is():
    out = _chunked(_page())
    assert out.is_truncated, out.summary
    assert _window_of_total(out) in out.summary, out.summary


def test_the_last_window_says_it_is_a_slice_even_though_nothing_is_truncated():
    """尾巴那一窗 `is_truncated=False`，但正文仍只是整页的一角 ——
    这一支此前最容易骗人：没有 truncated 字样，summary 又报整页大小。"""
    out = _chunked(_page(), offset=PAGE_CHARS - 300)
    assert not out.is_truncated, out.summary
    assert out.total_extracted_chars == PAGE_CHARS
    assert _window_of_total(out) in out.summary, out.summary


def test_a_whole_page_still_reports_one_number():
    out = _chunked(_page(), max_chars=10_000)
    assert not out.is_truncated
    window = len(out.content[0])
    assert f"{_format_size(window)} of " not in out.summary, out.summary
    assert f"{_format_size(window)} markdown" in out.summary, out.summary


def test_the_total_still_counts_the_whole_stream():
    """本片标签不许动续读坐标：`total_extracted_chars` 仍是整条路线的长度。"""
    out = _chunked(_page())
    assert out.total_extracted_chars == PAGE_CHARS


def test_the_no_more_content_marker_is_not_counted_as_a_window():
    """`[No more content.]` 是替身不是正文：18 个字的标记不许被说成
    "18B of 6.0KB"。那一支保留原来的整页长度。"""
    out = _chunked(_page(), offset=10 ** 6)
    assert out.content == ["[No more content.]"], out.content
    assert " of " not in out.summary, out.summary


def test_a_focused_window_measures_the_focused_stream():
    """focus 之后总量本来就是"过滤后那一路"；本片标签照同一把尺子量。"""
    page = ResponseModel(
        url="https://e.test/focus", status=200, extracted_type="markdown",
        content=["cookie lifetime is three thousand seconds. " * 60
                 + "\n\n" + "fonts fallback and layout details. " * 60])
    out = _chunked(page, max_chars=400, focus="cookie lifetime")
    assert out.pagination_stream == "focused", out.pagination_stream
    assert _window_of_total(out) in out.summary, out.summary
