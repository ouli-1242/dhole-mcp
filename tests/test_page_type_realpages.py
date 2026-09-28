"""G16 回归：真实页面的 page_type 判定（tests/page_fixtures）。

报告 G16 的症状是「正文页被判成 list」。交接文档里的处方（把
`extracted_text_len` 换成 `min(extracted_text_len, len(visible_text))`）方向是**反**的
—— 它把文本量改小，只会更容易判成 list。REPLAN 把它列为阻塞项 B1，理由是「需要一份真实
页面的 HTML 才能定阈值」。这里补上那份真实页面，并按实测改判：

    页面                              n_links  md字符   可见字符  链接标签占比  判定
    gnu.org free-sw（正文，无 article）   112     25785     25245      0.05     unknown
    rfc-editor RFC9110（正文）           194      8357    446123      0.01     list（旧）
    quotes.toscrape.com（列表）            52      2179      1681      0.26     list
    docs.python.org/3/（索引）             21      2385      3232      0.16     docs

两条判据（见 envelope.detect_page_type）：
1. 文本量取 max(抽取长度, 可见文本长度)：抽取器（trafilatura）报的是最密集的一段，
   RFC 9110 那种长文档上只给出全页 2% 的量 —— 判定不该跟着抽取的成败一起翻。
2. 链接标签占比 ≥ 0.10：列表页的「正文」就是链接文字，正文页不是。
3. 最长非链接正文 ≤ 400 字符（复测后新增）：占比是个比值，压不住「正文 + 一整个参考
   书目块」这种页。实测 en.wikipedia.org/wiki/Hypertext_Transfer_Protocol：914 条同域
   链接、v/link≈110、占比 0.12（**跨过第 2 条线**）、最长非链接正文 705+ —— 第 3 条把
   它救回正文页；真列表页的最长非链接正文实测 79（HN）/148（quotes）/279（FSF 分类页）。

RFC 9110 那页 1.18MB 没入库（仓库里的 fixture 都在几十 KB 量级），所以第 1 条用
GNU 那页 + 人为压低抽取长度来复现同一条不等式 —— 被钉住的是「判定对抽取长度不敏感」
这个性质，而 GNU 页的真实抽取长度（25785）与可见文本（25245）本来就在文件旁边记着。
第 3 条同理：那份 Wikipedia 页面 200KB+ 也没入库，所以拿实测数字搭了一个同形状的
构造件（_wiki_shaped_page），并配上「删掉正文段落就退回 list」的对照，防止判据只是
把 list 关掉了。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dhole_mcp.envelope import (
    LIST_LABEL_SHARE_MIN, LIST_PROSE_RUN_MAX, _count_content_links,
    _label_share, _list_shape, detect_page_type,
)

FIXTURES = Path(__file__).parent / "page_fixtures"

GNU_URL = "https://www.gnu.org/philosophy/free-sw.html"
QUOTES_URL = "https://quotes.toscrape.com/"

# Recorded in the .meta.json sidecars; asserted against them below.
GNU = {"html": "gnu_free_sw_definition.html", "links": 112, "markdown": 25785,
       "visible": 25245, "share": 0.051, "run": 756}
QUOTES = {"html": "quotes_index.html", "links": 52, "markdown": 2179,
          "visible": 1681, "share": 0.264, "run": 148}


def _page(spec: dict) -> str:
    path = FIXTURES / spec["html"]
    meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
    html = path.read_text(encoding="utf-8", errors="replace")
    # The sidecar and the constants above must describe the bytes on disk.
    assert meta["content_links"] == spec["links"]
    return html


@pytest.mark.parametrize("spec,url", [(GNU, GNU_URL), (QUOTES, QUOTES_URL)])
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
    meta = json.loads((FIXTURES / spec["html"]).with_suffix(".meta.json")
                      .read_text(encoding="utf-8"))
    assert meta["label_share"] == pytest.approx(spec["share"], abs=0.001)
    assert meta["longest_nonlabel_run_chars"] == spec["run"]


def test_link_dense_article_page_is_not_a_list():
    """G16 本体：链接密集的正文页不能再判 list。"""
    html = _page(GNU)
    assert detect_page_type(html, GNU_URL, "text/html; charset=utf-8", GNU["markdown"]) != "list"


def test_verdict_does_not_follow_the_extractor_collapse():
    """抽取崩了（RFC 9110 实测 8.4k vs 全页 446k）时，判定必须不变。

    旧判据在这里会判成 list：112 条链接、文本量 200 < 1500。
    """
    html = _page(GNU)
    collapsed = detect_page_type(html, GNU_URL, "text/html; charset=utf-8", 200)
    honest = detect_page_type(html, GNU_URL, "text/html; charset=utf-8", GNU["markdown"])
    assert collapsed == honest != "list"


def test_real_index_page_is_still_a_list():
    """收紧不能把真列表页一起收走（报告的诉求两边都要满足）。"""
    html = _page(QUOTES)
    assert detect_page_type(html, QUOTES_URL, "text/html; charset=utf-8",
                            QUOTES["markdown"]) == "list"


def test_index_page_is_a_list_even_if_extraction_collapse():
    html = _page(QUOTES)
    assert detect_page_type(html, QUOTES_URL, "text/html; charset=utf-8", 200) == "list"


def test_verdict_does_not_depend_on_the_extraction_shape_asked_for():
    """G16 的同一件事，反方向：`extraction_type="html"` 时「抽出来的文本长度」是**标记语言**
    的长度（标签、属性、实体都算字符），在同一页上比正文本大好几倍 —— 实测
    quotes 首页走 markdown 是 2179、走 html 是 11021，只有后者过了列表页的文本量闸门，
    于是「换一种取法」能把 list 变成 unknown。文本量闸门读到的必须始终是正文量。
    """
    html = _page(QUOTES)
    ct = "text/html; charset=utf-8"
    markdown_shape = detect_page_type(html, QUOTES_URL, ct, QUOTES["markdown"])
    raw_html_shape = detect_page_type(html, QUOTES_URL, ct, len(html))
    assert markdown_shape == raw_html_shape == "list", (
        f"同一份 HTML，只因 extraction_type 不同就给出 {markdown_shape} / {raw_html_shape}")
    # 正文页那头也不能被换一种取法翻掉
    gnu = _page(GNU)
    assert (detect_page_type(gnu, GNU_URL, ct, len(gnu))
            == detect_page_type(gnu, GNU_URL, ct, GNU["markdown"]) != "list")


def test_threshold_sits_in_the_measured_gap():
    """0.10 落在实测两组之间：正文 ≤0.05，列表 ≥0.16。留一头就够，别贴着边界。"""
    assert _label_share(_page(GNU), GNU_URL.split("//", 1)[1]) < LIST_LABEL_SHARE_MIN
    assert _label_share(_page(QUOTES), QUOTES_URL.split("//", 1)[1]) > LIST_LABEL_SHARE_MIN


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
