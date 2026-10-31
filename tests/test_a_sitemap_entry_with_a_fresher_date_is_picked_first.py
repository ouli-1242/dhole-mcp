"""When two sitemap entries score the same, the newer lastmod goes first.

Document order in a sitemap says what the site listed first, not what it changed
last. ``lastmod`` is the only freshness signal the sitemap itself carries; using
it as a tie-break makes a map budget land on maintained pages first without
dropping or demoting anything.
"""

from __future__ import annotations

import pytest

from dhole_mcp.crawl import _order_map_entries
from dhole_mcp.sitemap import SitemapURL

pytestmark = pytest.mark.smoke


def test_the_newer_lastmod_wins_a_tie_and_undated_rows_stay_after_it():
    entries = [
        SitemapURL(url="https://site.test/old", lastmod="2024-01-01"),
        SitemapURL(url="https://site.test/new", lastmod="2026-09-27T08:00:00Z"),
        SitemapURL(url="https://site.test/undated"),
    ]

    shipping, _stats = _order_map_entries(entries, "")

    assert [row.url for row in shipping] == [
        "https://site.test/new",
        "https://site.test/old",
        "https://site.test/undated",
    ]


def test_an_unparseable_date_is_not_treated_as_now():
    entries = [
        SitemapURL(url="https://site.test/broken", lastmod="not-a-date"),
        SitemapURL(url="https://site.test/dated", lastmod="2025-01-01"),
    ]

    shipping, _stats = _order_map_entries(entries, "")

    assert [row.url for row in shipping] == [
        "https://site.test/dated",
        "https://site.test/broken",
    ]
