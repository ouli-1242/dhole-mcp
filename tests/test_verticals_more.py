"""17.7 新增的免密纵向源：Bing News / OpenAlex / Reddit / Wikipedia / 三个包注册表。

判据只有两条：
1. 每家只读自己的真形状（RSS / JSON），URL 与日期从该形状里来，不靠猜。
2. 新后端挂在既有 kind 下时，`source_detail` 仍按后端各说各的；`reference` 是
   新 kind，但它仍然不是网页索引家族，共识串必须保持为空。

所有网络调用都走注入的 fetch，默认套件不碰网络。
"""

from __future__ import annotations

import json

import pytest

from dhole_mcp import search_verticals as sv

pytestmark = pytest.mark.smoke


_BING_NEWS = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:News="https://www.bing.com/news/search?q=x&amp;format=RSS">
  <channel>
    <title>transformer - BingNews</title>
    <item>
      <title>Quake hits the coast</title>
      <link>http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;url=https%3a%2f%2fnews.example.test%2Fquake&amp;c=1</link>
      <description>Coastal damage reported.</description>
      <pubDate>Sun, 25 Oct 2026 17:00:00 GMT</pubDate>
      <News:Source>Example News</News:Source>
    </item>
    <item>
      <title>Second story</title>
      <link>https://publisher.example.test/second</link>
      <description>More coverage.</description>
      <pubDate>Mon, 26 Oct 2026 08:30:00 GMT</pubDate>
    </item>
  </channel>
</rss>"""

_OPENALEX = json.dumps({"results": [{
    "display_name": "Task Groups for Everyone",
    "doi": "https://doi.org/10.1000/xyz",
    "publication_date": "2024-01-02",
    "publication_year": 2024,
    "cited_by_count": 12,
    "authorships": [
        {"author": {"display_name": "Ada Lovelace"}},
        {"author": {"display_name": "Alan Turing"}},
    ],
    "primary_location": {
        "landing_page_url": "https://journal.example.test/paper",
        "source": {"display_name": "Journal of Tasks"},
    },
    "abstract_inverted_index": {
        "We": [0], "study": [1], "structured": [2], "concurrency.": [3],
    },
}]}).encode()

_PULLPUSH = json.dumps({"data": [{
    "title": "TaskGroup patterns",
    "permalink": "/r/Python/comments/abc/taskgroup_patterns/",
    "url": "https://blog.example.test/taskgroups",
    "subreddit": "Python",
    "score": 42,
    "num_comments": 7,
    "created_utc": 1704164645,
    "selftext": "How do you structure this?",
    "over_18": False,
}, {
    "title": "removed post",
    "permalink": "/r/example/comments/dead/removed/",
    "over_18": False,
    "removed_by_category": "moderator",
}]}).encode()

_WIKIPEDIA = json.dumps({"query": {"search": [{
    "title": "Transformer (deep learning)",
    "snippet": 'A <span class="searchmatch">transformer</span> is a model.',
    "wordcount": 1234,
}]}}).encode()

_RUBYGEMS = json.dumps({
    "name": "rails",
    "version": "8.1.4",
    "version_created_at": "2026-09-24T14:22:14.180Z",
    "downloads": 809658919,
    "licenses": ["MIT"],
    "info": "Full-stack framework",
    "homepage_uri": "https://rubyonrails.org",
}).encode()

_NUGET = json.dumps({"data": [{
    "id": "Newtonsoft.Json",
    "version": "13.0.3",
    "description": "Json.NET is a popular high-performance JSON framework.",
    "totalDownloads": 1000,
    "authors": ["James Newton-King"],
}]}).encode()

_PACKAGIST = json.dumps({"packages": {"monolog/monolog": [{
    "version": "3.9.0",
    "description": "Sends your logs to files, sockets, inboxes, and various web services.",
    "license": ["MIT"],
    "time": "2026-01-02T00:00:00+00:00",
    "source": {"url": "https://github.com/Seldaek/monolog.git"},
}]}}).encode()


def _fetch_for(routes: dict[str, tuple[int, bytes]]):
    calls: list[str] = []

    async def fetch(url: str, headers: dict, timeout: float):
        calls.append(url)
        for needle, response in routes.items():
            if needle in url:
                return response
        raise AssertionError(f"unexpected URL: {url}")

    return fetch, calls


@pytest.mark.asyncio
async def test_bing_news_returns_the_publisher_url_not_the_apiclick_wrapper():
    fetch, _ = _fetch_for({"bing.com/news/search": (200, _BING_NEWS)})
    hits, backend, status, error = await sv._search_bing_news(
        "transformer", 5, 0, "us-en", None, 8.0, fetch)

    assert (backend, status, error) == ("bing_news", "ok", "")
    assert [hit.url for hit in hits] == [
        "https://news.example.test/quake",
        "https://publisher.example.test/second",
    ]
    assert hits[0].published == "2026-10-25"
    assert "Example News" in hits[0].snippet


@pytest.mark.asyncio
async def test_bing_news_has_no_page_two_and_says_so():
    fetch, calls = _fetch_for({"bing.com/news/search": (200, _BING_NEWS)})
    hits, _backend, status, error = await sv._search_bing_news(
        "transformer", 5, 1, "us-en", None, 8.0, fetch)

    assert hits == [] and status == "empty"
    assert "no page offset" in error
    assert calls == []


@pytest.mark.asyncio
async def test_openalex_reads_a_work_and_rebuilds_the_inverted_abstract():
    fetch, _ = _fetch_for({"api.openalex.org/works": (200, _OPENALEX)})
    hits, backend, status, error = await sv._search_openalex(
        "task groups", 5, 0, "us-en", None, 8.0, fetch)

    assert (backend, status, error) == ("openalex", "ok", "")
    hit = hits[0]
    assert hit.url == "https://doi.org/10.1000/xyz"
    assert hit.published == "2024-01-02"
    assert "Ada Lovelace" in hit.snippet
    assert "We study structured concurrency." in hit.snippet


@pytest.mark.asyncio
async def test_reddit_uses_the_public_archive_and_drops_removed_posts():
    fetch, _ = _fetch_for({"api.pullpush.io": (200, _PULLPUSH)})
    hits, backend, status, error = await sv._search_reddit(
        "task groups", 5, 0, "us-en", None, 8.0, fetch)

    assert (backend, status, error) == ("reddit", "ok", "")
    assert len(hits) == 1
    assert hits[0].url == "https://blog.example.test/taskgroups"
    assert hits[0].published == "2024-01-02"
    assert "r/Python" in hits[0].snippet and "42 points" in hits[0].snippet


@pytest.mark.asyncio
async def test_wikipedia_picks_the_query_language_and_keeps_article_urls():
    fetch, _ = _fetch_for({"en.wikipedia.org": (200, _WIKIPEDIA)})
    hits, backend, status, error = await sv._search_wikipedia(
        "transformer", 5, 0, "us-en", "en", 8.0, fetch)

    assert (backend, status, error) == ("wikipedia", "ok", "")
    assert hits[0].kind == "reference"
    assert hits[0].url == (
        "https://en.wikipedia.org/wiki/Transformer_(deep_learning)")
    assert "<span" not in hits[0].snippet

    zh_fetch, _ = _fetch_for({"zh.wikipedia.org": (200, _WIKIPEDIA)})
    zh_hits, _backend, status, _error = await sv._search_wikipedia(
        "注意力机制", 5, 0, "cn-zh", None, 8.0, zh_fetch)
    assert status == "ok"
    assert zh_hits[0].url.startswith("https://zh.wikipedia.org/wiki/")

    ja_fetch, _ = _fetch_for({"ja.wikipedia.org": (200, _WIKIPEDIA)})
    ja_hits, _backend, status, _error = await sv._search_wikipedia(
        "トランスフォーマー", 5, 0, "jp-ja", None, 8.0, ja_fetch)
    assert status == "ok"
    assert ja_hits[0].url.startswith("https://ja.wikipedia.org/wiki/")


@pytest.mark.asyncio
async def test_rubygems_reads_the_exact_gem():
    fetch, _ = _fetch_for({"rubygems.org/api/v1/gems": (200, _RUBYGEMS)})
    hits, backend, status, _error = await sv._search_rubygems(
        "rails", 5, 0, "us-en", None, 8.0, fetch)

    assert (backend, status) == ("rubygems", "ok")
    assert hits[0].url == "https://rubygems.org/gems/rails"
    assert "v8.1.4" in hits[0].snippet
    assert hits[0].published == "2026-09-24"


@pytest.mark.asyncio
async def test_nuget_does_not_accept_a_fuzzy_match_as_the_exact_package():
    fetch, _ = _fetch_for({"azuresearch-usnc.nuget.org": (
        200, json.dumps({"data": [{"id": "Other.Package", "version": "1.0.0"}]}).encode())})
    hits, backend, status, error = await sv._search_nuget(
        "newtonsoft.json", 5, 0, "us-en", None, 8.0, fetch)

    assert (backend, status) == ("nuget", "empty")
    assert hits == [] and "no package named" in error


@pytest.mark.asyncio
async def test_packagist_requires_vendor_and_package():
    fetch, calls = _fetch_for({"repo.packagist.org": (200, _PACKAGIST)})
    hits, _backend, status, error = await sv._search_packagist(
        "monolog", 5, 0, "us-en", None, 8.0, fetch)
    assert status == "bad_query" and "vendor/package" in error
    assert calls == []

    hits, _backend, status, _error = await sv._search_packagist(
        "monolog/monolog", 5, 0, "us-en", None, 8.0, fetch)
    assert status == "ok"
    assert hits[0].url == "https://packagist.org/packages/monolog/monolog"


def test_new_backends_are_registered_under_their_kinds():
    expected = {
        "news": "bing_news",
        "academic": "openalex",
        "community": "reddit",
        "package": "rubygems",
        "reference": "wikipedia",
    }
    for kind, backend in expected.items():
        assert backend in [name for name, _adapter in sv._KIND_ADAPTERS[kind]]
    assert "reference" in sv.SOURCE_KINDS


@pytest.mark.asyncio
async def test_reference_kind_returns_a_vertical_row_with_no_web_consensus():
    fetch, _ = _fetch_for({"wikipedia.org": (200, _WIKIPEDIA)})
    hits, reports = await sv.search_many(
        ["reference"], "transformer", 5, fetch=fetch)

    assert hits and hits[0].kind == "reference"
    assert reports[0].source == "reference"
    assert reports[0].backend == "wikipedia"
    assert reports[0].status == "ok"
