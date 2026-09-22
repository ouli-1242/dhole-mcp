"""smart_fetch 的 ``schema`` 参数：要么真的产出结构化 JSON，要么明确报错。

回归背景 —— 此前两个形态都会**静默退回 markdown 并返回 200**：

1. ``schema`` 以 JSON 字符串送达（部分 MCP 客户端会序列化嵌套对象，agent 也会）
2. ``schema`` 里没有非空 ``properties``（例如 ``{"type": "object"}``）

调用方完全无从知道 schema 被丢掉了 —— 一个看起来成功的调用其实没做结构化提取，
这正是 ``_strict_options`` 存在的意义要防的那类失败。现在前者被解析，后者直接报错。
"""

import json
from unittest.mock import AsyncMock

import pytest

from dhole_mcp.server import MasterFetchServer, ResponseModel, _normalize_schema

HTML = """<html><head><title>T</title></head><body>
<h1 class="headline">Structured Title</h1>
<span class="price">$29.99</span>
<ul class="feature-list"><li>Alpha</li><li>Beta</li></ul>
</body></html>"""

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "selector": "h1.headline"},
        "price": {"type": "string", "selector": ".price"},
        "features": {"type": "array", "selector": ".feature-list li"},
    },
}


def _html_result(extracted_type: str = "html") -> ResponseModel:
    return ResponseModel(
        status=200, content=[HTML], url="https://example.com",
        fetcher_used="http", content_type="text/html",
        extracted_type=extracted_type,
    )


class TestNormalizeSchema:

    def test_none_means_no_schema(self):
        assert _normalize_schema(None) is None

    def test_blank_string_and_empty_dict_mean_no_schema(self):
        assert _normalize_schema("   ") is None
        assert _normalize_schema({}) is None

    def test_object_schema_passes_through(self):
        assert _normalize_schema(SCHEMA) is SCHEMA

    def test_json_string_schema_is_parsed(self):
        assert _normalize_schema(json.dumps(SCHEMA)) == SCHEMA

    def test_auto_type_and_mode_are_accepted(self):
        assert _normalize_schema({"type": "auto"}) == {"type": "auto"}
        assert _normalize_schema({"mode": "auto"}) == {"mode": "auto"}

    def test_schema_without_properties_raises(self):
        with pytest.raises(ValueError, match="nothing to extract"):
            _normalize_schema({"type": "object"})

    def test_empty_properties_raises(self):
        with pytest.raises(ValueError, match="nothing to extract"):
            _normalize_schema({"type": "object", "properties": {}})

    def test_malformed_json_string_raises(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            _normalize_schema("{not json")

    def test_non_object_schema_raises(self):
        with pytest.raises(ValueError, match="must be a JSON object"):
            _normalize_schema(["h1"])


class TestSchemaReachesStructuredExtraction:
    """契约：传了 schema 就必须拿到 JSON，不能悄悄给 markdown。"""

    def _server(self):
        server = MasterFetchServer()
        server._auto_escalate = AsyncMock(return_value=_html_result())
        return server

    @pytest.mark.asyncio
    async def test_object_schema_returns_structured_json(self):
        server = self._server()
        out = await server.smart_fetch("https://example.com", schema=SCHEMA, cache_ttl=0)

        assert out.extracted_type == "structured"
        payload = json.loads(out.content[0])
        assert payload["title"] == "Structured Title"
        assert payload["features"] == ["Alpha", "Beta"]

    @pytest.mark.asyncio
    async def test_json_string_schema_is_not_silently_dropped(self):
        """回归：字符串形式的 schema 此前会静默退回 markdown。"""
        server = self._server()
        out = await server.smart_fetch(
            "https://example.com", schema=json.dumps(SCHEMA), cache_ttl=0,
        )

        assert out.extracted_type == "structured"
        assert json.loads(out.content[0])["price"] == "$29.99"

    @pytest.mark.asyncio
    async def test_unusable_schema_is_rejected_before_any_fetch(self):
        """回归：无 properties 的 schema 此前会静默退回 markdown。

        拒绝的形式也进了契约：抛异常会被兜底成 is_error 的 MCP 结果，与 resolve_url
        之类返回结构化错误的做法不一致（BUG-16）。现在是 status=0 + error，且仍然
        不发请求、不返回 markdown。
        """
        server = self._server()
        out = await server.smart_fetch(
            "https://example.com", schema={"type": "object"}, cache_ttl=0,
        )
        assert out.status == 0
        assert out.content == []
        assert "nothing to extract" in out.error
        server._auto_escalate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_schema_still_returns_markdown(self):
        """不传 schema 是正常用法，不得被顺手改成结构化路径。"""
        server = MasterFetchServer()
        server._auto_escalate = AsyncMock(return_value=_html_result("markdown"))
        server._finalize_result = AsyncMock(side_effect=lambda result, *args: result)

        out = await server.smart_fetch("https://example.com", cache_ttl=0)

        assert out.extracted_type != "structured"
        assert out.content == [HTML]


class TestSchemaFromOptionsBag:
    """schema 与 css_selector/timeout 一样是「提升参数」：顶层优先，options 兜底。"""

    @pytest.mark.asyncio
    async def test_options_bag_schema_is_accepted(self):
        server = MasterFetchServer()
        server._auto_escalate = AsyncMock(return_value=_html_result())

        out = await server._dispatch(
            "smart_fetch",
            {"url": "https://example.com", "options": {"schema": SCHEMA}, "cache_ttl": 0},
        )
        assert out[1]["extracted_type"] == "structured"

    @pytest.mark.asyncio
    async def test_top_level_schema_wins_over_options(self):
        server = MasterFetchServer()
        server._auto_escalate = AsyncMock(return_value=_html_result())
        other = {"properties": {"only": {"selector": "h1"}}}

        out = await server._dispatch(
            "smart_fetch",
            {
                "url": "https://example.com",
                "schema": SCHEMA,
                "options": {"schema": other},
                "cache_ttl": 0,
            },
        )
        payload = json.loads(out[1]["content"][0])
        assert "title" in payload and "only" not in payload
