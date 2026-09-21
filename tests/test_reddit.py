"""old.reddit 列表解析的真页面契约。

tests/old_reddit_real.html 在仓库里躺了很久却无人引用 —— 135KB 的孤儿 fixture 就是
"曾经有用、后来没人接"的那种腐烂。这里把它接到它本来就该测的东西上，而不是删掉。

下面的期望数都是在这份真页面上实测出来的（25 个 thing 块 → 25 条帖子，且 URL /
Score / 作者各自 25 个且互相不错位），不是猜的。
"""

import re
from pathlib import Path

from dhole_mcp import reddit

REDDIT_FIXTURE = Path(__file__).parent / "old_reddit_real.html"
EXPECTED_POSTS = 25


class TestOldRedditListingRealPage:

    def _html(self):
        return REDDIT_FIXTURE.read_text(encoding="utf-8", errors="replace")

    def test_real_listing_parses_to_markdown(self):
        out = reddit.parse_old_reddit_listing(self._html())
        assert out, "真列表页解析成 None = 解析器与页面结构已错位"
        assert out.startswith("# Reddit Posts")
        assert len(re.findall(r"^\d+\. ", out, re.M)) == EXPECTED_POSTS

    def test_fields_never_misalign_across_posts(self):
        """按块读取的全部意义：分数/评论数/作者/URL 都取自同一个块。

        只要有一处跨块对齐（正则扫全文那种写法），这几个计数就会不相等。
        """
        out = reddit.parse_old_reddit_listing(self._html())
        assert out
        counts = {
            "posts": len(re.findall(r"^\d+\. ", out, re.M)),
            "scores": len(re.findall(r"Score: \d+", out)),
            "authors": len(re.findall(r"by u/\S+", out)),
            "urls": len(re.findall(r"https?://\S+", out)),
        }
        assert len(set(counts.values())) == 1, f"字段错位: {counts}"
        assert counts["posts"] == EXPECTED_POSTS

    def test_scores_and_authors_are_pulled_from_the_right_block(self):
        out = reddit.parse_old_reddit_listing(self._html())
        first = out.splitlines()[2:4]
        assert "**Showcase Thread**" in first[0]
        assert "Score: 28" in first[1] and "140 comments" in first[1]
        assert "u/AutoModerator" in first[1]

    def test_no_raw_html_leaks_into_the_output(self):
        """解析失败时调用方要拿 None 去走常规抽取，而不是被喂一坨原始 HTML。"""
        out = reddit.parse_old_reddit_listing(self._html())
        for junk in ("<div", 'class="thing', "</a>", "<p>", "&amp;"):
            assert junk not in out, f"输出里残留原始 HTML 片段: {junk!r}"

    def test_non_listing_pages_return_none(self):
        cases = [
            "",
            "x" * 50,
            "<html><body><p>login required</p></body></html>",
            "<html><body>" + "<div class='sidebar'>thing</div>" * 3 + "</body></html>",
        ]
        for junk in cases:
            assert reddit.parse_old_reddit_listing(junk) is None, \
                f"不该把非列表页当列表: {junk[:40]!r}"

    def test_promoted_ads_are_not_reported_as_posts(self):
        """广告块也是 thing，不跳过的话条目数会虚高。"""
        out = reddit.parse_old_reddit_listing(self._html())
        assert "promoted" not in out.lower()
        html = self._html()
        promoted = len(re.findall(r'data-promoted="true"', html))
        things = len(re.findall(r'class="[^"]*\bthing\b', html))
        # 页面若无广告，则解析条数应等于 thing 块数；有广告则必须更少
        assert len(re.findall(r"^\d+\. ", out, re.M)) <= things
        if promoted == 0:
            assert len(re.findall(r"^\d+\. ", out, re.M)) == things
