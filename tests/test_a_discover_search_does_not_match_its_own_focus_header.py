"""web_discover(search=...) 的内容补考不许把自己的 focus 头部当成命中。

2026-10-28 第二轮 H9 实测：`search='work'` 的 URL/标题过滤在靶站地图上 0 命中，
补考用 focus='work' 扫了 3 页，交回 `/article` 并声称"1 page carrying the word"。
同一条 content 的第一段却是 focus 头部，自己写着
"none of the 1 words you named occurs on this page ('work')" —— 命中来自头部里
回显的查询词，不是页面正文。按 diagnosis/summary 分支的调用方会引用一页没有
那个词的正文。
"""
from types import SimpleNamespace

from dhole_mcp.server import MasterFetchServer

_discover_page_body = MasterFetchServer._discover_page_body


def _page(content, title=""):
    return SimpleNamespace(content=content, title=title)


def test_a_focus_header_is_not_searchable_page_text():
    page = _page(["[Focus: 'work'; NO block cleared the relevance cut - none of the "
                  "1 words you named occurs on this page ('work').]\n\n"
                  "本页讲缓存与抓取，没有那个英文词。"], title="本地抓取工具配置指南")
    body = _discover_page_body(page)
    assert "work" not in body.lower(), body
    assert "缓存" in body


def test_a_page_without_a_focus_header_is_unchanged():
    page = _page(["第一段。\n\n第二段 true work appears here."], title="t")
    assert _discover_page_body(page) == "第一段。\n\n第二段 true work appears here."


def test_only_the_leading_focus_block_is_removed():
    """正文里真的出现查询词时仍要算命中 —— 这一条守的是"别剥过头"。"""
    page = _page(["[Focus: 'work'; x]\n\n第一段里 work 真的在正文里。"], title="t")
    body = _discover_page_body(page)
    assert "work" in body.lower(), body
    assert "第一段" in body
