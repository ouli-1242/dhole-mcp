"""A PDF response must say how much of the file it read.

R03-027 / R04-002 (live, four rounds): the body said "pages 1-2 of 3" while the
envelope had no `pages_total` and no `pages_returned` — the one number a caller
needs to decide whether to ask for the rest was only available by parsing prose.

The extractor already knows both (``PdfResult.pages_total`` /
``PdfResult.pages_extracted``); the two doors simply dropped them. These tests
pin both doors to the same source, and pin the non-PDF absence (0/0, omitted
from the wire) so a text file does not start claiming a page count.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from dhole_mcp.server import MasterFetchServer, _extract_pdf_response

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
