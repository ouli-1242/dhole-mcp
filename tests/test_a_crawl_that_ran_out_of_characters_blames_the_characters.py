"""字符预算先到时，不许把停下的原因记在路径过滤头上（2026-10-08 验收 · D-04 的回归锁）

现场（本机 mock 站点，真实 `web_crawl`）：站点每页 4,346 字符，调用方把整轮字符帽设成
`max_total_chars=1500` —— 第一页就付完了钱，队列里还挂着 83 个 URL。回执里那句 note 却
写着

    "only the start page was kept - path_include=['/docs'] / path_exclude=[] scoped out
     every discovered link (nothing was left queued)"

而 `path_include=['/docs']` 恰恰是**对的**：同一批链接在第二次调用里被这个 scope 收下了。
一次停止不能同时有两个原因，字符预算那条已经在同一句里说了 "truncation=total_chars …
83 URL(s) still queued" —— 两句互相拆台时，读者只会挑一句不信。

判据：那句"过滤器把链接都滤掉了"为真，需要**队列真的空**（并且不是被字符预算砍空的）。
本轮补的就是这一条门槛，此处补的是它的回归锁：

* 队列还有残余时，note 必须闭嘴（哪怕 path_include 确实给了）；
* 队列真空、且确实只剩起始页时，note 必须照说 —— 这是当年"看起来像一次成功的爬取"
  那个假象唯一的解药，摘掉它就是倒退；
* 起始页因为别的闸门没进来时，不能怪 scope。
"""

from __future__ import annotations

import pytest

from dhole_mcp import crawl as crawl_mod
from dhole_mcp.models import ResponseModel

SEED = "https://scoped.example.com/"


class _FakeSite:
    """每一页秒回；页面正文按 URL 查表，链接从 HTML 里现取。"""

    def __init__(self, pages: dict[str, str]) -> None:
        self.fetched: list[str] = []
        self.pages = pages

    async def web_read(self, url, **kwargs):
        self.fetched.append(url)
        return ResponseModel(url=url, status=200, content=[self.pages.get(url, "<p>leaf</p>")],
                             content_type="text/html", fetcher_used="http",
                             content_ok=True)


def _page_with_links(links: list[str], filler: int = 0) -> str:
    body = "<p>" + ("word " * filler) + "</p>" if filler else "<p>start</p>"
    return ("<html><body>" + body
            + "".join(f'<a href="{h}">link</a>' for h in links)
            + "</body></html>")


SCOPE_NOTE = "scoped out every"


@pytest.mark.asyncio
async def test_a_character_budget_does_not_blame_the_scope_filter():
    """症状本体：预算先到、队列里还挂着 83 个 URL，note 却怪 path_include。"""
    links = [f"/docs/p{i}" for i in range(12)]
    site = _FakeSite({SEED: _page_with_links(links, filler=900)})   # 一页就超帽

    out = await crawl_mod.web_crawl(site, SEED, max_pages=20, max_depth=1,
                                      path_include=["/docs"], max_total_chars=1500,
                                      cache_ttl=0)

    assert SCOPE_NOTE not in out.summary, out.summary
    assert "truncation=total_chars" in out.summary, out.summary
    assert "still queued" in out.summary, out.summary


@pytest.mark.asyncio
async def test_a_scope_that_really_did_scatter_everything_still_says_so():
    """反向锁：这条 note 不是被"少说"解决的 —— 队列真空时它是唯一说明这件事的地方。"""
    site = _FakeSite({SEED: _page_with_links(["/docs/a", "/docs/b", "/news/c"])})

    out = await crawl_mod.web_crawl(site, SEED, max_pages=20, max_depth=1,
                                      path_include=["/nothing-here"], cache_ttl=0)

    assert out.pages_crawled == 1, [p.url for p in out.pages]
    assert SCOPE_NOTE in out.summary, out.summary
    assert "/nothing-here" in out.summary, out.summary


@pytest.mark.asyncio
async def test_the_two_claims_are_never_both_made_about_one_stop():
    """一句里可以同时有预算与 scope 的陈述，但"过滤器清空了队列"这一条只能有一个真相。"""
    links = [f"/docs/p{i}" for i in range(10)]
    site = _FakeSite({SEED: _page_with_links(links, filler=900)})

    out = await crawl_mod.web_crawl(site, SEED, max_pages=20, max_depth=1,
                                      path_include=["/docs"], max_total_chars=1500,
                                      cache_ttl=0)

    blames_filter = SCOPE_NOTE in out.summary
    blames_budget = "truncation=total_chars" in out.summary
    assert not (blames_filter and blames_budget), out.summary


@pytest.mark.asyncio
async def test_a_run_that_kept_more_than_the_start_page_says_nothing_about_scope():
    """起始页之外还有页面时，"过滤器把每个链接都滤掉了"本身就是假话。"""
    links = ["/docs/a", "/docs/b", "/news/c"]
    site = _FakeSite({SEED: _page_with_links(links)})

    out = await crawl_mod.web_crawl(site, SEED, max_pages=5, max_depth=1,
                                      path_include=["/docs"], cache_ttl=0)

    assert out.pages_crawled > 1, out.pages_crawled
    assert SCOPE_NOTE not in out.summary, out.summary
