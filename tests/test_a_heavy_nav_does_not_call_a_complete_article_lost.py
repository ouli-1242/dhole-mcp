"""R04-016a: heavy chrome must not make a complete article look collapsed.

The live shape: c-api/stable returned 14–19k extracted characters while
`visible_text_chars` was 39,327 — 48.5% on the whole-page ruler, just under the
0.5 gate, although the article had run to its last section. The page's nav and
chrome were being counted as text the extractor "lost".

`server._attach_dom_visible_chars` already measures the content area for thin
bodies; this batch makes that measurement lazy on large bodies too, but only
when the whole-page ratio already fired. The gate then asks both rulers.
"""

from __future__ import annotations

from dhole_mcp import crawl as crawl_mod
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _collapsed_by_both_rulers, extraction_collapsed
from dhole_mcp.server import _attach_dom_visible_chars

ARTICLE = "word " * 1_000          # ~5,000 visible characters
CHROME = "menu item " * 4_000      # ~40,000 visible characters


def _heavy_nav(article: str = ARTICLE) -> str:
    return ("<html><body><nav>" + CHROME +
            "</nav><article><p>" + article + "</p></article></body></html>")


def _plain(article: str = ARTICLE) -> str:
    return "<html><body><article><p>" + article + "</p></article></body></html>"


def test_a_complete_article_under_heavy_chrome_is_not_collapsed():
    result = ResponseModel(
        status=200, content=["x" * 4_000], url="https://e.test/a",
        content_type="text/html", fetcher_used="http",
        visible_text_chars=45_000,
    )
    result._dom_chrome_free_chars = 4_100

    assert extraction_collapsed(result) is False
    assert _collapsed_by_both_rulers(result, 4_000) is False


def test_a_real_loss_under_the_same_chrome_is_still_collapsed():
    result = ResponseModel(
        status=200, content=["tiny"], url="https://e.test/a",
        content_type="text/html", fetcher_used="http",
        visible_text_chars=45_000,
    )
    result._dom_chrome_free_chars = 4_100

    assert extraction_collapsed(result) is True


def test_an_unmeasured_second_ruler_keeps_the_old_verdict():
    result = ResponseModel(
        status=200, content=["x" * 1_000], url="https://e.test/a",
        content_type="text/html", fetcher_used="http",
        visible_text_chars=45_000,
    )

    assert result._dom_chrome_free_chars == -1
    assert extraction_collapsed(result) is True


def test_a_suspected_large_collapse_measures_the_prose_floor():
    result = ResponseModel(
        status=200, content=["tiny"], url="https://e.test/a",
        content_type="text/html", fetcher_used="http",
    )

    _attach_dom_visible_chars(result, _heavy_nav(), "https://e.test/a")

    assert result.visible_text_chars > 2_000
    assert result._dom_chrome_free_chars > 0, (
        "large suspected collapse skipped the second ruler")


def test_a_healthy_large_body_does_not_pay_for_the_second_parse():
    result = ResponseModel(
        status=200, content=[ARTICLE], url="https://e.test/a",
        content_type="text/html", fetcher_used="http",
    )

    _attach_dom_visible_chars(result, _plain(), "https://e.test/a")

    assert result._dom_chrome_free_chars == -1, (
        "a healthy page paid for a parse it did not need")


def test_the_crawl_row_uses_the_same_second_ruler():
    stats: dict = {}
    crawl_mod._measure_row(
        stats, _heavy_nav(ARTICLE), ARTICLE, "article",
        "https://e.test/a", "https://e.test/")

    assert not stats.get("extraction_collapsed"), stats
    assert stats["visible_chars"] > 2 * stats["extracted_chars"]


def test_the_crawl_row_still_flags_a_real_loss():
    stats: dict = {}
    crawl_mod._measure_row(
        stats, _heavy_nav(ARTICLE), "tiny", "article",
        "https://e.test/a", "https://e.test/")

    assert stats.get("extraction_collapsed") is True, stats
