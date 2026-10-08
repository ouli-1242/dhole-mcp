"""续页与切片不许重付整份章节地图（2026-10-08 真实使用模拟 · 刀7）

现场（arXiv 2005.11401，19 页）：

    web_read(url=..., max_content_chars=500)            -> table_of_contents: 29 条
    web_read(url=..., offset=500)                       -> table_of_contents: 29 条（同一份）
    doc_parse(file_path=paper.pdf, pages="1-2")         -> table_of_contents: 29 条
                                                        （含 "H Retrieval Collapse, page 19"）

书签大纲读的是整本文档，`pages=` 只裁正文不裁大纲，`offset=` 每一片都重付一遍。按
`approx_tokens` 量，那 29 条约 1,000 token —— 而 `offset=` 恰恰是 `next_action` 自己
推荐的动作（"offset=500 to continue paginating"），读者为翻页付的每一页里十分之一是
一份用不上的地图。

无书签的那一路早就按提取范围构造大纲（`_heading_outline(..., page_nums, ...)`，注释
写着"Clamped to the extracted page range"）—— 有书签的这一路漏了，两条路的口径不一致。

契约（本文件钉住）：
  * `pages=` 时大纲只留与**实际交回的页**相交的条目；被裁掉要在 summary 里说一声
    （与"每一处钳制都要announce"同一纪律）。
  * 放不进任何页的条目（缺 `page`）宁可留着，不许悄悄丢一章。
  * `offset>0` 的续页不再重发大纲：它随第 0 片交过，省略要说明。
  * 第一片与不带 `pages=` 的整本读取一寸不变。
"""

from __future__ import annotations

import pymupdf
import pytest

from dhole_mcp import context
from dhole_mcp.models import ResponseModel
from dhole_mcp.pdf_extractor import extract_pdf
from dhole_mcp.server import _apply_chunking


def _booklet(pages: int = 6, toc=None) -> bytes:
    doc = pymupdf.open()
    for i in range(1, pages + 1):
        page = doc.new_page()
        page.insert_text((72, 90), f"Section {i} body. " * 30)
    doc.set_toc(toc if toc is not None else [
        [1, "Chapter one", 1], [1, "Chapter two", 3], [1, "Chapter three", 5]])
    out = doc.tobytes()
    doc.close()
    return out


def _titles(result):
    return [e["title"] for e in result.table_of_contents]


class TestASlicedPdfCarriesOnlyItsOwnMap:

    def test_the_whole_document_keeps_the_whole_map(self):
        """反向锁：不带 `pages=` 时一寸不许少。"""
        assert _titles(extract_pdf(_booklet())) == ["Chapter one", "Chapter two",
                                                    "Chapter three"]

    def test_pages_narrows_the_outline_to_the_pages_returned(self):
        got = _titles(extract_pdf(_booklet(), pages="1-2"))
        assert got == ["Chapter one"], got
        assert "Chapter three" not in got, "第 5 章不在交回的页里"

    def test_a_range_spanning_two_chapters_keeps_both(self):
        assert _titles(extract_pdf(_booklet(), pages="2-4")) == [
            "Chapter one", "Chapter two"]

    def test_the_narrowing_is_announced(self):
        with context._arg_notes_scope() as notes:
            extract_pdf(_booklet(), pages="1-2")
        assert any("table_of_contents" in n for n in notes), (
            f"裁了大纲却没说：{notes}")

    def test_an_entry_that_cannot_be_placed_is_kept_not_dropped(self):
        """放不进任何页的条目宁可多留一条，不许悄悄丢一章。"""
        odd = [[1, "No page", 0], [1, "Chapter two", 3]]
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("dhole_mcp.pdf_extractor._extract_toc",
                       lambda body: [{"title": t, "page": p, "end_page": p}
                                     for _lvl, t, p in odd])
            got = _titles(extract_pdf(_booklet(), pages="1-2"))
        assert "No page" in got, got


class TestAContinuationPageDoesNotRepayIt:

    def _result(self, toc):
        body = "x" * 2000
        return ResponseModel(url="https://docs.test/p.pdf", status=200,
                             content=[body], fetcher_used="http",
                             extracted_type="markdown", content_ok=True,
                             total_extracted_chars=2000,
                             table_of_contents=toc)

    def test_offset_zero_still_carries_the_map(self):
        out = _apply_chunking(self._result([{"title": "A", "page": 1}]),
                              max_chars=500, offset=0)
        assert len(out.table_of_contents) == 1

    def test_a_continuation_slice_says_why_the_map_is_absent(self):
        toc = [{"title": "A", "page": 1}, {"title": "B", "page": 9}]
        with context._arg_notes_scope() as notes:
            out = _apply_chunking(self._result(toc), max_chars=500, offset=500)
        assert out.table_of_contents == [], "续页又付了一遍整份地图"
        assert any("table_of_contents" in n for n in notes), (
            f"省略了要说明，不许静默缺席：{notes}")

    def test_a_pdf_with_no_map_at_all_stays_silent(self):
        """没有地图就没什么可宣布的。"""
        with context._arg_notes_scope() as notes:
            _apply_chunking(self._result([]), max_chars=500, offset=500)
        assert not any("table_of_contents" in n for n in notes), notes
