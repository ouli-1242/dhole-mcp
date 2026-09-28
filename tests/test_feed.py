"""Tests for RSS/Atom feed parsing (feed.py)."""

from datetime import datetime, timezone

import pytest

from dhole_mcp.feed import (
    _parse_feed_xml, _parse_date, _keep_newer, fetch_feed, fetch_feeds,
    FeedItem, FeedResult,
)

RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Channel</title>
    <item>
      <title>Old Post</title>
      <link>https://example.com/old</link>
      <pubDate>Mon, 01 Jan 2024 10:00:00 +0000</pubDate>
      <description>First paragraph of old post.</description>
    </item>
    <item>
      <title>New Post</title>
      <link>https://example.com/new</link>
      <pubDate>Tue, 02 Jan 2024 10:00:00 +0000</pubDate>
      <description>Fresh content here.</description>
    </item>
  </channel>
</rss>
"""

ATOM_XML = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Feed</title>
  <entry>
    <title>Entry One</title>
    <link href="https://example.com/one" />
    <published>2024-05-01T08:00:00Z</published>
    <summary>Summary text.</summary>
  </entry>
</feed>
"""


class TestParseDate:
    def test_rfc822(self):
        assert _parse_date("Tue, 02 Jan 2024 10:00:00 +0000").startswith("2024-01-02T10:00:00")

    def test_iso_with_z(self):
        assert _parse_date("2024-05-01T08:00:00Z").startswith("2024-05-01T08:00:00")

    def test_empty(self):
        assert _parse_date("") == ""

    def test_garbage(self):
        assert _parse_date("not a date") == ""


class TestParseRss:
    def test_parses_items_and_title(self):
        result = _parse_feed_xml(RSS_XML, "https://example.com/feed.xml")
        assert result.source_title == "Test Channel"
        assert len(result.items) == 2

    def test_newest_first(self):
        result = _parse_feed_xml(RSS_XML, "https://example.com/feed.xml")
        assert result.items[0].title == "New Post"
        assert result.items[1].title == "Old Post"

    def test_item_fields(self):
        result = _parse_feed_xml(RSS_XML, "https://example.com/feed.xml")
        item = result.items[0]
        assert item.url == "https://example.com/new"
        assert "Fresh content" in item.summary
        assert item.published.startswith("2024-01-02")


class TestParseAtom:
    def test_parses_atom(self):
        result = _parse_feed_xml(ATOM_XML, "https://example.com/atom.xml")
        assert result.source_title == "Atom Feed"
        assert len(result.items) == 1
        assert result.items[0].title == "Entry One"
        assert result.items[0].url == "https://example.com/one"
        assert result.items[0].published.startswith("2024-05-01")


class TestFetchFeed:
    @pytest.mark.asyncio
    async def test_fetch_error_isolated(self):
        result = await fetch_feed("http://127.0.0.1:1/nonexistent", timeout=2)
        assert isinstance(result, FeedResult)
        assert result.items == []
        assert result.error  # fetch failed, error surfaced

    @pytest.mark.asyncio
    async def test_fetch_feeds_batch(self, monkeypatch):
        async def fake_fetch(url, timeout=20, max_items=20, since="",
                             if_modified_since="", if_none_match=""):
            return FeedResult(source_url=url, source_title=url, items=[FeedItem(title="x")])
        monkeypatch.setattr("dhole_mcp.feed.fetch_feed", fake_fetch)
        results = await fetch_feeds(["https://a.com/feed", "https://b.com/feed"])
        assert len(results) == 2
        assert all(r.source_title for r in results)


ARXIV_QUIET_RSS = """<?xml version='1.0' encoding='UTF-8'?>
<rss xmlns:arxiv="http://arxiv.org/schemas/atom" version="2.0">
  <channel>
    <title>cs.LG updates on arXiv.org</title>
    <lastBuildDate>Sun, 27 Sep 2026 04:00:00 +0000</lastBuildDate>
    <pubDate>Sun, 27 Sep 2026 00:00:00 -0400</pubDate>
    <skipDays>
      <day>Saturday</day>
      <day>Sunday</day>
    </skipDays>
  </channel>
</rss>
"""

EMPTY_ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>No entries yet</title>
  <updated>2026-09-27T21:00:00Z</updated>
</feed>
"""


class TestAQuietFeedExplainsItself:
    """`items: []` 加空 `error` 是两种相反情况共有的形状。

    「今天没更新」和「我们的解析器跟它的结构脱节了」从这里看完全一样，而修法
    相反：前者等着，后者要改代码。ARXIV_QUIET_RSS 是真实抓回来的文档（arXiv
    周末刊），它自己就把原因写在 channel 里 —— 以前这些标记被整段丢掉。
    """

    def test_the_documents_own_markers_travel_with_the_answer(self):
        r = _parse_feed_xml(ARXIV_QUIET_RSS, "https://arxiv.org/rss/cs.LG")
        assert r.items == [] and r.error == ""
        assert "lastBuildDate=Sun, 27 Sep 2026 04:00:00 +0000" in r.note
        assert "skipDays=Saturday,Sunday" in r.note

    def test_it_says_what_it_is_ruling_out(self):
        r = _parse_feed_xml(ARXIV_QUIET_RSS, "https://arxiv.org/rss/cs.LG")
        assert "parsed as a feed" in r.note, \
            "得说明这不是解析失败，否则读的人以为坏了"

    def test_atom_is_covered_too(self):
        r = _parse_feed_xml(EMPTY_ATOM, "https://example.com/atom.xml")
        assert r.items == []
        assert "updated=2026-09-27T21:00:00Z" in r.note

    def test_a_feed_with_items_says_nothing_extra(self):
        assert _parse_feed_xml(RSS_XML, "https://example.com/feed.xml").note == ""
        assert _parse_feed_xml(ATOM_XML, "https://example.com/atom.xml").note == ""

    def test_a_filter_that_emptied_a_real_feed_owns_the_explanation(self):
        """条目是被 since 滤掉的，就不该由 note 来说"文档本来是空的"。"""
        r = _parse_feed_xml(RSS_XML, "https://example.com/feed.xml")
        out = _keep_newer(r, "2024-03-01T00:00:00+00:00",
                          datetime(2024, 3, 1, tzinfo=timezone.utc), 20)
        assert out.items == [] and out.note == ""
        assert out.items_older_than_since == 2, "收据才是这里的解释"

    def test_an_empty_document_keeps_its_note_through_a_poll(self):
        """本来就没有条目 + 带 since：收据会是 0，只有 note 还能说明情况。"""
        r = _parse_feed_xml(ARXIV_QUIET_RSS, "https://arxiv.org/rss/cs.LG")
        out = _keep_newer(r, "2026-09-01T00:00:00+00:00",
                          datetime(2026, 9, 1, tzinfo=timezone.utc), 20)
        assert out.items == [] and out.items_older_than_since == 0
        assert "skipDays" in out.note

    def test_a_document_with_no_markers_at_all_still_says_so(self):
        """没有任何日期标记的空文档，note 本身就是那条值得看到的观察。"""
        r = _parse_feed_xml(
            '<rss version="2.0"><channel><title>T</title></channel></rss>',
            "https://example.com/bare.xml")
        assert r.items == []
        assert r.note == "0 entries in a document that parsed as a feed"


class TestAnItemPreviewOwnsItsCut:
    """摘要的 500 字预览上限一直是静默的。

    被拦腰截断的一条摘要，看起来跟「这个 feed 本来就只写了这么多」完全一样，
    而 agent 无从知道自己拿到的是半句 —— 一个 URL 断在中间时尤其危险，因为它
    会被当成一条合法链接用出去。截断现在由 summary_truncated 自己说出口。
    """

    LONG = "x" * 620

    def _rss(self, description):
        return ('<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>'
                '<item><title>i</title><link>https://e.test/1</link>'
                f'<description>{description}</description></item></channel></rss>')

    def test_a_longer_description_is_previewed_and_says_so(self):
        item = _parse_feed_xml(self._rss(self.LONG), "https://e.test/f").items[0]
        assert len(item.summary) == 500
        assert item.summary_truncated is True

    def test_a_short_one_is_whole_and_says_nothing_extra(self):
        item = _parse_feed_xml(self._rss("short"), "https://e.test/f").items[0]
        assert item.summary == "short"
        assert item.summary_truncated is False

    def test_exactly_the_cap_is_not_a_cut(self):
        """边界：正好 500 字是「完整」，不是「被裁过」。"""
        item = _parse_feed_xml(self._rss("y" * 500), "https://e.test/f").items[0]
        assert item.summary_truncated is False

    def test_atom_uses_the_same_rule(self):
        xml = ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
               '<title>T</title><entry><title>e</title>'
               f'<content>{self.LONG}</content></entry></feed>')
        item = _parse_feed_xml(xml, "https://e.test/a").items[0]
        assert item.summary_truncated is True

    def test_the_flag_travels_only_when_it_is_true(self):
        from dhole_mcp.server import _wire_json
        rows = FeedResult(source_url="https://e.test/f", items=[
            FeedItem(title="cut", summary="x" * 500, summary_truncated=True),
            FeedItem(title="whole", summary="short")])
        payload = _wire_json({"feeds": [rows.model_dump()]})[1]
        cut, whole = payload["feeds"][0]["items"]
        assert cut["summary_truncated"] is True
        assert "summary_truncated" not in whole, \
            "默认值不该上线：每条 item 都带一个 false 是纯开销"

    def test_the_wire_tells_the_reader_the_cap_exists(self):
        """看见 summary_truncated 之前，agent 得先知道有这个上限。"""
        from dhole_mcp.server import MasterFetchServer
        desc = [t["description"] for t in MasterFetchServer._TOOL_DEFS
                if t["name"] == "feed_fetch"][0]
        assert "500" in desc and "summary_truncated" in desc
