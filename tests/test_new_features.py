"""Tests for new features: fetch_content, schema auto, batch structured extraction."""

import pytest

from dhole_mcp.structured import extract_structured


# ─── Schema auto mode ─────────────────────────────────────────────────────────

SAMPLE_HTML_WITH_TABLE = """
<html><body>
<table>
  <tr><th>Name</th><th>Price</th></tr>
  <tr><td>Widget</td><td>$10</td></tr>
  <tr><td>Gadget</td><td>$20</td></tr>
</table>
</body></html>
"""

SAMPLE_HTML_WITH_LIST = """
<html><body>
<ul>
  <li>First item</li>
  <li>Second item</li>
  <li>Third item</li>
</ul>
</body></html>
"""

SAMPLE_HTML_WITH_JSONLD = """
<html><head>
<script type="application/ld+json">
{"@type": "Product", "name": "Widget", "price": "$10"}
</script>
</head><body><p>Hello</p></body></html>
"""


class TestSchemaAuto:
    """Test schema={"type": "auto"} detection."""

    def test_auto_detects_table(self):
        result = extract_structured(SAMPLE_HTML_WITH_TABLE, {"type": "auto"})
        assert result.get("_mode") == "auto"
        assert "tables" in result
        assert len(result["tables"]) >= 1

    def test_auto_detects_list(self):
        result = extract_structured(SAMPLE_HTML_WITH_LIST, {"type": "auto"})
        assert "lists" in result
        # lists is a list of extracted list structures
        assert len(result["lists"]) >= 1
        # Each list contains items (structure depends on implementation)
        first_list = result["lists"][0]
        assert first_list  # non-empty

    def test_auto_detects_jsonld(self):
        result = extract_structured(SAMPLE_HTML_WITH_JSONLD, {"type": "auto"})
        assert "json_ld" in result
        assert result["json_ld"][0]["name"] == "Widget"

    def test_auto_mode_via_mode_key(self):
        result = extract_structured(SAMPLE_HTML_WITH_TABLE, {"mode": "auto"})
        assert result.get("_mode") == "auto"

    def test_auto_empty_html(self):
        result = extract_structured("", {"type": "auto"})
        assert result.get("_mode") == "auto"

    def test_auto_no_structure(self):
        result = extract_structured("<html><body><p>Just text</p></body></html>", {"type": "auto"})
        assert result.get("_mode") == "auto"


# ─── fetch_content + fetch_schema in web_search ─────────────────────────────

class TestFetchContentSchema:
    """fetch_content=true 的行为测试（签名/属性存在性快照测试已删：
    它们只在参数改名时报警，抓不到任何行为 bug）。"""

    @pytest.mark.asyncio
    async def test_fetch_content_failure_is_visible_in_fetched_pages(self, monkeypatch):
        """回归：fetch_content=true 时 web_read 抛错曾被静默吞掉——agent 看到
        fetched_pages 缺一条却不知道为什么。现在失败也占位，带 error 字段。"""
        from unittest.mock import AsyncMock
        from dhole_mcp import search as search_mod
        from dhole_mcp.search_engines import RawResult, EngineReport
        from dhole_mcp.server import MasterFetchServer

        async def fake_multi_search(query, max_results, **kwargs):
            return (
                [RawResult(title="Doc about widgets",
                           url="https://docs.example.test/widgets",
                           snippet="widgets", source="brave", position=1)],
                [EngineReport(name="brave", ok=True)],
            )

        async def fake_ensure_reranker():
            return None

        monkeypatch.setattr(search_mod, "multi_search", fake_multi_search)
        monkeypatch.setattr(search_mod, "ensure_reranker", fake_ensure_reranker)

        server = MasterFetchServer()
        server.web_read = AsyncMock(side_effect=RuntimeError("boom: network down"))

        resp = await server.web_search("widgets doc", fetch_content=True, cache_ttl=0)

        assert len(resp.fetched_pages) == 1
        page = resp.fetched_pages[0]
        assert page["url"] == "https://docs.example.test/widgets"
        assert page["content_ok"] is False
        assert page["content"] == ""
        assert "boom" in page["error"]


# ─── source_type detection ────────────────────────────────────────────────────

class TestSourceType:
    """域名分类只有一套词表（体验修复刀2-S6）。

    此前搜索行走 `search._source_type`、抓取行走 `envelope.classify_source`，同一个
    字段名两套枚举：搜索行产不出 `github|qa|ecommerce|docs-site`，抓取行产不出
    `repo`，而手册只能描述其中一套。合并的实测理由在 `envelope` 的表头 —— 直接把
    搜索行改调抓取侧那颗函数会**净亏 121 个标签**（747 条真实 SERP URL 里 191 行从
    有名掉成 unknown），所以是把两份知识折进一套词表，不是扔掉一套表。
    """

    @staticmethod
    def _st(url: str) -> str:
        from dhole_mcp.envelope import classify_source
        return classify_source(url)[0]

    def test_docs_domains(self):
        assert self._st("https://docs.python.org/3/tutorial") == "docs-site"
        assert self._st("https://huggingface.co/models") == "docs-site"
        assert self._st("https://kubernetes.io/docs/") == "docs-site"

    def test_paper_domains(self):
        assert self._st("https://arxiv.org/abs/2301.00001") == "paper"
        assert self._st("https://nature.com/articles/xyz") == "paper"

    def test_repo_domains(self):
        # github 现在是**独立取值**且 is_official —— 名字是注册局控制的，这正是
        # 搜索行此前发不出来的那个值。
        assert self._st("https://github.com/user/repo") == "github"
        assert self._st("https://gitlab.com/user/repo") == "repo"
        # 包注册表不是"代码托管"：它决定一个名字解析到什么，所以归 reference/official。
        assert self._st("https://pypi.org/project/dhole-mcp/") == "reference"

    def test_the_registry_claim_survives_the_merge(self):
        """两套表并一张时，权威位不许跟着漂移。"""
        from dhole_mcp.envelope import classify_source
        assert classify_source("https://pypi.org/project/dhole-mcp/")[1] is True
        assert classify_source("https://gitlab.com/user/repo")[1] is False

    def test_forum_domains(self):
        # stackoverflow 在合并后拿到更具体的那个词（此前一侧叫 forum、一侧叫 qa）。
        assert self._st("https://stackoverflow.com/questions/123") == "qa"
        assert self._st("https://zhihu.com/question/456") == "forum"

    def test_news_domains(self):
        assert self._st("https://bbc.com/news/article") == "news"
        assert self._st("https://36kr.com/p/123") == "news"

    def test_blog_domains(self):
        assert self._st("https://medium.com/@user/post") == "blog"
        assert self._st("https://csdn.net/article/123") == "blog"

    def test_path_heuristic(self):
        assert self._st("https://example.com/docs/api") == "docs-site"
        assert self._st("https://example.com/blog/post-1") == "blog"

    def test_an_api_endpoint_is_not_documentation(self):
        """/api/ 那一半**刻意没有**折过来：机器端点不是文档，而把 docs-site 发给它
        还会连带换上 730 天的过期视野（`compute_freshness`）—— 那是行为改动，不是
        标签改动，不能靠一套表顺手做掉。"""
        assert self._st("https://example.com/api/v1/users") == "unknown"

    def test_unknown_returns_unknown(self):
        assert self._st("https://randomsite12345.com/page") == "unknown"


# ─── ddg alias ────────────────────────────────────────────────────────────────

class TestDdgAlias:
    """Test that 'ddg' is accepted as engine name."""

    def test_ddg_in_validate_engines(self):
        from dhole_mcp.search import _validate_engines
        # Should not raise
        result = _validate_engines(["ddg"])
        assert result == ["ddg"]

    def test_ddg_maps_to_duckduckgo(self):
        from dhole_mcp.search_metasearch import _DHOLE_TO_BACKEND
        assert _DHOLE_TO_BACKEND["ddg"] == "duckduckgo"
