"""G19 回归：把站点页当 feed 传进来时，要顺着页面自己声明的链接找到 feed。

报告的复现是 `feed_fetch("https://quotes.toscrape.com/")` 回一句
`XMLSyntaxError: Opening and ending tag mismatch: meta line 5 ...`。那句话没错，但它
回答的是「为什么这不是 XML」，而调用方的问题是「这个站发了什么」。一个 HTML 首页几乎
总是用 `<link rel="alternate" type="application/rss+xml">` 写明自己的 feed 在哪，
所以那一步该由工具走掉；走不到时也要给一句人话 + 试过的候选，而不是解析器的转储。

还有一条安全边界：发现到的链接是**页面给的**，不是用户给的，所以它同样要过
validate_url —— 一个恶意页面不能借「自动发现」把工具指向内网地址。
"""

from __future__ import annotations

import pytest

from dhole_mcp import feed as feed_mod
from dhole_mcp.feed import (
    FeedResult,
    _discover_feed_links,
    _friendly_parse_error,
    _looks_like_html,
    fetch_feed,
)

PAGE = b"""<!DOCTYPE html><html><head><title>Blog</title>
<link rel="alternate" type="application/rss+xml" title="RSS" href="/feed.xml">
</head><body><h1>Blog</h1><meta charset="utf-8"></body></html>"""

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<title>Blog</title>
<item><title>Post one</title><link>https://blog.test/1</link></item>
<item><title>Post two</title><link>https://blog.test/2</link></item>
</channel></rss>"""


def _server(table: dict):
    """A stand-in network: `_grab` answers from {url: (body, error)}."""
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
class TestThePageTellsYouWhereItsFeedIs:

    async def test_a_site_page_is_followed_to_its_own_feed(self, patch_grab):
        calls = patch_grab({
            "https://blog.test/": PAGE,
            "https://blog.test/feed.xml": RSS,
        })

        out = await fetch_feed("https://blog.test/")

        assert [i.title for i in out.items] == ["Post one", "Post two"]
        assert out.error == ""
        assert out.source_url == "https://blog.test/feed.xml"
        assert out.discovered_from == "https://blog.test/", "换了地址必须说出来"
        assert calls == ["https://blog.test/", "https://blog.test/feed.xml"]

    async def test_a_broken_first_candidate_does_not_end_the_search(self, patch_grab):
        patch_grab({
            "https://blog.test/": b"""<html><head>
<link rel="alternate" type="application/rss+xml" href="/broken">
<link rel="alternate" type="application/atom+xml" href="/atom">
</head><body>x</body></html>""",
            "https://blog.test/broken": b"<?xml version='1.0'?><rss><unclosed>",
            "https://blog.test/atom": b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>A</title>
<entry><title>From atom</title><link href="https://blog.test/a1"/></entry></feed>""",
        })

        out = await fetch_feed("https://blog.test/")

        assert [i.title for i in out.items] == ["From atom"], (out.items, out.error)
        assert out.source_url == "https://blog.test/atom"

    async def test_a_real_feed_url_costs_exactly_one_request(self, patch_grab):
        calls = patch_grab({"https://blog.test/feed.xml": RSS})

        out = await fetch_feed("https://blog.test/feed.xml")

        assert out.items and out.discovered_from == ""
        assert calls == ["https://blog.test/feed.xml"], "已经是 feed 就不该多跑一步"

    async def test_a_relative_href_is_resolved_against_the_page(self):
        found = _discover_feed_links(
            PAGE.decode(), "https://blog.test/deep/index.html")
        assert found == ["https://blog.test/feed.xml"]


@pytest.mark.asyncio
class TestWhatIsSaidWhenNothingIsAFeed:

    async def test_an_html_page_with_no_feed_link_gives_one_readable_line(self, patch_grab):
        patch_grab({"https://blog.test/": PAGE.replace(b'<link rel="alternate"',
                                                       b'<link rel="stylesheet"')})

        out = await fetch_feed("https://blog.test/")

        assert out.items == []
        assert out.error.startswith("not a feed:"), out.error
        assert "HTML page" in out.error
        assert "line 5" not in out.error, "解析器的行号对调用方没有意义"
        assert out.error.count("\n") <= 1, out.error

    async def test_the_candidates_that_failed_are_listed(self, patch_grab):
        patch_grab({
            "https://blog.test/": PAGE,
            "https://blog.test/feed.xml": b"<html><body>also a page</body></html>",
        })

        out = await fetch_feed("https://blog.test/")

        assert "feed.xml" in out.error, out.error
        assert "none of them parsed" in out.error

    async def test_a_fetch_failure_is_reported_as_is(self, patch_grab):
        patch_grab({"https://blog.test/feed": (b"", "HTTP 410")})

        out = await fetch_feed("https://blog.test/feed")

        assert out.error == "HTTP 410"


@pytest.mark.asyncio
class TestADiscoveredLinkIsNotATrustedOne:
    """页面给的地址和站内任何一段文本一样不可信：它不能把工具带进内网。"""

    async def test_a_private_discovered_target_is_refused_before_the_request(
            self, patch_grab, monkeypatch):
        page = b"""<html><head><link rel="alternate" type="application/rss+xml"
        href="http://169.254.169.254/latest/meta-data/feed"></head><body>x</body></html>"""
        calls = patch_grab({"https://blog.test/": page})

        out = await fetch_feed("https://blog.test/")

        assert calls == ["https://blog.test/"], "元数据端点不能被发现步骤访问"
        assert out.items == [] and out.error

    async def test_a_loopback_discovered_target_is_refused_too(self, patch_grab):
        page = b"""<html><head><link rel="alternate" type="application/atom+xml"
        href="http://127.0.0.1:8080/feed"></head><body>x</body></html>"""
        calls = patch_grab({"https://blog.test/": page})

        await fetch_feed("https://blog.test/")

        assert calls == ["https://blog.test/"]


class TestTheSniffer:
    @pytest.mark.parametrize("body,expected", [
        (b"<!DOCTYPE html><html>", True),
        (b"\n\n<html lang=en>", True),
        (b'<?xml version="1.0"?><rss>', False),
        (b"", False),
    ])
    def test_html_is_told_apart_from_xml(self, body, expected):
        assert _looks_like_html(body) is expected

    def test_a_feed_result_still_defaults_the_new_field(self):
        assert FeedResult(source_url="https://x.test").discovered_from == ""


def test_the_lxml_dump_is_not_the_answer_anyway():
    exc = Exception("Opening and ending tag mismatch: meta line 5, column 99, line 6")
    line = _friendly_parse_error(exc, "", tried_html=True)

    assert "line 5" not in line
    assert "not a feed" in line and "Pass the feed URL itself" in line
