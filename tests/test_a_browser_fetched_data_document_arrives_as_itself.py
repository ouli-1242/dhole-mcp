"""浏览器档取回的数据文档，交出去的必须是它自己而不是 Chrome 的相框（2026-10-08 验收 · D-21）

现场（真实调用，本机起的真浏览器）：

    web_read(url="https://httpbin.org/json", force_fetcher="stealthy")
      content_type : application/json          <-- 头来自网络响应，是真的
      content      : '<html><head><meta name="color-scheme" …><body><pre>{'
                     '  "slideshow": { … "Why &lt;em&gt;WonderWidgets&lt;/em&gt; are great'
      page_type    : json
      summary      : 200 OK · 616B markdown · stealthy

三层问题叠在一发里：

1. **正文不是那份文件**。`fetcher.response_from_browser_page` 把 `.body` 定成
   `page.content()` —— 渲染后的 DOM。Chromium 不给 JSON/XML 渲染页面，它给它们套
   一个自己的查看器，于是交出去的是相框；而且 `<em>` 已经被换成 `&lt;em&gt;`，
   拿它去 `json.loads` 直接失败。XML 那一族更贵：仓库里早就量过 428KB 的 sitemap
   被换成 2,335,300 个字符的 xml-viewer（`quality._never_escalate` 的 docstring）。
2. **`extracted_type` 说 markdown**。JSON/XML 那两条原样交回的支路从来没设过这个
   字段，于是它落在模型默认值 `markdown` 上，`summary` 跟着写 "429B markdown" ——
   一次转换都没发生过。（图片 OCR 那几条支路早就写 `text`，这里缺的只是同一处置。）
3. 判据"这是数据文档，浏览器不该去"原先只有 `quality._never_escalate` 一个读者写得
   出来（`_is_xml_content_type(ct) or ct.startswith(("application/json","text/json"))`），
   浏览器档这边没有读者 —— 所以自动升级躲开了相框，被 `force_fetcher` / `cdp_url`
   按到这一档时却躲不开。

修法：那张判据有了名字（`extractor._is_data_document_content_type`），两边共读；
`response_from_browser_page` 认它时改取**这次导航的网络响应原始字节**
（`await resp.body()`，Playwright 一直拿得到），拿不到才退回渲染 DOM。

真实复核（同一发 force_fetcher='stealthy' 的调用，修完之后）：429 个字符、
`json.loads` 通过、与 HTTP 档逐字节一致、`&lt;` 一个都不在。
"""

from __future__ import annotations

import pytest

from dhole_mcp.extractor import _is_data_document_content_type
from dhole_mcp.fetcher import Response, response_from_browser_page
from dhole_mcp.quality import _never_escalate
from dhole_mcp.server import _translate_response_body

VIEWER_SHELL = ('<html><head><meta name="color-scheme" content="light dark">'
                '</head><body><pre>{"why": "&lt;em&gt;WonderWidgets&lt;/em&gt;"}</pre>'
                '<div class="json-formatter-container"></div></body></html>')
PAYLOAD = b'{"why": "<em>WonderWidgets</em>"}'
HTML_DOC = "<html><body><h1>a real page</h1></body></html>"


class _FakePage:
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


class _FakeResponse:
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


async def _build(content_type: str, *, rendered=VIEWER_SHELL, network=PAYLOAD,
                 raises: bool = False) -> Response:
    resp = _FakeResponse(content_type, network, raises=raises)
    return await response_from_browser_page(_FakePage(rendered), None, resp)


class TestTheBrowserTierHandsBackTheDocument:

    @pytest.mark.asyncio
    async def test_a_json_document_comes_back_as_its_own_bytes(self):
        got = await _build("application/json")
        assert got.body == PAYLOAD, "交出去的还是 Chrome 的查看器"
        assert b"&lt;" not in got.body, "实体转义过的正文不能再当 JSON 解析"

    @pytest.mark.asyncio
    async def test_an_xml_document_gets_the_same_treatment(self):
        sitemap = b'<?xml version="1.0"?><urlset><url>https://x.test/</url></urlset>'
        got = await _build("application/xml", network=sitemap)
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
        got = await _build(ct, network=PAYLOAD)
        assert got.body == PAYLOAD, ct

    @pytest.mark.asyncio
    async def test_a_real_page_keeps_its_rendered_dom(self):
        """反向锁：这条只针对数据文档 —— 渲染过的 HTML 正是浏览器档的意义。"""
        got = await _build("text/html; charset=utf-8", rendered=HTML_DOC,
                           network=b"<html><body><h1>stale</h1></body></html>")
        assert got.body.decode() == HTML_DOC

    @pytest.mark.asyncio
    async def test_an_html_document_wearing_xml_is_still_the_rendered_one(self):
        """application/xhtml+xml 是穿了 XML 外衣的 HTML：不许被当成数据文档。"""
        got = await _build("application/xhtml+xml", rendered=HTML_DOC,
                           network=b"<xml>no</xml>")
        assert got.body.decode() == HTML_DOC

    @pytest.mark.asyncio
    async def test_a_body_that_cannot_be_read_falls_back_to_the_rendered_page(self):
        """取不到原件时不能把正文交成空的：那比一个带框的正文更难解释。"""
        got = await _build("application/json", raises=True)
        assert got.body.decode() == VIEWER_SHELL

    @pytest.mark.asyncio
    async def test_headers_and_status_still_come_from_the_network_response(self):
        got = await _build("application/json; charset=utf-8")
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
            self._page(HTML_DOC.encode(), "text/html"), "markdown", None)
        assert result.extracted_type == "markdown", result.extracted_type


class TestTheEscalationGateStillRefuses:
    """判据换了作者，行为不许变：为一份数据文档花一次浏览器渲染仍然是浪费。"""

    @pytest.mark.asyncio
    async def test_a_json_document_never_buys_a_browser_render(self):
        from dhole_mcp.context import _METHOD
        _METHOD.set("GET")
        got = await _build("application/json")
        model = _translate_response_body(got, "markdown", None, fetcher_used="http")
        model.content_type = "application/json"
        assert _never_escalate(model, "https://data.test/doc")

    @pytest.mark.asyncio
    async def test_a_real_page_can_still_escalate(self):
        from dhole_mcp.context import _METHOD
        _METHOD.set("GET")
        got = await _build("text/html", rendered=HTML_DOC, network=HTML_DOC.encode())
        model = _translate_response_body(got, "markdown", None, fetcher_used="http")
        model.content = ["a real page with a body long enough to be judged content"]
        model.content_type = "text/html"
        assert not _never_escalate(model, "https://data.test/doc")
