"""17.0 批次 2 · 一张静态列表页不是 JS 壳（抽取塌陷 → js_shell 误判是一条因果链）。

2026-10-05 实测现场：`http://books.toscrape.com/catalogue/category/books/travel_2/index.html`
—— 37,009 字节纯静态 HTML、11 本书。当时的结果：

    抽取正文 196 字符（11 个价格 + 11 个 "In stock"，**书名 0 个**）
    quality._JS_SHELL_MIN_TEXT_CHARS = 200  →  差 4 个字符
    error = js_shell_detected → content_ok = false → page_type 被覆盖成 js_shell
    next_action = "page is a JS shell; re-fetch auto-escalates to the stealthy browser"

链条上有两处独立失败，它们合起来才成为一次误判：

1. 抽取侧（根因）：`trafilatura.extract(...)` 默认臂不带 `favor_recall`，这一页的
   `<h3><a>` 整棵子树在"选正文"阶段就被丢掉；随后 `:192` 那条反灌水比例闸
   （`len(md_with_links) < len(result) * 3`，196×3=588）把同一函数已经恢复出来的
   2,406 字符带书名版本**当灌水丢掉**。也就是说：书名在同一颗函数里被算出来过，然后
   被扔了。
2. 判定侧：体积启发式只看"body 大而正文少"，不看这一页自己有没有字。它甚至已经在
   注释里承认"大 body 小文字可以是真实的图片/画布页"，但豁免只做在**档位**上。

判据（夹具 = tests/page_fixtures/travel_2.html，sha256 12647dc86ef4e070）：

1. 11 个书名全部出现在 markdown 里。
2. `_is_js_shell(...) is False`、`content_ok is true`。
3. 抽取真塌的时候要说"塌陷"，不许说"壳"，也不许因此推荐浏览器（新 token）。
4. 反向守卫：真 JS 壳（大 body、DOM 自己也没字）仍然判成壳 —— 豁免不是万能开关。
5. 读取路径与爬取路径同结果（同一入口，不许各抽一遍）。
6. 健康页不许被这次改动改变：quotes_index / gnu 正文页逐字节不变（防"召回上来了、
   导航也涌进来"）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from dhole_mcp.envelope import (
    EXTRACT_COLLAPSE_MIN_VISIBLE, detect_page_type, is_extraction_collapsed,
    page_type_from_error, visible_text_chars,
)
from dhole_mcp.fetcher import Response
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _detect_content_issue, _is_js_shell, extraction_collapsed
from dhole_mcp.trafilatura_extractor import (
    extract_content_from_html, extract_with_trafilatura,
)

FIXTURES = Path(__file__).parent / "page_fixtures"
TRAVEL_URL = "http://books.toscrape.com/catalogue/category/books/travel_2/index.html"
QUOTES_URL = "https://quotes.toscrape.com/"
GNU_URL = "https://www.gnu.org/philosophy/free-sw.html"


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8", errors="replace")


def _meta(name: str) -> dict:
    return json.loads((FIXTURES / name).with_suffix(".meta.json")
                      .read_text(encoding="utf-8"))


def _result(html: str, url: str, *, fetcher: str = "http",
            status: int = 200) -> ResponseModel:
    """按真实抓取后的信封形状搭一个结果：正文来自公共抽取入口，DOM 可见字符由
    `_translate_response` 那一处量好 —— 挂具若绕过量尺这一步，测的就是一个产品里
    不存在的形状。
    """
    from dhole_mcp.server import _attach_dom_visible_chars

    md = extract_content_from_html(html, url) or ""
    out = ResponseModel(status=status, content=[md], url=url,
                        fetcher_used=fetcher, content_type="text/html; charset=utf-8",
                        total_size_bytes=len(html.encode("utf-8")),
                        page_type=detect_page_type(html, url, "text/html", len(md)))
    return _attach_dom_visible_chars(out, html)


# ─── 1 · 书名回来了 ───────────────────────────────────────────────────────

class TestTheTitlesComeBack:

    def test_every_book_title_on_the_page_reaches_the_markdown(self):
        html = _html("travel_2.html")
        titles = [a.get_text(strip=True) for a in BeautifulSoup(html, "lxml").select("h3 a")]
        md = extract_content_from_html(html, TRAVEL_URL) or ""
        assert len(titles) == 11, titles
        missing = [t for t in titles if t not in md]
        assert not missing, f"11 本书里没回到 markdown 的：{missing}"

    def test_the_sidecar_still_describes_the_bytes_on_disk(self):
        """阈值与"塌没塌"都记在 sidecar 里；页面变了或口径变了就要重新看，不许默默漂。"""
        meta = _meta("travel_2.html")
        html = _html("travel_2.html")
        md = extract_content_from_html(html, TRAVEL_URL) or ""
        assert meta["extracted_markdown_chars"] == len(md), (
            meta["extracted_markdown_chars"], len(md))
        assert meta["visible_text_chars"] == visible_text_chars(html)
        assert meta["captured_from_url"] == TRAVEL_URL

    def test_the_collapse_ratio_that_used_to_fire_no_longer_fires(self):
        """196/1374=0.14 塌；1640/1374=1.19 不塌。这条钉的是修完之后现场落在哪一边。"""
        html = _html("travel_2.html")
        visible = visible_text_chars(html)
        md = extract_content_from_html(html, TRAVEL_URL) or ""
        assert is_extraction_collapsed(196, visible), "这份夹具的塌陷形状变了"
        assert not is_extraction_collapsed(len(md), visible)

    def test_the_crawl_entry_gets_the_same_body_as_the_read_entry(self):
        """G20 那对守卫的同一条等式，落在这一页上：塌陷修在读路径而爬路径没修，
        就是同一个工具两种真相。"""
        html = _html("travel_2.html")
        via_string = extract_content_from_html(html, TRAVEL_URL) or ""
        page = Response(url=TRAVEL_URL, body=html.encode("utf-8"), status=200,
                        headers={"content-type": "text/html"})
        via_page = extract_with_trafilatura(page, "markdown") or [""]
        assert via_string == via_page[0], "两条入口抽出了不同的正文"
        assert "Himalayas" in via_string


# ─── 2 · 它不是壳 ────────────────────────────────────────────────────────

class TestAListPageIsNotCalledAJsShell:

    def test_the_static_list_page_is_not_a_js_shell(self):
        assert _is_js_shell(_result(_html("travel_2.html"), TRAVEL_URL)) is False

    def test_the_static_list_page_is_not_reported_as_a_wall(self):
        """error 是调用方唯一会读的那一句：`js_shell_detected` 在这里说的原因是假的。"""
        result = _result(_html("travel_2.html"), TRAVEL_URL)
        assert _detect_content_issue(result) == "", _detect_content_issue(result)
        assert page_type_from_error(_detect_content_issue(result)) != "js_shell"

    def test_the_old_verdict_would_have_fired_on_the_pre_fix_body(self):
        """把正文换回修之前那 196 个字符，同一套判据必须说"塌陷"而不是"壳"。

        这一条不是在测抽取器（上一条已经在测了），是在测**判据**：现场差 4 个字符就
        跨过 200 那条线，所以"这条链修好了"必须由一个绕过抽取的件来证明 —— 否则抽取
        哪天因为别的原因再次塌陷，同一个误判会原样复发。
        """
        html = _html("travel_2.html")
        collapsed = ResponseModel(
            status=200, content=["£45.17 In stock " * 11], url=TRAVEL_URL,
            fetcher_used="http", content_type="text/html",
            total_size_bytes=len(html.encode("utf-8")), page_type="article")
        collapsed._dom_visible_chars = visible_text_chars(html)
        assert _is_js_shell(collapsed) is False, "塌陷不该起浏览器"
        issue = _detect_content_issue(collapsed)
        assert issue.startswith("extraction_collapsed"), issue
        assert "js_shell" not in issue
        assert page_type_from_error(issue) == "", "新 token 不许把结构化 page_type 盖掉"


# ─── 3 · 豁免不是万能开关 ────────────────────────────────────────────────

class TestTheExemptionIsNotABlanket:

    def test_a_real_spa_shell_still_reads_as_a_shell(self):
        """真壳：body 大（脚本），DOM 自己的可见文本没几个字，抽取也少。

        这一条是豁免的唯一反向守卫 —— 把"内容侧证据"做成"永远放行"，下一次真 SPA
        就会安静地回一份导航菜单，而没人会知道。
        """
        shell_html = ("<html><head><title>App</title></head><body><div id='app'></div>"
                      + "<script>var boot = '" + "x" * 4000 + "';</script></body></html>")
        result = _result(shell_html, "https://spa.example.com/")
        assert visible_text_chars(shell_html) < EXTRACT_COLLAPSE_MIN_VISIBLE
        assert extraction_collapsed(result) is False
        assert _is_js_shell(result) is True

    def test_a_thin_page_with_a_thin_dom_is_not_called_collapsed(self):
        """塌陷的定义是"页面自己有字而我没抽到"。两边都少，就不是抽取的问题。"""
        assert is_extraction_collapsed(100, 200) is False
        assert is_extraction_collapsed(100, EXTRACT_COLLAPSE_MIN_VISIBLE) is True

    def test_the_health_pages_do_not_change_shape(self):
        """批次 2 最大的风险是"召回上来了、导航也涌进来"。健康页逐字节不变是它唯一的证人。"""
        for name, url in (("quotes_index.html", QUOTES_URL),
                          ("gnu_free_sw_definition.html", GNU_URL)):
            meta = _meta(name)
            html = _html(name)
            md = extract_content_from_html(html, url) or ""
            assert meta["extracted_markdown_chars"] == len(md), (
                f"{name}: {meta['extracted_markdown_chars']} -> {len(md)}")


# ─── 4 · 塌陷要作为事实说出来（而不是被"壳"盖住）─────────────────────────

class TestACollapseIsStatedAsACollapse:

    def test_the_collapse_message_names_both_numbers_and_denies_the_browser(self):
        html = _html("travel_2.html")
        visible = visible_text_chars(html)
        collapsed = ResponseModel(
            status=200, content=["£45.17 In stock"] * 11, url=TRAVEL_URL,
            fetcher_used="http", content_type="text/html",
            total_size_bytes=len(html.encode("utf-8")), page_type="article")
        collapsed._dom_visible_chars = visible
        issue = _detect_content_issue(collapsed)
        assert str(visible) in issue, issue
        assert "Do NOT re-fetch for rendering" in issue, issue
        assert "extraction_type='html'" in issue, issue

    def test_the_collapse_predicate_only_answers_for_html_bodies(self):
        """PDF 早有自己的那一态（pdf_no_text）；把塌陷判据铺到它上面就是又一次
        "用一个猜测盖掉一个检查"。"""
        pdf = ResponseModel(status=200, content=["a"], url="https://x/y.pdf",
                            fetcher_used="http", content_type="application/pdf",
                            total_size_bytes=9_000)
        pdf._dom_visible_chars = 5_000
        assert extraction_collapsed(pdf) is False
