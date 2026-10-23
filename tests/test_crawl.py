"""test_crawl.py —— 由 3 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import json
from dhole_mcp.crawl import CrawlPage, CrawlResponseModel
from dhole_mcp.search import SearchResponseModel, SearchResult
from dhole_mcp.server import BulkResponseModel, ResponseModel, SessionCensusRow, SysStateModel
from dhole_mcp.wire import _WIRE_OMIT, _wire_dump, _wire_json
import asyncio
import pytest
from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer
import re
import socket
import sys
import threading
from pathlib import Path
import fixture_site as fs
from dhole_mcp import identity
from dhole_mcp.crawl import CHROME_REPEAT_MIN, LIST_ITEMS_MIN, _chrome_rows, _classify_and_extract, _measure_note, _measure_row, _row_verdict
from dhole_mcp.envelope import is_extraction_collapsed
from tests._extraction_ledger import recorded_markdown_chars


# ============================================================================
# 来源：test_wire_compaction.py
# ============================================================================
# 线格式压缩（wire compaction）的契约测试。

# 17.0 B6 第三刀：压实层有了自己的家，测试直接认它，不再借 server 的名字。

# 每个响应都必须保留的"行动通道"：agent 读一次就要能决定下一步。
ACTION_CHANNEL = ("status", "url", "content", "content_ok", "summary",
                  "error", "next_action")


def _payload(model) -> dict:
    _text, payload = _wire_json(model)
    return payload


def _fetch(**kw) -> ResponseModel:
    """A fetch response with only what the caller names; the rest stay default."""
    base = dict(status=200, content=["body"], url="https://x.test/a")
    base.update(kw)
    return ResponseModel(**base)


class TestDefaultsLeaveTheWire:
    def test_a_failure_carries_no_quiet_fields(self):
        r = _fetch(status=404, content=[], duration_ms=1539.8719310760498,
                   error="http_error_404: server returned error status",
                   content_ok=False, content_type="text/html; charset=utf-8",
                   fetcher_used="http",
                   fetched_at="2026-09-27T17:09:15.010602+00:00",
                   metadata={"robots": "complied"},
                   summary="404 ERR", next_action="check the URL")
        p = _payload(r)
        for quiet in ("original_url", "cached", "is_truncated", "next_offset",
                      "retry_count", "escalation_path", "page_type",
                      "source_type", "is_official", "is_stale",
                      "source", "archived_at", "quality_score", "media",
                      "links", "table_of_contents",
                      "session_id", "session_cookie_names", "extracted_type"):
            assert quiet not in p, f"{quiet} 停在默认值上却仍然上线"
        assert p["status"] == 404 and p["content_ok"] is False

    def test_the_action_channel_never_disappears(self):
        p = _payload(_fetch())
        for key in ACTION_CHANNEL:
            assert key in p, f"{key} 是 agent 每轮都要读的，不能因为恰好为空被删"

    def test_empty_containers_say_nothing(self):
        p = _payload(_fetch(metadata={}, links={}, media=[]))
        assert "metadata" not in p and "links" not in p and "media" not in p

    def test_an_unasked_search_round_does_not_list_empty_diagnostic_arrays(self):
        s = SearchResponseModel(query="q", results=[SearchResult(title="T", url="U")],
                                total_results=1)
        p = _payload(s)
        for quiet in ("engines_used", "engine_blocked", "engine_empty",
                      "engine_preempted", "date_filter", "consensus_basis",
                      "related_queries", "fetched_pages", "cached"):
            assert quiet not in p
        assert p["total_results"] == 1


class TestVerdictsStayOnTheWire:
    """压缩只删沉默，不删结论——这几条是"看起来像默认值但不是"的字段。"""

    def test_a_false_content_ok_is_a_verdict(self):
        assert _payload(_fetch(content_ok=False))["content_ok"] is False

    def test_a_zero_relevance_is_the_reranker_saying_off_topic(self):
        # relevance_score's default IS 0.0, and 0.0 is also its most negative
        # verdict. Dropping it would turn "this is off-topic" into "unscored",
        # which is the exact misreading the min_raw_relevance floor exists to avoid.
        row = SearchResult(title="T", url="U", relevance_score=0.0)
        p = _payload(SearchResponseModel(query="q", results=[row], total_results=1))
        assert p["results"][0]["relevance_score"] == 0.0

    def test_a_nonzero_offset_and_truncation_flag_travel_together(self):
        p = _payload(_fetch(is_truncated=True, next_offset=8000))
        assert p["is_truncated"] is True and p["next_offset"] == 8000

    def test_a_cached_receipt_still_says_it_was_cached(self):
        assert _payload(_fetch(cached=True))["cached"] is True

    def test_an_archive_receipt_still_says_so(self):
        p = _payload(_fetch(source="archive.org", archived_at="2024-01-01",
                             content_age_days=900, is_stale=True))
        assert p["source"] == "archive.org"
        assert p["archived_at"] == "2024-01-01"
        assert p["is_stale"] is True

    def test_a_pdf_verdict_is_not_mistaken_for_the_unscored_default(self):
        assert _payload(_fetch(quality_score=0.4))["quality_score"] == 0.4
        # and the unscored case is carried by absence, never by a 0.0
        assert "quality_score" not in _payload(_fetch())

    def test_a_list_page_still_names_its_type(self):
        p = _payload(_fetch(page_type="list", source_type="gov",
                             is_official=True, extracted_type="structured"))
        assert p["page_type"] == "list" and p["source_type"] == "gov"
        assert p["is_official"] is True and p["extracted_type"] == "structured"

    def test_a_conditional_hit_is_visible(self):
        """304 是个判定，不是默认值：压缩不许把它当"没什么可说"抹掉。

        17.0 之后没有 `not_modified` 这个字段了 —— 那个事实只有一个载体，就是
        `status`。所以这条守卫改钉 status：它带着 304 上线，读的人才知道
        "空正文是答复，不是失败"。
        """
        p = _payload(_fetch(status=304, content=[]))
        assert p["status"] == 304, p


class TestNumbersStopPayingRent:
    def test_duration_is_whole_milliseconds(self):
        assert _payload(_fetch(duration_ms=1583.716630935669))["duration_ms"] == 1584

    def test_fetched_at_loses_its_fraction_of_a_second(self):
        assert (_payload(_fetch(fetched_at="2026-09-27T17:06:35.685286+00:00"))
                ["fetched_at"] == "2026-09-27T17:06:35Z")


class TestEveryRowGetsItsOwnPolicy:
    def test_bulk_rows_are_compacted_like_the_single_one(self):
        b = BulkResponseModel(results=[_fetch(cached=True), _fetch()],
                              total=2, successful=2)
        p = _payload(b)
        assert "cached" in p["results"][0] and "cached" not in p["results"][1]

    def test_a_crawl_page_row_keeps_only_what_differs(self):
        c = CrawlResponseModel(start_url="https://a.test", pages=[
            CrawlPage(url="https://a.test/1", status=200, content_ok=True,
                      content=["md"], content_chars=2)])
        row = _payload(c)["pages"][0]
        assert row == {"url": "https://a.test/1", "status": 200,
                       "content_ok": True, "content": ["md"], "content_chars": 2}

    def test_map_mode_pruning_survives_the_second_pass(self):
        # CrawlResponseModel already drops the per-row constants a sitemap map has
        # in common. The wire pass must prune on top of that, not rebuild the rows
        # from the live objects and hand all four constants back.
        rows = [CrawlPage(url=f"https://a.test/{i}", status=200,
                          page_type="discover_only", fetcher_used="none",
                          content_ok=True, summary="mapped") for i in range(3)]
        c = CrawlResponseModel(start_url="https://a.test", pages=rows,
                              discover_only=True, sitemap_used=True,
                              pages_discovered=3)
        p = _payload(c)
        assert all(set(row) == {"url", "status"} for row in p["pages"]), p["pages"]
        assert p["sitemap_used"] is True

    def test_a_state_call_does_not_pay_for_an_empty_pool_snapshot(self):
        p = _payload(SysStateModel(message="purged 0 entries"))
        assert "engine_health" not in p and "purged" not in p
        assert p["message"]


    def test_a_projected_row_without_a_model_still_gets_its_policy(self):
        """feed_read 交回的是自己拼的 dict，_wire_dump 无从查类。

        容器名登记在 _WIRE_PROJECTED_ROWS 里，策略才落得下去。漏掉它的代价是按
        feed 计费的：一批安静的订阅每行都付 error:"" / since:"" /
        discovered_from:""。而两张轮询收据不在策略里，
        必须照常出现——0 是它们的默认值，也是它们的含义。
        """
        from dhole_mcp.feed import FeedResult
        row = FeedResult(source_url="https://x.test/feed.xml",
                         source_title="T", items=[]).model_dump()
        _text, payload = _wire_json({"feeds": [row]})
        out = payload["feeds"][0]
        assert out == {"source_url": "https://x.test/feed.xml",
                       "source_title": "T", "items": [],
                       "items_older_than_since": 0, "items_without_date": 0}, out
        for quiet in ("error", "since", "discovered_from"):
            assert quiet not in out

    def test_a_stubbed_batch_that_returns_nothing_does_not_crash_the_wire(self):
        """{"feeds": None} 这类形状过去会在压缩里炸成 TypeError。"""
        _text, payload = _wire_json({"feeds": None})
        assert payload == {"feeds": None}


class TestTheCompressionCannotReachInsideTheModel:
    def test_nothing_but_a_default_is_ever_dropped(self):
        """The one invariant that matters: a field is omitted ONLY at its default.

        Built by reflection rather than by hand: every field of every wire model
        gets a value that is NOT its default, and the compacted copy must still
        carry all of them. A future edit to _WIRE_OMIT that omits a field on a
        wrong sentinel (a falsy 0, an empty-but-meaningful list) fails here
        instead of quietly deleting an agent's data.
        """
        from typing import get_args, get_origin

        from pydantic import BaseModel as _BM

        from dhole_mcp.crawl import CrawlPage, CrawlResponseModel
        from dhole_mcp.feed import FeedItem, FeedResult
        from dhole_mcp.search import SearchResponseModel, SearchResult

        models = [ResponseModel, BulkResponseModel, SysStateModel, CrawlPage,
                  CrawlResponseModel, SearchResult, SearchResponseModel,
                  FeedItem, FeedResult, SessionCensusRow]

        def probe(annotation):
            origin = get_origin(annotation)
            if origin in (list, set, tuple):
                args = [a for a in get_args(annotation) if a is not type(None)]
                return [probe(args[0])] if args else ["x"]
            if origin is dict:
                # The value type matters: hosts is Dict[str, List[str]], and a
                # probe of {"k": "v"} fails pydantic rather than proving anything.
                from typing import Any as _Any
                args = [a for a in get_args(annotation) if a is not type(None)]
                value = args[1] if len(args) > 1 else None
                return {"k": "v" if value in (None, _Any) else probe(value)}
            args = get_args(annotation)
            if args and type(None) in args:  # Optional[X]
                return probe(next(a for a in args if a is not type(None)))
            if isinstance(annotation, type):
                if issubclass(annotation, _BM):
                    return {n: probe(f.annotation)
                            for n, f in annotation.model_fields.items()}
                if annotation is str:
                    return "probe"
                if annotation is bool:
                    return True
                if annotation is int:
                    return 7
                if annotation in (float,):
                    return 0.5
                if annotation is list:
                    return ["x"]
                if annotation is dict:
                    return {"k": "v"}
            return "probe"

        for model in models:
            values = {name: probe(field.annotation)
                      for name, field in model.model_fields.items()}
            instance = model(**values)
            wire = _wire_dump(instance)
            lost = [n for n in instance.model_dump() if n not in wire]
            assert not lost, f"{model.__name__} 丢掉了非默认值的字段: {lost}"

    def test_the_model_itself_still_holds_every_field(self):
        # The cache writes model_dump_json() of the FULL model; if compaction ever
        # leaked into the model, a stored envelope would silently lose fields and
        # the next call would read defaults it never wrote.
        r = _fetch()
        assert set(r.model_dump()) == set(ResponseModel.model_fields)
        assert r.is_truncated is False and r.page_type == "unknown"

    def test_json_stays_valid_and_ascii_preserved(self):
        text, _ = _wire_json(_fetch(content=["中文内容 · 示例"],
                                    metadata={"title": "标题"}))
        assert json.loads(text)["content"] == ["中文内容 · 示例"]
        assert "\\u" not in text, "非 ASCII 被转义会让中文正文体积膨胀数倍"


class TestOneChannelPerCall:
    """structuredContent was a second copy of the same JSON, always paid, never
    asked for. 17.0: the opt-in (and the flag that drove it) is gone — the text
    channel is the whole wire surface, unconditionally."""

    def _result(self):
        from dhole_mcp.wire import _wire_result
        return _wire_result(_fetch(status=200, content=["body"],
                                   metadata={"title": "T"}))

    def test_the_text_channel_carries_the_whole_response(self):
        from dhole_mcp.wire import _call_result
        res = _call_result(self._result())
        assert res.structured_content is None
        assert json.loads(res.content[0].text)["metadata"] == {"title": "T"}

    def test_the_flag_that_switched_the_channel_is_gone(self):
        import dhole_mcp.server as server_mod
        import dhole_mcp.wire as wire_mod
        assert not hasattr(server_mod, "_STRUCTURED_CONTENT"), (
            "第二通道的开关必须连同第二通道一起删除，不能留一个永远无效的旋钮")
        assert not hasattr(wire_mod, "_STRUCTURED_CONTENT")

    def test_a_content_list_without_a_payload_passes_through(self):
        # screenshot returns list[ImageContent|TextContent], not a tuple.
        from mcp.types import TextContent
        from dhole_mcp.wire import _call_result
        res = _call_result([TextContent(type="text", text="x")])
        assert res.content[0].text == "x"


class TestNoEscapeHatch:
    """17.0: DHOLE_WIRE_FULL 已注销 —— 压缩无条件。"缺席即默认"写进了工具文档
    与 instructions；一个读不懂缺席语义的调用方也读不懂工具文档。保留逃生阀
    等于把两套线格式都当永久承诺养着。"""

    def test_the_flag_is_gone(self):
        import dhole_mcp.server as server_mod
        import dhole_mcp.wire as wire_mod
        # 压实层搬进 wire.py 之后，"退役名字没有偷偷复活"必须在它的新家也成立，
        # 只查 server 就成了假守卫。
        for mod in (server_mod, wire_mod):
            assert not hasattr(mod, "_WIRE_FULL")
            assert not hasattr(mod, "_wire_dump_full"), (
                "旧形状的序列化器要么被引用（两套承诺）要么不存在")

    def test_the_default_stays_compact(self):
        """压缩无条件 —— 否则这轮瘦身等于没做。"""
        p = _wire_json(_fetch())[1]
        assert "is_truncated" not in p and "page_type" not in p

    def test_map_mode_row_pruning_is_untouched(self):
        """说清边界：地图行的按列裁剪是模型自己的序列化器，不是线上策略。"""
        rows = [CrawlPage(url=f"https://a.test/{i}", status=200,
                          page_type="discover_only", content_ok=True,
                          summary="mapped") for i in range(2)]
        c = CrawlResponseModel(start_url="https://a.test", pages=rows,
                               discover_only=True, pages_discovered=2)
        _text, payload = _wire_json(c)
        assert all("page_type" not in row for row in payload["pages"]), (
            "地图模式仍按列裁剪（那是 discover_only 的语义，不是线上策略）")


class TestThePolicyTableItself:
    def test_every_named_field_is_real(self):
        """A typo in _WIRE_OMIT is a silent no-op: the field just keeps shipping.

        The table is the whole contract, so an entry that matches nothing has to
        fail here rather than quietly cost bytes forever.

        模型名单是**扫**出来的，不是手抄的（B-75 那一族：手抄的登记表每加一副回执就要有人
        记得补一行，忘的那一次红的是"名单没抄全"而不是"策略点错了字段"—— R02-133 新加的
        `FeedRefusalModel` 正好撞在这行上）。改名仍会红：策略表里那个名字在派生名单里找不到。
        """
        import inspect

        from pydantic import BaseModel

        import dhole_mcp.crawl as _crawl
        import dhole_mcp.feed as _feed
        import dhole_mcp.models as _models
        import dhole_mcp.search as _search

        models = {}
        for mod in (_models, _feed, _crawl, _search):
            for name, obj in inspect.getmembers(mod, inspect.isclass):
                if obj.__module__ == mod.__name__ and issubclass(obj, BaseModel):
                    models[name] = obj
        assert set(_WIRE_OMIT) <= set(models), \
            "策略表点名了不存在的模型（模型改名会让整组压缩静默失效）"
        for name, fields in _WIRE_OMIT.items():
            real = set(models[name].model_fields)
            missing = set(fields) - real
            assert not missing, f"{name} 的压缩策略点名了不存在的字段：{sorted(missing)}"


# ============================================================================
# 来源：test_params_reach_the_tool.py
# ============================================================================
# 17.0 B4：参数只有顶层这一个家，于是"传了就得抵达"成为唯一的契约。

_CRAWL_OK = CrawlResponseModel(start_url="https://example.com/", pages=[])
_SEARCH_OK = SearchResponseModel(query="q", results=[])
_FETCH_OK = ResponseModel(url="https://example.com/", status=200, content=["body"],
                          fetcher_used="http")

URL = "https://example.com/"

# tool -> 这条调用最少要有的东西，以及桩返回什么
BASE: dict[str, tuple[dict, object]] = {
    "web_read": ({"url": URL}, _FETCH_OK),
    # 17.0 §4 第 4 行：写操作自己的门。`method` 在这里是主语之一（必填），所以 BASE
    # 得带上它 —— 少一颗，这条 dispatch 就在测"缺 method 的拒答"而不是抵达。
    "web_request": ({"url": URL, "method": "POST"}, _FETCH_OK),
    # 17.0 §4 第 2 行：动作链那颗门。`actions` 与 `url` 一样是必填主语，BASE 得带上。
    "web_act": ({"url": URL, "actions": [{"wait": 1}]}, _FETCH_OK),
    "web_crawl": ({"url": URL}, _CRAWL_OK),
    # 17.0 §4 拆出来的那颗：同一个引擎，另一个入口，参数只剩地图那一半
    "web_discover": ({"url": URL}, _CRAWL_OK),
    "web_search": ({"query": "python asyncio"}, _SEARCH_OK),
}

# 每个参数一个探针值。值要能活过线路层的强制转换，所以不是一律 "x"。
#
# 17.0 §3-17 把五颗身份参数（auth / cookies / proxy / extra_headers / allow_private）
# 从公共面上搬进 cred_defaults 的落盘存储，探针表里因此不再有它们的位置：这里的每个
# 名字都必须还是某个工具的参数（下面那条守卫会钉住），删掉的键按这个文件对退役键的
# 既有做法办 —— 从表里拿掉，另立一条「传了就拒」的用例（TestTheIdentityKeysRefused）。
#
# B5-13d 之后同理少了两颗渲染时机（wait / network_idle）：它们不再是任何工具的参数，
# 表里留着就是让"覆盖了多少颗"这个数字掺水。"传了就拒"那半边由
# tests/test_render_settle_b5.py 的 TestTheCallSurfaceLostBothKnobs 钉住，能力本身
# （认出壳页才付的那 5 秒）由同一份文件守。
VALUES: dict[str, object] = {
    # web_read
    "css_selector": "p", "max_content_chars": 5000, "timeout": 20000,
    "pages": "1-2", "password": "pw",
    "schema": {"properties": {"t": {"selector": "p"}}},
    # B5-14d 的两层扇出。nested_max 的探针值是 5：既不是"没传"（None），也不是
    # 默认展开数 8 —— 两个都不撞，丢了才分辨得出是丢了。
    "nested_selector": "div.quote",
    "nested_fields": [{"name": "t", "selector": "p"}],
    "nested_max": 5,
    # B5-13a：档位是字符串枚举，探针值要和默认值 "auto" 不同才探得出丢没丢。
    "anti_detect": "aggressive",
    # W1：一颗对象参数。探针值避开"没传"（None）与空字典
    # （那在管道里同罪于没传），名字也不是任何一族拒绝的住户。
    "headers": {"x-probe": "1"},
    "max_links": 5, "session_id": "probe-session",
    # 整轮字符帽（批次 5 新增）：探针值要避开默认 60000，也避开 0（"不要帽"）——
    # 两个都不撞，丢了才分辨得出是丢了。
    "max_total_chars": 30000,
    "method": "GET", "body": {"a": 1}, "content_type": "application/json",
    "focus": "topic", "focus_mode": "best", "focus_engine": "bm25",
    "actions": [{"wait": 1}], "cache_ttl": 300,
    # 探针值不许与工具自己的默认值重合，否则"丢了"和"到了"看起来一样：
    # extraction_type 用 html（默认是 markdown），offset 用 100（默认 0）。
    "extraction_type": "html", "force_fetcher": "http", "offset": 100,
    # 批量：唯一一个"丢了就根本走错代码路径"的参数
    "urls": [URL + "a", URL + "b"],
    # G33/G35：CDP attach 与无模型登录判定（探针值而已，方法被桩替换，不真 attach）
    "cdp_url": "http://127.0.0.1:9222",
    # True 而不是 False：默认就是不放行，探得出"值被丢了"的探针才叫探针。
    "confirm": True,
    # 批次 11：同一句理由。默认是隐形，探针要说"看得见"，丢了才看得出来丢了。
    "headed": True,
    # 刀9-7（F9）：驻留那颗也必须走完全程 —— `web_act(pin=true)` → `web_read` →
    # `_force_fetch` → `stealthy_fetch` → `bulk_stealthy_fetch` → 浏览器层，少一环
    # 就等于调用方说了话而下面没人听见（这一颗的"听见"由
    # test_a_pinned_page_... 那组量，这里只钉它到得了方法）。
    "pin": True,
    # R3：证据那颗。True 而不是 False —— 默认就是关，探针要探得出"值被丢了"。
    "include_headers": True,
    # R4：落盘那两颗。`save_to` 的探针值是一行合法的相对路径（方法被桩替换，不会
    # 真的写盘，所以它只需要是一颗**认得出**的字符串）；`max_save_bytes` 避开默认的
    # 268435456，也避开 0（那是"没给"），丢了才分辨得出是丢了。
    "save_to": "probe-download.bin", "max_save_bytes": 33_554_432,
    # web_crawl
    "max_pages": 3, "max_depth": 1, "path_include": "/a", "path_exclude": "/b",
    "max_content_chars_per": 2000, "concurrency": 2,
    "deadline_ms": 60000, "sitemap": True, "search": "topic",
    # P11 断点续传三颗（selector 的形状探针与 web_read 的 schema 同款；
    # resume_token 用 plan_id:offset 的真实形状，而不是任意串）。
    "resume_token": "abcd1234ef56:0", "selector": "h1.title",
    # web_search
    "max_results": 5, "mode": "auto", "engines": ["bing"], "site": "example.com",
    "exclude_sites": ["other.example"], "location": "cn", "language": "zh",
    "region": "cn-zh", "page": 1, "freshness": "week", "after": "2026-09-20",
    "before": "2026-01-01", "url": URL, "fetch_content": True,
    "fetch_schema": {"properties": {"t": {"selector": "p"}}},
    "min_relevance": 0.2, "min_raw_relevance": 0.1,
    # P0 纵向搜索：两颗都在 web_search 上。sources 取一个纵向池而不是默认
    # ['web']，sort 取非默认值，丢了才分辨得出是丢了。
    "sources": ["academic"], "sort": "newest",
    # #8：共识地板。探针取 2（合法下界）——1 会被 web_search 在发请求前拒掉，
    # 那测的是拒绝路径而不是抵达性。BASE 里带着 fetch_content=True，所以这颗
    # 有作用对象、不会被"没开 fetch_content"那条拒绝挡下。
    "fetch_min_consensus": 2,
    # A1：screenshot 的六颗旋钮（quality/save_to/image_type/wait_selector/
    # full_page/selector/decode_qr）随 web_screenshot 一起离开顶层参数面 ——
    # 它们现在住在 web_act 截图动作的对象里（actions 归一化层守形状），不再
    # 是任何工具的顶层参数，按本文件对退役键的既有做法从表里拿掉。
}
# 布尔类共用一个值；列出来是为了让"表里查不到"这条守卫说得准。
# §3-17：allow_private 原来是这张表里的一颗（每次调用放行内网），现在它只能从
# cred_defaults 落盘，所以按上面 VALUES 的同一处理从表里拿掉。
# B5-13d：network_idle 同样拿掉 —— 渲染时机不再是调用方能说的话。
BOOL_KEYS = {
    "include_media", "include_links", "ignore_robots",
    "sitemap", "check_login",
}
for _k in BOOL_KEYS:
    VALUES.setdefault(_k, True)

# 管线会合法改写这几个值（清理、规范化），断言因此改成"到了且被认下来"。
REWRITTEN = {"method": "GET", "force_fetcher": "http", "before": None}


def _optional_names(tool: str) -> frozenset:
    """这个工具能被接受的参数名，去掉那条调用必带的主语。

    主语按工具算：web_search 的 `url=` 不是主语（它的主语是 query，url 是
    find_similar 的输入，一个真正的可选参数）。
    """
    subject = {"query"} if tool == "web_search" else {"url"}
    return server_mod._TOP_LEVEL_ARGS[tool] - subject


def _capture(monkeypatch, tool: str) -> list[dict]:
    calls: list[dict] = []

    async def fake(self, **kwargs):
        calls.append(dict(kwargs))
        return BASE[tool][1]

    monkeypatch.setattr(MasterFetchServer, tool, fake)
    return calls


def _dispatch(tool: str, args: dict):
    return asyncio.run(MasterFetchServer()._dispatch(tool, args))


def _def_of(name: str) -> dict:
    return {td["name"]: td for td in MasterFetchServer._TOOL_DEFS}[name]


class TestTheValueTableCoversTheSurface:

    @pytest.mark.parametrize("tool", sorted(BASE))
    def test_every_accepted_parameter_has_a_probe_value(self, tool):
        """新参数进了 `_TOP_LEVEL_ARGS` 就必须进这张表，否则它躲过整组抵达性用例。"""
        missing = sorted(_optional_names(tool) - set(VALUES))
        assert not missing, f"{tool} 的 {missing} 没有探针值"

    def test_the_probe_table_names_no_key_no_tool_accepts(self):
        """反向半边：表里不许躺着没人接受的键 —— 那会让"覆盖了"这个数字是假的。"""
        everywhere = set().union(*(set(v) for v in server_mod._TOP_LEVEL_ARGS.values()))
        dead = sorted(set(VALUES) - everywhere)
        assert not dead, f"{dead} 已经不是任何工具的参数了，从表里删掉"


class TestEveryParameterArrives:
    """每个被接受的参数名都要真的抵达方法，值还是调用方给的那个。"""

    @pytest.mark.parametrize("tool", sorted(BASE))
    def test_each_parameter_reaches_the_method(self, tool, monkeypatch):
        offenders = []
        for key in sorted(_optional_names(tool)):
            calls = _capture(monkeypatch, tool)
            _dispatch(tool, {**BASE[tool][0], key: VALUES[key]})
            assert calls, f"{key} 连方法都没到"
            got = calls[-1].get(key, _MISSING)
            if got is _MISSING:
                offenders.append(f"{key}: 被丢了（方法根本没收到这个名字）")
            elif key not in REWRITTEN and got != VALUES[key]:
                offenders.append(f"{key}: 传 {VALUES[key]!r} 收到 {got!r}")
        assert not offenders, offenders


class _Missing:
    def __repr__(self):
        return "<absent>"


_MISSING = _Missing()


class TestAbsentMeansTheToolsOwnDefault:
    """没传的键不许被递成 None —— 那会盖掉方法自己那个非 None 的默认值。"""

    def test_an_unsent_parameter_is_absent_not_none(self, monkeypatch):
        calls = _capture(monkeypatch, "web_read")
        _dispatch("web_read", {"url": URL})
        got = calls[-1]
        for key in ("extraction_type", "cache_ttl", "focus", "pages", "method",
                    "allow_private", "session_id", "force_fetcher"):
            assert key not in got, f"{key} 被代答成了 {got.get(key)!r}"

    def test_a_null_valued_parameter_is_not_set_either(self, monkeypatch):
        """客户端把每个 schema 属性都回显成 null 是常见的，那不等于"传了一个 null"。"""
        calls = _capture(monkeypatch, "web_crawl")
        _dispatch("web_crawl", {"url": URL, "max_pages": None, "focus": None})
        assert "max_pages" not in calls[-1] and "focus" not in calls[-1]

    def test_the_server_default_cache_ttl_survives_the_dispatcher(self, monkeypatch):
        """``--cache-ttl`` 在 MCP 这条路上必须是活的。

        分支曾把 cache_ttl 硬写成模块默认值（3600）再传下去，于是服务端配置的 TTL
        永远读不到。不传就是"让服务端决定"，这条不能被调度器代答。
        """
        seen: dict = {}

        async def fake(self, url, *a, **kw):
            seen.update(kw)
            return ResponseModel(url=url, status=200, content=["x"])

        monkeypatch.setattr(MasterFetchServer, "_auto_escalate", fake)
        server = MasterFetchServer(cache_ttl=7)
        asyncio.run(server._dispatch("web_read", {"url": URL}))
        assert seen["cache_ttl"] == 7, "服务端配置的 TTL 被调度器代答成了模块默认值"

        seen.clear()
        asyncio.run(server._dispatch("web_read", {"url": URL, "cache_ttl": 0}))
        assert seen["cache_ttl"] == 0, "显式的 cache_ttl=0 必须仍然绕过缓存"


class TestAWrongNameIsRefused:
    """名字打错必须抛，而且要说清支持哪些名字。"""

    def test_a_typo_is_refused_with_the_supported_set(self):
        with pytest.raises(ValueError) as ei:
            _dispatch("web_crawl", {"url": URL, "max_pges": 3})
        msg = str(ei.value)
        assert "max_pges" in msg and "max_pages" in msg, msg

    def test_the_removed_bag_is_named_as_removed_not_as_unknown(self):
        """老写法的调用撞上来时，"陌生键"三个字会把人引向"这工具没这个能力"——
        正好相反：能力全在，只是位置换了。所以这句话必须点名**袋子没有、键在顶层**，
        并把能原样搬出去的那几颗点名。
        （R02-F49：这句从前写「removed in 17.0」—— 版本号对不在那一版上的调用方是假话，
        而它站在处方位上。守的两件事一件不少，字面跟着改。）
        """
        with pytest.raises(ValueError) as ei:
            _dispatch("web_search", {"query": "q", "options": {"max_results": 5}})
        msg = str(ei.value)
        assert "no options object" in msg, msg
        assert "max_results" in msg, msg

    def test_a_bag_key_that_could_not_move_anywhere_says_so(self):
        """袋子里装了一个哪儿都不存在的键：不能假装它"可以搬出来"。"""
        with pytest.raises(ValueError) as ei:
            _dispatch("web_search", {"query": "q", "options": {"frobnicate": 1}})
        assert "frobnicate" not in str(ei.value).split("no options object")[1]


class TestTheTwoChoiceKnobsAreGone:
    """§3-27：三级提取梯子一寸不动，但"走哪条提取路径"不再是调用方的选择权。

    这两个键（`main_content_only` / `use_trafilatura`）还能动的时候，删掉的不是
    参数而是"没人用的参数"；它们必须进拒绝名单，否则 schema 上仍然挂着两个
    今天不需要的开关。梯子本身由 test_extractor.py 那组端到端用例守着 ——
    这条删的是选择权，不是那三条路径。
    """

    @pytest.mark.parametrize("key", ["main_content_only", "use_trafilatura"])
    def test_the_key_is_refused(self, key):
        with pytest.raises(ValueError, match="Unsupported argument") as ei:
            _dispatch("web_read", {"url": URL, key: False})
        assert key in str(ei.value)

    @pytest.mark.parametrize("key", ["main_content_only", "use_trafilatura"])
    def test_the_key_is_not_advertised(self, key):
        props = _def_of("web_read")["inputSchema"]["properties"]
        assert key not in props


class TestTheIdentityKeysRefused:
    """§3-17：五颗身份键从 VALUES / BOOL_KEYS 里拿掉了，拿掉的代价是要有人钉住
    「传了会拒」。静默接受一个不再存在的 `cookies=` 比拒掉它坏得多 —— 调用方会带着
    「我传了 cookie」的错觉继续往下走。

    存储侧（cred_defaults 写、工具读回）由 test_cred_defaults 那一组守，这里只管公共面。
    """

    @pytest.mark.parametrize("tool,key", [
        ("web_read", "auth"), ("web_read", "cookies"), ("web_read", "proxy"),
        ("web_read", "extra_headers"), ("web_read", "allow_private"),
    ])
    def test_the_key_is_refused(self, tool, key):
        with pytest.raises(ValueError, match="Unsupported argument") as ei:
            _dispatch(tool, {**BASE[tool][0], key: True})
        assert key in str(ei.value)
        assert key not in VALUES and key not in BOOL_KEYS

    @pytest.mark.parametrize("key", ["auth", "cookies", "proxy", "extra_headers",
                                     "allow_private"])
    def test_the_key_is_not_advertised_by_web_read(self, key):
        props = _def_of("web_read")["inputSchema"]["properties"]
        assert key not in props

    @pytest.mark.parametrize("key", ["anti_detect", "cdp_url", "session_id"])
    def test_the_three_names_that_stayed_keep_their_probe(self, key):
        """留在每次调用上的三颗必须还有探针值、还在接受名单里 —— 否则这张表下次
        清身份键的时候会顺手把它们一起清掉，抵达性用例就少守了三颗参数。"""
        assert key in VALUES and key in server_mod._TOP_LEVEL_ARGS["web_read"], key
        if key == "anti_detect":
            assert VALUES[key] != "auto", "探针值与默认值重合就探不出丢没丢"


class TestTheBagIsGoneFromTheWire:
    """袋子不许从 schema 里爬回来 —— 它回来了，这整批就白做。"""

    def test_no_tool_advertises_an_options_property(self):
        for td in MasterFetchServer._TOOL_DEFS:
            props = (td.get("inputSchema") or {}).get("properties") or {}
            assert "options" not in props, td["name"]

    def test_the_instructions_put_every_parameter_at_the_top_level(self):
        """常驻文本只正面声明「不嵌套」，不写历史 —— 袋形的解释住在拒绝消息里，
        也就是唯一会用得上它的时刻（刚写错一次）。
        """
        from dhole_mcp.server import DHOLE_INSTRUCTIONS
        assert "nothing is nested" in DHOLE_INSTRUCTIONS, "放置规则没写清只有一个位置"
        assert '"options"' not in DHOLE_INSTRUCTIONS, "常驻文本又教起袋形了"


# ============================================================================
# 来源：test_a_crawl_row_is_measured_before_the_length_gate_is_blamed.py
# ============================================================================
# R2 两刀：crawl 的一行先被**量到**（0b4cb15），然后判决去读那把尺（这一笔）。

EVALS = Path(__file__).resolve().parent.parent / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))



ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "page_fixtures"
START = "http://127.0.0.1:8399/"
TRAVEL = "http://books.toscrape.com/catalogue/category/books/travel_2/index.html"

_REAL_GETADDRINFO = socket.getaddrinfo


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8", errors="replace")


def _run(html: str, url: str = f"{START}x", start: str = START):
    stats: dict = {}
    md, kind, ok, cut = _classify_and_extract(html, url, start, None, 40_000,
                                              stats=stats)
    return md, kind, ok, stats


@pytest.fixture
def live_site(monkeypatch, tmp_path):
    """靶站真起在 0 号端口：这一族判据要的是真 HTTP 回来的字节。"""
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    monkeypatch.setattr(fs, "RUNTIME", tmp_path)
    monkeypatch.setattr(fs, "REQUESTS", tmp_path / "requests.log")
    # 爬的门只认存量放行（cred_defaults 的 allow_private；conftest 把身份库重定向进
    # tmp_path，所以这一句不会碰用户的机器）。
    identity.set_value("allow_private", True)
    srv = fs.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


class TestTheCollapseRulerIsTheOneWebReadAlreadyCalibrated:

    def test_the_sidecar_numbers_still_read_as_collapsed(self):
        """先确认这一刀用的就是 web_read 那把尺，不是又一颗阈值。"""
        assert is_extraction_collapsed(196, 1374), "travel 那一份的塌陷形状变了"
        assert not is_extraction_collapsed(2179, 1681)
        assert not is_extraction_collapsed(25785, 25245)

    def test_collapse_is_only_asked_where_the_extractor_chose(self):
        """同一份 travel 页：抽成 196 字的 article 是塌陷，渲染成 5,874 字的列表不是。

        list 那一支的 md 是 `* [标题](url)` 清单，它**故意**丢掉非链接的字 —— 拿整页
        可见字去比是拿错的量具量对的东西。这一条钉的就是那个分岔。
        """
        html = _html("travel_2.html")
        collapsed: dict = {}
        _measure_row(collapsed, html, "x" * 196, "article", TRAVEL, TRAVEL)
        assert collapsed == {"visible_chars": 1374, "extracted_chars": 196,
                             "extraction_collapsed": True}, collapsed

        rendered_md, kind, ok, stats = _run(html, TRAVEL, TRAVEL)
        assert (kind, ok) == ("list", True), (kind, ok, stats)
        assert "extraction_collapsed" not in stats, stats
        assert len(rendered_md) > 1374, "渲染出来的列表比整页可见字还短，比值那一问就跑偏了"

    @pytest.mark.parametrize("name", ["gnu_free_sw_definition.html",
                                      "quotes_index.html"])
    def test_a_faithful_extraction_is_not_reported_as_a_failure(self, name):
        """这两份页的比值带（1.02 / 1.30）就是 sidecar 里那组数 —— 抽取是忠实的。

        两个数都从 sidecar 现读，不再在参数表里手抄一份：长度那一格按 trafilatura 版本
        登记（同一份字节 2,179 与 2,864 的差是抽取器换版量出来的），第二个作者迟早和
        第一个对不上账 —— 本条 2026-10-20 就是这样在 CI 上红的。
        """
        meta = json.loads((FIXTURES / name).with_suffix(".meta.json")
                          .read_text(encoding="utf-8"))
        url = meta["captured_from_url"]
        _, kind, ok, stats = _run(_html(name), url, url)
        assert kind == "article", (name, kind)
        assert ok is True
        assert not stats.get("extraction_collapsed"), stats
        assert (stats["extracted_chars"], stats["visible_chars"]) == (
            recorded_markdown_chars(meta, name), meta["visible_text_chars"])

    def test_the_sentence_names_both_numbers_and_the_way_out(self):
        """上报的那一句话要自带证据与解药，不是"有点问题"。"""
        note = _measure_note({"extraction_collapsed": True, "visible_chars": 1374,
                              "extracted_chars": 196})
        assert "1,374" in note and "196" in note and "14%" in note, note
        assert "extraction_type='html'" in note, note
        assert "dhole's reader lost them" in note, note

    def test_a_refusal_names_its_own_reason(self):
        """第二笔之后的语义：被这两个发现拒掉的行，必须把理由一起交出去。

        这一条在 1/2 里写的是「已经 false 的行不再补一句」（那时闸还没动，补一句是
        噪音）。翻闸之后同一个输入是**被这个发现拒的**，没有理由的 false 与本批要消灭
        的那些假 true 是同一种罪，所以那句断言在这里被反过来 —— 不是测试坏了。
        """
        stats = {"extraction_collapsed": True, "visible_chars": 1374,
                 "extracted_chars": 196}
        assert _row_verdict("article", "x" * 196, stats) is False
        assert _measure_note(stats), "拒了这一行却没有给理由"


class TestTheVerdictNowComesFromTheRuler:
    """第二笔：判定开始读那两把尺。这一节钉的是翻完之后每一形落在哪一边。"""

    def test_every_corpus_page_judges_the_same_with_and_without_the_stats_outlet(self):
        """`stats=` 只决定证据给不给调用方看，不决定判决 —— 两处必须是同一次判定。"""
        pages = [(name, getattr(fs, name)) for name in dir(fs)
                 if isinstance(getattr(fs, name), bytes)]
        pages.append(("_obfuscated_shell_page", fs._obfuscated_shell_page()))
        assert len(pages) >= 13, sorted(n for n, _ in pages)
        for name, raw in pages:
            html = raw.decode("utf-8", "replace")
            a = _classify_and_extract(html, f"{START}x", START, None, 40_000)
            b = _classify_and_extract(html, f"{START}x", START, None, 40_000,
                                      stats={})
            assert a[1:] == b[1:], (name, a[1], b[1])

    def test_the_nav_only_row_still_comes_back_a_success(self):
        """`/nav` 的 content_ok 翻闸后**仍然是 true** —— 这一条守的是"没证据就不开火"。

        它是这批的反面承诺：一行看起来确实像菜单（12 条链接全在 `<nav>` 里、主区为空），
        但单行之内没有站得住的判据（剥标签会吃掉 travel 那 11 本书，按 URL 形状聚会把
        `/about`、`/cart` 与 `/quote/1`、`/quote/2` 混为一谈），所以宁可不说也不误杀。
        能说的是跨行那一组同集，而它只上报。断言里把 12 这个数字钉住：判据哪天找到了，
        是这一条先红，不是某个闸悄悄生效。
        """
        md, kind, ok, stats = _run(fs.NAV_PAGE.decode(), f"{START}nav", START)
        assert (kind, ok) == ("list", True), (kind, ok, stats)
        assert stats["list_items"] == 12, stats
        assert not stats.get("thin_list"), stats
        assert len(md) > 300, len(md)

    def test_the_thin_list_flag_needs_a_list_below_three_items(self):
        """`LIST_ITEMS_MIN`（=3）的判据本身：一条列表行只有两三条条目链接时的差别。"""
        assert LIST_ITEMS_MIN == 3
        html = ('<html><body><nav>' + "".join(
            f'<a href="/section/{i}">栏目 {i}</a>' for i in range(12)) +
            '</nav><p>' + "正文太短以至于不成列表。" * 1 + '</p></body></html>')
        stats: dict = {}
        _measure_row(stats, html, "* [a](/x)\n* [b](/y)", "list",
                     f"{START}nav", START)
        assert stats["list_items"] == 12, stats  # nav 里的链接照样数得到（这就是它不够格当闸的原因）

        thin: dict = {}
        _measure_row(thin, '<a href="/one">一</a><a href="/two">二</a>',
                     "* [一](/one)\n* [二](/two)", "list", f"{START}x", START)
        assert thin.get("thin_list") is True, thin
        assert "2 item link(s)" in _measure_note(thin), thin

        # 翻闸的那一半：发现要真的接进判决。两行都用够 50 字的 md，否则两边的差别会被
        # 长度那把尺先判掉，这一条量的就不是薄列表而是长度。
        roomy = ("* [一](/one) 一条足够长的条目说明，长到不会被长度那把尺先判掉\n"
                 "* [二](/two) 另一条同样长的说明文字，形状与上面一致")
        assert len(roomy) >= 50, len(roomy)
        assert _row_verdict("list", roomy, thin) is False, "薄列表没被拒"
        assert _row_verdict("list", roomy, {"list_items": 12}) is True


class TestTheRejectedFloorStaysRejected:
    """一颗看起来合理的阈值怎样被一份正对照挡在门外 —— 写下来免得有人再提。"""

    def test_a_legit_list_page_has_far_fewer_visible_chars_than_its_markdown(self):
        """/collections?items=1：可见字 37 而 markdown 80 —— 那颗"整页要 ≥200 字"的闸
        会把这一页判成没内容，而它的三张卡片一个字都没丢。"""
        md, kind, ok, stats = _run(fs._collections_page(1).decode(),
                                   f"{START}collections", START)
        assert ok is True, stats
        assert stats["visible_chars"] == 37, stats
        assert stats["extracted_chars"] == 80, stats
        assert "专题 1" in md and "/work/1" in md, md

    @pytest.mark.parametrize("items,kind", [(1, "fallback"), (8, "article")])
    def test_visible_text_is_not_a_measure_of_content_for_pages_of_links(self, items,
                                                                        kind):
        md, got_kind, ok, stats = _run(fs._collections_page(items).decode(),
                                       f"{START}collections", START)
        assert got_kind == kind, stats
        assert ok is True
        # 卡片页的正文字数天生低于它交回去的 markdown：内容是链接，不是散文。
        assert stats["visible_chars"] < stats["extracted_chars"], stats


class TestTheChromeRuleNeedsMoreThanOneRow:
    """"这一行其实是菜单"只在跨行时可证，而它今天只上报。"""

    @staticmethod
    def _measured(sets):
        return {f"http://s/{i}": {"item_set": frozenset(s), "list_items": len(s)}
                for i, s in enumerate(sets)}

    def test_three_rows_carrying_the_identical_item_set_are_named(self):
        group = {"/about", "/cart", "/login"}
        rows = _chrome_rows(self._measured([group, group, group]))
        assert len(rows) == 3 and all(v == CHROME_REPEAT_MIN for v in rows.values()), rows

    def test_a_pair_is_not_evidence_yet(self):
        """两行同集还可能是两个姊妹栏目页真的列了同一批东西 —— 阈值不是装饰。"""
        group = {"/a", "/b", "/c"}
        assert _chrome_rows(self._measured([group, group])) == {}

    def test_a_row_with_its_own_entries_is_never_flagged(self):
        nav = {"/about", "/cart", "/login"}
        rows = _chrome_rows(self._measured([nav | {"/p/1"}, nav | {"/p/2"},
                                            nav | {"/p/3"}, nav, nav, nav]))
        assert sorted(rows) == ["http://s/3", "http://s/4", "http://s/5"], rows

    def test_a_page_that_contributed_no_item_set_is_not_chrome(self):
        """只有散文的页面没有 item set —— 它不该被任何一行的判据牵连。"""
        assert _chrome_rows(self._measured([set(), set(), set()])) == {}


class TestTheLiveCrawlCarriesTheDisclosure:
    """端到端：这一族尺在真 HTTP 字节上不误伤，而报出来的话落在该行与整轮上。"""

    @pytest.mark.asyncio
    async def test_a_collapsing_row_recovers_in_place_and_says_so(self, live_site):
        """管路 + 判决一起证：markdown 路塌陷的那一行现在**就地补回**，且把这笔账说出口。

        历史契约演变：1/2 里断言 `content_ok is True`（"这一刀不动结论位"）→ 翻闸后改为
        拒绝并交出处方（`extraction_type='html'`）→ 2026-10-23 起与 `web_read` 的坍缩补救
        同一刀：同一份字节的 html 路就地接管，不发第二个请求，行 summary 写明接管与形态。
        挂具 `/longaside` 是量出来的形状（markdown 路 2.5k 抽得到、aside 里 4.6k+ 个可见
        字被主内容过滤器整块丢掉），不是照着断言捏的。
        """
        result = await MasterFetchServer().web_crawl(
            f"{live_site}/longaside", max_pages=1, max_depth=0)
        assert result.pages, result.error
        row = result.pages[0]
        assert row.page_type == "article", row.summary
        assert row.content_ok is True, (row.summary, row.error)
        assert row.content_chars >= 7_000, (row.summary, row.content_chars)
        found = re.search(r"collapsed on the markdown pass \(([\d,]+) of "
                          r"([\d,]+) visible", row.summary or "")
        assert found, row.summary
        md_got, visible = (int(x.replace(",", "")) for x in found.groups())
        assert md_got < visible * 0.5, (visible, md_got)
        assert visible > 4_000, visible
        assert "recovered in place from the same bytes by the plain converter" in row.summary
        assert "rather than the main-content detector" in row.summary

    @pytest.mark.asyncio
    async def test_the_read_path_and_the_crawl_path_agree_on_the_same_bytes(self, live_site):
        """`/longaside` 上两条路径给出同一份字节、同一个结论。

        历史实测三阶段：修复前 read 说 `content_ok=false`、crawl 写 `content_ok=true`
        （各说各话）→ 翻闸后两边都拒 → 2026-10-23 坍缩补救两侧同刀后，两边都就地补回
        同一份标记。一致指：字数一致、结论一致、且都是接管后的产物。
        """
        server = MasterFetchServer()
        via_read = await server.web_read(url=f"{live_site}/longaside",
                                         force_fetcher="http")
        via_crawl = await server.web_crawl(f"{live_site}/longaside", max_pages=1,
                                           max_depth=0)
        assert via_read.content_ok is True, via_read.error
        assert via_read.extracted_type == "markdown", via_read.extracted_type
        row = via_crawl.pages[0]
        assert row.content_ok is True, row.summary
        assert "plain converter" in (row.summary or ""), row.summary
        read_chars = sum(len(x) for x in via_read.content)
        assert abs(read_chars - row.content_chars) < 60, (read_chars, row.content_chars)

    @pytest.mark.asyncio
    async def test_legit_rows_carry_no_measurement_noise(self, live_site):
        result = await MasterFetchServer().web_crawl(
            f"{live_site}/nav", max_pages=6, max_depth=1)
        assert result.pages, result.error
        for p in result.pages:
            assert "extraction collapsed" not in (p.summary or ""), p.summary
            assert "site chrome" not in (p.summary or ""), p.summary
            assert "thin list" not in (p.summary or ""), p.summary
        assert "site chrome" not in result.summary, result.summary

    @pytest.mark.asyncio
    async def test_the_nav_seed_row_still_comes_back_as_a_list(self, live_site):
        """翻闸翻不到 `/nav`：这一行仍然 `content_ok=true`。

        不是没想到它 —— 是单行判据不存在（`TestTheChromeRuleNeedsMoreThanOneRow` 那组
        用例说清了为什么：剥标签会吃掉 travel 的 11 本书，按 URL 形状聚会把 section 菜单
        与 slug 列表混为一谈）。能说的是跨行那一组同集，而它只上报。
        """
        result = await MasterFetchServer().web_crawl(
            f"{live_site}/nav", max_pages=1, max_depth=0)
        assert len(result.pages) == 1, result.pages
        row = result.pages[0]
        assert row.page_type == "list", row.summary
        assert row.content_ok is True, row.summary
