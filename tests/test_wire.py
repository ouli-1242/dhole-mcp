"""test_wire.py —— 由 2 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""


from __future__ import annotations


import asyncio
import pytest
from pathlib import Path

from dhole_mcp.models import BulkResponseModel, ResponseModel
from dhole_mcp.server import MasterFetchServer, _extract_pdf_response
from dhole_mcp.wire import _WIRE_OMIT

pytestmark = pytest.mark.smoke


# ============================================================================
# 来源：test_two_counts_in_a_batch_envelope_say_two_things.py
# ============================================================================
# R01-031 · 一个信封里两个数，就得说清各自数的是什么。

def _row(url: str, *, status: int = 200, content_ok: bool = True,
         error: str = "") -> ResponseModel:
    return ResponseModel(url=url, status=status, content_ok=content_ok,
                         content=["text"] if content_ok else [],
                         fetcher_used="http", error=error)


def _dump(**kw):
    return BulkResponseModel(**kw).model_dump(mode="json")


def test_the_second_count_is_derived_from_the_rows_not_from_the_caller():
    rows = [_row("http://a/", content_ok=True),
            _row("http://b/", content_ok=False),          # 200 但是登录墙
            _row("http://c/", status=404, content_ok=False, error="http_error_404: x"),
            _row("http://d/", content_ok=False)]          # 正文被截成空
    data = _dump(results=rows, total=4, successful=3)
    assert data["successful"] == 3, "第一颗数不许被顺手动掉（它有自己的定义）"
    assert data["usable"] == 1, data["usable"]


def test_the_envelope_says_which_question_each_number_answers():
    rows = [_row("http://a/"), _row("http://b/", content_ok=False),
            _row("http://c/", content_ok=False), _row("http://d/", content_ok=False)]
    text = _dump(results=rows, total=4, successful=3)["summary"]
    assert "successful" in text and "content_ok" in text, text
    assert "3" in text and "1" in text, text
    assert "different questions" in text, text


def test_a_lying_usable_value_is_overwritten_by_the_rows():
    """两颗数只能有一个作者：行。调用方（或将来某条路径）填错了也必须被纠正。"""
    rows = [_row("http://a/"), _row("http://b/", content_ok=False)]
    data = _dump(results=rows, total=2, successful=2, usable=99)
    assert data["usable"] == 1


def test_every_row_usable_still_reports_both_counts():
    rows = [_row("http://a/"), _row("http://b/")]
    data = _dump(results=rows, total=2, successful=2)
    assert data["usable"] == 2
    assert "2" in data["summary"]


def test_a_refused_batch_says_zero_usable():
    """批量与 attach 不兼容那条路（R01-031 的兄弟现场）：交回的是拒绝行，
    一颗可用的都没有 —— 这一格必须是 0，而不是缺席。"""
    rows = [_row("http://a/", content_ok=False, error="invalid_request: x")]
    data = _dump(results=rows, total=1, successful=0)
    assert data["usable"] == 0 and data["successful"] == 0, data


def test_both_fields_are_declared_and_never_trimmed_off_the_wire():
    """`响应内无定义` 的另一半修复在 schema：两颗数都得有说明，
    而且不许被上线裁剪当成默认值省掉（那正是"看不见"的来源）。"""
    fields = BulkResponseModel.model_fields
    assert "content_ok" in fields["usable"].description
    assert "status<400" in fields["successful"].description
    omit = _WIRE_OMIT.get("BulkResponseModel") or frozenset()
    assert "usable" not in omit and "summary" not in omit, omit


# ============================================================================
# 来源：test_a_pdf_reports_how_many_pages_it_has_and_returned.py
# ============================================================================
# A PDF response must say how much of the file it read.

HERE = Path(__file__).resolve().parent
THREE_PAGE_PDF = HERE / "wrapped_heading.pdf"


def _pdf_response(pages: str | None = None):
    body = THREE_PAGE_PDF.read_bytes()
    if pages is not None:
        from dhole_mcp import context

        token = context._PDF_PAGES.set(pages)
        try:
            return _extract_pdf_response(
                body, "application/pdf", len(body), "https://docs.test/paper.pdf",
                "markdown", "http", 1.0)
        finally:
            context._PDF_PAGES.reset(token)
    return _extract_pdf_response(
        body, "application/pdf", len(body), "https://docs.test/paper.pdf",
        "markdown", "http", 1.0)


def test_a_whole_pdf_reports_its_page_count():
    out = _pdf_response()

    assert out.pages_total == 3, out.pages_total
    assert out.pages_returned == 3, out.pages_returned


def test_a_page_range_reports_what_was_extracted_not_the_whole_file():
    out = _pdf_response("1-2")

    assert out.pages_total == 3
    assert out.pages_returned == 2


def test_doc_parse_carries_the_same_two_numbers():
    out = asyncio.run(MasterFetchServer().doc_parse(
        str(THREE_PAGE_PDF), pages="1-2"))

    assert out.pages_total == 3, out.pages_total
    assert out.pages_returned == 2, out.pages_returned


def test_a_non_pdf_says_zero_on_both_fields(tmp_path):
    note = tmp_path / "note.txt"
    note.write_text("plain text, no pages", encoding="utf-8")

    out = asyncio.run(MasterFetchServer().doc_parse(str(note)))

    assert out.pages_total == 0
    assert out.pages_returned == 0


def test_the_two_fields_are_omitted_from_the_wire_at_their_defaults():
    """0/0 is not a measurement; a non-PDF response must not pay for it."""
    from dhole_mcp.models import ResponseModel
    from dhole_mcp.wire import _WIRE_OMIT

    assert {"pages_total", "pages_returned"} <= _WIRE_OMIT["ResponseModel"]
    plain = ResponseModel(status=200, url="https://x.test/", content=["x"])
    assert plain.pages_total == 0 and plain.pages_returned == 0
