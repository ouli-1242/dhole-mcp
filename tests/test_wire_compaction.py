"""线格式压缩（wire compaction）的契约测试。

为什么要单独测：描述是连接时付一次（实测 instructions 1,454 字符 + tools/list
21,630 字符 ≈ 5.8k token），而**响应 envelope 是每次调用都要付**。实测：

  - 一个 0 正文的 404 返回 927 字符，其中 33% 是停在默认值上的字段
    （is_stale=false、page_type="unknown"、links={}、retry_count=0……）；
  - duration_ms 发成 1583.716630935669（32 字符），fetched_at 带微秒+偏移（48 字符）。

默认值在"缺席表达同样意思"时不携带信息，所以线上格式把它删掉：上面那个 404
压到 501 字符（-50%），成功抓取 871 → 441（-49%）。

这里守的是两条容易被后续改动破掉的边界：

  1. **只有线格式被压**。ResponseModel 本身字段齐全，缓存信封、crawl 的逐页聚合、
     actions 层读到的仍是完整对象——压缩出错不能污染已存储的东西。
  2. **删的必须是默认值，不能是判定**。content_ok=false 是一个结论；
     relevance_score=0.0 是重排器在说"离题"；quality_score=None 是"从没打过分"。
     它们各自的语义必须留在响应里（缺席或显式值均可，但不得翻转成 0.0 那种误读）。
"""

from __future__ import annotations

import json

from dhole_mcp.crawl import CrawlPage, CrawlResponseModel
from dhole_mcp.search import SearchResponseModel, SearchResult
from dhole_mcp.server import (
    BulkResponseModel,
    CacheInfoModel,
    LoginModel,
    RecipeModel,
    ResponseModel,
    SessionCensusRow,
    SessionClosedModel,
    VaultModel,
    WatchModel,
    _WIRE_OMIT,
    _wire_dump,
    _wire_json,
)

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
                      "source_type", "is_official", "is_stale", "not_modified",
                      "source", "archived_at", "quality_score", "media",
                      "links", "table_of_contents", "cache_validators",
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
        p = _payload(_fetch(not_modified=True, cache_validators={"etag": "E"}))
        assert p["not_modified"] is True and p["cache_validators"] == {"etag": "E"}


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

    def test_cache_clear_does_not_pay_for_an_empty_pool_snapshot(self):
        p = _payload(CacheInfoModel(message="purged 0 entries"))
        assert "engine_health" not in p and "purged" not in p
        assert p["message"]


    def test_a_projected_row_without_a_model_still_gets_its_policy(self):
        """feed_fetch 交回的是自己拼的 dict，_wire_dump 无从查类。

        容器名登记在 _WIRE_PROJECTED_ROWS 里，策略才落得下去。漏掉它的代价是按
        feed 计费的：一批安静的订阅每行都付 error:"" / since:"" /
        discovered_from:"" / not_modified:false。而两张轮询收据不在策略里，
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
        for quiet in ("error", "since", "discovered_from", "not_modified",
                      "cache_validators"):
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

        models = [ResponseModel, BulkResponseModel, CacheInfoModel, CrawlPage,
                  CrawlResponseModel, SearchResult, SearchResponseModel,
                  FeedItem, FeedResult, SessionClosedModel, SessionCensusRow]

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
        from dhole_mcp.server import _wire_result
        return _wire_result(_fetch(status=200, content=["body"],
                                   metadata={"title": "T"}))

    def test_the_text_channel_carries_the_whole_response(self):
        import dhole_mcp.server as server_mod
        res = server_mod._call_result(self._result())
        assert res.structured_content is None
        assert json.loads(res.content[0].text)["metadata"] == {"title": "T"}

    def test_the_flag_that_switched_the_channel_is_gone(self):
        import dhole_mcp.server as server_mod
        assert not hasattr(server_mod, "_STRUCTURED_CONTENT"), (
            "第二通道的开关必须连同第二通道一起删除，不能留一个永远无效的旋钮")

    def test_a_content_list_without_a_payload_passes_through(self):
        # screenshot returns list[ImageContent|TextContent], not a tuple.
        from mcp.types import TextContent
        import dhole_mcp.server as server_mod
        res = server_mod._call_result([TextContent(type="text", text="x")])
        assert res.content[0].text == "x"


class TestNoEscapeHatch:
    """17.0: DHOLE_WIRE_FULL 已注销 —— 压缩无条件。"缺席即默认"写进了工具文档
    与 instructions；一个读不懂缺席语义的调用方也读不懂工具文档。保留逃生阀
    等于把两套线格式都当永久承诺养着。"""

    def test_the_flag_is_gone(self):
        import dhole_mcp.server as server_mod
        assert not hasattr(server_mod, "_WIRE_FULL")
        assert not hasattr(server_mod, "_wire_dump_full"), (
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
        """
        models = {"ResponseModel": ResponseModel,
                  "CrawlPage": CrawlPage,
                  "CrawlResponseModel": CrawlResponseModel,
                  "SearchResult": SearchResult,
                  "SearchResponseModel": SearchResponseModel,
                  "CacheInfoModel": CacheInfoModel,
                  "RecipeModel": RecipeModel,
                  "WatchModel": WatchModel,
                  "VaultModel": VaultModel,
                  "LoginModel": LoginModel}
        from dhole_mcp.feed import FeedItem, FeedResult
        models.update({"FeedItem": FeedItem, "FeedResult": FeedResult,
                       "SessionCensusRow": SessionCensusRow,
                       "SessionClosedModel": SessionClosedModel})
        assert set(_WIRE_OMIT) <= set(models), \
            "策略表点名了不存在的模型（模型改名会让整组压缩静默失效）"
        for name, fields in _WIRE_OMIT.items():
            real = set(models[name].model_fields)
            missing = set(fields) - real
            assert not missing, f"{name} 的压缩策略点名了不存在的字段：{sorted(missing)}"
