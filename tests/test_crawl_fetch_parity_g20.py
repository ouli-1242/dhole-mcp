"""G20：同一种内容在同一个工具里必须是同一种形态。

报告的复现是 `quotes.toscrape.com/` 上 `smart_fetch` 给出干净 markdown，而
`smart_crawl` 的 `pages[0].content` 是一段带大量空白的原始 HTML（`fetcher_used:"http"`）。
现在的实现里 crawl 的正文与 `smart_fetch` 走的是同一个抽取入口
（`extract_content_from_html` → `_extract_type`，crawl.py:249），实测两处逐字节相同。

所以这一项不是改代码，是**把已经成立的等式钉住**：抽取分叉在两个方向上都发生过
（`parse` 与 URL 路径曾经各写一套表格处理，就是 G13），而 crawl 是复制一份最容易的
地方——它已经有整页 HTML 在手，「顺手自己抽一下」看着比调公共入口更省。

保留的、有意的差异只有列表页：`page_type=list` 时 crawl 给的是结构化的链接清单而不是
正文，那是它的设计（`page_type` 字段就是为了让调用方看得见这次给的是哪种）。
"""

from __future__ import annotations

import pytest

from dhole_mcp.fetcher import Response
from dhole_mcp.trafilatura_extractor import (
    extract_content_from_html,
    extract_with_trafilatura,
)

PROSE = """<html><head><title>Post</title></head><body>
<h1>A heading</h1><p>First real paragraph of the article with enough words in it
that a content extractor will consider it the main body of the page.</p>
<p>Second paragraph, also prose, also the point of this fixture.</p>
<script>var junk = "must not appear";</script>
</body></html>"""

TABLE = """<html><head><title>T</title></head><body><h1>T</h1>
<p>Prose before the table keeps the extraction on the main-content path.</p>
<table><tr><th>K</th><th>V</th></tr><tr><td>a</td><td>1</td></tr></table>
<p>Prose after.</p></body></html>"""

LINKS = """<html><body><h1>Index</h1>
<p>Navigating somewhere.</p>
<ul>""" + "".join(f'<li><a href="/p{i}">Page {i}</a></li>' for i in range(6)) + """</ul>
</body></html>"""


def _page(html: str) -> Response:
    return Response(url="https://parity.test/", body=html.encode("utf-8"), status=200,
                    headers={"content-type": "text/html"})


class TestTheTwoEntryPointsAreOnePath:
    """crawl 拿到的是 HTML 字符串，URL 路径拿到的是 Response 对象——两条入口必须
    给出同一份 markdown，否则「crawl 用同一套抽取」就只是注释里的承诺。"""

    @pytest.mark.parametrize("html,name", [(PROSE, "prose"), (TABLE, "table"),
                                           (LINKS, "links")])
    def test_the_same_body_extracts_identically(self, html, name):
        via_string = extract_content_from_html(html, "https://parity.test/", "markdown") or ""
        via_page = extract_with_trafilatura(_page(html), "markdown") or [""]

        assert via_string, f"{name}: the shared entry produced nothing to compare"
        assert via_string == via_page[0], f"{name} 在两条入口上抽出了不同的东西"

    def test_a_table_survives_on_both_entries(self):
        """G13 修的是共享函数；这里守的是它对 crawl 也生效。"""
        from_string = extract_content_from_html(TABLE, "https://parity.test/", "markdown")
        from_page = (extract_with_trafilatura(_page(TABLE), "markdown") or [""])[0]

        assert "| K | V |" in from_string, from_string
        assert "| K | V |" in from_page, from_page


@pytest.mark.live
@pytest.mark.asyncio
async def test_the_crawl_and_the_fetch_answer_the_same_for_one_page():
    """报告那两条调用放在一起跑：同一个 URL、同一次会话、两种形态就是 G20。"""
    from dhole_mcp.server import MasterFetchServer

    url = "https://quotes.toscrape.com/"
    srv = MasterFetchServer()
    fetched = await srv.smart_fetch(url, cache_ttl=0, timeout=40000)
    crawled = await srv.smart_crawl(url, max_pages=1, cache_ttl=0)

    assert fetched.content_ok, fetched.error
    assert crawled.pages, crawled.error
    page = crawled.pages[0]
    assert page.content_ok, page.error
    assert "".join(page.content).strip() == "".join(fetched.content).strip(), (
        "smart_crawl 与 smart_fetch 对同一页给了不同形态的正文")
    # The raw-HTML symptom the report described: markup leaking into the body.
    assert "<div" not in "".join(page.content)[:400].lower()


@pytest.mark.live
@pytest.mark.asyncio
async def test_a_list_page_is_still_a_link_list_on_purpose():
    """唯一的有意分叉要在测试里留着名字，否则下一次「统一」会把它也统一掉。"""
    from dhole_mcp.server import MasterFetchServer

    out = await MasterFetchServer().smart_crawl("https://quotes.toscrape.com/",
                                                max_pages=1, cache_ttl=0)
    if not out.pages:
        pytest.skip(f"no crawl answer: {out.error[:80]}")

    page = out.pages[0]
    assert page.page_type in ("list", "article"), page.page_type
    if page.page_type == "list":
        assert "http" in "".join(page.content).lower()
        assert "<a " not in "".join(page.content).lower(), "结构化清单不是原始标签"
