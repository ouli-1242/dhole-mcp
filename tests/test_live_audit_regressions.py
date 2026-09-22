"""描述改写后的多角度实测（2026-09-22）挖出的两个缺陷。

都不是报告里的条目，是拿真网络撞出来的，两个都属于这个项目最在意的那一类：
**调用看起来成功，实际没做它承诺的事**。

1. `smart_fetch` 一旦同时传 `schema` 和 `max_content_chars`，抽取器拿到的是
   **被截断的 HTML**。实测 HN 首页同一份 schema：不传 `max_content_chars`
   得到 `{"n":30,"first":"Xiaomi MiMo v2.6",…}`，传 400（被钳到 500）得到三个
   空字符串，而回包 `content_ok: true`、`next_action` 还建议「offset=500 继续
   翻页」——那段 JSON 只有 40 字符，翻页建议是错的。根因：schema 分支把
   `max_content_chars` 当 `max_chars` 传给了 `_auto_escalate`，而那个参数本来
   是给「返回给调用方的内容」用的。

2. 空 body 的 404 被判成 JS shell。实测 `https://httpbin.org/status/404`
   （该端点本来就不返回 body）得到 `error: js_shell_detected`、
   `page_type: js_shell`、`next_action: "re-fetch auto-escalates to the stealthy
   browser"`——没有任何 JS 需要渲染，agent 照办只会白烧一次 30-40s 的浏览器
   升级，结果一样是空。status 404 本身报得对，所以这是「误导恢复建议」而不是
   「假成功」，但恢复路径是 agent 唯一能看到的下一步，错不得。
"""

import asyncio
import json

import pytest

from dhole_mcp import server as server_mod
from dhole_mcp.server import ResponseModel, _agent_hints, _detect_content_issue, _is_js_shell

# ~3.6KB of filler before the real markup: anything the tier caps below this
# and the selector below can never match. Real pages look like this too — head,
# styles and scripts come first.
_PADDED_HTML = (
    "<html><head><style>"
    + ("/* framework css */" * 200)
    + "</style></head><body><main><article>"
    + "<h1 class='late-title'>Late Title</h1>"
    + "</article></main></body></html>"
)


def _stub_tier(monkeypatch, html: str, captured: dict) -> None:
    """Stand in for the fetch tiers, faithfully: they truncate to `max_chars`."""
    async def fake(
        self, url, extraction_type, css_selector, main_content_only,
        use_trafilatura, cache_ttl, offset, headless, real_chrome, wait,
        proxy, timeout, network_idle, solve_cloudflare, block_webrtc,
        hide_canvas, extra_headers, useragent, cookies,
        max_chars: int = server_mod.MAX_CONTENT_CHARS,
    ) -> ResponseModel:
        captured["max_chars"] = max_chars
        body = html[:max_chars]
        result = ResponseModel(
            url=url, status=200, content=[body], fetcher_used="http",
            extracted_type="html", content_type="text/html",
            total_size_bytes=len(html), total_extracted_chars=len(body),
        )
        return server_mod._apply_chunking(result, max_chars=max_chars)

    monkeypatch.setattr(server_mod.MasterFetchServer, "_auto_escalate", fake)


def _fetch_schema(schema: dict, **kwargs) -> ResponseModel:
    server = server_mod.MasterFetchServer()
    return asyncio.run(server.smart_fetch(
        url="https://example.com/padded", schema=schema, **kwargs,
    ))


class TestSchemaExtractionSeesTheWholeDocument:

    def test_return_cap_does_not_starve_the_selector(self, monkeypatch):
        """`max_content_chars` 是返回给调用方的上限，不该决定抽取器看到多少 HTML。"""
        captured: dict = {}
        _stub_tier(monkeypatch, _PADDED_HTML, captured)

        out = _fetch_schema(
            {"properties": {"title": {"selector": "h1.late-title"}}},
            max_content_chars=500,
        )

        assert json.loads(out.content[0])["title"] == "Late Title", (
            "抽取器只拿到了被 max_content_chars 截断的 HTML，选择器必然全部落空"
        )

    def test_the_tier_is_not_asked_for_a_starved_document(self, monkeypatch):
        captured: dict = {}
        _stub_tier(monkeypatch, _PADDED_HTML, captured)

        _fetch_schema(
            {"properties": {"title": {"selector": "h1.late-title"}}},
            max_content_chars=500,
        )

        assert captured["max_chars"] >= len(_PADDED_HTML), (
            "抓 HTML 的那一层被喂了调用方的返回上限"
        )

    def test_envelope_describes_the_json_not_the_html(self, monkeypatch):
        """回包的信封必须描述真正返回的 JSON；否则 next_action 会让人去翻一个不存在的长页。"""
        captured: dict = {}
        _stub_tier(monkeypatch, _PADDED_HTML, captured)

        out = _fetch_schema(
            {"properties": {"title": {"selector": "h1.late-title"}}},
            max_content_chars=500,
        )

        assert out.is_truncated is False
        assert out.next_offset == 0
        assert out.total_extracted_chars == len(out.content[0])

    def test_focus_never_filters_the_structured_json(self, monkeypatch):
        """描述里写明「schema 生效时 focus 被忽略」，重排 chunking 顺序后也得成立。"""
        captured: dict = {}
        _stub_tier(monkeypatch, _PADDED_HTML, captured)

        out = _fetch_schema(
            {"properties": {"title": {"selector": "h1.late-title"}}},
            focus="something the json does not contain",
            max_content_chars=500,
        )

        assert json.loads(out.content[0])["title"] == "Late Title"

    def test_all_empty_fields_are_not_a_success(self, monkeypatch):
        """schema 一个字段都没抽到 = 没做到，不能同时报 content_ok: true。"""
        captured: dict = {}
        _stub_tier(monkeypatch, _PADDED_HTML, captured)

        out = _fetch_schema(
            {"properties": {"missing": {"selector": "h1.not-on-this-page"}}},
            max_content_chars=500,
        )

        assert out.content_ok is False
        assert out.error.startswith("schema_no_match"), out.error

    def test_partial_match_stays_ok(self, monkeypatch):
        """只要有一个字段命中，就还是成功——不能因为可选字段空着就全盘否定。"""
        captured: dict = {}
        _stub_tier(monkeypatch, _PADDED_HTML, captured)

        out = _fetch_schema(
            {"properties": {
                "title": {"selector": "h1.late-title"},
                "missing": {"selector": "h1.not-on-this-page"},
            }},
            max_content_chars=500,
        )

        payload = json.loads(out.content[0])
        assert payload["title"] == "Late Title"
        assert out.content_ok is True


def _result(**kwargs) -> ResponseModel:
    defaults = dict(
        status=200, content=["Hello world"], url="https://example.com",
        fetcher_used="http", content_type="text/html",
        total_size_bytes=1000, extracted_type="markdown",
    )
    defaults.update(kwargs)
    return ResponseModel(**defaults)


class TestEmptyErrorPagesAreNotJsShells:

    def test_empty_404_body_is_not_a_js_shell(self):
        """4xx 的空 body 是 HTTP 错误，不是「需要 JS 渲染」。"""
        assert _is_js_shell(_result(status=404, content=[])) is False

    def test_empty_500_body_is_not_a_js_shell(self):
        assert _is_js_shell(_result(status=503, content=[])) is False

    def test_empty_200_body_is_still_a_js_shell(self):
        """2xx 空 body 仍然要触发浏览器升级——不能把升级路径一起修掉。"""
        assert _is_js_shell(_result(status=200, content=[])) is True

    def test_404_reports_the_status_not_the_shell(self):
        issue = _detect_content_issue(_result(status=404, content=[]))
        assert issue.startswith("http_error_404"), issue

    def test_404_does_not_recommend_a_browser_escalation(self):
        result = _result(status=404, content=[])
        result.error = _detect_content_issue(result)
        _summary, next_action, content_ok = _agent_hints(result)
        assert content_ok is False
        assert "stealthy" not in next_action, (
            "404 空 body 没有可渲染的内容，把 agent 送去跑浏览器升级是纯浪费"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
