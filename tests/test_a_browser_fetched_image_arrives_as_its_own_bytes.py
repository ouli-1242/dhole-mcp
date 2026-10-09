# -*- coding: utf-8 -*-
"""浏览器档打一张图片，交出去的必须是那张图自己，不是 Chrome 给它的框（D-27）

现场（2026-10-09，连着的那台服务器真机调用，修之前）：

    web_read(url="https://httpbin.org/image/png", force_fetcher="stealthy")
      status        : 200
      content_type  : image/png              <-- 头是真的（来自网络响应）
      total_size    : 403                    <-- 而这 403 字节是 Chrome 的查看器文档
      error         : image_ocr_failed: cannot identify image file an in-memory
                      buffer - the browser tier FRAMES an image document in its
                      own viewer, so OCR was handed 403 bytes of markup…
      next_action   : no text could be read from this image - …

    两件事叠在一起：`response_from_browser_page` 把 `.body` 定成 `page.content()`
    （渲染后的 DOM），而 D-21 那道"取原件"的判据只认数据文档（JSON/XML），图片与 PDF
    不在射程里。于是 OCR 收到一份 HTML，报"认不出这张图"，出路写着
    `force_fetcher='http'` —— 那一发确实能救，但它是让调用方绕过浏览器档去拿本来就在
    这次导航里的字节。同一发里 `next_action` 还在说"这张图里读不出字"，而 `error` 说
    "交给 OCR 的是一张相框"：两个读者，两件不同的事。

修法：那道判据有了更大的一张表（`extractor._is_framed_document_content_type` =
数据文档 + 光栅图片 + PDF），浏览器档取原件那一步读它；OCR 那句"相框"改由**字节自己**
决定说不说（原件取回来之后，这句话就不再是档位属性，而是这一次导航没取到原件的属性）。

SVG 故意留在表外：一个 SVG 就是它的标记，Chrome 渲染出来的那棵树是它的正当读法。
"""
from __future__ import annotations

import io

import pytest

from dhole_mcp.extractor import (_is_data_document_content_type,
                                 _is_framed_document_content_type)
from dhole_mcp.fetcher import Response, response_from_browser_page
from dhole_mcp.server import (_looks_like_document_shell, _ocr_failure_note,
                              _translate_response_body)

VIEWER_SHELL = ('<html><head><meta name="color-scheme" content="light dark">'
                '</head><body><div class="shadow"><img src="blob:https://x.test/1">'
                '</div></body></html>')
SVG_MARKUP = b'<svg xmlns="http://www.w3.org/2000/svg"><text>RAW SVG</text></svg>'
HTML_DOC = "<html><body><h1>a real page</h1></body></html>"


def _png_with_text(text: str = "DHOLE ORIGINAL 27") -> bytes:
    """一张真会读出字的 PNG（判据量的是"OCR 收到的是不是图"，不是引擎的眼力）。"""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (640, 170), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=64)
    except TypeError:                       # Pillow < 10.1
        font = ImageFont.load_default()
    draw.text((24, 60), text, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


PNG = _png_with_text()


def _pdf_with_text(text: str = "Dhole original pdf marker 27") -> bytes:
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 120), text, fontsize=18)
    out = doc.tobytes()
    doc.close()
    return out


class _FakePage:
    def __init__(self, content: str, url: str = "https://img.test/pic") -> None:
        self._content = content
        self.url = url

    async def content(self) -> str:
        return self._content

    async def wait_for_timeout(self, _ms: int) -> None:
        return None

    class _Context:
        async def cookies(self):
            return []

    context = _Context()


class _FakeResponse:
    """Playwright's navigation response: headers AND the untouched network body."""

    def __init__(self, content_type: str, body: bytes, *, raises: bool = False) -> None:
        self.status = 200
        self.url = "https://img.test/pic"
        self._headers = {"content-type": content_type}
        self._body = body
        self._raises = raises

    async def all_headers(self):
        return dict(self._headers)

    async def body(self) -> bytes:
        if self._raises:
            raise RuntimeError("body is not available after the page closed")
        return self._body


async def _build(content_type: str, *, rendered=VIEWER_SHELL, network=PNG,
                 raises: bool = False) -> Response:
    resp = _FakeResponse(content_type, network, raises=raises)
    return await response_from_browser_page(_FakePage(rendered), None, resp)


class TestTheBrowserTierHandsBackThePicture:

    @pytest.mark.asyncio
    async def test_a_png_comes_back_as_its_own_bytes(self):
        got = await _build("image/png")
        assert got.body == PNG, "交出去的还是 Chrome 的图片查看器"
        assert got.body[:8] == b"\x89PNG\r\n\x1a\n"
        assert b"shadow" not in got.body

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ct", ["image/jpeg", "image/webp", "image/gif"],
                             ids=["jpeg", "webp", "gif"])
    async def test_the_raster_family_gets_the_same_treatment(self, ct):
        got = await _build(ct)
        assert got.body == PNG, ct

    @pytest.mark.asyncio
    async def test_a_pdf_comes_back_as_its_own_bytes(self):
        """PDF 走同一处：内建查看器把文档画进 <embed>，DOM 里没有正文。"""
        raw = _pdf_with_text()
        got = await _build("application/pdf", network=raw)
        assert got.body[:5] == b"%PDF-", "交出去的还是查看器，不是那份 PDF"
        assert got.body == raw

    @pytest.mark.asyncio
    async def test_an_svg_keeps_its_rendered_tree(self):
        """SVG 是表外那一支：它的标记就是它的内容，渲染出来的树是正当读法。"""
        got = await _build("image/svg+xml", rendered=HTML_DOC, network=SVG_MARKUP)
        assert got.body.decode() == HTML_DOC

    @pytest.mark.asyncio
    async def test_a_real_page_keeps_its_rendered_dom(self):
        """反向锁：这条只针对"浏览器只会套框"的那一类 —— HTML 正是要渲染的。"""
        got = await _build("text/html; charset=utf-8", rendered=HTML_DOC,
                           network=b"<html><body><h1>stale</h1></body></html>")
        assert got.body.decode() == HTML_DOC

    @pytest.mark.asyncio
    async def test_a_body_that_cannot_be_read_falls_back_to_the_rendered_page(self):
        got = await _build("image/png", raises=True)
        assert got.body.decode() == VIEWER_SHELL

    def test_the_new_table_is_the_old_one_plus_the_framed_types(self):
        """一张表扩一张表，不另起一份清单：数据文档那一族必须仍然在册。"""
        for ct in ("application/json", "text/xml", "application/rss+xml"):
            assert _is_data_document_content_type(ct), ct
            assert _is_framed_document_content_type(ct), ct
        assert _is_framed_document_content_type("image/png")
        assert _is_framed_document_content_type("application/pdf")
        assert not _is_framed_document_content_type("image/svg+xml")
        assert not _is_framed_document_content_type("text/html")
        assert not _is_framed_document_content_type("")


class TestTheOcrNoteDescribesTheBytesItGot:
    """修完之后，"这是相框"不再是档位的属性，而是这一次真把框喂进去了才说的话。"""

    def test_a_shell_handed_to_ocr_is_still_named_as_a_shell(self):
        note = _ocr_failure_note(RuntimeError("cannot identify image file"),
                                 "stealthy", 403, VIEWER_SHELL.encode())
        assert "rendered document, not the picture" in note
        assert "force_fetcher='http'" in note
        assert "could not hand back the original bytes" in note

    def test_the_picture_itself_is_never_described_as_a_frame(self):
        """这一条是 D-27 的承诺：原件到手就别再宣称那是框。"""
        note = _ocr_failure_note(RuntimeError("bad image data"), "stealthy",
                                 len(PNG), PNG)
        assert note == "image_ocr_failed: bad image data", note
        assert "force_fetcher" not in note

    def test_the_http_tier_says_nothing_about_a_browser(self):
        note = _ocr_failure_note(RuntimeError("bad image data"), "http", 10, PNG)
        assert note == "image_ocr_failed: bad image data"

    def test_the_process_internals_stay_out_of_both_branches(self):
        """D-13b 那半句性质（内存地址不进回执）在两支上都不许退。"""
        exc = RuntimeError("cannot identify image file "
                           "<_io.BytesIO object at 0x0000017559546C50>")
        for handed, tier in ((VIEWER_SHELL.encode(), "stealthy"), (PNG, "stealthy")):
            note = _ocr_failure_note(exc, tier, 12, handed)
            assert "0x" not in note, note
            assert "an in-memory buffer" in note, note

    def test_the_shell_sniff_answers_about_bytes_not_about_tiers(self):
        assert _looks_like_document_shell(VIEWER_SHELL.encode())
        assert _looks_like_document_shell(b"  \n<!DOCTYPE html><html>")
        assert not _looks_like_document_shell(PNG)
        assert not _looks_like_document_shell(b"")
        assert not _looks_like_document_shell(_pdf_with_text("x"))


class TestThePictureReachesTheReader:
    """整条通道：浏览器档交回的 Response 走 _translate_response_body 之后是什么样。"""

    @staticmethod
    def _page(body: bytes, content_type: str) -> Response:
        return Response(url="https://img.test/pic", body=body, status=200,
                        headers={"content-type": content_type}, encoding="utf-8")

    def test_a_png_measures_its_own_bytes(self):
        result = _translate_response_body(self._page(PNG, "image/png"),
                                          "markdown", None, fetcher_used="stealthy")
        assert result.total_size_bytes == len(PNG)
        assert result.content_type == "image/png"
        # 无论 OCR 读出没读出，都不许再说"交给你的是一张框"。
        assert "rendered document" not in (result.error or ""), result.error
        assert "FRAMES" not in (result.error or "")

    def test_the_ocr_actually_reads_the_picture(self):
        from dhole_mcp.ocr import ocr_available
        if not ocr_available():
            pytest.skip("OCR extras not installed on this machine")
        result = _translate_response_body(self._page(PNG, "image/png"),
                                          "markdown", None, fetcher_used="stealthy")
        text = " ".join(result.content)
        assert "DHOLE" in text.upper(), (result.error, result.content, text[:200])
        assert result.extracted_type == "text"
        assert not result.error

    def test_a_pdf_handed_the_original_extracts_its_text(self):
        raw = _pdf_with_text("Dhole original pdf marker 27")
        result = _translate_response_body(self._page(raw, "application/pdf"),
                                          "markdown", None, fetcher_used="stealthy")
        text = " ".join(result.content)
        assert "pdf marker 27" in text.replace("\n", " "), (result.error, text[:200])
        assert not result.error
