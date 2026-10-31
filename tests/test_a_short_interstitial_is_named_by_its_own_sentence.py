"""New wall/interstitial wordings join the existing verdict, with the same length gates.

A 200 response that says "Service temporarily unavailable" or "Checking if the
site connection is secure" is a placeholder, not an article. The word tables are
only half the test: a real article *about* one of these sentences must stay an
article, which is what the length caps are for.
"""

from __future__ import annotations

import pytest

from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _is_bot_wall, _is_soft_failure

pytestmark = pytest.mark.smoke


def _page(text: str, *, url: str = "https://site.test/page") -> ResponseModel:
    return ResponseModel(url=url, status=200, content=[text],
                         content_type="text/html")


@pytest.mark.parametrize("sentence", [
    "Checking if the site connection is secure",
    "Enable JavaScript and cookies to continue",
    "Verify you are human",
    "Attention Required! | Cloudflare",
])
def test_a_short_challenge_page_is_a_bot_wall(sentence):
    assert _is_bot_wall(_page(sentence))


@pytest.mark.parametrize("sentence", [
    "Service temporarily unavailable",
    "Too many requests",
    "请求过于频繁",
    "服务暂时不可用",
])
def test_a_short_placeholder_is_a_soft_failure(sentence):
    assert _is_soft_failure(_page(sentence))


def test_an_article_that_mentions_a_wall_stays_an_article():
    article = ("A long article about rate limiting. " * 40) + "Too many requests"
    assert not _is_soft_failure(_page(article))
    assert not _is_bot_wall(_page(article))
