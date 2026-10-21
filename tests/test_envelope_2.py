"""test_envelope_2.py —— 由 5 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import json
from pathlib import Path
from bs4 import BeautifulSoup
from dhole_mcp.envelope import EXTRACT_COLLAPSE_MIN_VISIBLE, detect_page_type, is_extraction_collapsed, page_type_from_error, visible_text_chars
from dhole_mcp.fetcher import Response
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _detect_content_issue, _is_js_shell, extraction_collapsed
from dhole_mcp.trafilatura_extractor import extract_content_from_html, extract_with_trafilatura
from tests._extraction_ledger import recorded_markdown_chars
import pytest
from dhole_mcp.envelope import LIST_LABEL_SHARE_MIN, LIST_PROSE_RUN_MAX, _count_content_links, _label_share, _list_shape
from dhole_mcp.envelope import _stale_days_for, classify_source
from dhole_mcp.search import RawResult, _build_results
from datetime import date
from dhole_mcp.envelope import parse_serp_date
from dhole_mcp import crawl
from dhole_mcp.quality import body_substance


# ============================================================================
# 来源：test_a_list_page_is_not_called_a_js_shell.py
# ============================================================================
# 17.0 批次 2 · 一张静态列表页不是 JS 壳（抽取塌陷 → js_shell 误判是一条因果链）。

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
        """阈值与"塌没塌"都记在 sidecar 里；页面变了或口径变了就要重新看，不许默默漂。

        长度那一格按 trafilatura 版本登记：同一份字节在 2.2.0 抽成 1,640、在 2.3.1 抽成
        1,566。写成一个裸数字等于把"这台机器装了哪版抽取器"钉进判据 —— CI 与全新克隆
        就是因此在 2026-10-20 一起红的（查表口径见 `tests/_extraction_ledger.py`）。
        """
        meta = _meta("travel_2.html")
        html = _html("travel_2.html")
        md = extract_content_from_html(html, TRAVEL_URL) or ""
        want = recorded_markdown_chars(meta, "travel_2.html")
        assert want == len(md), (want, len(md))
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
        collapsed.visible_text_chars = visible_text_chars(html)
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
        """批次 2 最大的风险是"召回上来了、导航也涌进来"。健康页逐字节不变是它唯一的证人。

        这位证人只在量它的那版抽取器面前立誓：长度按版本查表，没登记的版本直接红 ——
        quotes_index 在 2.2.0 是 2,179 字、在 2.3.1 是 2,864 字，静悄悄放过等于没测。
        """
        for name, url in (("quotes_index.html", QUOTES_URL),
                          ("gnu_free_sw_definition.html", GNU_URL)):
            meta = _meta(name)
            html = _html(name)
            md = extract_content_from_html(html, url) or ""
            want = recorded_markdown_chars(meta, name)
            assert want == len(md), f"{name}: {want} -> {len(md)}"


# ─── 4 · 塌陷要作为事实说出来（而不是被"壳"盖住）─────────────────────────

class TestACollapseIsStatedAsACollapse:

    def test_the_collapse_message_names_both_numbers_and_denies_the_browser(self):
        html = _html("travel_2.html")
        visible = visible_text_chars(html)
        collapsed = ResponseModel(
            status=200, content=["£45.17 In stock"] * 11, url=TRAVEL_URL,
            fetcher_used="http", content_type="text/html",
            total_size_bytes=len(html.encode("utf-8")), page_type="article")
        collapsed.visible_text_chars = visible
        issue = _detect_content_issue(collapsed)
        assert str(visible) in issue, issue
        # 这句的措辞在 1.4 收拢成了一个作者（`quality._COLLAPSE_RECOVERY`：散文、
        # `next_action`、`diagnosis.recover_with` 三处读同一颗常量），祈使句"do NOT
        # re-fetch for rendering"换成了它的事实依据 —— 浏览器只是把同一份静态标记交给
        # 刚刚丢掉正文的那个读取器。要禁的还是同一件事，判决位上点名这条的守卫见
        # tests/test_a_collapsed_page_offers_only_the_doors_that_change_the_reader.py
        assert "a browser hands that reader the same markup" in issue, issue
        assert "extraction_type='html'" in issue, issue

    def test_the_collapse_predicate_only_answers_for_html_bodies(self):
        """PDF 早有自己的那一态（pdf_no_text）；把塌陷判据铺到它上面就是又一次
        "用一个猜测盖掉一个检查"。"""
        pdf = ResponseModel(status=200, content=["a"], url="https://x/y.pdf",
                            fetcher_used="http", content_type="application/pdf",
                            total_size_bytes=9_000)
        pdf.visible_text_chars = 5_000
        assert extraction_collapsed(pdf) is False


# ============================================================================
# 来源：test_page_type_realpages.py
# ============================================================================
# G16 回归：真实页面的 page_type 判定（tests/page_fixtures）。
# 重名消歧：FIXTURES → FIXTURES__page_type_realpages、GNU_URL → GNU_URL__page_type_realpages、QUOTES_URL → QUOTES_URL__page_type_realpages

FIXTURES__page_type_realpages = Path(__file__).parent / "page_fixtures"

GNU_URL__page_type_realpages = "https://www.gnu.org/philosophy/free-sw.html"
QUOTES_URL__page_type_realpages = "https://quotes.toscrape.com/"

# Recorded in the .meta.json sidecars; asserted against them below.
GNU = {"html": "gnu_free_sw_definition.html", "links": 112, "markdown": 25785,
       "visible": 25245, "share": 0.051, "run": 756}
QUOTES = {"html": "quotes_index.html", "links": 52, "markdown": 2179,
          "visible": 1681, "share": 0.264, "run": 148}


def _page(spec: dict) -> str:
    path = FIXTURES__page_type_realpages / spec["html"]
    meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
    html = path.read_text(encoding="utf-8", errors="replace")
    # The sidecar and the constants above must describe the bytes on disk.
    assert meta["content_links"] == spec["links"]
    return html


@pytest.mark.parametrize("spec,url", [(GNU, GNU_URL__page_type_realpages), (QUOTES, QUOTES_URL__page_type_realpages)])
def test_measured_values_still_hold_for_the_saved_bytes(spec, url):
    """阈值来自这几个数；页面变了（或计数口径变了）就要重新看阈值，不能默默漂。"""
    html = _page(spec)
    host = url.split("//", 1)[1]
    assert _count_content_links(html, host) == spec["links"]
    share, run = _list_shape(html, host)
    assert abs(share - spec["share"]) < 0.01, f"{spec['html']} label share moved: {share:.3f}"
    assert abs(run - spec["run"]) <= 5, f"{spec['html']} longest non-link run moved: {run}"
    # The sidecar must keep describing the bytes: re-measuring without updating it
    # (or the other way round) is how a threshold stops meaning anything.
    meta = json.loads((FIXTURES__page_type_realpages / spec["html"]).with_suffix(".meta.json")
                      .read_text(encoding="utf-8"))
    assert meta["label_share"] == pytest.approx(spec["share"], abs=0.001)
    assert meta["longest_nonlabel_run_chars"] == spec["run"]


def test_link_dense_article_page_is_not_a_list():
    """G16 本体：链接密集的正文页不能再判 list。"""
    html = _page(GNU)
    assert detect_page_type(html, GNU_URL__page_type_realpages, "text/html; charset=utf-8", GNU["markdown"]) != "list"


def test_verdict_does_not_follow_the_extractor_collapse():
    """抽取崩了（RFC 9110 实测 8.4k vs 全页 446k）时，判定必须不变。

    旧判据在这里会判成 list：112 条链接、文本量 200 < 1500。
    """
    html = _page(GNU)
    collapsed = detect_page_type(html, GNU_URL__page_type_realpages, "text/html; charset=utf-8", 200)
    honest = detect_page_type(html, GNU_URL__page_type_realpages, "text/html; charset=utf-8", GNU["markdown"])
    assert collapsed == honest != "list"


def test_real_index_page_is_still_a_list():
    """收紧不能把真列表页一起收走（报告的诉求两边都要满足）。"""
    html = _page(QUOTES)
    assert detect_page_type(html, QUOTES_URL__page_type_realpages, "text/html; charset=utf-8",
                            QUOTES["markdown"]) == "list"


def test_index_page_is_a_list_even_if_extraction_collapse():
    html = _page(QUOTES)
    assert detect_page_type(html, QUOTES_URL__page_type_realpages, "text/html; charset=utf-8", 200) == "list"


def test_verdict_does_not_depend_on_the_extraction_shape_asked_for():
    """G16 的同一件事，反方向：`extraction_type="html"` 时「抽出来的文本长度」是**标记语言**
    的长度（标签、属性、实体都算字符），在同一页上比正文本大好几倍 —— 实测
    quotes 首页走 markdown 是 2179、走 html 是 11021，只有后者过了列表页的文本量闸门，
    于是「换一种取法」能把 list 变成 unknown。文本量闸门读到的必须始终是正文量。
    """
    html = _page(QUOTES)
    ct = "text/html; charset=utf-8"
    markdown_shape = detect_page_type(html, QUOTES_URL__page_type_realpages, ct, QUOTES["markdown"])
    raw_html_shape = detect_page_type(html, QUOTES_URL__page_type_realpages, ct, len(html))
    assert markdown_shape == raw_html_shape == "list", (
        f"同一份 HTML，只因 extraction_type 不同就给出 {markdown_shape} / {raw_html_shape}")
    # 正文页那头也不能被换一种取法翻掉
    gnu = _page(GNU)
    assert (detect_page_type(gnu, GNU_URL__page_type_realpages, ct, len(gnu))
            == detect_page_type(gnu, GNU_URL__page_type_realpages, ct, GNU["markdown"]) != "list")


def test_threshold_sits_in_the_measured_gap():
    """0.10 落在实测两组之间：正文 ≤0.05，列表 ≥0.16。留一头就够，别贴着边界。"""
    assert _label_share(_page(GNU), GNU_URL__page_type_realpages.split("//", 1)[1]) < LIST_LABEL_SHARE_MIN
    assert _label_share(_page(QUOTES), QUOTES_URL__page_type_realpages.split("//", 1)[1]) > LIST_LABEL_SHARE_MIN


WIKI_URL = "https://en.wikipedia.org/wiki/Example_Article"


def _wiki_shaped_page(with_prose: bool = True) -> str:
    """复现 en.wikipedia.org/wiki/Hypertext_Transfer_Protocol 的实测形状。

    不是某个站点的存档（存档 200KB+，仓库里没入库），而是照着那页量出来的数字搭的：
    914 条同域链接、v/link≈110、标签占比 **0.12**（跨过 0.10 那条线）、最长非链接
    正文 705+。占比被抬到 0.12 的原因是页尾那些「几乎全是链接文字」的参考/外部链接
    区块，而不是这页是索引 —— 所以光看占比分不开两者，绝对形状（有没有一段不属于任何
    链接的长正文）才分得开。

    ``with_prose=False`` 把正文段落删掉，剩下的就是真索引的形状 —— 用来证明这条判据
    两头都在起作用，而不是把 list 整个关掉了。
    """
    host_links = []
    if with_prose:
        for p in range(8):
            # 每段 ~700 字符、段内不放链接：真文章的段落就是这样
            host_links.append(
                "<p>" + f"Section {p} prose. " + ("dictionary terms and history of the protocol. " * 22)
                + "</p>")
    host_links.append("<h2>References</h2><ol>")
    for i in range(45):
        host_links.append(f'<li><a href="/wiki/Article_{i}">Cited work number {i} on the web</a></li>')
    host_links.append("</ol>")
    return "<html><body><div id='mw-content-text'>" + "".join(host_links) + "</div></body></html>"


class TestLabelShareAloneCannotCallAnArticleAList:
    """G16 复测留下的那一半：占比 ≥0.10 的正文页仍然被判成 list。"""

    def test_the_constructed_page_really_does_cross_the_old_line(self):
        """先证构造件名副其实：旧判据三条全过，所以它在旧代码里必然判 list。"""
        html = _wiki_shaped_page()
        share, run = _list_shape(html, "en.wikipedia.org")
        assert _count_content_links(html, "en.wikipedia.org") >= 20
        assert share >= LIST_LABEL_SHARE_MIN, f"构造件没有复现出 wiki 的占比: {share:.3f}"
        assert run > LIST_PROSE_RUN_MAX

    def test_a_link_dense_article_page_is_not_a_list(self):
        assert detect_page_type(_wiki_shaped_page(), WIKI_URL, "text/html", 0) != "list"

    def test_the_same_page_without_its_prose_is_a_list(self):
        """删掉正文段落只剩链接块 —— 判据必须还认它是索引，而不是把 list 关掉了。"""
        bare = _wiki_shaped_page(with_prose=False)
        assert detect_page_type(bare, WIKI_URL, "text/html", 0) == "list"


# ============================================================================
# 来源：test_one_field_name_means_one_vocabulary.py
# ============================================================================
# 同一个字段名只能有一套词表（体验修复刀2-S6）。

def _row(url: str, i: int = 1) -> RawResult:
    return RawResult(title=f"t{i}", url=url, snippet="s", source="bing",
                     position=i, consensus=1, sources=("bing",))


def _search_row_source(url: str) -> str:
    """走真正的铸行路径，而不是再调一次分类器 —— 要守的是"两边同源于一个函数"。"""
    built = _build_results("q", [_row(url)], [1.0], total_families=2)
    return built[0].source_type


class TestOneFunctionAnswersOneFieldName:

    @pytest.mark.parametrize("url", [
        "https://github.com/openclaw/openclaw",
        "https://stackoverflow.com/questions/1/how",
        "https://huggingface.co/models",
        "https://docs.aws.amazon.com/lambda/",
        # 这两条不是凑数：老词表把"路径里有 /docs/"当成一条独立规则，任何"两侧各有一套
        # 判定"的写法都会在这里显形 —— 只测按域名认的 URL，第二套词表可以完全藏住
        # （我第一次跑反向实验时就是这样，那条实验白做了）。
        "https://kubernetes.io/docs/concepts/services-networking/",
        "https://redis.io/docs/latest/operate/oss_and_stack/",
        "https://www.zhihu.com/question/2",
        "https://arxiv.org/abs/2401.00001",
        "https://36kr.com/p/1",
        "https://randomsite12345.example/page",
    ])
    def test_a_search_row_says_what_the_fetched_page_says(self, url):
        """同一条 URL，搜索行与抓取行必须是同一个词。这一条是这一刀的守门员：
        只要有人再给搜索行接上第二套表，它就红。"""
        assert _search_row_source(url) == classify_source(url)[0]

    def test_the_values_that_used_to_be_unreachable_now_reach_search_rows(self):
        """体测里我见过的形状：一个 github 结果在搜索行上只能被写成 `repo`。"""
        assert _search_row_source("https://github.com/openclaw/openclaw") == "github"
        assert _search_row_source("https://stackoverflow.com/q/1") == "qa"
        assert _search_row_source("https://docs.rs/serde/latest/serde/") == "docs-site"
        assert _search_row_source("https://shopee.example.shop/item") == "ecommerce"


class TestNothingThatHadANameLostIt:
    """折表进来的最低要求：老表认得的站，新词表一个都不许退回 unknown。

    这些 URL 不是编的 —— 每条都来自冻结 SERP 语料里实测"会丢名字"的那 92 个域，
    逐域留一发代表。写死在这里是因为这件事**只 regression 一次就再也看不见**：
    一个字段值从 `blog` 变成 `unknown` 不会让任何测试失败，除非有人专门钉它。
    """

    @pytest.mark.parametrize("url,expected", [
        ("https://www.zhihu.com/question/1234567", "forum"),
        ("https://zhuanlan.zhihu.com/p/9", "forum"),
        ("https://www.cnblogs.com/someone/p/1.html", "blog"),
        ("https://dev.to/someone/a-post", "blog"),
        ("https://juejin.cn/post/7123456", "blog"),
        ("https://blog.csdn.net/x/article/details/1", "blog"),
        ("https://huggingface.co/models", "docs-site"),
        ("https://doc.rust-lang.org/book/", "docs-site"),
        ("https://learn.microsoft.com/en-us/azure/", "docs-site"),
        ("https://kubernetes.io/docs/concepts/", "docs-site"),
        ("https://web.dev/articles/ttfb", "docs-site"),
        ("https://realpython.com/python-generators/", "docs-site"),
        ("https://nginx.org/en/docs/http/ngx_http_core_module.html", "docs-site"),
        ("https://segmentfault.com/q/101", "forum"),
        ("https://news.ycombinator.com/item?id=1", "forum"),
    ])
    def test_a_domain_the_old_table_knew_still_has_its_word(self, url, expected):
        assert classify_source(url)[0] == expected, url

    def test_the_corpus_paths_that_only_a_path_rule_could_name(self):
        """/docs/ 那一半折进来了：实测语料里 14 条 URL 的**唯一**名字来源就是路径，
        全是被任何表都不认识的文档站（typescriptlang.org、redis.io、一个 MDN 镜像）。
        /api/ 那一半刻意没折 —— 见下一条。"""
        assert classify_source("https://www.typescriptlang.org/docs/handbook/2/everyday-types.html")[0] == "docs-site"  # fmt: skip
        assert classify_source("https://redis.io/docs/latest/operate/")[0] == "docs-site"
        assert classify_source("https://example.com/blog/post-1")[0] == "blog"


class TestWhatWasDeliberatelyNotFolded:

    def test_an_api_path_is_not_documentation(self):
        """`/api/` 不折：机器端点不是文档，而 docs-site 这个标签还会连带换上长视野
        过期判定 —— 那是行为改动，得单独拿证据，不能趁合并顺手做掉。"""
        assert classify_source("https://api.example.com/v1/users")[0] == "unknown"
        assert classify_source("https://example.com/api/health")[0] == "unknown"

    def test_repo_pages_join_github_in_the_long_horizon(self):
        """`repo` 进长视野是这一刀的**唯一**一处视野改动，理由写在函数 docstring：
        此前只有 github 因为挂在注册表名单上而顺带拿到 730 天，gitlab 却没有 ——
        那不是一个关于"页面类型"的判断，是一个巧合。"""
        assert _stale_days_for("https://gitlab.com/group/project") == 730
        assert _stale_days_for("https://github.com/o/r") == 730
        assert _stale_days_for("https://example.com/blog/x") == 365
        assert _stale_days_for("https://36kr.com/p/1") == 30


class TestTheManualNamesEveryValueInTheVocabulary:
    """词表加长之后，手册那一行必须逐个点名 —— 否则只读手册的 agent 拿着一套旧枚举
    去匹配，"这个取值不存在"看起来像数据坏了。表面取自语料生成的取值，不取自代码。"""

    def test_every_value_produced_over_the_corpus_is_documented(self):
        from dhole_mcp import guide
        manual = (guide._ROOT / "references" / "fields.md").read_text(encoding="utf-8")
        row = next(ln for ln in manual.splitlines()
                   if ln.startswith("| `source_type` |"))
        urls = [
            "https://www.nasa.gov/mission", "https://www.mit.edu/research",
            "https://github.com/o/r", "https://gitlab.com/g/p",
            "https://docs.python.org/3/", "https://stackoverflow.com/q/1",
            "https://www.zhihu.com/question/1", "https://medium.com/@u/p",
            "https://shop.example.shop/x", "https://en.wikipedia.org/wiki/A",
            "https://arxiv.org/abs/1", "https://bbc.com/news/x",
            "https://randomsite12345.example/p",
        ]
        values = {classify_source(u)[0] for u in urls}
        missing = sorted(v for v in values if f"`{v}`" not in row)
        assert not missing, f"手册的 source_type 行没点名的取值: {missing}"


# ============================================================================
# 来源：test_a_serp_date_carries_the_precision_it_was_read_at.py
# ============================================================================
# SERP 上那枚日期章要带着它的精度一起走 —— 否则 "3 hours ago" 会被存成一句精确的谎。

NOW = date(2026, 10, 10)


# ─── 相对写法：永远不许冒充日精度 ────────────────────────────────

def test_an_hours_ago_stamp_is_relative_not_a_day():
    d, precision, matched = parse_serp_date("3 hours ago", now=NOW)
    assert d == NOW
    assert precision == "relative"
    assert matched == "3 hours ago"


def test_a_chinese_relative_stamp_resolves_to_the_same_precision():
    d, precision, matched = parse_serp_date("5天前", now=NOW)
    assert d == date(2026, 10, 5)
    assert precision == "relative"
    assert "5天前" in matched


def test_the_chinese_forms_without_a_number_still_say_relative():
    assert parse_serp_date("昨天", now=NOW)[:2] == (date(2026, 10, 9), "relative")
    assert parse_serp_date("前天", now=NOW)[:2] == (date(2026, 10, 8), "relative")
    assert parse_serp_date("刚刚", now=NOW)[:2] == (NOW, "relative")


def test_weeks_and_months_and_years_are_relative_with_their_own_offsets():
    assert parse_serp_date("2 weeks ago", now=NOW)[0] == date(2026, 9, 26)
    assert parse_serp_date("1 month ago", now=NOW)[0] == date(2026, 9, 10)
    assert parse_serp_date("1 year ago", now=NOW)[0] == date(2025, 10, 10)
    assert all(parse_serp_date(s, now=NOW)[1] == "relative"
               for s in ("2 weeks ago", "1 month ago", "1 year ago"))


def test_relative_arithmetic_does_not_read_the_wall_clock():
    """`now` 可注入是这条守卫存在的前提：读今天的日期会让这一族测试在跨月的凌晨自己变红。"""
    a = parse_serp_date("10 days ago", now=date(2026, 3, 5))
    b = parse_serp_date("10 days ago", now=date(2026, 12, 31))
    assert a[0] == date(2026, 2, 23) and b[0] == date(2026, 12, 21)
    assert a[1] == b[1] == "relative"


# ─── 绝对写法：精度到日，并把原文带出来 ──────────────────────────

def test_the_iso_stamp_keeps_day_precision_and_quotes_where_it_came_from():
    d, precision, matched = parse_serp_date("2026-10-01", now=NOW)
    assert (d, precision) == (date(2026, 10, 1), "day")
    assert matched == "2026-10-01"


def test_the_chinese_absolute_stamp_is_a_day_not_a_month():
    """`2026年10月1日` 不许被"只到月"那条规则截走 —— 那是把一个已知的事实改写成猜测。"""
    assert parse_serp_date("2026年10月1日", now=NOW)[:2] == (date(2026, 10, 1), "day")
    assert parse_serp_date("2026-10-01 12:00:00", now=NOW)[1] == "day"


def test_the_english_absolute_stamps_are_days():
    for text, want in (("March 14, 2026", date(2026, 3, 14)),
                       ("14 March 2026", date(2026, 3, 14)),
                       ("Mar 14, 2026", date(2026, 3, 14))):
        d, precision, _m = parse_serp_date(text, now=NOW)
        assert (d, precision) == (want, "day"), text


# ─── 只到月与只到年：补出来的那位是下界，不是断言 ────────────────

def test_a_month_only_stamp_is_a_month_with_the_day_at_its_floor():
    for text in ("2026-03", "2026年3月", "2026.3"):
        d, precision, _m = parse_serp_date(text, now=NOW)
        assert (d, precision) == (date(2026, 3, 1), "month"), text


def test_a_month_named_in_words_is_a_month():
    assert parse_serp_date("March 2026", now=NOW)[:2] == (date(2026, 3, 1), "month")
    assert parse_serp_date("Oct 2026", now=NOW)[:2] == (date(2026, 10, 1), "month")


def test_a_bare_year_is_a_date_only_when_the_text_is_nothing_but_the_year():
    """整串就是一个年份才认 —— "See 1984 by Orwell" 不是一条发布日期。"""
    d, precision, _m = parse_serp_date("2026", now=NOW)
    assert (d, precision) == (date(2026, 1, 1), "year")
    assert parse_serp_date("See 1984 by Orwell", now=NOW)[0] is None
    assert parse_serp_date("£45.17 in stock, updated 2019 edition", now=NOW)[0] is None


def test_a_future_year_is_not_a_publish_date():
    assert parse_serp_date("2031", now=NOW)[0] is None


# ─── 没有日期时不发明一个 ────────────────────────────────────────

def test_text_without_a_date_returns_no_date_no_precision_and_no_quote():
    for text in ("", "   ", "In stock", "£45.17", "首页 关于 登录"):
        d, precision, matched = parse_serp_date(text, now=NOW)
        assert (d, precision, matched) == (None, "", ""), text


def test_the_precisions_are_the_four_declared_ones():
    """手册与 `before=` 的逐行判决读的就是这四个取值；第五种取值是契约外的东西。"""
    seen = {parse_serp_date(t, now=NOW)[1] for t in (
        "2026-10-01", "3 hours ago", "2026年3月", "2026", "no date here")}
    assert seen == {"day", "relative", "month", "year", ""}


# ============================================================================
# 来源：test_a_crawled_row_is_judged_by_the_same_author_as_a_read_page.py
# ============================================================================
# 爬取的一行与读取的那一封用**同一位作者**判正文形状，而它们量的是各自的正文。
# 重名消歧：_row → _row__a_crawled_row_is_judged__03bc

MENU_HTML = ("<html><body><nav>" + "".join(f"<a href='/{i}'>{w}</a>" for i, w in enumerate(
    ["首页", "关于", "登录", "我的账户", "购物车", "结算", "文档", "集合", "作品", "订阅"]))
    + "</div><div id='app'></div></body></html>")
MENU_TEXT = "\n".join(["首页", "关于", "登录", "我的账户", "购物车", "结算", "文档",
                       "集合", "作品一", "订阅", "验证", "外壳"])
INDEX_LIST_MD = "\n".join(f"[专题 {i}：一件值得点进去的完整标题](/work/{i}) {i*3} 件"
                          for i in range(12))


def _row__a_crawled_row_is_judged__03bc(md: str, html: str, kind: str) -> dict:
    """走真的那一行：`_measure_row` 量，`_row_verdict` 判，`_measure_note` 说。"""
    stats: dict = {}
    crawl._measure_row(stats, html, md, kind, "https://e.test/p", "https://e.test/p")
    stats.pop("item_set", None)
    return {"stats": stats, "ok": crawl._row_verdict(kind, md, stats),
            "note": crawl._measure_note(stats)}


def test_the_shape_author_is_shared_not_reimplemented():
    """爬取那一行不许有自己的三份阈值：判决与读取侧必须是同一次函数调用的产物。"""
    html = MENU_HTML
    stats: dict = {}
    crawl._measure_row(stats, html, MENU_TEXT, "fallback",
                       "https://e.test/p", "https://e.test/p")
    direct = body_substance(MENU_TEXT, visible_text_chars(html))
    assert (stats.get("substance"), stats.get("substance_basis")) == direct


def test_a_row_whose_body_is_bare_labels_is_refused_and_says_why():
    got = _row__a_crawled_row_is_judged__03bc(MENU_TEXT, MENU_HTML, "fallback")
    assert got["stats"]["substance"] == "nav_only", got["stats"]
    assert got["ok"] is False, "一堆菜单碎片又被算成拿到了内容"
    assert "no prose in this row" in got["note"], got["note"]
    # 理由必须落在**这一行**有的那两格里（爬取的行没有 next_action）
    assert "force_fetcher='stealthy'" in got["note"] or "follow the links" in got["note"]


def test_a_link_list_is_content_because_there_the_links_are_the_content():
    """误报侧：目录页在爬取里交回的就是链接清单，这一行不许被同一把尺翻掉。"""
    html = ("<html><body><ul>" + "".join(
        f"<li><a href='/work/{i}'>专题 {i}：一件值得点进去的完整标题</a> {i*3} 件</li>"
        for i in range(12)) + "</ul></body></html>")
    got = _row__a_crawled_row_is_judged__03bc(INDEX_LIST_MD, html, "list")
    assert got["stats"].get("substance") == "ok", got["stats"]
    assert got["ok"] is True


def test_a_row_with_prose_keeps_the_verdict_it_had():
    md = "\n".join(f"第 {i} 段讲一个完整的道理，读起来是一段正文，句末有标点。"
                   for i in range(9))
    html = "<html><body>" + "".join(f"<p>{ln}</p>" for ln in md.split("\n")) + "</body></html>"
    got = _row__a_crawled_row_is_judged__03bc(md, html, "article")
    assert got["stats"]["substance"] == "ok"
    assert got["ok"] is True


def test_a_row_too_thin_to_have_an_opinion_is_left_unjudged_not_flipped():
    """一两个标签字的行没有形状判决可给 —— 留空不等于 `ok`，也不许因此翻闸（翻闸另有依据）。"""
    got = _row__a_crawled_row_is_judged__03bc("关于", "<html><body><div id='app'>关于</div></body></html>", "fallback")
    assert got["stats"]["substance"] == "nav_only", got["stats"]
    # 2 个字符本来就过不了 `len(md) >= 50` 那把旧尺，这一行被拒的理由是**空**而不是判决
    assert got["ok"] is False


def test_the_collapse_branch_still_wins_when_both_apply():
    """塌陷与形状同在一行上时，理由要说塌陷那一句（它给出的出路不同：换 extraction_type）。"""
    html = "<html><body>" + "<p>这一段是页面上真实写着的正文，句末有标点。</p>" * 90 + "</body></html>"
    stats: dict = {}
    crawl._measure_row(stats, html, "一点点抽回来的字", "article",
                       "https://e.test/p", "https://e.test/p")
    assert stats.get("extraction_collapsed") is True
    assert crawl._row_verdict("article", "一点点抽回来的字", stats) is False
    assert "extraction collapsed" in crawl._measure_note(stats)


def test_a_short_but_real_body_is_not_flipped_by_a_floor_read_does_not_have():
    """靶站 `/work/1`：同一页 `web_read` 回 content_ok=true（39 字、一句正文），
    `web_crawl` 却因 `len(md) >= 50` 那把读取侧根本没有的长度尺翻成 false，且不给理由。
    同一 URL 不许一页一个样读成内容、爬成 shell —— 这里钉住两位作者的对齐。"""
    body = "作品 1\n街景\n夜景\n人像\n第 1 篇的独有正文，用来证明这一列真的每页不同。"
    got = _row__a_crawled_row_is_judged__03bc(body, f"<html><body><h1>作品 1</h1><p>{body}</p></body></html>", "article")
    assert got["stats"].get("substance") != "nav_only"
    assert got["ok"] is True, got
