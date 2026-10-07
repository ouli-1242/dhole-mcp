"""R2刀2（P2-4）：编译不了的 CSS 选择器曾被静默丢弃，200 + 整页未过滤内容回来。

现象：`css_selector="a >> b {;})"`（评审现场是 `"a >> b {;}"`）拿到的是**成功响应**，
正文是整页未过滤的内容，summary / next_action 只字不提选择器没生效。
根因：`security.validate_css_selector` 是注入守卫，它不查可编译性；真正的
`CSSSelector()` 编译发生在提取期，异常被 `except → None` 吃掉 → 回落到全文档。

修法的边界（这条刀必须守住的两侧）：
  * 抽取层（lxml/cssselect）的选择器在发请求前试编译，编不出来就 invalid_request；
  * 浏览器层的选择器（`wait_selector`、actions 里的 selector）**不**由 lxml 判决 ——
    Chromium 认 `:visible`，lxml 不认。把两侧混成一把尺，就是一次能力回退。
"""

import pytest

from dhole_mcp.models import ResponseModel
from dhole_mcp.security import SecurityError, validate_css_selector
from dhole_mcp.server import MasterFetchServer

BROKEN = "a >> b {;}"
URL = "https://example.test/article"

HTML = ("<html><head><title>Widget</title></head><body>"
        "<article><h1>Widgets</h1><p>body text long enough to extract here</p>"
        "</article></body></html>")


def _let_it_fetch(monkeypatch):
    """把取回那一步换成记账的替身：验证到底有没有发生在发包之前，一眼可见。

    `_auto_escalate(self, url, extraction_type, css_selector, ...)` 是按位置传的，
    替身得照同一个顺序接下来，否则测的是签名而不是行为。
    """
    seen: list = []

    async def fake(self, url, extraction_type=None, css_selector=None, *a, **tier):
        seen.append({"url": url, "css_selector": css_selector,
                     "schema": tier.get("schema")})
        return ResponseModel(url=url, status=200, content=[HTML],
                             content_ok=True)

    monkeypatch.setattr(MasterFetchServer, "_auto_escalate", fake)
    return seen


class TestTheGuardItself:
    """安全层的新增判决：只给抽取层用，报错点名参数与解析器的原话。"""

    def test_an_uncompilable_selector_is_refused(self):
        from dhole_mcp.security import validate_html_css_selector
        with pytest.raises(SecurityError):
            validate_html_css_selector(BROKEN)

    def test_the_message_names_the_parameter_and_the_parser(self):
        from dhole_mcp.security import validate_html_css_selector
        with pytest.raises(SecurityError) as exc:
            validate_html_css_selector(BROKEN)
        text = str(exc.value)
        assert "css_selector" in text, text
        assert BROKEN in text, text

    def test_the_param_name_follows_the_caller(self):
        """同一条守卫服务三颗参数，报错点名的是**这一颗**。"""
        from dhole_mcp.security import validate_html_css_selector
        with pytest.raises(SecurityError) as exc:
            validate_html_css_selector(BROKEN, param="nested_selector")
        assert "nested_selector" in str(exc.value)

    def test_the_injection_guard_still_wins(self):
        """危险内容型的拒绝零回归：注入守卫先判，编译检查只是补上另一类。"""
        from dhole_mcp.security import validate_html_css_selector
        with pytest.raises(SecurityError, match="dangerous"):
            validate_html_css_selector("a[href='javascript:alert(1)']")

    @pytest.mark.parametrize("selector", [
        "article", ".main-content", "div.content > p", 'a[href*="/works/"]',
        "#x li:nth-child(2)", "div:has(> button)",
    ])
    def test_ordinary_extraction_selectors_still_pass(self, selector):
        from dhole_mcp.security import validate_html_css_selector
        assert validate_html_css_selector(selector) == selector

    def test_none_and_empty_stay_none(self):
        from dhole_mcp.security import validate_html_css_selector
        assert validate_html_css_selector(None) is None
        assert validate_html_css_selector("   ") is None

    def test_the_browser_tier_is_not_lxml_judged(self):
        """`:visible` 是 Chromium 认、lxml 不认的选择器。

        `wait_selector` / actions 的 selector 活在浏览器层，用抽取层的尺判它，
        等于把一颗本来能用的能力判死。这条钉住两侧的分界。
        """
        assert validate_css_selector("div:visible") == "div:visible"


class TestTheCallRefusesBeforeFetching:

    @pytest.mark.asyncio
    async def test_a_broken_css_selector_is_an_invalid_request(self, monkeypatch):
        seen = _let_it_fetch(monkeypatch)
        out = await MasterFetchServer().web_read(url=URL, css_selector=BROKEN)
        assert out.error.startswith("invalid_request"), out.error
        assert "css_selector" in out.error, out.error
        assert out.content_ok is False and out.content == []
        assert seen == [], "发请求前就该拒掉"

    @pytest.mark.asyncio
    async def test_a_working_selector_still_reaches_the_fetcher(self, monkeypatch):
        seen = _let_it_fetch(monkeypatch)
        out = await MasterFetchServer().web_read(url=URL, css_selector="article")
        assert not out.error, out.error
        assert seen[0]["css_selector"] == "article"

    @pytest.mark.asyncio
    async def test_a_schema_selector_gets_the_same_gate(self, monkeypatch):
        """schema 的 selector 走 schema 那条既有信封（前缀已由别的测试钉住）；这里要钉
        的是编译不过的选择器**在这里也被拒**，而不是等到提取期静默失效。"""
        seen = _let_it_fetch(monkeypatch)
        out = await MasterFetchServer().web_read(
            url=URL, schema={"properties": {"title": {"selector": BROKEN}}})
        assert out.status == 0 and out.content_ok is False
        assert "selector" in out.error and BROKEN in out.error, out.error
        assert "title" in out.error, out.error
        assert seen == [], "发请求前就该拒掉"

    @pytest.mark.asyncio
    async def test_a_nested_selector_gets_the_same_gate(self, monkeypatch):
        _let_it_fetch(monkeypatch)
        out = await MasterFetchServer().web_read(
            url=URL, nested_selector=BROKEN,
            nested_fields=[{"name": "作品", "selector": ".work-item a"}])
        assert out.error.startswith("invalid_request"), out.error

    @pytest.mark.asyncio
    async def test_a_nested_field_selector_gets_the_same_gate(self, monkeypatch):
        _let_it_fetch(monkeypatch)
        out = await MasterFetchServer().web_read(
            url=URL, nested_selector=".work-item a",
            nested_fields=[{"name": "作品", "selector": BROKEN}])
        assert out.error.startswith("invalid_request"), out.error


class TestTheExtractionTimeEdge:
    """编译已经前移，提取期仍可能出事（lxml 在真文档上抛的边角）。那种情况下正文是
    整页的，响应必须说出来 —— 与 XML content-type 那条同款措辞（"was NOT applied"）。"""

    def test_a_runtime_selector_failure_records_the_note(self, monkeypatch):
        import lxml.cssselect

        from dhole_mcp.context import _arg_notes_scope
        from dhole_mcp.trafilatura_extractor import extract_with_trafilatura

        class _Boom:
            def __init__(self, selector, *a, **kw):
                self.selector = selector

            def __call__(self, tree):
                raise RuntimeError("boom at match time")

        # 构造成功（校验过的选择器走到这里），匹配时才炸 —— 校验期测不出的那一类
        monkeypatch.setattr(lxml.cssselect, "CSSSelector", _Boom)

        class _Page:
            url = "https://example.test/article"
            status = 200
            content_type = "text/html"
            encoding = "utf-8"
            body = HTML.encode()
            content = HTML

        with _arg_notes_scope() as notes:
            out = extract_with_trafilatura(_Page(), extraction_type="markdown",
                                           css_selector="article")

        assert any("was NOT applied" in n and "article" in n for n in notes), notes
        assert out, "整页正文仍然带回：要说的是「没过滤」，不是「没内容」"

    def test_a_working_selector_records_nothing(self):
        from dhole_mcp.context import _arg_notes_scope
        from dhole_mcp.trafilatura_extractor import extract_with_trafilatura

        class _Page:
            url = "https://example.test/article"
            status = 200
            content_type = "text/html"
            encoding = "utf-8"
            body = HTML.encode()
            content = HTML

        with _arg_notes_scope() as notes:
            extract_with_trafilatura(_Page(), extraction_type="markdown",
                                     css_selector="article")

        assert not any("NOT applied" in n for n in notes), notes
