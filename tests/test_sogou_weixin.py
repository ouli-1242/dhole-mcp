"""搜狗微信搜索引擎（_SogouWeixin）：国内裸网直连的免费引擎，独家公众号内容池。

fixture 是从 weixin.sogou.com 真实结果页截取的 li 片段（2026-09-20 抓取），
锁的是页面结构契约，不是活网络。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURE = Path(__file__).resolve().parent / "sogou_weixin_fixture.html"
BASE = "https://weixin.sogou.com"


class TestSogouWeixinExtraction:
    """对真实页面片段的解析契约：标题去高亮、链接绝对化、摘要带公众号行。"""

    def _results(self):
        from dhole_mcp.search_metasearch import SogouWeixin

        return SogouWeixin().extract_results(FIXTURE.read_text(encoding="utf-8"))

    def test_extracts_two_results(self):
        results = self._results()
        assert len(results) == 2

    def test_titles_are_stripped_of_highlight_tags(self):
        results = self._results()
        for r in results:
            assert r.title
            assert "<em>" not in r.title and "red_beg" not in r.title

    def test_hrefs_are_absolute_sogou_links(self):
        results = self._results()
        for r in results:
            assert r.href.startswith("https://weixin.sogou.com/link?url="), r.href

    def test_body_carries_summary_text(self):
        results = self._results()
        assert all(r.body for r in results)
