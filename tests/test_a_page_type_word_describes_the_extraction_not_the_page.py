"""R03-004：page_type 的词表是「抽取形状」，不是「页面体裁」。

现场（round-03 P10）：B 站视频频道索引页被标 article——抽取没错（trafilatura
找到了散文），错在字段描述只说 "How the page was extracted"，读者把它当成
「这页是文章」。描述必须自己把「article = 散文抽取成功，不代表页面本身是
文章」说出口，词表本身不动（article/list/js_shell/fallback 的语义在库内
一致）。
"""
from dhole_mcp.crawl import CrawlPage


def test_page_type_says_what_article_means():
    desc = CrawlPage.model_fields["page_type"].description or ""
    assert "article" in desc
    # 名词必须自证是抽取形状：散文抽到了 = article，与页面真实体裁无关。
    assert "prose" in desc.lower(), (
        f"page_type description must say article means prose was extracted "
        f"(extraction shape), got: {desc!r}")
    assert "genre" in desc.lower() or "not the page" in desc.lower(), (
        f"page_type description must disclaim the page's real genre, got: {desc!r}")
