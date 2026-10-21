"""test_structured.py —— 由 3 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

import json
from unittest.mock import AsyncMock
import pytest
from dhole_mcp.server import MasterFetchServer, ResponseModel, _normalize_schema
from dhole_mcp.structured import extract_structured, _propose_schema, _parse_html


# ============================================================================
# 来源：test_a_page_can_propose_its_own_schema.py
# ============================================================================
# 动态 schema：`schema="auto"` 既抽取结构，又**提议**一份可复用的选择器 schema。

# 四个同构的容器：propose 应当认出 li.book 是容器，title/price/link 是字段。
LISTING = """<html><body>
<ul class="book-list">
  <li class="book"><h3 class="title">Alpha</h3><span class="price">$10</span><a class="link" href="/a">buy</a></li>
  <li class="book"><h3 class="title">Beta</h3><span class="price">$20</span><a class="link" href="/b">buy</a></li>
  <li class="book"><h3 class="title">Gamma</h3><span class="price">$30</span><a class="link" href="/c">buy</a></li>
  <li class="book"><h3 class="title">Delta</h3><span class="price">$40</span><a class="link" href="/d">buy</a></li>
</ul></body></html>"""

# 一篇文章：没有重复容器，propose 应当什么都提不出来。
ARTICLE = """<html><body><article>
<h1 class="headline">A single story</h1>
<p class="lede">One paragraph, no repetition anywhere on this page.</p>
</article></body></html>"""


class TestTheAutoStringIsAccepted:
    """回归：`schema="auto"` 此前被 JSON 解析拒绝，文档承诺与实际行为不符。"""

    def test_the_bare_auto_string_normalizes(self):
        assert _normalize_schema("auto") == {"type": "auto"}

    def test_case_and_surrounding_space_do_not_matter(self):
        assert _normalize_schema("AUTO") == {"type": "auto"}
        assert _normalize_schema("  auto  ") == {"type": "auto"}

    def test_the_json_spellings_still_work(self):
        assert _normalize_schema('{"type": "auto"}') == {"type": "auto"}
        assert _normalize_schema({"mode": "auto"}) == {"mode": "auto"}

    def test_a_non_auto_string_is_still_a_json_error(self):
        """只有 `auto` 是特例；别的裸串仍然报错，不静默吞掉。"""
        with pytest.raises(ValueError, match="not valid JSON"):
            _normalize_schema("book")


class TestTheProposedSchema:
    """`_propose_schema` 的纯函数契约。"""

    def test_it_finds_the_repeating_container_and_its_fields(self):
        prop = _propose_schema(_parse_html(LISTING))
        assert prop["type"] == "object"
        (container, spec), = prop["properties"].items()
        assert container == "book"
        assert spec["selector"] == "li.book"
        assert spec["type"] == "array"
        assert set(spec["properties"]) == {"title", "price", "link"}

    def test_the_proposal_is_itself_a_valid_schema_shape(self):
        """提议必须能当 schema 用：容器在 properties 里，顶层没有 selector。

        顶层放 selector 会被 `_normalize_schema` 具名拒绝（批次12），所以提议若把
        容器提到顶层就是自相矛盾 —— 这里钉住它不会。
        """
        prop = _propose_schema(_parse_html(LISTING))
        assert "selector" not in prop
        assert prop["type"] != "array"
        # 直接喂回 normalize：应当原样通过，而不是抛 "container" / "nothing to extract"。
        assert _normalize_schema(prop) is prop

    def test_a_page_with_no_repetition_proposes_nothing(self):
        assert _propose_schema(_parse_html(ARTICLE)) == {}

    def test_two_repeats_are_below_the_floor(self):
        """`_MIN_GROUP = 3`：两页里各一次的重合不算结构。"""
        html = """<html><body>
        <div class="card">One</div><div class="card">Two</div>
        </body></html>"""
        assert _propose_schema(_parse_html(html)) == {}

    def test_classless_generic_descendants_are_not_fields(self):
        """无 class 的 div/span/p 当字段名没有意义，不当字段。"""
        html = """<html><body>
        <li class="row">A</li><li class="row">B</li><li class="row">C</li>
        </body></html>"""
        prop = _propose_schema(_parse_html(html))
        assert prop == {}   # 容器里除了裸文本没有具名后代 → 提不出字段

    def test_the_field_count_is_capped(self):
        cells = "".join(f'<span class="f{i}">v{i}</span>' for i in range(20))
        html = "<html><body>" + "".join(
            f'<div class="item">{cells}</div>' for _ in range(4)) + "</body></html>"
        prop = _propose_schema(_parse_html(html))
        (spec,) = prop["properties"].values()
        assert len(spec["properties"]) == 8   # _MAX_PROPOSED_FIELDS


class TestTheProposalRoundTrips:
    """提议的价值在于**复用**：抄回去要真能抽出记录，且保留分组。"""

    def test_the_proposed_schema_extracts_the_records(self):
        prop = _propose_schema(_parse_html(LISTING))
        out = extract_structured(LISTING, prop)
        records = out["book"]
        assert [r["title"] for r in records] == ["Alpha", "Beta", "Gamma", "Delta"]
        assert records[0]["price"] == "$10"
        assert records[3]["link"] == "buy"

    def test_grouping_is_kept_not_flattened(self):
        """每个容器的字段跟着自己的记录走，不是全页并成一条。"""
        prop = _propose_schema(_parse_html(LISTING))
        out = extract_structured(LISTING, prop)
        assert len(out["book"]) == 4
        assert {r["title"] for r in out["book"]} == {"Alpha", "Beta", "Gamma", "Delta"}


class TestAutoModeCarriesTheProposal:
    """`extract_structured(html, {"type": "auto"})` 的输出里带 `_schema`。"""

    def test_auto_includes_the_proposed_schema(self):
        out = extract_structured(LISTING, {"type": "auto"})
        assert out["_mode"] == "auto"
        assert "_schema" in out
        assert "book" in out["_schema"]["properties"]

    def test_auto_accepts_the_bare_string_too(self):
        """`_normalize_schema` 把 "auto" 归一成 {"type":"auto"}，两者等价。"""
        out = extract_structured(LISTING, _normalize_schema("auto"))
        assert "_schema" in out

    def test_a_page_without_structure_has_no_schema_key(self):
        out = extract_structured(ARTICLE, {"type": "auto"})
        assert "_schema" not in out


def _html_result() -> ResponseModel:
    return ResponseModel(
        status=200, content=[LISTING], url="https://example.com",
        fetcher_used="http", content_type="text/html",
        extracted_type="html",
    )


class TestTheServerPath:
    """端到端：`web_read(schema="auto")` 返回 structured，且带提议。"""

    def _server(self):
        server = MasterFetchServer()
        server._auto_escalate = AsyncMock(return_value=_html_result())
        return server

    @pytest.mark.asyncio
    async def test_auto_string_drives_a_structured_read(self):
        server = self._server()
        out = await server.web_read("https://example.com",
                                    schema="auto", cache_ttl=0)
        assert out.extracted_type == "structured"
        payload = json.loads(out.content[0])
        assert payload["_mode"] == "auto"
        assert "book" in payload["_schema"]["properties"]

    @pytest.mark.asyncio
    async def test_auto_does_not_count_as_a_failed_extraction(self):
        """auto 命中结构时必须 content_ok，不能被当成 schema_no_match。"""
        server = self._server()
        out = await server.web_read("https://example.com",
                                    schema="auto", cache_ttl=0)
        assert not out.error


class TestTheHandbookStillDocumentsIt:
    """发现链守卫：wire 不写 `auto`（连接期不为此加字），只逐字指向手册那一节。

    能力要可达，链条就得完整：`_TOOL_DEFS` 里 `schema` 的描述写着
    `sys_guide(topic='behavior', section='Structured extraction')`，所以那一节
    必须真的讲了 `auto` 与 `_schema` —— 否则重写手册的人会无声地把这颗能力弄丢，
    而 wire 上的指针仍在，指向一段不再提它的文字。这正是"搬家不是丢"那条守卫的
    同一类关切，只是这里搬的方向相反：wire 从来没有过它，它一开始就在手册里。
    """

    def _volume(self, name: str) -> str:
        from dhole_mcp import guide
        return (guide._ROOT / "references" / name).read_text(encoding="utf-8")

    def test_the_behavior_volume_documents_auto(self):
        text = self._volume("tools.md")
        assert 'schema="auto"' in text
        assert "_schema" in text
        assert "propose" in text.lower() or "proposes" in text.lower()

    def test_the_wire_points_at_the_section_that_documents_it(self):
        """指针与落点必须对得上：wire 说去哪一节，那一节就得有。"""
        from dhole_mcp.server import MasterFetchServer
        defs = {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}
        schema_desc = defs["web_read"]["inputSchema"]["properties"]["schema"]["description"]
        assert "section='Structured extraction'" in schema_desc
        assert "Structured extraction (schema)" in self._volume("tools.md")

    def test_the_call_volume_has_a_copy_ready_auto_example(self):
        assert 'schema": "auto"' in self._volume("examples.md")


# ============================================================================
# 来源：test_structured.py
# ============================================================================
# Tests for structured data extraction (structured.py).

SAMPLE_HTML = """
<html>
<head>
    <title>Test Product</title>
    <script type="application/ld+json">
    {"name": "Widget", "price": "$29.99", "description": "A fine widget"}
    </script>
</head>
<body>
    <h1 class="title">Widget Pro</h1>
    <span class="price">$29.99</span>
    <ul class="features">
        <li>Fast</li>
        <li>Reliable</li>
        <li>Compact</li>
    </ul>
    <p>Contact: support@example.com</p>
</body>
</html>
"""


class TestExtractBySelector:
    """CSS selector-based extraction."""

    def test_single_element(self):
        schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string", "selector": "h1.title"},
            }
        }
        result = extract_structured(SAMPLE_HTML, schema)
        assert result["title"] == "Widget Pro"

    def test_array_elements(self):
        schema = {
            "type": "object",
            "properties": {
                "features": {"type": "array", "selector": ".features li"},
            }
        }
        result = extract_structured(SAMPLE_HTML, schema)
        assert result["features"] == ["Fast", "Reliable", "Compact"]

    def test_count_type_returns_element_count(self):
        schema = {
            "type": "object",
            "properties": {
                "li_count": {"type": "count", "selector": ".features li"},
                "h1_count": {"type": "count", "selector": "h1.title"},
            }
        }
        result = extract_structured(SAMPLE_HTML, schema)
        assert result["li_count"] == 3
        assert result["h1_count"] == 1

    def test_count_missing_selector_returns_none(self):
        schema = {
            "type": "object",
            "properties": {
                "ghost_count": {"type": "count", "selector": ".does-not-exist"},
            }
        }
        result = extract_structured(SAMPLE_HTML, schema)
        assert result["ghost_count"] in ("", None)

    def test_missing_selector_returns_empty(self):
        schema = {
            "type": "object",
            "properties": {
                "nonexistent": {"type": "string", "selector": ".does-not-exist"},
            }
        }
        result = extract_structured(SAMPLE_HTML, schema)
        assert result["nonexistent"] == ""

    def test_invalid_selector_graceful(self):
        schema = {
            "type": "object",
            "properties": {
                "bad": {"type": "string", "selector": ">>>invalid[[["},
            }
        }
        # Should not raise
        result = extract_structured(SAMPLE_HTML, schema)
        assert result["bad"] == ""


class TestExtractFromJsonLd:
    """JSON-LD extraction."""

    def test_jsonld_field(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            }
        }
        result = extract_structured(SAMPLE_HTML, schema)
        assert result["name"] == "Widget"

    def test_jsonld_price(self):
        schema = {
            "type": "object",
            "properties": {
                "price": {"type": "string"},
            }
        }
        result = extract_structured(SAMPLE_HTML, schema)
        # Could come from JSON-LD or selector
        assert "$29.99" in result["price"]


class TestExtractFromMetadata:
    """Metadata matching."""

    def test_metadata_match(self):
        metadata = {"og:title": "OG Title", "author": "John"}
        schema = {
            "type": "object",
            "properties": {
                "author": {"type": "string"},
            }
        }
        result = extract_structured(SAMPLE_HTML, schema, metadata=metadata)
        assert result["author"] == "John"


class TestExtractByPattern:
    """Regex pattern fallback."""

    def test_email_pattern(self):
        schema = {
            "type": "object",
            "properties": {
                "email": {"type": "string"},
            }
        }
        result = extract_structured(SAMPLE_HTML, schema)
        assert "support@example.com" in result["email"]

    def test_price_pattern(self):
        html = "<html><body><p>Only $19.99 today!</p></body></html>"
        schema = {
            "type": "object",
            "properties": {
                "price": {"type": "string"},
            }
        }
        result = extract_structured(html, schema)
        assert "$19.99" in result["price"]


class TestEdgeCases:
    """Edge cases and error handling."""

    def test_empty_schema(self):
        result = extract_structured(SAMPLE_HTML, {})
        assert result == {}

    def test_none_schema(self):
        result = extract_structured(SAMPLE_HTML, None)
        assert result == {}

    def test_empty_html(self):
        schema = {"properties": {"title": {"type": "string", "selector": "h1"}}}
        result = extract_structured("", schema)
        assert result["title"] == ""

    def test_no_properties(self):
        result = extract_structured(SAMPLE_HTML, {"type": "object"})
        assert result == {}

    def test_field_spec_not_dict(self):
        schema = {"properties": {"title": "string"}}
        result = extract_structured(SAMPLE_HTML, schema)
        # Should treat as {"type": "string"}
        assert "title" in result

    def test_multiple_fields(self):
        schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string", "selector": "h1"},
                "features": {"type": "array", "selector": ".features li"},
                "price": {"type": "string", "selector": ".price"},
            }
        }
        result = extract_structured(SAMPLE_HTML, schema)
        assert result["title"] == "Widget Pro"
        assert len(result["features"]) == 3
        assert "$29.99" in result["price"]


# ============================================================================
# 来源：test_new_features.py
# ============================================================================
# Tests for new features: fetch_content, schema auto, batch structured extraction.

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
