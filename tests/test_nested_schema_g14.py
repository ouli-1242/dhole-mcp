"""G14 回归：容器选择器 + 每项子 schema（重复记录不并被压平）。

报告的复现是抽 quotes 的 tags：10 条 quote 的标签被拍平成**一个 40 元素的数组**，
「哪个标签属于哪条 quote」这个信息在输出里没了。原因不是缺功能，而是缺**作用域**：
子选择器一直拿整篇文档求值，所以 `.tag` 回答的是「这页所有标签」。

于是这组测试守的重点是「求值范围」而不是「能不能写嵌套」：能写嵌套而作用域错，输出
看着比以前更丰富，实际是同一份拍平数据换了个外壳——更难发现的那种错。

另外两条边界：记录数封顶（一个 5000 项的列表不能变成 5000 条响应，且上限写在描述里），
以及子选择器同样要过 `validate_css_selector` —— 注入守卫停在第一层的话，嵌套字段就是
绕开它的那条路。

最后一组不在抽取器里，在调用链上：实测报告复现的 `'int' object is not iterable` 是
`smart_fetch` 的 schema 分支把参数送错了位置，与嵌套无关。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from dhole_mcp.security import SecurityError
from dhole_mcp.server import (
    MasterFetchServer,
    ResponseModel,
    _SCHEMA_SOURCE_MAX_CHARS,
    _schema_value_empty,
    _validate_schema_selectors,
)
from dhole_mcp.structured import (
    MAX_RECORDS_PER_FIELD,
    extract_structured,
)

QUOTES = """<html><body>
<div class="quote"><span class="text">First</span><small class="author">Alice</small>
  <div class="tags"><a class="tag">time</a><a class="tag">change</a></div></div>
<div class="quote"><span class="text">Second</span><small class="author">Bob</small>
  <div class="tags"><a class="tag">mirror</a></div></div>
</body></html>"""


def _schema(container="quote", child="text"):
    return {"properties": {
        "items": {"type": "array", "selector": f".{container}",
                  "properties": {child: {"selector": f".{child}"}}}}}


class TestRecordsKeepTheirGrouping:

    def test_each_container_becomes_its_own_record(self):
        out = extract_structured(QUOTES, {
            "properties": {"quotes": {
                "type": "array", "selector": ".quote",
                "properties": {"text": {"selector": ".text"},
                               "author": {"selector": ".author"}}}}})

        assert out["quotes"] == [{"text": "First", "author": "Alice"},
                                 {"text": "Second", "author": "Bob"}]

    def test_a_repeated_child_belongs_to_its_own_record(self):
        """这一条就是报告的那次复现：tags 不能是两页合并后的四个。"""
        out = extract_structured(QUOTES, {"properties": {"quotes": {
            "type": "array", "selector": ".quote",
            "properties": {"tags": {"type": "array", "selector": ".tag"}}}}})

        assert [q["tags"] for q in out["quotes"]] == [["time", "change"], ["mirror"]]
        assert "time" not in out["quotes"][1]["tags"], "第二个容器的记录里混进了第一个的标签"

    def test_two_levels_deep(self):
        html = """<html><body>
        <section class="row"><h2>R1</h2>
          <span class="cell">a</span><span class="cell">b</span></section>
        <section class="row"><h2>R2</h2><span class="cell">c</span></section>
        </body></html>"""
        out = extract_structured(html, {"properties": {"rows": {
            "type": "array", "selector": ".row",
            "properties": {"title": {"selector": "h2"},
                           "cells": {"type": "array", "selector": ".cell"}}}}})

        assert out["rows"] == [{"title": "R1", "cells": ["a", "b"]},
                               {"title": "R2", "cells": ["c"]}]

    def test_the_json_schema_spelling_of_items_is_accepted(self):
        out = extract_structured(QUOTES, {"properties": {"quotes": {
            "selector": ".quote",
            "items": {"properties": {"text": {"selector": ".text"}}}}}})

        assert [q["text"] for q in out["quotes"]] == ["First", "Second"]

    def test_a_single_container_field_gives_one_object_not_a_list(self):
        out = extract_structured(QUOTES, {"properties": {"first": {
            "selector": ".quote",
            "properties": {"text": {"selector": ".text"}}}}})

        assert out["first"] == {"text": "First"}

    def test_grouping_without_a_container_stays_in_the_same_scope(self):
        """没有 selector 的 properties 只是给字段分组，作用域跟着父级。"""
        out = extract_structured('<html><body><h1>H</h1></body></html>', {
            "properties": {"head": {"properties": {"title": {"selector": "h1"}}}}})

        assert out["head"] == {"title": "H"}

    def test_an_attribute_can_be_read_inside_a_record(self):
        html = """<html><body>
        <div class="item"><a href="/1">one</a></div>
        <div class="item"><a href="/2">two</a></div></body></html>"""
        out = extract_structured(html, {"properties": {"items": {
            "type": "array", "selector": ".item",
            "properties": {"url": {"selector": "a", "attribute": "href"}}}}})

        assert [i["url"] for i in out["items"]] == ["/1", "/2"]

    def test_records_do_not_borrow_page_metadata(self):
        """页面级兜底（OG/JSON-LD）回答的是「这个字段在页面上被叫作什么」；记录里
        用它填一个没命中的子字段，等于把别处的值当成这一行的答案。"""
        out = extract_structured('<html><head><meta property="og:title" content="Page">'
                                 "</head><body><div class='row'></div></body></html>",
                                 {"properties": {"rows": {
                                     "type": "array", "selector": ".row",
                                     "properties": {"title": {}}}}},
                                 metadata={"og:title": "Page"})

        assert out["rows"][0]["title"] != "Page", out["rows"]


class TestEmptyAndBounded:

    def test_a_container_that_is_not_there_gives_an_empty_list(self):
        out = extract_structured(QUOTES, _schema(container="product"))

        assert out["items"] == []

    def test_containers_present_but_fields_absent_are_still_no_answer(self):
        """外壳在、内容全空：这不是「抽到了 N 条」，schema_no_match 必须还能发现。"""
        out = extract_structured('<html><body><div class="quote"></div>'
                                 '<div class="quote"></div></body></html>',
                                 _schema())

        assert all(_schema_value_empty(r) for r in out["items"]), out["items"]

    def test_the_emptiness_check_recurses(self):
        assert _schema_value_empty([{"text": ""}, {"text": "  "}]) is True
        assert _schema_value_empty([{"text": "real"}]) is False
        assert _schema_value_empty({"a": {"b": ""}}) is True
        assert _schema_value_empty({"a": {"b": "x"}}) is False

    def test_the_record_count_is_capped_and_visible(self):
        html = "<html><body>" + "".join(
            f'<div class="quote"><span class="text">q{i}</span></div>'
            for i in range(MAX_RECORDS_PER_FIELD + 50)) + "</body></html>"

        out = extract_structured(html, _schema())

        assert len(out["items"]) == MAX_RECORDS_PER_FIELD
        assert out["items"][-1]["text"] == f"q{MAX_RECORDS_PER_FIELD - 1}"


class TestNestedSelectorsAreStillGuarded:
    """守卫本身拒什么由 security 的测试管；这里要证的是**走到子层**：过去循环只
    遍历第一层，嵌套字段的选择器是没被看过一眼就交给 lxml 的。

    所以每个用例都拿一个「一定被拒」的输入（超长 / 非字符串）放在不同的嵌套位置上。
    """

    def test_a_bad_nested_selector_is_reached_and_rejected(self):
        schema = {"properties": {"rows": {
            "type": "array", "selector": ".row",
            "properties": {"x": {"selector": "div" * 4000}}}}}

        with pytest.raises(SecurityError):
            _validate_schema_selectors(schema["properties"])

    def test_a_bad_doubly_nested_selector_is_rejected(self):
        schema = {"properties": {"rows": {"selector": ".row", "properties": {
            "cells": {"type": "array", "selector": ".cell",
                      "properties": {"v": {"selector": ".c" * 4000}}}}}}}

        with pytest.raises(SecurityError):
            _validate_schema_selectors(schema["properties"])

    def test_the_items_spelling_is_guarded_too(self):
        schema = {"properties": {"rows": {"selector": ".row", "items": {
            "properties": {"v": {"selector": 123}}}}}}

        with pytest.raises(SecurityError):
            _validate_schema_selectors(schema["properties"])

    def test_an_unbounded_nesting_is_refused_rather_than_recursed_forever(self):
        deep: dict = {}
        node = deep
        for _ in range(30):
            node["selector"] = ".x"
            node["properties"] = {"kid": {}}
            node = node["properties"]

        with pytest.raises(ValueError):
            _validate_schema_selectors(deep)

    def test_a_valid_nested_schema_passes_untouched(self):
        schema = {"properties": {"quotes": {"type": "array", "selector": ".quote",
                                            "properties": {"t": {"selector": ".text"}}}}}
        _validate_schema_selectors(schema["properties"])
        assert schema["properties"]["quotes"]["selector"] == ".quote"


NESTED_QUOTES = {"properties": {"quotes": {
    "type": "array", "selector": ".quote",
    "properties": {"text": {"selector": ".text"},
                   "tags": {"type": "array", "selector": ".tag"}}}}}


def _stub_tier(monkeypatch, seen: dict) -> dict:
    """Stand in for the fetch tier, binding to its REAL signature.

    A stub that swallows ``*args, **kwargs`` accepts any misordering, which is how
    the second half of G14 stayed invisible: every existing schema test replaces
    ``_auto_escalate`` itself, so the call site can hand ``cookies`` an int and the
    suite still goes green.
    """
    import inspect

    sig = inspect.signature(MasterFetchServer._auto_escalate)

    async def fake(self, *args, **kwargs):
        bound = dict(sig.bind(self, *args, **kwargs).arguments)
        bound.pop("self", None)
        seen.update(bound)
        return ResponseModel(
            status=200, content=[QUOTES], url=bound.get("url", ""),
            fetcher_used="http", extracted_type="html", content_type="text/html",
        )

    monkeypatch.setattr(MasterFetchServer, "_auto_escalate", fake)
    return seen


class TestTheSchemaBranchCallsTheTierInOrder:
    """G14 的另一半不在抽取器里，在 ``smart_fetch`` 的 schema 分支：那次
    ``_auto_escalate`` 调用漏了一个位置参数（``use_trafilatura``），之后每个参数集体
    错一位 —— ``cookies`` 因此收到 ``_SCHEMA_SOURCE_MAX_CHARS``，被
    ``_safe_cookie_dict`` 当成 cookie 列表遍历。症状是**任何**带 schema 的调用都回
    ``Error: 'int' object is not iterable``，报告把它记成了「嵌套 schema 的 bug」。

    这一族（参数错位、抽取器被喂了被截断的 HTML）过去全靠真网络才发现，所以这里把
    参数名逐个钉住：错一位就有一条断言红，而不是静默地把 int 送到 cookie 的位置上。
    """

    def test_every_tier_parameter_gets_its_own_value(self, monkeypatch):
        cookies = [{"name": "sid", "value": "v"}]
        seen = _stub_tier(monkeypatch, {})

        asyncio.run(MasterFetchServer().smart_fetch(
            url="https://example.com", schema=NESTED_QUOTES, cache_ttl=0,
            cookies=cookies, extra_headers={"X-Test": "1"},
            useragent="dhole-test/1", timeout=12345,
        ))

        assert seen["extraction_type"] == "html", "schema 分支必须抓原始 HTML"
        assert seen["cookies"] == cookies
        assert seen["useragent"] == "dhole-test/1"
        assert seen["extra_headers"] == {"X-Test": "1"}
        assert seen["timeout"] == 12345
        assert seen["use_trafilatura"] is True
        # 抽取器要看到整篇文档，所以喂给取回层的不是调用方的返回上限（见
        # test_live_audit_regressions）；分页 offset 由 JSON 的 re-chunk 负责，
        # 取回层拿到 0。
        assert seen["max_chars"] == _SCHEMA_SOURCE_MAX_CHARS
        assert seen["offset"] == 0

    def test_a_conditional_schema_fetch_still_bypasses_the_cache(self, monkeypatch):
        seen = _stub_tier(monkeypatch, {})

        asyncio.run(MasterFetchServer().smart_fetch(
            url="https://example.com", schema=NESTED_QUOTES, cache_ttl=3600,
            if_modified_since="2020-01-01",
        ))

        assert seen["conditional"]
        assert seen["cache_ttl"] == 0, "条件请求问的是源站，不是 dhole 的记忆"

    def test_the_nested_schema_comes_back_as_grouped_records(self, monkeypatch):
        """整条链路的验收：抽取器的单测全绿时，工具仍可能一个字节都返不回来。"""
        _stub_tier(monkeypatch, {})

        out = asyncio.run(MasterFetchServer().smart_fetch(
            url="https://example.com", schema=NESTED_QUOTES, cache_ttl=0,
        ))

        assert out.error == "", out.error
        assert out.extracted_type == "structured"
        payload = json.loads(out.content[0])
        assert [q["tags"] for q in payload["quotes"]] == [["time", "change"], ["mirror"]]
