"""test_extractor.py —— 由 3 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import io
import pytest
from dhole_mcp.extractor import _is_data_document_content_type, _is_framed_document_content_type
from dhole_mcp.fetcher import Response, looks_like_rendered_shell, response_from_browser_page
from dhole_mcp.server import _ocr_failure_note, _translate_response_body
from dhole_mcp.quality import _never_escalate
from dhole_mcp.extractor import extract_content


# ============================================================================
# 来源：test_a_browser_fetched_image_arrives_as_its_own_bytes.py
# ============================================================================
# 浏览器档打一张图片，交出去的必须是那张图自己，不是 Chrome 给它的框（D-27）

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


# ============================================================================
# 来源：test_a_browser_fetched_data_document_arrives_as_itself.py
# ============================================================================
# 浏览器档取回的数据文档，交出去的必须是它自己而不是 Chrome 的相框（2026-10-08 验收 · D-21）
# 重名消歧：HTML_DOC → HTML_DOC__a_browser_fetched_data_d_66df、VIEWER_SHELL → VIEWER_SHELL__a_browser_fetched_data_d_66df、_FakePage → _FakePage__a_browser_fetched_data_d_66df、_FakeResponse → _FakeResponse__a_browser_fetched_data_d_66df、_build → _build__a_browser_fetched_data_d_66df

VIEWER_SHELL__a_browser_fetched_data_d_66df = ('<html><head><meta name="color-scheme" content="light dark">'
                '</head><body><pre>{"why": "&lt;em&gt;WonderWidgets&lt;/em&gt;"}</pre>'
                '<div class="json-formatter-container"></div></body></html>')
PAYLOAD = b'{"why": "<em>WonderWidgets</em>"}'
HTML_DOC__a_browser_fetched_data_d_66df = "<html><body><h1>a real page</h1></body></html>"


class _FakePage__a_browser_fetched_data_d_66df:
    def __init__(self, content: str, url: str = "https://data.test/doc") -> None:
        self._content = content
        self.url = url

    async def content(self) -> str:
        return self._content

    async def wait_for_timeout(self, _ms: int) -> None:
        return None

    async def close(self) -> None:
        return None

    class _Context:
        async def cookies(self):
            return []

    context = _Context()


class _FakeResponse__a_browser_fetched_data_d_66df:
    """Playwright's navigation response: headers AND the untouched network body."""

    def __init__(self, content_type: str, body: bytes, *, raises: bool = False) -> None:
        self.status = 200
        self.url = "https://data.test/doc"
        self._headers = {"content-type": content_type}
        self._body = body
        self._raises = raises

    async def all_headers(self):
        return dict(self._headers)

    async def body(self) -> bytes:
        if self._raises:
            raise RuntimeError("body is not available after the page closed")
        return self._body


async def _build__a_browser_fetched_data_d_66df(content_type: str, *, rendered=VIEWER_SHELL__a_browser_fetched_data_d_66df, network=PAYLOAD,
                 raises: bool = False) -> Response:
    resp = _FakeResponse__a_browser_fetched_data_d_66df(content_type, network, raises=raises)
    return await response_from_browser_page(_FakePage__a_browser_fetched_data_d_66df(rendered), None, resp)


class TestTheBrowserTierHandsBackTheDocument:

    @pytest.mark.asyncio
    async def test_a_json_document_comes_back_as_its_own_bytes(self):
        got = await _build__a_browser_fetched_data_d_66df("application/json")
        assert got.body == PAYLOAD, "交出去的还是 Chrome 的查看器"
        assert b"&lt;" not in got.body, "实体转义过的正文不能再当 JSON 解析"

    @pytest.mark.asyncio
    async def test_an_xml_document_gets_the_same_treatment(self):
        sitemap = b'<?xml version="1.0"?><urlset><url>https://x.test/</url></urlset>'
        got = await _build__a_browser_fetched_data_d_66df("application/xml", network=sitemap)
        assert got.body == sitemap

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ct", [
        "text/json; charset=utf-8",
        "application/rss+xml",
        "application/atom+xml",
    ], ids=["charset", "rss", "atom"])
    async def test_the_family_is_the_one_the_escalation_gate_already_knew(self, ct):
        """两处读同一张表：拒绝升级认的那些，取原件也必须认。"""
        assert _is_data_document_content_type(ct), ct
        got = await _build__a_browser_fetched_data_d_66df(ct, network=PAYLOAD)
        assert got.body == PAYLOAD, ct

    @pytest.mark.asyncio
    async def test_a_real_page_keeps_its_rendered_dom(self):
        """反向锁：这条只针对数据文档 —— 渲染过的 HTML 正是浏览器档的意义。"""
        got = await _build__a_browser_fetched_data_d_66df("text/html; charset=utf-8", rendered=HTML_DOC__a_browser_fetched_data_d_66df,
                           network=b"<html><body><h1>stale</h1></body></html>")
        assert got.body.decode() == HTML_DOC__a_browser_fetched_data_d_66df

    @pytest.mark.asyncio
    async def test_an_html_document_wearing_xml_is_still_the_rendered_one(self):
        """application/xhtml+xml 是穿了 XML 外衣的 HTML：不许被当成数据文档。"""
        got = await _build__a_browser_fetched_data_d_66df("application/xhtml+xml", rendered=HTML_DOC__a_browser_fetched_data_d_66df,
                           network=b"<xml>no</xml>")
        assert got.body.decode() == HTML_DOC__a_browser_fetched_data_d_66df

    @pytest.mark.asyncio
    async def test_a_body_that_cannot_be_read_falls_back_to_the_rendered_page(self):
        """取不到原件时不能把正文交成空的：那比一个带框的正文更难解释。"""
        got = await _build__a_browser_fetched_data_d_66df("application/json", raises=True)
        assert got.body.decode() == VIEWER_SHELL__a_browser_fetched_data_d_66df

    @pytest.mark.asyncio
    async def test_headers_and_status_still_come_from_the_network_response(self):
        got = await _build__a_browser_fetched_data_d_66df("application/json; charset=utf-8")
        assert got.status == 200
        assert got.headers["content-type"].startswith("application/json")
        assert got.encoding == "utf-8"


class TestThePassthroughSaysWhatHappened:

    @staticmethod
    def _page(body: bytes, content_type: str) -> Response:
        return Response(url="https://data.test/doc", body=body, status=200,
                        headers={"content-type": content_type}, encoding="utf-8")

    def test_a_raw_json_body_is_not_reported_as_markdown(self):
        result = _translate_response_body(self._page(PAYLOAD, "application/json"), "markdown", None)
        assert result.content == [PAYLOAD.decode()], result.content
        assert result.extracted_type == "text", result.extracted_type

    def test_a_raw_xml_body_is_not_reported_as_markdown(self):
        sitemap = b'<?xml version="1.0"?><urlset><url>https://x.test/</url></urlset>'
        result = _translate_response_body(self._page(sitemap, "application/xml"), "markdown", None)
        assert result.content == [sitemap.decode()], result.content
        assert result.extracted_type == "text", result.extracted_type

    def test_the_value_is_one_the_field_documents(self):
        from dhole_mcp.server import _EXTRACTED_TYPES
        for ct, body in (("application/json", PAYLOAD), ("application/xml", b"<a/>")):
            result = _translate_response_body(self._page(body, ct), "markdown", None)
            assert result.extracted_type in _EXTRACTED_TYPES, result.extracted_type

    def test_a_real_page_still_comes_back_as_markdown(self):
        """反向锁：那条默认值属于 HTML 抽取这一族，搬走会把摘要写坏。"""
        result = _translate_response_body(
            self._page(HTML_DOC__a_browser_fetched_data_d_66df.encode(), "text/html"), "markdown", None)
        assert result.extracted_type == "markdown", result.extracted_type


class TestTheEscalationGateStillRefuses:
    """判据换了作者，行为不许变：为一份数据文档花一次浏览器渲染仍然是浪费。"""

    @pytest.mark.asyncio
    async def test_a_json_document_never_buys_a_browser_render(self):
        from dhole_mcp.context import _METHOD
        _METHOD.set("GET")
        got = await _build__a_browser_fetched_data_d_66df("application/json")
        model = _translate_response_body(got, "markdown", None, fetcher_used="http")
        model.content_type = "application/json"
        assert _never_escalate(model, "https://data.test/doc")

    @pytest.mark.asyncio
    async def test_a_real_page_can_still_escalate(self):
        from dhole_mcp.context import _METHOD
        _METHOD.set("GET")
        got = await _build__a_browser_fetched_data_d_66df("text/html", rendered=HTML_DOC__a_browser_fetched_data_d_66df, network=HTML_DOC__a_browser_fetched_data_d_66df.encode())
        model = _translate_response_body(got, "markdown", None, fetcher_used="http")
        model.content = ["a real page with a body long enough to be judged content"]
        model.content_type = "text/html"
        assert not _never_escalate(model, "https://data.test/doc")


# ============================================================================
# 来源：test_extractor.py
# ============================================================================
# ``dhole_mcp.extractor`` 冒烟测试。
# 重名消歧：_FakePage → _FakePage__extractor

_ARTICLE_HTML = """<!doctype html>
<html lang="en"><head><title>Test Article</title></head>
<body>
  <article>
    <h1>Understanding Tide Pools</h1>
    <p>Intertidal zones host remarkably resilient organisms. The
    purple sea urchin withstands hours of exposure each day.</p>
    <p>A second paragraph gives trafilatura enough body text to treat this
    as real article content rather than boilerplate navigation chrome.</p>
  </article>
</body></html>"""


class _FakePage__extractor:
    """Response 的最小替身：只提供提取链路会用到的属性。"""

    def __init__(self, html: str, url: str = "https://example.com/post") -> None:
        self.body = html.encode("utf-8")
        self.content = html
        self.html_content = html
        self.encoding = "utf-8"
        self.url = url
        self.headers: dict[str, str] = {"content-type": "text/html"}
        self.status = 200

    def css(self, selector: str) -> list:
        return []


class TestExtractContent:
    def test_returns_list_of_strings(self):
        result = extract_content(_FakePage__extractor(_ARTICLE_HTML), "text")

        assert isinstance(result, list)
        assert result
        assert all(isinstance(item, str) for item in result)

    def test_extracts_article_body_text(self):
        result = extract_content(_FakePage__extractor(_ARTICLE_HTML), "article")

        joined = "\n".join(result)
        assert "purple sea urchin" in joined

    def test_empty_body_does_not_raise(self):
        result = extract_content(_FakePage__extractor(""), "markdown")

        assert isinstance(result, list)

    def test_html_type_uses_fallback_path(self):
        """extraction_type='html' 不走 trafilatura，直接走兜底提取。"""
        result = extract_content(_FakePage__extractor("<p>hello</p>"), "html")

        assert isinstance(result, list)
        assert result

    def test_css_selector_is_accepted(self):
        result = extract_content(_FakePage__extractor(_ARTICLE_HTML), "text", css_selector="article")

        assert isinstance(result, list)
