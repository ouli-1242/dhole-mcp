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


# ─── fetch_content + fetch_schema in smart_search ─────────────────────────────

class TestFetchContentSchema:
    """fetch_content=true 的行为测试（签名/属性存在性快照测试已删：
    它们只在参数改名时报警，抓不到任何行为 bug）。"""

    @pytest.mark.asyncio
    async def test_fetch_content_failure_is_visible_in_fetched_pages(self, monkeypatch):
        """回归：fetch_content=true 时 smart_fetch 抛错曾被静默吞掉——agent 看到
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
        server.smart_fetch = AsyncMock(side_effect=RuntimeError("boom: network down"))

        resp = await server.smart_search("widgets doc", fetch_content=True, cache_ttl=0)

        assert len(resp.fetched_pages) == 1
        page = resp.fetched_pages[0]
        assert page["url"] == "https://docs.example.test/widgets"
        assert page["content_ok"] is False
        assert page["content"] == ""
        assert "boom" in page["error"]


# ─── source_type detection ────────────────────────────────────────────────────

class TestSourceType:
    """Test _source_type domain classification."""

    def test_docs_domains(self):
        from dhole_mcp.search import _source_type
        assert _source_type("https://docs.python.org/3/tutorial") == "docs"
        assert _source_type("https://huggingface.co/models") == "docs"
        assert _source_type("https://kubernetes.io/docs/") == "docs"

    def test_paper_domains(self):
        from dhole_mcp.search import _source_type
        assert _source_type("https://arxiv.org/abs/2301.00001") == "paper"
        assert _source_type("https://nature.com/articles/xyz") == "paper"

    def test_repo_domains(self):
        from dhole_mcp.search import _source_type
        assert _source_type("https://github.com/user/repo") == "repo"
        assert _source_type("https://pypi.org/project/dhole-mcp/") == "repo"

    def test_forum_domains(self):
        from dhole_mcp.search import _source_type
        assert _source_type("https://stackoverflow.com/questions/123") == "forum"
        assert _source_type("https://zhihu.com/question/456") == "forum"

    def test_news_domains(self):
        from dhole_mcp.search import _source_type
        assert _source_type("https://bbc.com/news/article") == "news"
        assert _source_type("https://36kr.com/p/123") == "news"

    def test_blog_domains(self):
        from dhole_mcp.search import _source_type
        assert _source_type("https://medium.com/@user/post") == "blog"
        assert _source_type("https://csdn.net/article/123") == "blog"

    def test_path_heuristic(self):
        from dhole_mcp.search import _source_type
        assert _source_type("https://example.com/docs/api") == "docs"
        assert _source_type("https://example.com/blog/post-1") == "blog"

    def test_unknown_returns_other(self):
        from dhole_mcp.search import _source_type
        assert _source_type("https://randomsite12345.com/page") == "other"


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
