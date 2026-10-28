"""A PDF that yielded no words is a failure on both doors.

Live evidence, 2026-10-28: `doc_parse` on an image-only PDF returned
`content_ok=true`, `quality_score=0.9` and the body
"[No text detected on this page.]", while `web_read` called the same bytes
`pdf_no_text`. Two doors, two verdicts, both green on the wrong one.

The shared predicate (`quality.pdf_text_is_absent`) strips the extractor's own
envelope (OCR header, page separators, fallback note) so a mixed document —
some pages with a text layer — still counts as text.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dhole_mcp.models import ResponseModel
from dhole_mcp import pdf_extractor

pytestmark = pytest.mark.smoke
from dhole_mcp.quality import _detect_content_issue, pdf_text_is_absent

PLACEHOLDER = "[No text detected on this page.]"
ENVELOPE = ("> OCR-extracted from scanned PDF · 1 pages\n\n"
            "--- Page 1 ---\n\n" + PLACEHOLDER + "\n\n"
            "> OCR fallback used (scanned or CID-garbled pages)")


class TestTheSharedPredicate:

    def test_placeholder_and_envelope_only_means_absent(self):
        assert pdf_text_is_absent([PLACEHOLDER]) is True
        assert pdf_text_is_absent([ENVELOPE]) is True
        assert pdf_text_is_absent([]) is True

    def test_one_real_page_is_enough_to_be_text(self):
        mixed = ENVELOPE + "\n\n--- Page 2 ---\n\nA real sentence from page two."
        assert pdf_text_is_absent([mixed]) is False

    def test_web_read_flags_the_same_shape(self):
        blank = ResponseModel(
            status=200, content=[ENVELOPE], url="https://example.test/scan.pdf",
            content_type="application/pdf", fetcher_used="http")
        assert _detect_content_issue(blank).startswith("pdf_no_text")

    def test_web_read_does_not_flag_a_mixed_document(self):
        mixed = ResponseModel(
            status=200,
            content=[ENVELOPE + "\n\n--- Page 2 ---\n\nA real sentence from page two."],
            url="https://example.test/mixed.pdf",
            content_type="application/pdf", fetcher_used="http")
        assert _detect_content_issue(mixed) == ""


@pytest.fixture
def fake_scanned(monkeypatch):
    """A scanned PDF whose OCR ran and recognised nothing."""

    def fake_extract(body, extraction_type="markdown", pages=None, password=None):
        return SimpleNamespace(
            content=[ENVELOPE], content_ok=True, error="",
            quality_score=0.9, table_of_contents=[], metadata={},
            pages_total=1, pages_extracted=[1], ocr_fallback_used=True)

    def fake_recover(result, body, pages=None, password=None):
        return result

    monkeypatch.setattr(pdf_extractor, "extract_pdf", fake_extract)
    monkeypatch.setattr(pdf_extractor, "recover_scanned_pdf", fake_recover)


@pytest.mark.asyncio
async def test_doc_parse_refuses_a_scanned_pdf_instead_of_a_green_light(
        fake_scanned, tmp_path):
    from dhole_mcp.server import MasterFetchServer

    scan = tmp_path / "scan.pdf"
    scan.write_bytes(b"%PDF-1.4 fake bytes; extraction is stubbed")

    result = await MasterFetchServer().doc_parse(file_path=str(scan))

    assert result.error.startswith("pdf_no_text:"), result.error
    assert result.content_ok is False
    assert result.content == []
    assert result.quality_score is None, (
        "a score of 0.9 for zero words is the false green light this fix removes")
    assert "images" in (result.next_action or ""), result.next_action
