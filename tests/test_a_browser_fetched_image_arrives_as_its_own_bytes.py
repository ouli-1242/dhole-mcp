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

**D-41 把这一族里 PDF 那一支的假设打回去了**，所以这一文件的假对象重写过。修之前的
`_FakeResponse.body()` 对 PDF 交回的是真 PDF 字节 —— 那是我脑子里的 Chrome，不是量到的
Chrome。真机实测（2026-10-09，pid 7280，`rfc2616.pdf` 走 stealthy 档）：

    content-type : application/pdf          <-- 头是真的
    resp.body()  : 601 字节，`<!doctype html>` 起头   <-- 网络那一层已经被换成查看器文档
    page.content(): 271 字节的空壳（PDF 画在插件里，DOM 里没有）
    同一 URL 走 page.context.request.get(..., max_redirects=0)
                 : 200 / 550,558 字节 / `%PDF-1.1`    <-- 原件在这一层

所以"取原件"从一步变成两层，且第二层只问 `resp.url` 那一个地址、不跟跳转。这里的假
对象按这四行实测数字建模，真 Chrome 的那一发在 `tests/e2e_browser_tier_reads_a_pdf.py`。
"""
from __future__ import annotations

import io

import pytest

from dhole_mcp.extractor import (_is_data_document_content_type,
                                 _is_framed_document_content_type)
from dhole_mcp.fetcher import (Response, looks_like_rendered_shell,
                               response_from_browser_page)
from dhole_mcp.server import _ocr_failure_note, _translate_response_body

VIEWER_SHELL = ('<html><head><meta name="color-scheme" content="light dark">'
                '</head><body><div class="shadow"><img src="blob:https://x.test/1">'
                '</div></body></html>')
#: Chrome 给 PDF 的那一份，开头 80 字节是 2026-10-09 实测抄下来的（共 601 字节）。
PDF_VIEWER_SHELL = (b'<!doctype html>\n<html style="height:100%;width:100%">\n'
                    b'  <!-- the placeholder in the body style is for an optional '
                    b'background-color -->\n  <head></head>\n  <body style='
                    b'"height:100%;width:100%;overflow:hidden;margin:0;padding:0;'
                    b'">\n    \n  \n\n</body></html>\n')
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


class _FakeChannel:
    """`page.context.request` —— 这个浏览器上下文自己的那条 HTTP 通道。

    它记下单子（URL 与 kwargs），因为"只问 `resp.url` 那一个地址、且不跟跳转"是
    安全边际，不是实现细节：CDP 那只主机围栏只看 Document 请求，这一发它看不见。
    """

    def __init__(self, body: bytes, *, status: int = 200, raises: bool = False) -> None:
        self.calls: list[dict] = []
        self._body = body
        self._status = status
        self._raises = raises

    async def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self._raises:
            raise RuntimeError("request channel unavailable")
        status, body = self._status, self._body

        class _Api:
            pass
        api = _Api()
        api.status = status
        api.url = url
        api.headers = {}

        async def _body() -> bytes:
            return body
        api.body = _body
        return api


class _FakePage:
    def __init__(self, content: str, url: str = "https://img.test/pic",
                 channel: _FakeChannel | None = None) -> None:
        self._content = content
        self.url = url

        class _Context:
            request = channel

            async def cookies(self):
                return []
        self.context = _Context()

    async def content(self) -> str:
        return self._content

    async def wait_for_timeout(self, _ms: int) -> None:
        return None


class _FakeResponse:
    """导航的那份响应：头是真的，正文按 D-41 的实测给 —— PDF 那一支就是相框。"""

    def __init__(self, content_type: str, body: bytes, *, raises: bool = False,
                 url: str = "https://img.test/pic") -> None:
        self.status = 200
        self.url = url
        self._headers = {"content-type": content_type}
        self._body = body
        self._raises = raises

    async def all_headers(self):
        return dict(self._headers)

    async def body(self) -> bytes:
        if self._raises:
            raise RuntimeError("body is not available after the page closed")
        return self._body


async def _build_tracked(content_type: str, *, rendered=VIEWER_SHELL, network=PNG,
                         channel=PNG, channel_status: int = 200,
                         raises: bool = False, channel_raises: bool = False,
                         url: str = "https://img.test/pic"):
    chan = _FakeChannel(channel, status=channel_status, raises=channel_raises)
    resp = _FakeResponse(content_type, network, raises=raises, url=url)
    built = await response_from_browser_page(_FakePage(rendered, url, chan), None, resp)
    return built, chan


async def _build(content_type: str, **kwargs) -> Response:
    built, _chan = await _build_tracked(content_type, **kwargs)
    return built


class TestTheBrowserTierHandsBackThePicture:

    @pytest.mark.asyncio
    async def test_a_png_comes_back_as_its_own_bytes(self):
        got, chan = await _build_tracked("image/png")
        assert got.body == PNG, "交出去的还是 Chrome 的图片查看器"
        assert got.body[:8] == b"\x89PNG\r\n\x1a\n"
        assert b"shadow" not in got.body
        assert chan.calls == [], "网络那一层已经给到原件，不该再发第二发"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ct", ["image/jpeg", "image/webp", "image/gif"],
                             ids=["jpeg", "webp", "gif"])
    async def test_the_raster_family_gets_the_same_treatment(self, ct):
        got, chan = await _build_tracked(ct)
        assert got.body == PNG, ct
        assert chan.calls == []

    @pytest.mark.asyncio
    async def test_a_pdf_is_picked_up_from_the_request_channel(self):
        """D-41 的那一发：网络那一层交回的是查看器 HTML，原件在上下文自己的通道上。

        假对象照 2026-10-09 的实测建模：`resp.body()` → 601 字节 `<!doctype html>`，
        `context.request.get(resp.url, max_redirects=0)` → 550,558 字节 `%PDF-1.1`。
        """
        raw = _pdf_with_text()
        got, chan = await _build_tracked("application/pdf",
                                         rendered='<!DOCTYPE html><html></html>',
                                         network=PDF_VIEWER_SHELL, channel=raw)
        assert got.body[:5] == b"%PDF-", "交出去的还是查看器，不是那份 PDF"
        assert got.body == raw
        assert len(chan.calls) == 1
        assert chan.calls[0]["max_redirects"] == 0, "跟跳转的那一发不经主机名册"

    @pytest.mark.asyncio
    async def test_the_second_shot_asks_only_the_url_that_already_landed(self):
        """第二发不许换地址：它问的必须就是这次导航落地的那个 URL。"""
        raw = _pdf_with_text()
        _got, chan = await _build_tracked("application/pdf", network=PDF_VIEWER_SHELL,
                                          channel=raw, url="https://docs.test/a.pdf")
        assert [c["url"] for c in chan.calls] == ["https://docs.test/a.pdf"]

    @pytest.mark.asyncio
    async def test_a_pdf_the_channel_also_cannot_deliver_still_says_so(self):
        """站点真的在 .pdf 上回了一份 HTML：交回它说的那一份，不硬造原件。"""
        lie = b"<html><body>404 Not Found</body></html>"
        got, chan = await _build_tracked("application/pdf", network=PDF_VIEWER_SHELL,
                                         channel=lie)
        assert got.body == lie
        assert len(chan.calls) == 1

    @pytest.mark.asyncio
    async def test_a_redirect_on_the_second_shot_is_not_followed_into_the_body(self):
        """3xx 那一发不接正文：跟进去的下一跳没经过名册，也不经过 SSRF 终检。"""
        got, chan = await _build_tracked("application/pdf", network=PDF_VIEWER_SHELL,
                                         channel=b"%PDF-1.4 hidden behind a 301",
                                         channel_status=301)
        assert got.body == PDF_VIEWER_SHELL, got.body[:40]
        assert len(chan.calls) == 1

    @pytest.mark.asyncio
    async def test_a_body_that_cannot_be_read_asks_the_channel(self):
        raw = _pdf_with_text()
        got, chan = await _build_tracked("application/pdf", raises=True, channel=raw)
        assert got.body == raw
        assert len(chan.calls) == 1

    @pytest.mark.asyncio
    async def test_when_both_layers_fail_the_rendered_page_is_still_handed_over(self):
        """两层都空手时不许交空：老那半句承诺（回落 DOM）仍然成立。"""
        got, chan = await _build_tracked("image/png", raises=True, channel_raises=True)
        assert got.body.decode() == VIEWER_SHELL

    @pytest.mark.asyncio
    async def test_an_svg_keeps_its_rendered_tree(self):
        """SVG 是表外那一支：它的标记就是它的内容，渲染出来的树是正当读法。"""
        got, chan = await _build_tracked("image/svg+xml", rendered=HTML_DOC,
                                         network=SVG_MARKUP)
        assert got.body.decode() == HTML_DOC
        assert chan.calls == []

    @pytest.mark.asyncio
    async def test_a_real_page_keeps_its_rendered_dom(self):
        """反向锁：这条只针对"浏览器只会套框"的那一类 —— HTML 正是要渲染的。"""
        got, chan = await _build_tracked("text/html; charset=utf-8", rendered=HTML_DOC,
                                         network=b"<html><body><h1>stale</h1></body></html>")
        assert got.body.decode() == HTML_DOC
        assert chan.calls == []

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
        assert looks_like_rendered_shell(VIEWER_SHELL.encode())
        assert looks_like_rendered_shell(b"  \n<!DOCTYPE html><html>")
        assert looks_like_rendered_shell(PDF_VIEWER_SHELL), "PDF 那一层也要被认出来"
        assert not looks_like_rendered_shell(PNG)
        assert not looks_like_rendered_shell(b"")
        assert not looks_like_rendered_shell(_pdf_with_text("x"))



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
