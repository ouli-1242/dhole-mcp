"""声明标签一个都没有的首页，也该被找到它的 feed。

现场（2026-10-29）：`feed_read(url='https://www.fjdaily.com/')` 报 `not a feed`。
那是一个有 RSS 的报纸首页，但它不写 `<link rel="alternate" type="application/rss+xml">`
—— 从前的发现只认这一种声明，于是"feed_read 可以喂首页"这条说明书承诺，
在一整类站点上兑现不了。

契约：
  * 声明的 `<link>` 仍是第一顺位，且它成功时这一发仍然只花两次请求；
  * 没有声明时，页脚里以 RSS/Atom/Feed 为名或地址的**同源**锚点是第二顺位；
  * 连锚点都没有的**首页**，最后试 `/feed`、`/rss.xml` 一类常见路径；
  * 深层页面不猜常见路径 —— 那会把整站或别的栏目的源冒充成这一页的源；
  * 跨站锚点不跟随（声明标签可以指向第三方，页脚里的任意链接不行）；
  * 全部失败时，错误句点名试过哪些地址，而不是把一次探测写成"这不是 feed"。
"""

from __future__ import annotations

import pytest

from dhole_mcp import feed as feed_mod
from dhole_mcp.feed import fetch_feed

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<title>Blog</title>
<item><title>Post one</title><link>https://blog.test/1</link></item>
<item><title>Post two</title><link>https://blog.test/2</link></item>
</channel></rss>"""


def _server(table: dict):
    """A stand-in network: `_grab` answers from {url: body | (body, error)}."""
    calls: list[str] = []

    async def fake_grab(url, timeout, headers=None):
        calls.append(url)
        entry = table.get(url, (b"", "HTTP 404"))
        body, err = entry if isinstance(entry, tuple) else (entry, "")
        return body, (200 if body else 0), "utf-8", err, {}

    return fake_grab, calls


@pytest.fixture
def patch_grab(monkeypatch):
    def _apply(table):
        fake, calls = _server(table)
        monkeypatch.setattr(feed_mod, "_grab", fake)
        return calls
    return _apply


@pytest.mark.asyncio
async def test_a_footer_rss_anchor_is_followed(patch_grab):
    """没有 <link> 声明，但页脚写着 RSS：顺着它走，并说清源换了地址。"""
    page = (b"<!DOCTYPE html><html><head><title>Blog</title></head><body>"
            b"<h1>Blog</h1><footer><a href=\"/rss.xml\">RSS</a></footer>"
            b"</body></html>")
    calls = patch_grab({
        "https://blog.test/": page,
        "https://blog.test/rss.xml": RSS,
    })

    out = await fetch_feed("https://blog.test/")

    assert [i.title for i in out.items] == ["Post one", "Post two"], (out.items, out.error)
    assert out.source_url == "https://blog.test/rss.xml"
    assert out.discovered_from == "https://blog.test/"
    assert calls == ["https://blog.test/", "https://blog.test/rss.xml"]


@pytest.mark.asyncio
async def test_a_silent_homepage_is_probed_at_a_well_known_path(patch_grab):
    """连锚点都没有：首页试常见路径，/feed 命中。"""
    page = b"<!DOCTYPE html><html><head><title>Blog</title></head><body>x</body></html>"
    calls = patch_grab({
        "https://blog.test/": page,
        "https://blog.test/feed": RSS,
    })

    out = await fetch_feed("https://blog.test/")

    assert out.items, out.error
    assert out.source_url == "https://blog.test/feed"
    assert out.discovered_from == "https://blog.test/"
    assert calls == ["https://blog.test/", "https://blog.test/feed"]


@pytest.mark.asyncio
async def test_a_deep_page_is_not_probed_at_the_site_root(patch_grab):
    """深层页面不猜 /feed：那一发抓到的是站点的源，不是这一页的。"""
    page = b"<!DOCTYPE html><html><head><title>Post</title></head><body>x</body></html>"
    calls = patch_grab({"https://blog.test/posts/1": page})

    out = await fetch_feed("https://blog.test/posts/1")

    assert out.items == []
    assert out.error.startswith("not a feed:"), out.error
    assert calls == ["https://blog.test/posts/1"], (
        f"深层页面不该被猜常见路径：{calls}")


@pytest.mark.asyncio
async def test_a_cross_origin_anchor_is_not_followed(patch_grab):
    """页脚里的跨站 RSS 链接不是这个站的 feed，不许被我们抓。"""
    page = (b"<!DOCTYPE html><html><body>"
            b"<a href=\"https://evil.test/feed\">RSS</a></body></html>")
    calls = patch_grab({"https://blog.test/": page})

    await fetch_feed("https://blog.test/")

    assert "https://evil.test/feed" not in calls, calls


@pytest.mark.asyncio
async def test_a_declared_link_still_outranks_an_anchor(patch_grab):
    """声明是作者写下的合同：它成功时不许再去试页脚锚点。"""
    page = (b"<!DOCTYPE html><html><head>"
            b"<link rel=\"alternate\" type=\"application/rss+xml\" href=\"/declared.xml\">"
            b"</head><body><a href=\"/other.xml\">RSS</a></body></html>")
    calls = patch_grab({
        "https://blog.test/": page,
        "https://blog.test/declared.xml": RSS,
        "https://blog.test/other.xml": RSS,
    })

    out = await fetch_feed("https://blog.test/")

    assert out.source_url == "https://blog.test/declared.xml"
    assert calls == ["https://blog.test/", "https://blog.test/declared.xml"]


@pytest.mark.asyncio
async def test_every_probe_failing_is_reported_with_the_addresses(patch_grab):
    """全部落空时，句子要说试过哪些地址，而不是只宣布"这不是 feed"。"""
    page = b"<!DOCTYPE html><html><head><title>Blog</title></head><body>x</body></html>"
    patch_grab({"https://blog.test/": page})

    out = await fetch_feed("https://blog.test/")

    assert out.error.startswith("not a feed:"), out.error
    assert "https://blog.test/feed" in out.error, out.error
    assert "none of them parsed as a feed" in out.error, out.error
    assert out.error.count("\n") <= 1, out.error
