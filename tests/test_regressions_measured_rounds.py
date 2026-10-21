"""压力测试 / 上限测试 / 文档超时 / 扩展报告 / 线参数类型 的实测回归

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import asyncio
import json
import pytest
from dhole_mcp import server as server_mod
from dhole_mcp import search as search_mod
from dhole_mcp.focus import focus_content, _tokens
from dhole_mcp.search import SearchResult
from dhole_mcp.server import MasterFetchServer, ResponseModel
from dhole_mcp.structured import extract_structured
from mcp.types import Tool
from dhole_mcp import crawl as crawl_mod
from dhole_mcp.server import DHOLE_INSTRUCTIONS, _agent_hints, _annotate_quality, _is_soft_failure, _with_agent_hints
import dhole_mcp.fetcher as fetcher
from dhole_mcp.server import MAX_RESPONSE_BYTES, _document_budget_advice, _http_tier_budget, _is_document_url, _record_latency
from pathlib import Path
from dhole_mcp import feed as feed_mod
from dhole_mcp import identity
from dhole_mcp.envelope import detect_page_type
from dhole_mcp.fetcher import Response
from dhole_mcp.server import _detect_content_issue, _is_js_shell
from dhole_mcp import actions as actions_mod
from dhole_mcp.search_engines import EngineReport, RawResult


# ============================================================================
# 来源：test_loadtest_report_regressions.py
# ============================================================================
# Regressions for the 2026-09-24 load test (dhole_压力测试报告.md, v15.1).

# Helpers a fix ADDS are reached as server_mod.<name> inside each test, never
# bound at module level: a module-level attribute read of something that does not
# exist yet makes the whole file fail collection against the pre-fix revision,
# which hides which tests are actually red.

_PAGE_WITH_THREE_LINKS = (
    "<html><body><p>Body text.</p>"
    '<a href="https://example.test/one">One</a>'
    '<a href="https://example.test/two">Two</a>'
    '<a href="https://example.test/three">Three</a>'
    "</body></html>"
)


def _dispatch(tool: str, args: dict):
    """Call the real dispatcher; returns (content_list, structured_dict)."""
    return asyncio.run(MasterFetchServer()._dispatch(tool, args))


def _envelope(tool: str, args: dict) -> dict:
    """Dispatch a call that is expected to be rejected with an envelope."""
    _content, structured = _dispatch(tool, args)
    return structured


def _record(monkeypatch, method: str, result=None) -> dict:
    """Stub a tool method; return the dict it records its kwargs into."""
    seen: dict = {}

    async def fake(self, **kwargs):
        seen.update(kwargs)
        return result

    monkeypatch.setattr(server_mod.MasterFetchServer, method, fake)
    return seen


_REJECTED = ResponseModel(url="", status=0, content=[])


def _stub_fetch_layer(monkeypatch, result: ResponseModel) -> None:
    """Replace the fetch tiers with one canned result.

    The stub runs the real ``_apply_chunking``/``_with_agent_hints`` tail, so the
    response under test is assembled by production code — stubbing the whole
    method would test the stub instead of the envelope. The canned result keeps
    its own ``url``: that is the value the redirect test needs to observe.
    """
    async def fake_auto_escalate(self, url, *a, **kw):
        return server_mod._apply_chunking(result)

    monkeypatch.setattr(server_mod.MasterFetchServer, "_auto_escalate", fake_auto_escalate)


# ─── D-01 / D-02 / D-03 / D-09: dispatched argument types ──────────────


class TestDispatchedArgumentTypes:
    """The dispatcher reads every argument with ``args.get()``.

    The server runs on the low-level ``mcp.server.Server`` with a hand-written
    ``on_call_tool``, so ``Annotated``/``Literal`` reach clients as wire schema
    and validate nothing at runtime. Three silent defects lived in that gap.
    """

    def test_urls_as_a_string_is_rejected_not_iterated(self, monkeypatch):
        """D-01: ``urls="https://example.com"`` answered with 19 junk results.

        ``len(urls)`` on the string was 19, ``for u in urls`` iterated its
        characters, and every result carried a single character as its URL —
        ``total=19, successful=19``, no error anywhere.
        """
        seen = _record(monkeypatch, "web_read", _REJECTED)
        payload = _envelope("web_read", {"urls": "https://example.com"})

        assert payload["status"] == 0
        assert "array of strings" in payload["error"]
        assert "got a single string" in payload["error"]
        assert seen == {}, "a rejected call must not reach the tool at all"

    def test_a_non_string_element_is_named_instead_of_leaking_pydantic(self, monkeypatch):
        """D-09: ``urls=["https://...", 123]`` leaked a bare Pydantic error.

        It reached ``ResponseModel(url=123)`` and came back as
        "1 validation error for ResponseModel" — framework internals, no
        recovery hint, and nothing naming which element was wrong.
        """
        seen = _record(monkeypatch, "web_read", _REJECTED)
        payload = _envelope("web_read", {"urls": ["https://example.com", 123]})

        assert "urls[1]" in payload["error"]
        assert "int" in payload["error"]
        assert "validation error" not in payload["error"].lower()
        assert seen == {}

    def test_a_json_encoded_array_is_still_accepted(self, monkeypatch):
        """Clients that stringify nested structures must keep working.

        ``_coerce_arg_types`` exists for the same reason; an array literal has
        exactly one reading, so it is honoured rather than rejected with the
        bare string.
        """
        seen = _record(monkeypatch, "web_read", _REJECTED)
        _dispatch("web_read", {"urls": '["https://a.test", "https://b.test"]'})
        assert seen["urls"] == ["https://a.test", "https://b.test"]

    def test_a_url_list_of_strings_still_reaches_the_tool(self, monkeypatch):
        """The legitimate path is untouched."""
        seen = _record(monkeypatch, "web_read", _REJECTED)
        _dispatch("web_read", {"urls": ["https://a.test", "https://b.test"]})
        assert seen["urls"] == ["https://a.test", "https://b.test"]

    @pytest.mark.parametrize("value", ["false", "no", "0", "off", False, 0])
    def test_a_stringified_false_confirm_erases_no_sessions(self, value):
        """D-03, pinned on the argument that still decides an erasure.

        The defect was ``args.get("all", False)`` handed straight to ``if all:``,
        where every non-empty string is truthy. 17.0 B5-12 collapsed the
        expired-only sweep into the whole-cache clear (the rows it dropped were
        going to lapse anyway, and judging the difference was never the caller's
        to make), so the trap now lives on ``confirm`` - the one spelled-out
        boolean that gates erasing every session's cookies.
        """
        from dhole_mcp import sessions

        sessions.store("alpha", "https://shop.example.com/account", {"sid": "v"})
        _content, structured = _dispatch("sys_state", {"clear": ["sessions"],
                                                        "confirm": value})
        assert structured["pending_approval"] is True, f"confirm={value!r} must pause"
        assert sessions.header_for("alpha", "https://shop.example.com/account") == {"sid": "v"}, \
            f"confirm={value!r} must not erase everything"

    @pytest.mark.parametrize("value", ["true", "yes", "1", "on", True, 1])
    def test_a_stringified_true_confirm_still_erases(self, value):
        """The other direction keeps working, including stringified."""
        from dhole_mcp import sessions

        sessions.store("alpha", "https://shop.example.com/account", {"sid": "v"})
        _content, structured = _dispatch("sys_state", {"clear": ["sessions"],
                                                        "confirm": value})
        assert not structured.get("pending_approval", False), \
            f"confirm={value!r} should go through"
        assert sessions.header_for("alpha", "https://shop.example.com/account") == {}

    def test_an_unlisted_boolean_string_is_rejected(self):
        """Neither true nor false: guessing would be the original defect again."""
        with pytest.raises(ValueError, match="must be a boolean"):
            server_mod._validate_tool_args("sys_state", {"confirm": "maybe"})

    def test_a_missing_boolean_keeps_its_default(self):
        assert server_mod._validate_tool_args("sys_state", {})["confirm"] is False

    def test_force_fetcher_unlisted_value_is_rejected(self, monkeypatch):
        """D-02: ``force_fetcher="magic"`` silently ran the stealthy browser.

        The value only had to miss ``== "http"`` to land in the stealthy
        ``else`` — the heaviest tier, ~5s plus anti-detect overhead, reported as
        a normal success.
        """
        seen = _record(monkeypatch, "web_read", _REJECTED)
        payload = _envelope("web_read", {"url": "https://example.com", "force_fetcher": "magic"})

        assert "not a fetcher tier" in payload["error"]
        assert seen == {}

    def test_the_dynamic_alias_is_refused_and_says_what_to_use(self):
        """B4-3：'dynamic' 是 Playwright 层删掉（v3.5）之后留给老写法的别名，
        17.0 连别名一起删。它现在必须被拒，并且指名 'stealthy' —— 一句
        "not a fetcher tier" 会让照着旧笔记重写调用的人再猜一次。
        """
        with pytest.raises(ValueError) as exc:
            server_mod._validate_tool_args(
                "web_read", {"force_fetcher": "dynamic"})
        msg = str(exc.value)
        assert "dynamic" in msg and "stealthy" in msg, msg

    @pytest.mark.parametrize("value,expected", [
        ("http", "http"),
        ("stealthy", "stealthy"),
        ("HTTP", "http"),
        (" Stealthy ", "stealthy"),
    ])
    def test_force_fetcher_valid_values_are_preserved(self, value, expected):
        assert server_mod._validate_tool_args(
            "web_read", {"force_fetcher": value},
        )["force_fetcher"] == expected

    def test_an_absent_force_fetcher_stays_none(self):
        assert server_mod._validate_tool_args("web_read", {})["force_fetcher"] is None

    def test_smart_crawl_force_fetcher_is_validated_too(self):
        """crawl.py forwards it straight to web_read — same silent fallback."""
        with pytest.raises(ValueError, match="not a fetcher tier"):
            _dispatch("web_crawl", {
                "url": "https://example.com/", "force_fetcher": "magic",
            })


# ─── D-13: the schema says what is required ───────────────────────────


class TestToolSchemaRequired:
    def _schema(self, name: str) -> dict:
        for tool in MasterFetchServer._TOOL_DEFS:
            if tool["name"] == name:
                return tool["inputSchema"]
        raise AssertionError(f"no tool def for {name}")

    def test_smart_fetch_declares_one_of_url_or_urls(self):
        """The schema said nothing; the tool rejects a call with neither.

        Asserted by VALIDATING the shapes the tool documents rather than by
        comparing the literal to a literal: a hand-matched anyOf stayed green
        while the protocol layer rejected exactly that call - the gate and the
        promise had drifted, and only the promise was being read.
        """
        import jsonschema

        schema = self._schema("web_read")
        assert "required" not in schema, "either one alone satisfies the call"
        for documented in (
            {"url": "https://example.com/"},
            {"urls": ["https://example.com/"]},
            {"urls": ["https://example.com/"], "max_content_chars": 500},
        ):
            jsonschema.validate(instance=documented, schema=schema)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance={"max_content_chars": 500}, schema=schema)

    def test_sys_state_requires_nothing(self):
        """The report listed the cache tool here too; a bare call is the census."""
        assert "required" not in self._schema("sys_state")

    def test_every_other_tool_still_declares_its_required_arg(self):
        for name, required in (
            ("web_crawl", ["url"]), ("doc_parse", ["file_path"]),
            ("feed_read", ["urls"]),
        ):
            assert self._schema(name)["required"] == required

    def test_web_search_declares_no_required_arg(self):
        """评审R2刀4：`query` 从 wire 的 required 下来了。

        宿主按 required 拦调用，而 `mode='find_similar'` 的输入是一颗 URL ——
        留着这颗声明，那颗模式就撞死在宿主那一侧（不传被拒、硬传又被服务端忽略）。
        "缺词"的判决搬到服务端按 mode 判（见
        tests/test_find_similar_takes_url_and_no_query.py），wire 上不再拦。
        """
        assert "required" not in self._schema("web_search")


# ─── D-04: focus tokenization ─────────────────────────────────────────


class TestFocusTokenization:
    def test_a_cjk_query_filters_blocks(self):
        """D-04: a Chinese ``focus`` was a complete no-op.

        ``_TOKEN_RE = [a-z0-9]+`` matched nothing in a Chinese query, so
        ``qterms`` was empty and ``focus_content`` returned the page unchanged —
        no error, no note, just the full text the caller passed ``focus`` to
        avoid.
        """
        text = (
            "北京时间今天凌晨，世界杯决赛在卢赛尔球场结束。\n\n"
            "阿根廷队经过点球大战击败法国队，第三次捧起大力神杯。\n\n"
            "赛后新闻发布会上，主教练谈到了球队的备战计划。"
        )
        result = focus_content(text, "世界杯决赛比分")
        assert "Focus:" in result
        assert "世界杯决赛" in result
        assert "新闻发布会" not in result

    def test_cjk_runs_are_bigrammed(self):
        assert _tokens("如何创建任务") == ["如何", "何创", "创建", "建任", "任务"]

    def test_a_single_cjk_character_is_kept(self):
        assert _tokens("猫") == ["猫"]

    def test_a_mixed_run_splits_at_the_script_boundary(self):
        """"Python教程" is a word plus a phrase, not one unusable token."""
        assert _tokens("Python教程") == ["python", "教程"]

    def test_ascii_tokenisation_is_unchanged(self):
        tokens = _tokens("a b cd ef")
        assert tokens == ["cd", "ef"]

    def test_a_cyrillic_query_is_no_longer_dropped(self):
        assert _tokens("Привет мир") == ["привет", "мир"]

    def test_an_empty_query_is_still_a_no_op(self):
        assert _tokens("") == []


# ─── D-05: the fetcher pin belongs in the cache key ───────────────────


class TestCacheKeyCoversFetcherPin:
    def test_force_fetcher_changes_the_request_fingerprint(self):
        """D-05: force=http hit the stealthy entry written moments earlier.

        The pin decides WHICH tier produced the body, so it has to be part of
        what makes the cached body an answer to this request. The dangerous
        direction is the reverse of the one observed: an http-tier JS shell
        (content_ok false) gets cached, then an auto/stealthy request is served
        that bad body and never escalates.
        """
        plain = server_mod._cache_context({})
        http = server_mod._cache_context({"force_fetcher": "http"})
        stealthy = server_mod._cache_context({"force_fetcher": "stealthy"})

        assert plain == "", "a plain request keeps the pre-existing key"
        assert http != stealthy
        assert http != plain and stealthy != plain

    def test_the_same_pin_is_stable(self):
        assert server_mod._cache_context({"force_fetcher": "http"}) == server_mod._cache_context({"force_fetcher": "http"})


# ─── D-07 / D-08: clamp notes ─────────────────────────────────────────


class TestClampNotes:
    def test_a_search_clamp_note_survives_a_cache_hit(self, monkeypatch):
        """D-07: the second identical call silently lost the note.

        ``_clamp_note`` is derived from the request, not stored in the cache
        row, so the cache-hit branch dropped it: the caller saw 50 results with
        nothing saying 50 was the ceiling rather than the count.
        """
        payload = json.dumps({
            "results": [SearchResult(
                title="T", url="https://example.test/", snippet="s",
                source="brave", position=1,
            ).model_dump()],
            "engines_used": ["brave"], "engine_blocked": [],
            "engine_empty": [], "engine_preempted": [],
            "rerank_mode": "merge", "related_queries": [],
        })

        async def fake_get_cached(query, cache_type, css_selector, **kwargs):
            return {"content": [payload]}

        monkeypatch.setattr(search_mod, "get_cached", fake_get_cached)
        resp = asyncio.run(MasterFetchServer().web_search("clamp note", max_results=51))

        assert resp.cached is True
        assert "outside the supported 1-50 range" in resp.summary

    def test_an_in_range_search_gets_no_note(self, monkeypatch):
        payload = json.dumps({
            "results": [SearchResult(
                title="T", url="https://example.test/", snippet="s",
                source="brave", position=1,
            ).model_dump()],
            "engines_used": ["brave"], "engine_blocked": [],
            "engine_empty": [], "engine_preempted": [],
            "rerank_mode": "merge", "related_queries": [],
        })

        async def fake_get_cached(query, cache_type, css_selector, **kwargs):
            return {"content": [payload]}

        monkeypatch.setattr(search_mod, "get_cached", fake_get_cached)
        resp = asyncio.run(MasterFetchServer().web_search("clamp note", max_results=10))
        assert "outside the supported" not in resp.summary

    def test_max_content_chars_below_the_floor_is_reported(self, monkeypatch):
        """D-08: 499 became 500 with no signal anywhere in the response.

        The clamp itself stays (a documented hard cap beats an ugly parse
        error), but the sibling search tool tells the caller when it clamps, and
        the caller here was measuring its own context budget.
        """
        _stub_fetch_layer(monkeypatch, ResponseModel(
            url="https://example.com", status=200, content=["short body"],
            content_type="text/html", total_extracted_chars=10,
        ))
        out = asyncio.run(MasterFetchServer().web_read(
            "https://example.com", max_content_chars=499, cache_ttl=0,
        ))
        assert "clamped 499->500" in out.summary

    def test_an_in_range_max_content_chars_is_not_reported(self, monkeypatch):
        _stub_fetch_layer(monkeypatch, ResponseModel(
            url="https://example.com", status=200, content=["short body"],
            content_type="text/html", total_extracted_chars=10,
        ))
        out = asyncio.run(MasterFetchServer().web_read(
            "https://example.com", max_content_chars=5000, cache_ttl=0,
        ))
        assert "clamped" not in out.summary

    def test_the_note_does_not_leak_into_the_next_call(self, monkeypatch):
        """_ARG_NOTES is scoped per invocation, like _FOCUS."""
        _stub_fetch_layer(monkeypatch, ResponseModel(
            url="https://example.com", status=200, content=["short body"],
            content_type="text/html", total_extracted_chars=10,
        ))
        srv = MasterFetchServer()
        asyncio.run(srv.web_read("https://example.com", max_content_chars=499, cache_ttl=0))
        second = asyncio.run(srv.web_read("https://example.com", cache_ttl=0))
        assert "clamped" not in second.summary


# ─── D-11 / D-12: the feed contract ───────────────────────────────────


class TestFeedContract:
    def _keep_the_real_method(self, monkeypatch):
        """Walk the dispatcher around one stale line, without stubbing the feed layer.

        §3-17 之后 ``MasterFetchServer.feed_read`` 不再收 ``allow_private``（它自己
        从 identity 存储里读），可调度器那一支还在把它递下去
        （src/dhole_mcp/server.py:10041），于是**每一次** wire 级 feed_read 都在方法
        体跑起来之前就 TypeError。这两条用例验的是「feed 层的 ValueError 必须变成
        信封」，所以它们仍然跑真的 feed_read，只在接缝上把那颗已经不存在的关键词
        摘掉。删掉 server.py 那一行的同时删掉这个 helper。
        """
        real_feed_read = MasterFetchServer.feed_read

        async def without_the_dead_kwarg(self, **kw):
            kw.pop("allow_private", None)
            return await real_feed_read(self, **kw)

        monkeypatch.setattr(server_mod.MasterFetchServer, "feed_read",
                            without_the_dead_kwarg)

    def test_a_rejected_call_is_an_envelope_not_an_mcp_error(self, monkeypatch):
        """D-12: feed raised ValueError while every other tool used an envelope.

        ``call_tool`` turned that into an is_error result whose payload was a
        bare ``{"error": ...}`` — a second error shape for callers to detect,
        with no next_action.
        """
        self._keep_the_real_method(monkeypatch)
        payload = _envelope("feed_read", {"urls": []})
        assert payload["feeds"] == []
        assert payload["error"]
        assert payload["next_action"]

    def test_both_channels_carry_the_same_shape(self, monkeypatch):
        """D-11: content[0].text was a bare array, structured_content a dict.

        期望随 R02-015 改写，理由写在这里：这一格守的从来不是「顶层只能有 `feeds` 一颗键」，
        而是**两条通道是同一个形状**。原先那句 `structured == {"feeds": []}` 把当时的顶层键
        集合当成了契约，而 R02-015 证的正是「一条通道的回信没有信封」—— 于是这条锁与缺陷
        同形。改写后守的东西只多不少：两条通道逐字相等（D-11 的本意）、`feeds` 照旧在，
        而信封（summary / 空手时的 next_action）现在也被钉住。
        """
        async def fake_feed(self, **kwargs):
            return []

        monkeypatch.setattr(server_mod.MasterFetchServer, "feed_read", fake_feed)
        content, structured = _dispatch("feed_read", {"urls": ["https://example.test/feed"]})

        assert structured["feeds"] == []
        assert set(structured) == {"feeds", "summary", "next_action"}, structured
        assert "0 rows answered" in structured["summary"], structured
        assert json.loads(content[0].text) == structured

    def test_a_missing_urls_argument_is_reported_as_an_envelope(self, monkeypatch):
        self._keep_the_real_method(monkeypatch)
        payload = _envelope("feed_read", {})
        assert payload["feeds"] == []
        assert "at least one URL" in payload["error"]

    def test_the_store_is_the_only_way_left_to_open_the_scope(self, monkeypatch):
        """§3-17 换的是来源，不是那道闸：没存 allow_private 时内网 feed 照样得被拒。

        旧写法（``allow_private=`` 逐次传）现在连调度器都进不去，所以这里改成把值
        存进 identity 存储 —— 断言的是「存储之外没有第二条路」。
        """
        from dhole_mcp import identity

        self._keep_the_real_method(monkeypatch)
        seen: dict = {}

        async def fake_feeds(urls, **kw):
            seen["urls"] = list(urls)
            return []

        monkeypatch.setattr("dhole_mcp.feed.fetch_feeds", fake_feeds)
        payload = _envelope("feed_read", {"urls": ["http://127.0.0.1:8080/f.xml"]})
        assert payload["feeds"] == []
        assert "URL 校验失败" in payload["error"], "内网地址没被拒，闸就白守了"

        identity.set_value("allow_private", True)
        content, structured = _dispatch("feed_read",
                                        {"urls": ["http://127.0.0.1:8080/f.xml"]})
        assert structured["feeds"] == [], content
        assert seen["urls"] == ["http://127.0.0.1:8080/f.xml"]


# ─── D-10: the state call's payload ───────────────────────────────────


class TestStatePayload:
    def test_a_plain_state_call_carries_no_engine_health(self, monkeypatch):
        """D-10: every cache wipe shipped ~1KB of per-engine pool state.

        A call about the content cache says nothing about the search pool, so the
        snapshot is taken only when the call names engine_state.
        """
        from dhole_mcp import search_metasearch as ms

        called: list[str] = []

        def fake_snapshot():
            called.append("snapshot")
            return {"brave": {"n": 3, "mean": 0.5}}

        monkeypatch.setattr(ms, "engine_state_snapshot", fake_snapshot, raising=False)

        _content, structured = _dispatch("sys_state", {"clear": ["cache"]})
        assert "engine_health" not in structured, \
            "the plain call must not carry pool state, not even as an empty {}"
        assert called == [], "the pool snapshot must not even be taken"

    def test_engine_state_still_reports_health(self, monkeypatch):
        from dhole_mcp import search_metasearch as ms

        monkeypatch.setattr(
            ms, "engine_state_snapshot",
            lambda: {"brave": {"n": 3, "mean": 0.5}}, raising=False,
        )
        monkeypatch.setattr(
            ms, "engine_state_reset",
            lambda: {"engines_forgotten": 2, "released_cooldowns": {}}, raising=False,
        )

        _content, structured = _dispatch("sys_state", {"clear": ["engine_state"]})
        assert structured["engine_health"] == {"brave": {"n": 3, "mean": 0.5}}
        assert structured["engine_state_reset"] is True


# ─── D-14: redirect visibility ────────────────────────────────────────


class TestOriginalUrl:
    def test_a_rewritten_url_is_reported(self, monkeypatch):
        """D-14: ``url`` was rewritten to the final address with no trace.

        A caller's only clue was a URL it had not typed, and a wrong-but-
        plausible one reads as "the site moved" at best.
        """
        _stub_fetch_layer(monkeypatch, ResponseModel(
            url="https://example.com/final", status=200, content=["body"],
            content_type="text/html", total_extracted_chars=4,
        ))
        out = asyncio.run(MasterFetchServer().web_read(
            "https://example.com/start", cache_ttl=0,
        ))
        assert out.url == "https://example.com/final"
        assert out.original_url == "https://example.com/start"

    def test_the_summary_names_both_ends_of_a_rewrite(self, monkeypatch):
        """R05-005/018: the field was correct but the status line never said it.

        A caller scanning `summary` (the one line the receipt is built around)
        could not tell a stale id that redirected from the page it asked for.
        """
        _stub_fetch_layer(monkeypatch, ResponseModel(
            url="https://example.com/final", status=200, content=["body"],
            content_type="text/html", total_extracted_chars=4,
        ))
        out = asyncio.run(MasterFetchServer().web_read(
            "https://example.com/start", cache_ttl=0,
        ))
        assert ("redirected from https://example.com/start to "
                "https://example.com/final") in out.summary, out.summary

    def test_an_unchanged_url_leaves_the_field_empty(self, monkeypatch):
        _stub_fetch_layer(monkeypatch, ResponseModel(
            url="https://example.com/page", status=200, content=["body"],
            content_type="text/html", total_extracted_chars=4,
        ))
        out = asyncio.run(MasterFetchServer().web_read(
            "https://example.com/page", cache_ttl=0,
        ))
        assert out.original_url == ""

    def test_a_trailing_slash_is_not_a_redirect(self, monkeypatch):
        """The fetcher adds one itself; reporting that as a redirect is noise."""
        _stub_fetch_layer(monkeypatch, ResponseModel(
            url="https://example.com/page/", status=200, content=["body"],
            content_type="text/html", total_extracted_chars=4,
        ))
        out = asyncio.run(MasterFetchServer().web_read(
            "https://example.com/page", cache_ttl=0,
        ))
        assert out.original_url == ""

    def test_a_host_case_change_is_not_a_redirect(self, monkeypatch):
        _stub_fetch_layer(monkeypatch, ResponseModel(
            url="https://EXAMPLE.com/page", status=200, content=["body"],
            content_type="text/html", total_extracted_chars=4,
        ))
        out = asyncio.run(MasterFetchServer().web_read(
            "https://example.com/page", cache_ttl=0,
        ))
        assert out.original_url == ""


# ─── D-06: the scalar/array contract, pinned as behaviour ─────────────


class TestStructuredAttributeContract:
    """The report read the scalar path as "attribute returns only the first".

    It does — for a scalar property. ``"type": "array"`` returns every match, so
    the defect was an undocumented contract, not a missing capability. Both
    halves are pinned here so neither can drift.
    """

    def test_a_scalar_property_returns_the_first_match(self):
        out = extract_structured(
            _PAGE_WITH_THREE_LINKS,
            {"properties": {"links": {"selector": "a", "attribute": "href"}}},
        )
        assert out["links"] == "https://example.test/one"

    def test_an_array_property_returns_every_match(self):
        out = extract_structured(
            _PAGE_WITH_THREE_LINKS,
            {"properties": {"links": {
                "selector": "a", "attribute": "href", "type": "array",
            }}},
        )
        assert out["links"] == [
            "https://example.test/one",
            "https://example.test/two",
            "https://example.test/three",
        ]

    def test_the_contract_states_both_halves(self):
        """`schema` 的键语义搬到手册之后，这条查的是"搬过去且读得到"，不是"在 wire 上"。

        17.0 B5-17 之前这三句写在常驻层，于是每一次连接都在为它们付字。`tests/
        test_wire_moves_land_in_the_manual.py` 钉住同一个落点集合；这里保留的是**这条
        契约本身不能被删**：不写 type 只回第一条、写了 array 才回全部、自造关键字不起作用。
        """
        from dhole_mcp import guide

        _head, manual = guide.read("behavior", "Structured extraction")
        assert "FIRST match" in manual
        assert "array" in manual
        # 实测里 agent 会自己发明 `repeat`/`multiple` 这类键，然后拿着首条以为
        # 拿到了全部 —— 所以「除了这几个键别的都不起作用」必须和上面两句同处。
        assert "keyword is ignored" in manual
        wire = next(
            t["inputSchema"]["properties"]["schema"]["description"]
            for t in MasterFetchServer._TOOL_DEFS if t["name"] == "web_read"
        )
        assert "Structured extraction" in wire, "常驻层那句指针不能丢：它是到这一卷的路"

    def test_an_invented_keyword_stays_an_invented_keyword(self):
        """`repeat` 不在被读的键里：它被忽略，字段仍是首条，而不是变成列表。

        这条给上面那句描述提供事实依据 —— 描述说的「不起作用」必须真的不起作用。
        """
        out = extract_structured(
            _PAGE_WITH_THREE_LINKS,
            {"properties": {"links": {
                "selector": "a", "attribute": "href", "repeat": True,
            }}},
        )
        assert out["links"] == "https://example.test/one"


# ============================================================================
# 来源：test_ceiling_report_regressions.py
# ============================================================================
# 上限测试报告的处置守卫（第三轮外部实测，~60 次调用 / 8 个工具）。

def _result(**kwargs):
    defaults = dict(
        status=200, content=["Real article body text about Python packaging."],
        url="https://example.com/article", fetcher_used="http",
        content_type="text/html", total_size_bytes=4000,
        extracted_type="markdown",
    )
    defaults.update(kwargs)
    return ResponseModel(**defaults)


def _tools() -> dict[str, dict]:
    return {td["name"]: Tool(**td).model_dump(by_alias=True, exclude_none=True)
            for td in MasterFetchServer._TOOL_DEFS}


@pytest.fixture(scope="module")
def tools() -> dict[str, dict]:
    return _tools()


def _desc(tools: dict[str, dict], name: str) -> str:
    return tools[name]["description"]


# ─── 1. 软失败页：200 + 非空正文，但不是内容 ─────────────────────────────


class TestSoftFailureDetection:
    """实测样本：x.com「Try reloading」、douyin.com「Please wait...」。

    三者的共同点是 HTTP 200 且正文非空，所以 ``_agent_hints`` 的
    ``has_content`` 为真，content_ok 就为真。它们不含任何反爬/验证措辞，
    bot wall 与 auth wall 的检测都覆盖不到，需要单独一类。
    """

    @pytest.mark.parametrize("body,why", [
        ("Try reloading the page.", "x.com 实测：失败页被当作正文"),
        ("Please wait...", "douyin.com 实测：249 字符的占位页"),
        ("Something went wrong.", "通用客户端错误视图"),
        ("An error occurred while loading.", "通用错误视图"),
        ("页面加载失败，请稍后重试", "中文错误视图"),
    ])
    def test_measured_samples_are_detected(self, body, why):
        assert _is_soft_failure(_result(content=[body])), why

    def test_length_gate_keeps_real_articles(self):
        """门限存在的理由：短语本身在普通散文里很常见。

        一篇真的在讲故障排查的长文会含 "something went wrong"，若只匹配
        短语就会把正文判成失败页。长度是唯一的区分证据。
        """
        article = ("Something went wrong during the deploy. " * 30)
        assert len(article) > 600
        assert not _is_soft_failure(_result(content=[article]))

    def test_short_normal_page_is_left_alone(self):
        """短但不带失败措辞的页面（example.com 那类）必须保持 content_ok。"""
        body = ("Example Domain. This domain is for use in illustrative "
                "examples in documents. You may use this domain in literature "
                "without prior coordination or asking for permission.")
        assert not _is_soft_failure(_result(content=[body]))

    @pytest.mark.parametrize("status", [400, 403, 404, 500, 503])
    def test_error_statuses_are_not_claimed(self, status):
        """4xx/5xx 归 ``http_error_<status>`` 分支。

        软失败检测若也吃掉它们，错误原因就从「服务器拒绝」变成「页面是
        占位页」，agent 会去换源而不是去看状态码。
        """
        assert not _is_soft_failure(
            _result(status=status, content=["Something went wrong."]))

    def test_pdf_is_not_claimed(self):
        """PDF 无文本层是 ``pdf_no_text`` 的失败类，不归软失败。"""
        assert not _is_soft_failure(
            _result(content=["Something went wrong."],
                    content_type="application/pdf"))

    def test_empty_content_is_not_claimed(self):
        """空正文是 JS shell / HTTP 错误的地盘，两处判会给出矛盾的 next_action。"""
        assert not _is_soft_failure(_result(content=[]))

    def test_end_to_end_content_ok_is_false(self):
        """端到端：agent 真正读到的那个 content_ok 必须为 False。

        只测 ``_is_soft_failure`` 不够 —— 它得真的接进 ``_annotate_quality``
        才会写进 error，而 content_ok 又依赖 error。这条钉住整条链路。
        """
        out = _with_agent_hints(_annotate_quality(
            _result(content=["Try reloading"])))
        assert "soft_failure_detected" in out.error, out.error
        assert out.content_ok is False, "失败页仍被标记为可引用"

    def test_end_to_end_normal_short_page_stays_ok(self):
        # total_size_bytes 必须跟着正文一起小：否则 JS-shell 启发式（大 body
        # + 极少文本）会命中，这条就变成在测 js_shell 而不是"没被误判"。
        out = _with_agent_hints(_annotate_quality(
            _result(content=["Example Domain. This domain is for use in "
                             "illustrative examples in documents."],
                    total_size_bytes=600)))
        assert out.error == "", out.error
        assert out.content_ok is True


# ─── 2. archive.org 第三层降级必须写在 agent 看得见的地方 ────────────────


class TestArchiveTierDocumented:
    """降级本身是有意设计（字段描述里写了），缺的是「存在性」与「触发条件」。

    agent 只看工具描述决定怎么调，只在响应里看到 source 字段 —— 于是它
    无法在调用前知道时效敏感的请求可能拿到旧快照。
    """

    def test_smart_fetch_names_the_third_tier(self, tools):
        desc = _desc(tools, "web_read").lower()
        assert "archive.org" in desc, "第三层降级在工具描述里不存在"
        # 辨认存档来源的字段是**顶层** source / archived_at（server.py 的
        # BulkResponseModel 投影可证），不是 metadata.source —— 旧 needle 钉在
        # 一个不存在的字段路径上，描述照它写反而会教错读法。
        assert "archived_at" in desc, "没告诉 agent 去哪里辨认存档来源"

    def test_smart_fetch_says_it_cannot_be_disabled(self, tools):
        desc = _desc(tools, "web_read").lower()
        assert "disabl" in desc, "没说明这一层无法关闭（实测无开关可关）"

    def test_instructions_point_at_the_archive_receipt(self):
        low = DHOLE_INSTRUCTIONS.lower()
        assert "archived_at" in low, (
            "instructions 里 content_ok=true 的例外情况没写清楚")


# ─── 3. crawl 的两道天花板：写在 wire 上，且能被常量反查 ────────────────────


class TestCrawlCapsDocumented:
    """实测原话是「max_pages=100 仍只抓 31 页」—— 一道没写在描述里的钳制，
    后果是 agent 一直加同一个参数。

    17.0 §3-31 删掉了这一格的成因之一（总预算 `max_total_chars` 整个下线），留下
    `max_pages` 与每页 `max_content_chars_per` 两道仍然存在的天花板。教训跟着留下，
    形式也照旧：**数值从 crawl.py 的常量读出来，再要求 wire 上出现同一个数** ——
    只钉文档拦不住"代码改了钳制值、描述跟着改"的双双漂移。
    （那一道预算在 2026-10-05 的实测之后回来了，见本文件开头那条；所以这里现在读
    的是三道闸的常量，同一条"从代码反查 wire"的形式一处没变。）
    """

    @staticmethod
    def _prop(tools, tool, prop):
        return tools[tool]["inputSchema"]["properties"][prop]["description"]

    def test_the_page_ceiling_is_on_the_wire(self, tools):
        asked = str(crawl_mod.MAX_PAGES_CEILING)
        assert asked in _desc(tools, "web_crawl"), \
            "爬取页数的硬顶没写进工具描述"
        assert asked in self._prop(tools, "web_crawl", "max_pages"), \
            "max_pages 自己的描述没写上限"

    def test_the_per_page_ceiling_is_on_the_wire(self, tools):
        assert str(crawl_mod.PER_PAGE_CHARS_CEILING) in self._prop(
            tools, "web_crawl", "max_content_chars_per"), \
            "每页字符上限没写进参数描述"

    def test_the_whole_run_ceiling_is_on_the_wire(self, tools):
        """整轮帽的两个数（默认与上限）必须写在参数描述里 —— 它是被删过一次又回来的一颗。

        §3-31 删它的那句理由现在还刻在 crawl.py 的注释里，而注释不是调用方读的地方；
        调用方读的是这颗参数的描述。删过、回来、却不写清"回来了且默认会砍"，
        等于留下一颗会咬人而无名有主的闸。
        """
        prop = self._prop(tools, "web_crawl", "max_total_chars")
        assert str(crawl_mod.MAX_TOTAL_CHARS_DEFAULT) in prop, prop
        assert str(crawl_mod.MAX_TOTAL_CHARS_CEILING) in prop, prop
        assert "0 = no cap" in prop, prop


class TestHardClampBehavior:
    """钉钳制本身（上面那类钉的是文档）。

    这一类原来跑的是"显式 max_total_chars 被钳在 1,000,000"。17.0 §3-31 把整轮预算
    删掉过一阵，那道账只剩两道天花板；2026-10-05 的实测把它带回来了（40 页 × 默认
    每页 8,000 真跑出 223,599 字符，宿主把载荷截断落盘 —— 算不清不是花得起的理由）。
    所以这里跑一次真爬取（stub 掉网络层）证明两件事：要 500 页只会拿到
    `MAX_PAGES_CEILING` 页、每页不超过 `PER_PAGE_CHARS_CEILING` 字符，而**被钳这件事
    在 summary 里说得出名字** —— 静默才是这一族原来的 bug。整轮帽在这一条里显式关掉
    （`max_total_chars=0`），因为这三道闸要各自能被单独证明；它自己那一刀在下面。
    """

    def test_both_ceilings_hold_and_say_so(self, monkeypatch):
        big_page = (
            "<html><head><title>t</title></head><body><p>"
            + " ".join(f"sentence {i} of the crawl cap page" for i in range(6000))
            + "</p>"
            + "".join(f'<a href="/p{i}">p{i}</a>' for i in range(60))
            + "</body></html>"
        )

        async def fake_smart_fetch(self, url=None, **ignored):
            return ResponseModel(
                url=url or "", status=200, content=[big_page],
                fetcher_used="http", content_ok=True, content_type="text/html",
            )

        monkeypatch.setattr(MasterFetchServer, "web_read", fake_smart_fetch)
        result = asyncio.run(MasterFetchServer().web_crawl(
            "https://example.com/", max_pages=500, cache_ttl=0,
            max_content_chars_per=999_999, concurrency=1, max_total_chars=0,
        ))

        assert len(result.pages) == crawl_mod.MAX_PAGES_CEILING, \
            f"要了 500 页，拿到 {len(result.pages)} 页"
        assert all(p.content_chars <= crawl_mod.PER_PAGE_CHARS_CEILING
                   for p in result.pages), "有一页超出了每页上限"
        assert "max_content_chars_per clamped" in result.summary, \
            f"钳了却没说：{result.summary}"
        assert result.truncated_by_max_pages is True

    def test_the_whole_run_cap_stops_paying_and_names_what_it_left_out(
            self, monkeypatch):
        """整轮帽到点要做两件事：**不再发下一批**，并且说出被留下的有哪几页。

        这一颗是 §3-31 删过、实测又带回来的那道。删它的理由是"整趟多少字符估不准"，
        带回来的理由是"估不准 ≠ 花得起"。所以这条测的两半都不是数字本身：

        * 请求数必须跟着帽停 —— 只在装配时截一版的预算一次网络也省不下来，那既慢又是
          把已付的钱藏起来；
        * summary 必须自带"还剩几页没付、几页付了没返回"，因为调用方看到的只是
          "比我要求的少"，而两种少法要不同的解药。
        """
        big_page = (
            "<html><head><title>t</title></head><body><p>"
            + " ".join(f"sentence {i} of the whole-run cap page" for i in range(6000))
            + "</p>" + "".join(f'<a href="/p{i}">p{i}</a>' for i in range(60))
            + "</body></html>")
        calls: list[str] = []

        async def fake_smart_fetch(self, url=None, **ignored):
            calls.append(url or "")
            return ResponseModel(
                url=url or "", status=200, content=[big_page],
                fetcher_used="http", content_ok=True, content_type="text/html")

        monkeypatch.setattr(MasterFetchServer, "web_read", fake_smart_fetch)
        result = asyncio.run(MasterFetchServer().web_crawl(
            "https://example.com/", max_pages=500, cache_ttl=0,
            max_content_chars_per=999_999, concurrency=1, max_total_chars=30_000))

        assert result.truncated_by_total_chars is True
        assert result.truncated_by_max_pages is False, (
            "两道闸同时立起来 = 调用方不知道抬哪一个才有用")
        assert "truncation=total_chars" in result.summary, result.summary
        assert "max_total_chars=0 removes the cap" in result.summary
        # 每页 ~20,000 字符、帽 30,000、concurrency=1 ⇒ 两页就该收手。
        assert len(result.pages) == 2, [p.content_chars for p in result.pages]
        assert len(calls) <= len(result.pages) + 1, (
            f"付了 {len(calls)} 次请求只为回 {len(result.pages)} 页 —— "
            "帽到点之后还在抓，那截掉的就不是预算而是钱包")
        assert "whole-run character budget" in result.next_action, result.next_action
        # 数字是千分位写的（`{:,}`），所以比对之前要去掉分隔符 —— 否则这条断言
        # 挑的是排版而不是"上限有没有说出口"。
        assert str(crawl_mod.MAX_TOTAL_CHARS_CEILING) in result.next_action.replace(",", "")


# ─── 4. 错误状态的 next_action 不许被「截断」抢走 ────────────────────────


class TestErrorStatusNextAction:
    """实测：Wikipedia 404 页返回 next_action "page truncated...offset=".

    根因是 ``_agent_hints`` 里截断提示排在 elif 链最前，而 404 页的正文
    足够长、会被 max_content_chars 截断 —— 于是 agent 被引导去翻一个
    不存在的页面的下一页。状态码是比截断更具体的事实。
    """

    def _next_action(self, **kwargs):
        result = _result(**kwargs)
        _, next_action, _ = _agent_hints(_annotate_quality(result))
        return next_action

    def test_truncated_404_does_not_say_paginate(self):
        next_action = self._next_action(
            status=404, content=["Wikipedia does not have an article with this "
                                 "exact name. "] * 40,
            is_truncated=True, next_offset=1000)
        assert "truncated" not in next_action.lower(), (
            f"404 仍被当成可分页的截断页: {next_action}")
        assert "404" in next_action

    def test_truncated_200_still_says_paginate(self):
        """防回归：正常的长页面仍然要拿到分页提示。"""
        next_action = self._next_action(
            status=200, content=["Real article body. " * 400],
            is_truncated=True, next_offset=4000)
        assert "offset=4000" in next_action, next_action

    @pytest.mark.parametrize("status,needle", [
        (403, "403"), (429, "429"), (410, "410"),
    ])
    def test_other_error_statuses_get_their_own_advice(self, status, needle):
        next_action = self._next_action(
            status=status, content=["Nothing here."],
            is_truncated=True, next_offset=100)
        assert needle in next_action, next_action
        assert "truncated" not in next_action.lower()


# ─── 5. 声明的 charset 与字节不符时的 mojibake ───────────────────────────


class TestDeclaredCharsetLies:
    """实测：you’ve → youâ€™ve / you???ve，· → ??，content 与 summary 都中招。

    根因在 fetcher：encoding 只从 Content-Type header 取，页面自己的
    <meta charset> 完全不参与，且没有一致性校验 —— Apache 默认发
    ISO-8859-1 而内容其实是 UTF-8 时，声明编码赢。

    修法不是猜字符集，而是两种解码各打一次分、取更干净的。所以「真·非
    UTF-8 内容不被改坏」和「错声明被纠正」同样重要，各有一条。
    """

    _UTF8_HTML = ('<html><head><meta charset="utf-8"></head><body>'
                  '<p>you’ve · café</p></body></html>')

    def _decoded(self, body: bytes, declared: str) -> str:
        from dhole_mcp.trafilatura_extractor import _get_html_from_page
        from types import SimpleNamespace
        html = _get_html_from_page(SimpleNamespace(body=body, encoding=declared))
        return html[html.find("<p>") + 3:html.find("</p>")]

    def test_utf8_body_declared_latin1(self):
        assert self._decoded(self._UTF8_HTML.encode("utf-8"), "iso-8859-1") == \
            "you’ve · café"

    def test_utf8_body_declared_ascii(self):
        """us-ascii 声明会把每个非 ASCII 字节变成 U+FFFD —— 报告的 ?? 形态。"""
        assert self._decoded(self._UTF8_HTML.encode("utf-8"), "us-ascii") == \
            "you’ve · café"

    def test_utf8_body_declared_cp1252(self):
        assert self._decoded(self._UTF8_HTML.encode("utf-8"), "windows-1252") == \
            "you’ve · café"

    def test_true_latin1_body_is_left_alone(self):
        """真 latin-1 内容按 utf-8 解码会产生大量 U+FFFD，评分更差 → 保留。"""
        body = "<html><body><p>café naïve</p></body></html>".encode("iso-8859-1")
        assert self._decoded(body, "iso-8859-1") == "café naïve"

    def test_true_gbk_body_is_left_alone(self):
        """中文站最常见的真实场景，改坏比不改严重得多。"""
        body = "<html><body><p>中文内容测试</p></body></html>".encode("gbk")
        assert self._decoded(body, "gbk") == "中文内容测试"

    def test_unknown_charset_name_falls_back_to_utf8(self):
        from dhole_mcp.fetcher import _decode_html_bytes
        assert _decode_html_bytes("中文".encode("utf-8"), "not-a-real-charset") == "中文"


# ============================================================================
# 来源：test_document_timeout_regressions.py
# ============================================================================
# Regressions for the document-download timeout path (external report P2, v15.2).

PAGE_URL = "https://docs.test/guide"
DOC_URL = "https://docs.test/manual.pdf"


@pytest.fixture(autouse=True)
def _clean_latency_tables():
    """The latency tables are process-global; no test inherits another's host intel."""
    server_mod._DOMAIN_LATENCY.clear()
    server_mod._DOMAIN_DOC_LATENCY.clear()
    yield
    server_mod._DOMAIN_LATENCY.clear()
    server_mod._DOMAIN_DOC_LATENCY.clear()


class _ProbeResponse:
    """The two attributes _document_size reads off a primp response."""

    def __init__(self, headers=None):
        self.headers = headers or {}
        self.status_code = 206 if "content-range" in (headers or {}) else 200


# ─── A/B: two classes of request, learned and priced separately ─────────────


class TestDocumentsAreNotPricedByPageHabit:

    def test_a_fast_page_history_no_longer_gives_the_pdf_five_seconds(self):
        """The report's exact shape: 0.8s of page habit, 5s for a 40MB download."""
        server_mod._DOMAIN_LATENCY["docs.test"] = 800.0

        page_s, page_retries, page_bound = _http_tier_budget(PAGE_URL, 60000, 60.0)
        doc_s, doc_retries, doc_bound = _http_tier_budget(DOC_URL, 60000, 60.0, document=True)

        assert (page_s, page_retries) == (5, 3), "a host that answers in 0.8s should not hold a page call open"
        assert (doc_s, doc_retries) == (60, 0), "and that habit must not price a download"
        # 第三位是 R02-041 的原料：谁把这颗数压到 5s。文档那一发不戴这顶帽，所以也
        # 不该被说成 learned。（数值一位没改，改的只是函数肯把"是哪一颗"一起交回来。）
        assert page_bound == "learned", page_bound
        assert doc_bound == "", doc_bound

    def test_a_document_takes_one_attempt_because_a_retry_restarts_it(self):
        """Four attempts of 15s cannot deliver a file that needs 60s, ever; one
        attempt of 60s can. The caller retries the call if it was a blip."""
        _s, retries, _bound = _http_tier_budget(DOC_URL, 60000, 60.0, document=True)
        assert retries == 0

    def test_pages_keep_the_attempt_count_that_fits_the_budget_left(self):
        """Each attempt stays as long as the host needs; only the COUNT is what
        the budget affords. Shrinking an attempt to buy more of them would fail a
        slow-but-healthy site to insure against a stalled one."""
        assert _http_tier_budget(PAGE_URL, 60000, 60.0) == (60, 0, "")   # one attempt already fills the call
        assert _http_tier_budget(PAGE_URL, 30000, 30.0) == (30, 0, "")   # 4x30 would not fit 30 -> one try
        assert _http_tier_budget(PAGE_URL, 6000, 6.0) == (6, 0, "")
        assert _http_tier_budget(PAGE_URL, 1500, 1.5) == (1, 0, "")
        # A host learned to answer in 0.8s keeps its four short attempts, because
        # they fit: 4 x 5s inside the 60s that is left.
        server_mod._DOMAIN_LATENCY["docs.test"] = 800.0
        assert _http_tier_budget(PAGE_URL, 60000, 60.0) == (5, 3, "learned")

    def test_the_tier_never_asks_for_more_wall_time_than_is_left(self):
        for left in (1.5, 5, 8, 20, 31, 90, 120):
            for document in (False, True):
                per_attempt, retries, _bound = _http_tier_budget(
                    DOC_URL if document else PAGE_URL, left * 1000, left,
                    document=document)
                assert per_attempt * (retries + 1) <= left + 1, (left, document)

    def test_a_successful_document_does_not_inflate_the_page_budget(self):
        """The other direction of the same contamination: 40s of PDF is not 40s of HTML."""
        _record_latency(DOC_URL, 40000.0, document=True)

        assert server_mod._DOMAIN_DOC_LATENCY.get("docs.test") == pytest.approx(40000.0)
        assert server_mod._DOMAIN_LATENCY == {}
        assert server_mod._adaptive_timeout(PAGE_URL, 30000) == 30000

    def test_the_document_predicate_covers_what_the_rules_assume(self):
        assert _is_document_url("https://x.test/a/REPORT.PDF?v=3")
        assert not _is_document_url("https://x.test/report?file=a.pdf")
        assert not _is_document_url("https://x.test/report")
        assert not _is_document_url("not a url at all")


# ─── tier 1, through the real escalation code ───────────────────────────────


def _server_with_captured_get(result=None):
    """A server whose HTTP tier records its arguments instead of going out."""
    seen: dict = {}

    async def capture_get(*args, **kwargs):
        seen.update(kwargs)
        url = args[0] if args else kwargs.get("url", "")
        return result or ResponseModel(url=url, status=200, content=["text"],
                                       content_type="text/html")

    srv = MasterFetchServer()
    srv.get = capture_get
    return srv, seen


# `_auto_escalate` 与 `_force_fetch` 这两层的必填自变量：除了 url / 预算，其余都是
# 本题不收的值。以前每条调用都抄成一串位置参数（17 个），13d 删掉 wait /
# network_idle 后位置整体错位而不报错 —— timeout 被喂成 None、max_chars 被喂成
# None。按名字传，并且只在这一处传，签名再动只会红在相关的那一行。
_TIER_BASE = dict(
    extraction_type="markdown", css_selector=None, cache_ttl=0, offset=0,
    headless=True, real_chrome=False, proxy=None, solve_cloudflare=False,
    block_webrtc=False, hide_canvas=False, extra_headers=None,
    useragent=None, cookies=None,
)


async def _escalate(srv, url, timeout_ms, body_size=None):
    """Run tier 1 with the network replaced: TCP preflight, size probe, HTTP."""

    async def fake_http_get(u, **kw):
        if body_size is None:
            return _ProbeResponse({})
        return _ProbeResponse({"content-range": f"bytes 0-0/{body_size}"})

    orig_preflight, orig_get = fetcher.tcp_preflight, fetcher.http_get
    fetcher.tcp_preflight = lambda url, timeout=2.0: (True, "")
    fetcher.http_get = fake_http_get
    try:
        return await srv._auto_escalate(url, timeout=timeout_ms, **_TIER_BASE)
    finally:
        fetcher.tcp_preflight, fetcher.http_get = orig_preflight, orig_get


class TestTierOneAsksForTheRightBudget:

    @pytest.mark.asyncio
    async def test_a_pdf_asks_for_the_callers_budget_not_the_learned_five_seconds(self):
        server_mod._DOMAIN_LATENCY["docs.test"] = 800.0
        srv, seen = _server_with_captured_get()

        await _escalate(srv, DOC_URL, 60000)

        assert seen["timeout"] >= 55, "the clock has spent microseconds; the point is it is not 5"
        assert seen["retries"] == 0

    @pytest.mark.asyncio
    async def test_a_page_still_gets_the_cheap_timeout_and_its_retries(self):
        server_mod._DOMAIN_LATENCY["docs.test"] = 800.0
        srv, seen = _server_with_captured_get()

        await _escalate(srv, PAGE_URL, 60000)

        assert seen["timeout"] == 5
        assert seen["retries"] == 3

    @pytest.mark.asyncio
    async def test_a_successful_pdf_is_recorded_as_a_document(self):
        srv, _seen = _server_with_captured_get()

        await _escalate(srv, DOC_URL, 60000)

        assert server_mod._DOMAIN_DOC_LATENCY.get("docs.test") is not None
        assert server_mod._DOMAIN_LATENCY == {}, "the download must not become page history"

    @pytest.mark.asyncio
    async def test_an_extensionless_pdf_is_recorded_as_a_document_too(self):
        """arxiv serves PDFs at /pdf/<id>, so the URL alone cannot class them.

        Measured on a live run of this change: such a fetch took 26s and went into
        the PAGE table, which is the same contamination in the direction the URL
        predicate cannot see.
        """
        pdf = ResponseModel(url="https://arxiv.org/pdf/2103.00020", status=200,
                            content=["x"], content_type="application/pdf")
        srv, _seen = _server_with_captured_get(pdf)

        await _escalate(srv, "https://arxiv.org/pdf/2103.00020", 60000)

        assert server_mod._DOMAIN_DOC_LATENCY.get("arxiv.org") is not None
        assert server_mod._DOMAIN_LATENCY == {}


# ─── E: the size preflight, which is what the body cap should have been ──────


class TestOversizedDocumentIsRefusedBeforeItDownloads:

    @pytest.mark.asyncio
    async def test_a_body_over_the_cap_never_starts(self):
        """The cap used to be checked once the body was already in memory, so a
        200MB PDF was paid for at length and reported as a timeout."""
        srv, seen = _server_with_captured_get()

        out = await _escalate(srv, DOC_URL, 60000, body_size=200 * 1024 * 1024)

        assert seen == {}, "no document request may go out for a body that cannot fit"
        assert "Response body too large" in out.error
        assert "200.0MB" in out.error
        assert f"{MAX_RESPONSE_BYTES:,} bytes" in out.error
        assert "no timeout or fetcher changes that" in out.next_action
        assert "size preflight" in out.escalation_path

    @pytest.mark.asyncio
    async def test_a_body_that_fits_is_downloaded_normally(self):
        srv, seen = _server_with_captured_get()

        await _escalate(srv, DOC_URL, 60000, body_size=8 * 1024 * 1024)

        assert seen["timeout"] >= 55

    @pytest.mark.asyncio
    async def test_a_host_that_wont_state_a_size_is_not_guessed_at(self):
        """Refusing on an unknown size would turn every chunked-response host into
        a failure. The probe is a chance to know, not a permission."""
        srv, seen = _server_with_captured_get()

        await _escalate(srv, DOC_URL, 60000, body_size=None)

        assert seen["timeout"] >= 55


# ─── F: advice the caller can act on ────────────────────────────────────────


class TestDocumentTimeoutAdviceIsTrue:

    @pytest.mark.asyncio
    async def test_the_advice_names_the_file_and_not_the_browser(self):
        """The generic sentence recommended force_fetcher='http' "to skip browser
        rendering" for a URL that never went near a browser."""
        srv, _seen = _server_with_captured_get()

        async def stalling_get(*args, **kwargs):
            await asyncio.sleep(5)
            return ResponseModel(url=DOC_URL, status=200, content=[])

        srv.get = stalling_get
        out = await _escalate(srv, DOC_URL, 1000, body_size=40 * 1024 * 1024)

        assert out.error.startswith("timeout: the ")
        # "file is" 而不是 "the file is"：这句现在开在字段的第一位，作者会把它句首大写
        # （R01-010 的文案规矩，见 `quality._as_sentence`）。这里量的是"尺寸被点名"，
        # 不是第一个字母的大小写。
        assert "file is 40.0MB" in out.next_action
        assert "raise timeout" in out.next_action
        assert "EXTRACTED, not what gets downloaded" in out.next_action
        assert "skip browser rendering" not in out.next_action

    def test_a_call_past_the_per_attempt_cap_is_not_told_to_raise_it_higher(self):
        """Dangling a raise the caller cannot make sends them round a loop that
        cannot terminate.

        体验修复刀3-C5 把这道门从 120s 挪到它真实的位置：一次 HTTP 尝试封顶 30s
        （`bulk_get` 的算式），而一份文档只有一次尝试。所以"已经到顶"的那一句在 30s
        就该说出口 —— 原来它坐在 120s 上，中间那 90s 全是劝人抬一颗不动的数。
        """
        advice = _document_budget_advice(DOC_URL, None, "", 120000, "HTTP tier")

        assert "already has the whole 30s" in advice
        assert "raise timeout" not in advice

    def test_a_host_that_couldnt_state_the_size_says_so(self):
        advice = _document_budget_advice(DOC_URL, None, "host reported no size (chunked)",
                                         30000, "HTTP tier")

        assert "chunked" in advice
        # 30000 已经是一试次的全部，所以这里剩下的出路不是 `timeout=`（C5）；那颗参数
        # 能买的只剩解析与 OCR 的余量，句子要说的是这件事。
        assert "raise timeout" not in advice
        assert "switch source" in advice

    @pytest.mark.asyncio
    async def test_a_document_that_never_arrived_names_its_size_too(self):
        """primp reports a stalled download as a failed RESPONSE (status 0), which
        is not the over-budget result — so the tier's advice has to reach that
        path as well, or the common real failure still gets the generic sentence."""
        srv, _seen = _server_with_captured_get(ResponseModel(
            url=DOC_URL, status=0, content=[],
            error="network_error: timeout while downloading"))

        out = await _escalate(srv, DOC_URL, 30000, body_size=8 * 1024 * 1024)

        assert "file is 8.0MB" in out.next_action
        assert "skip browser rendering" not in out.next_action

    @pytest.mark.asyncio
    async def test_an_expired_certificate_is_not_rewritten_as_a_timeout(self):
        """R05-001: status 0 + no body is not a synonym for timeout.

        A certificate that expired fails before a single byte of the download;
        the document branch used to overwrite the classifier's `tls_error` with
        "raise timeout", sending the caller after a clock that never mattered.
        """
        srv, _seen = _server_with_captured_get(ResponseModel(
            url=DOC_URL, status=0, content=[],
            error="tls_error: certificate has expired"))

        out = await _escalate(srv, DOC_URL, 30000, body_size=8 * 1024 * 1024)

        assert out.diagnosis and out.diagnosis["code"] == "tls_error", out.diagnosis
        assert "raise timeout" not in out.next_action
        assert "file is" not in out.next_action, (
            "the size of a file that never started downloading is not the story")

    @pytest.mark.asyncio
    async def test_a_pdf_that_arrived_keeps_its_own_advice(self):
        """A file that downloaded fine but extracted badly is a fact about its
        contents, not the clock — the size advice must not swallow the OCR one."""
        scanned = ResponseModel(url=DOC_URL, status=200, content=["[PDF: 12 pages]"],
                                content_type="application/pdf",
                                error="scanned_pdf: no extractable text layer")
        srv, _seen = _server_with_captured_get(scanned)

        out = await _escalate(srv, DOC_URL, 30000, body_size=3 * 1024 * 1024)

        assert "OCR" in out.next_action
        assert "file is" not in out.next_action


# ─── C: the forced tier, same bug through a different door ──────────────────


class TestForcedHttpTierRespectsTheBudget:

    @pytest.mark.asyncio
    async def test_the_literal_30_second_ceiling_is_gone(self):
        """`min(timeout/1000, 30)` meant timeout=90000 bought thirty seconds."""
        srv, seen = _server_with_captured_get()
        orig_get = fetcher.http_get
        fetcher.http_get = lambda u, **kw: _ProbeResponse({})
        try:
            await srv._force_fetch(DOC_URL, "http", timeout=90000, **_TIER_BASE)
        finally:
            fetcher.http_get = orig_get

        assert seen["timeout"] == 90
        assert seen["retries"] == 0


# ─── G: the facts that used to live only in the code ────────────────────────


class TestTheWireStatesTheBudgetIsForBytes:

    @pytest.fixture()
    def props(self):
        tool = next(td for td in MasterFetchServer._TOOL_DEFS if td["name"] == "web_read")
        return tool["inputSchema"]["properties"]

    def test_timeout_says_a_document_spends_it_on_the_download(self, props):
        assert "DOWNLOAD" in props["timeout"]["description"]

    def test_pages_no_longer_promises_to_save_time(self, props):
        """It was advertised as saving "tokens/time on big PDFs". It saves tokens;
        the file still arrives whole."""
        desc = props["pages"]["description"]

        assert "EXTRACTED, not what is downloaded" in desc
        assert "saves tokens/time" not in desc.lower()


# ============================================================================
# 来源：test_extended_report_regressions.py
# ============================================================================
# 两轮扩展报告的回归：BUG-1 mojibake、BUG-5 since 掉包、N2 引擎未观测、
# 问题-3 缓存丢判定、问题-4 非英文的墙、N1 click 不等导航。
# 重名消歧：_dispatch → _dispatch__extended_report_regressions、_record → _record__extended_report_regressions

# APOSTROPHE = U+2019; its UTF-8 bytes read as windows-1252 are "â€™", as latin-1 "â"+控制符.
TYPED = "Saturn\u2019s icy moons \u2013 report"
MOJIBAKE_MARKS = ("\xe2", "\xc3", "\xc2", "\ufffd")


def _page(meta: str | None) -> bytes:
    """One document, utf-8 bytes, whose <meta charset> says whatever `meta` says.

    `None` is the common real-world shape: HN ships no meta charset at all and
    relies on the HTTP header, which is exactly where libxml2 falls back to the
    host locale (the tester's console then eats the two control bytes, which is
    why the report shows a bare "â").
    """
    tag = f"<meta charset='{meta}'>" if meta else ""
    return ("<html><head>" + tag + "<title>t</title></head><body>"
            f"<div class='story'><a class='title'>{TYPED}</a></div>"
            "</body></html>").encode("utf-8")


SCHEMA = {"properties": {"stories": {
    "type": "array", "selector": ".story",
    "properties": {"title": {"selector": ".title"}}}}}


class TestTheSchemaPathDecodesExactlyOnce:
    """BUG-1 本体：解码之后不许再有第二个字符集裁判。"""

    @pytest.mark.parametrize("meta", [None, "utf-8", "iso-8859-1", "windows-1252"])
    def test_a_meta_charset_cannot_remangle_bytes_we_already_decoded(self, meta):
        out = extract_structured(_page(meta).decode("utf-8"), SCHEMA)

        assert out["stories"][0]["title"] == TYPED
        assert not any(m in json.dumps(out, ensure_ascii=False) for m in MOJIBAKE_MARKS), \
            f"meta charset={meta} reintroduced the report's mojibake"

    @pytest.mark.parametrize("meta", [None, "iso-8859-1", "windows-1252"])
    def test_the_response_css_path_uses_the_header_charset_too(self, meta):
        """`page.css()` 是同一个洞的另一半：它曾经把**原始字节**交给 libxml2。"""
        page = Response(url="https://example.test/", body=_page(meta), status=200,
                             headers={}, encoding="utf-8")

        assert page.css(".title")[0].text_content() == TYPED

    def test_a_lying_header_is_still_overridden_by_the_bytes(self):
        """修 mojibake 不能把 16.0 那头的修好方向弄丢：header 说谎时字节要赢。"""
        page = Response(url="https://example.test/", body=_page(None), status=200,
                             headers={}, encoding="iso-8859-1")

        assert page.content.count("\u2019") == 1
        assert page.css(".title")[0].text_content() == TYPED

    def test_a_true_latin1_body_is_not_forced_through_utf8(self):
        latin = ("<html><body><div class='story'><a class='title'>café "
                 "naïve</a></div></body></html>").encode("iso-8859-1")
        page = Response(url="https://example.test/", body=latin, status=200,
                             headers={}, encoding="iso-8859-1")

        assert page.css(".title")[0].text_content() == "café naïve"


def _dispatch__extended_report_regressions(tool: str, args: dict):
    return asyncio.run(MasterFetchServer()._dispatch(tool, args))


def _record__extended_report_regressions(monkeypatch, method: str, result=None) -> dict:
    """Stub a tool method; return the kwargs the dispatcher actually passed it.

    Bound against the real signature, so a value handed to the wrong parameter
    shows up under the wrong name instead of being filed away under the right one.
    """
    import inspect
    sig = inspect.signature(getattr(MasterFetchServer, method))
    seen: dict = {}

    async def fake(self, **kwargs):
        bound = sig.bind(self, **kwargs)
        seen.update(bound.arguments)
        seen.pop("self")
        return result

    monkeypatch.setattr(MasterFetchServer, method, fake)
    return seen


def _shape_for(tool: str):
    """Whatever each dispatch branch expects back, so the stub is not the failure."""
    if tool == "feed_read":
        return []
        return {}
    return ResponseModel(url="https://example.test/", status=200, content=["x"])


class TestTheWireReadsEveryArgumentItAdvertises:
    """BUG-5 本体，外加一条不再靠人记得的守卫。"""

    def test_since_reaches_the_method(self, monkeypatch):
        seen = _record__extended_report_regressions(monkeypatch, "feed_read")
        _dispatch__extended_report_regressions("feed_read", {"urls": ["https://example.test/feed.xml"],
                                 "since": "2026-09-27T11:00:00Z"})

        assert seen.get("since") == "2026-09-27T11:00:00Z"

    @pytest.mark.parametrize("tool,call", [
        ("feed_read", {"urls": ["https://example.test/feed.xml"], "max_items": 3,
                        "timeout": 7, "since": "2026-09-27T11:00:00Z"}),
        ("doc_parse", {"file_path": "x.csv", "cwd": ".", "encoding": "gbk"}),
        ("sys_state", {"show": ["cache"], "clear": ["engine_state"],
                       "confirm": True}),
    ])
    def test_every_advertised_property_is_forwarded(self, monkeypatch, tool, call):
        """schema 声明即承诺：写了却没读 = 对调用方说谎。

        报告的复现是「结果与不传 since 完全一致」。只有把每个声明的参数都送一遍、再核对
        门方法收到了什么，这类掉包才会红 —— 单测 `_since_cutoff` 或直调 `self.feed_read`
        都看不见它。四个网络工具的每个参数名由 test_params_reach_the_tool.py 同样地钉住。
        """
        defs = {d["name"]: d for d in MasterFetchServer._enabled_tool_defs()}
        props = set(defs[tool]["inputSchema"]["properties"])

        seen = _record__extended_report_regressions(monkeypatch, tool, result=_shape_for(tool))
        _dispatch__extended_report_regressions(tool, call)

        unread = sorted(props - set(seen))
        assert not unread, f"{tool} advertises {unread} but the dispatcher drops them"


class TestSinceActuallyFilters:
    """门方法往下的那半段也要从 wire 走一遍，不然又只剩单元测试。"""

    FEED = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>t</title>
  <item><title>new</title><link>https://example.test/n</link>
        <pubDate>Sun, 27 Sep 2026 12:30:00 +0000</pubDate></item>
  <item><title>old-one</title><link>https://example.test/o1</link>
        <pubDate>Sun, 27 Sep 2026 10:26:00 +0000</pubDate></item>
  <item><title>old-two</title><link>https://example.test/o2</link>
        <pubDate>Sat, 26 Sep 2026 21:00:00 +0000</pubDate></item>
  <item><title>undated</title><link>https://example.test/u</link></item>
</channel></rss>""".encode("utf-8")

    @pytest.fixture
    def served(self, monkeypatch):
        async def fake_grab(url, timeout, headers=None):  # noqa: ARG001
            # (body, status, encoding, error, response headers) - the headers are
            # what P2-10 asked for: without them a poll re-downloads the feed.
            return self.FEED, 200, "utf-8", "", {"etag": '"feed-etag-1"'}

        monkeypatch.setattr(feed_mod, "_grab", fake_grab)
        return fake_grab

    def test_entries_before_the_cutoff_are_dropped_and_counted(self, served):
        _content, structured = _dispatch__extended_report_regressions("feed_read", {
            "urls": ["https://example.test/feed.xml"], "since": "2026-09-27T11:00:00Z"})

        feed = structured["feeds"][0]
        assert [i["title"] for i in feed["items"]] == ["new", "undated"]
        assert feed["items_older_than_since"] == 2
        assert feed["items_without_date"] == 1
        assert feed["since"] == "2026-09-27T11:00:00+00:00"

    def test_without_since_the_same_feed_comes_back_whole(self, served):
        """对照：有 since 与没有 since 必须**不一样**，那才是过滤器在跑的证据。"""
        _content, structured = _dispatch__extended_report_regressions("feed_read", {
            "urls": ["https://example.test/feed.xml"]})

        assert len(structured["feeds"][0]["items"]) == 4
        assert structured["feeds"][0]["items_older_than_since"] == 0


class _ServedPdf:
    """A loopback server that answers one URL with a real text PDF.

    Real extraction, real sqlite cache, real `_finalize_result` — 问题-3 的两半
    （写入带不带 content_ok、命中读不读它）都在这条链上，桩位打在任何一端都测不到它。
    """

    def __enter__(self):
        import http.server
        import socketserver
        import threading

        pdf = (Path(__file__).parent / "background_checks.pdf").read_bytes()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(len(pdf)))
                self.end_headers()
                self.wfile.write(pdf)

            def log_message(self, *args):
                pass

        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.srv.server_address[1]}/paper.pdf"

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.srv.server_close()
        return False


def _fetch(url: str, **args) -> dict:
    """One web_read through the real dispatcher (loopback hosts need the opt-in).

    17.0 §3-17：那颗 opt-in 不再是每次调用都传的 `allow_private=`，而是
    `cred_defaults` 存一次的东西 —— 全局键，不需要 host，web_read 在入口把它读回来
    并开到自己的 SSRF 闸上。conftest 把 identity.db 指到每个用例自己的 tmp 目录，
    所以这一次存下的放行不会跟着下一个用例去敲别人的门。
    """
    identity.set_value("allow_private", True)
    return asyncio.run(MasterFetchServer()._dispatch(
        "web_read", {"url": url, "timeout": 60000, **args}))[1]


class TestACachedPdfKeepsTheExtractorsVerdict:
    """问题-3：缓存命中的那份 PDF 报 content_ok=false，而 content 是非空的。

    `_agent_hints` 在 quality_score>0 时把判断权交给抽取器（乱码 PDF 必须留 false，
    那是 P3 的修法），于是它读的 `result.content_ok` 必须随行。缓存写入存了
    quality_score 却没存 content_ok，命中路径就把它落在默认值 False 上 —— 同一份
    内容，第一次抓是 true、第二次抓是 false，而且第二次看着更像失败。
    """

    def test_a_real_pdf_round_trips_one_verdict(self):
        pytest.importorskip("pdfplumber")
        with _ServedPdf() as url:
            first, second = _fetch(url), _fetch(url)

        assert first["error"] == "", f"抓取本身失败了：{first['error'][:120]}"
        assert (first.get("cached", False), second["cached"]) == (False, True), \
            f"第二次没走缓存：{second['error'][:80]}"
        assert first["quality_score"] > 0, "这条用例要靠 quality_score>0 走抽取器判定分支"
        assert first["content_ok"] is True
        assert second["content_ok"] == first["content_ok"], (
            "同一份内容两次调用给了两个 content_ok —— 报告说的「易误导调用方判失败」")
        assert any(c.strip() for c in second["content"]), "content 非空却 content_ok=false"

    def test_a_stored_garbled_verdict_is_not_promoted(self):
        """反向也要钉住：恢复 content_ok 不能把乱码 PDF 洗成 true（那是 P3 当年的错）。"""
        import asyncio as aio

        from dhole_mcp.cache import set_cached

        async def seed(url):
            await set_cached(url, "markdown", ["[cid garbage] " * 60], 200,
                             content_type="application/pdf", ttl=3600,
                             envelope={"quality_score": 0.4, "content_ok": False,
                                       "page_type": "pdf"})

        with _ServedPdf() as url:
            aio.run(seed(url))
            hit = _fetch(url)

        assert hit["cached"] is True
        assert hit["quality_score"] == 0.4
        assert hit["content_ok"] is False


class _VirtualClock:
    """`time` as actions.py sees it, with waits that move the clock instead of sleeping."""

    def __init__(self):
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t


class _FakePage:
    """The slice of the playwright page that a click touches.

    `snapshots` is what the page reports after each poll; the list is consumed in
    order, and a None entry stands for "no execution context" (a document being
    replaced). `loads` records the wait-for-navigation calls, which is the thing
    the report says never happened.
    """

    def __init__(self, snapshots, start_href="https://news.ycombinator.test/",
                 clock=None, click_exc=None):
        self._snapshots = list(snapshots)
        self.href = start_href
        self.polls = 0
        self.loads: list[tuple] = []
        self.clicked = False
        self.click_exc = click_exc
        self.clock = clock or _VirtualClock()

    async def evaluate(self, script):
        self.polls += 1
        if not self._snapshots:
            return [self.href, 10, 100]
        snap = self._snapshots.pop(0)
        if snap is None:
            raise RuntimeError("Execution context was destroyed")
        href, nodes, chars = snap
        self.href = href
        return [href, nodes, chars]

    async def wait_for_timeout(self, ms: int) -> None:
        self.clock.t += ms / 1000.0

    async def wait_for_load_state(self, state="load", timeout=None):
        self.loads.append((state, timeout))

    def locator(self, selector):
        page = self

        class _First:
            async def click(self, timeout=None):
                if page.click_exc is not None:
                    raise page.click_exc
                page.clicked = True

        class _Loc:
            first = _First()

        return _Loc()


def _run_click(page, monkeypatch, actions_mod):
    async def clock_monotonic():
        return page.clock.t

    monkeypatch.setattr(actions_mod, "time", page.clock)
    action = actions_mod.build_page_action([{"click": "a.more-link"}])
    asyncio.run(action(page))
    return page


class TestAClickWaitsForWhatItCaused:
    """N1：click 派发完就返回，抽取发生在导航完成之前，拿到的是原页面。"""

    def test_a_navigation_after_the_click_is_waited_for(self, monkeypatch):
        from dhole_mcp import actions as actions_mod

        base = "https://news.ycombinator.test/"
        page = _FakePage([
            (base, 100, 1000),                      # before the click
            (base, 100, 1000),                      # nothing yet
            ("https://news.ycombinator.test/?p=2", 140, 1800),   # the new page lands
        ])
        _run_click(page, monkeypatch, actions_mod)

        assert page.clicked
        assert page.loads, "点击造成了导航却没有等文档就绪 —— 抽取会停在原页面"
        assert page.loads[0][0] == "domcontentloaded"

    def test_a_detached_context_counts_as_a_navigation(self, monkeypatch):
        """playwright 换文档时 evaluate 直接抛 —— 那正是该等的时候，不是失败。"""
        from dhole_mcp import actions as actions_mod

        base = "https://news.ycombinator.test/"
        page = _FakePage([(base, 100, 1000), None])
        _run_click(page, monkeypatch, actions_mod)

        assert page.loads and page.loads[0][0] == "domcontentloaded"

    def test_a_page_that_grew_in_place_is_not_a_navigation(self, monkeypatch):
        """展开器（本页变长，不换 URL）：等它停下来就行，不该去等新文档。"""
        from dhole_mcp import actions as actions_mod

        base = "https://news.ycombinator.test/"
        page = _FakePage([
            (base, 100, 1000), (base, 100, 1000),
            (base, 120, 1600), (base, 120, 1600), (base, 120, 1600),
        ])
        _run_click(page, monkeypatch, actions_mod)

        assert page.loads == []
        assert page.polls >= 4, "本页变长的形状没被等到"

    def test_a_click_nothing_answers_stays_fast(self, monkeypatch):
        """封顶的另一半：4s 的额度不能变成每个空点击的固定开销。"""
        from dhole_mcp import actions as actions_mod

        base = "https://news.ycombinator.test/"
        page = _FakePage([(base, 100, 1000)] * 40)
        _run_click(page, monkeypatch, actions_mod)

        assert page.polls <= 6, f"空点击花了 {page.polls} 轮轮询"
        assert page.clock.t * 1000 <= actions_mod.CLICK_SETTLE_MIN_MS + 600


class _ServedPage:
    """A loopback origin that answers every GET with the same small document.

    17.0 之前这里还有一条 `/honoured` 路径专门回 304，用来钉「验证器被无视」这个
    标记 —— 那个标记随条件请求参数一起下线了，所以剩下的这一条路径就是全部需要
    的东西。真实 304 的证人搬到 `test_revalidation_g23.py` 的那个源站上去了。
    """

    def __enter__(self):
        import http.server
        import socketserver
        import threading

        body = (b"<html><body><article><h1>Doc</h1><p>"
                + b"loopback validator probe body text. " * 30
                + b"</p></article></body></html>")

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("ETag", '"probe-etag"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.srv.server_address[1]}/page"

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.srv.server_close()
        return False


# 实测抓回来的文本（本机 2026-09-27，问题-4）：
#   GET https://www.google.com/search?q=quantum+entanglement
#   -> 200, http 层, 1053 字符, content_ok=True, page_type=unknown
GOOGLE_ZH_JS_WALL = (
    "启用 JavaScript 才能使用搜索功能\n\n"
    "# 开启 JavaScript 才能继续搜索\n\n"
    "您使用的浏览器已关闭 JavaScript。若要继续搜索，请开启 JavaScript。\n\n"
    "了解如何在 [Google Chrome](https://support.google.com/chrome/answer/114662) "
    "中进行操作\n\n"
    "1. 点击 ，然后点击**设置**。\n2. 点击**隐私和安全**。\n3. 点击**网站设置**。"
    "\n4. 点击 **JavaScript**。\n5. 选择**网站**。\n")


class TestAWallInAnotherLanguageIsStillAWall:
    """问题-4：墙按访客的出口语言出文案，而信号表整张是英文。"""

    def _result(self, text: str, **kw):
        return ResponseModel(url="https://www.google.com/search?q=x", status=200,
                             content=[text], fetcher_used="http", **kw)

    def test_the_localized_google_wall_is_not_content(self):
        assert _is_js_shell(self._result(GOOGLE_ZH_JS_WALL))
        assert _detect_content_issue(self._result(GOOGLE_ZH_JS_WALL)).startswith(
            "js_shell_detected")

    def test_a_chinese_tutorial_about_javascript_is_not_a_wall(self):
        """长度闸门：正文里出现「开启 JavaScript」的长文不是墙（与 bot-wall 同一道理）。"""
        body = ("开启 JavaScript 后，浏览器会执行页面里的脚本。" * 60)

        assert len(body) > 1500
        assert not _is_js_shell(self._result(body))


class TestTheResponseSaysHowRobotsWasDecided:
    """两轮报告从两边要同一件事：调用方/审计无法从响应区分「站点允许」与「全局绕过」。

    本机默认就是 DHOLE_IGNORE_ROBOTS=1（conftest 为了不让测试打 /robots.txt 而设），
    所以这条链在这里正好是它要暴露的那一格。
    """

    def test_the_three_ways_to_get_permission_are_distinguishable(self, monkeypatch):
        with _ServedPage() as url:
            monkeypatch.setenv("DHOLE_IGNORE_ROBOTS", "1")
            by_env = _fetch(url, include_links=False)
            monkeypatch.delenv("DHOLE_IGNORE_ROBOTS")
            by_param = _fetch(url, ignore_robots=True, cache_ttl=0, include_links=False)
            complying = _fetch(url + '#', cache_ttl=0, include_links=False)

        assert "DHOLE_IGNORE_ROBOTS" in by_env["metadata"]["robots"], \
            f"进程级绕过没有如实写出：{by_env['metadata']}"
        assert "ignore_robots=true" in by_param["metadata"]["robots"]
        assert by_env["metadata"]["robots"] != by_param["metadata"]["robots"]
        assert complying["metadata"]["robots"] == "complied"


class TestAnActionThatNeverRanSaysSo:
    """验证报告把 N1 记成「仍未修复」的那一次，真相是动作根本没跑。

    `click a.more-link` 打的是 HN 不存在的 class（它是 `morelink`）：playwright 10s
    超时，异常被 page_action 的 except 吃掉，只留一条 logger.warning —— 于是响应是
    「第 1 页的正常内容 + 一切成功」，读起来就是「click 不等导航」。计时那一半上一轮
    已经修了；这一半（动作的收执）才是让下一次报告不再指错地方的东西。
    """

    def _run(self, monkeypatch, click_exc=None, selector="a.more-link"):
        """One click action, run against the fake page through the real builder."""
        from dhole_mcp import actions as actions_mod

        base = "https://news.ycombinator.test/"
        page = _FakePage([(base, 100, 1000)], click_exc=click_exc)
        runner = actions_mod.build_page_action([{"click": selector}])
        monkeypatch.setattr(actions_mod, "time", page.clock)
        asyncio.run(runner(page))
        return runner, page

    def test_a_timed_out_click_is_reported_with_its_selector(self, monkeypatch):
        runner, _page = self._run(
            monkeypatch,
            RuntimeError("Timeout 10000ms exceeded.\nCall log: waiting for locator('a.more-link')"))

        assert runner.outcomes == [{
            "action": "click 'a.more-link'", "ok": False,
            "error": runner.outcomes[0]["error"]}]
        assert "matched no element" in runner.outcomes[0]["error"], \
            "超时必须分出「选择器没命中」这一格，否则下一步仍是猜"

    def test_a_successful_action_reports_no_failure(self, monkeypatch):
        runner, page = self._run(monkeypatch, selector="a.morelink")

        assert page.clicked
        assert runner.outcomes == [{"action": "click 'a.morelink'", "ok": True}]

    def test_the_receipt_reaches_the_response(self):
        from dhole_mcp.server import _report_action_outcomes

        result = ResponseModel(url="https://x.test/", status=200,
                               content=["page one content" * 20])
        result.content_ok = True

        class _Runner:
            outcomes = [{"action": "click 'a.more-link'", "ok": True},
                        {"action": "scroll x5", "ok": False, "error": "detached"}]

        _report_action_outcomes(result, _Runner())

        assert len(result.metadata["actions"]) == 2
        assert "1 of 2 actions did not take effect" in result.summary
        assert "scroll x5" in result.summary
        assert "never happened" in result.next_action

    def test_an_all_green_sequence_adds_no_complaint(self):
        from dhole_mcp.server import _report_action_outcomes

        result = ResponseModel(url="https://x.test/", status=200,
                               content=["page one content" * 20])

        class _Runner:
            outcomes = [{"action": "click 'a.morelink'", "ok": True}]

        _report_action_outcomes(result, _Runner())

        assert result.metadata["actions"] == _Runner.outcomes
        assert result.next_action == ""


def _fake_page(content_type: str, body: bytes, url: str = "https://example.test/x"):
    class Page:
        status = 200
        encoding = "utf-8"
    page = Page()
    page.body = body
    page.url = url
    page.headers = {"content-type": content_type}
    return page


SITEMAP = (b'<?xml version="1.0" encoding="UTF-8"?>'
           b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           b'<url><loc>https://docusaurus.dev/lander</loc>'
           b'<lastmod>2026-09-27</lastmod></url></urlset>')


class TestADataXmlDocumentKeepsItsTags:
    """P2-7：sitemap / feed / XML API 的数据就在标签结构里，压平即丢失。"""

    @pytest.mark.parametrize("ct", ["application/xml", "text/xml",
                                    "application/rss+xml", "application/sitemap+xml",
                                    "application/soap+xml"])
    def test_the_elements_survive(self, ct):
        from dhole_mcp.server import _translate_response

        text = "".join(_translate_response(_fake_page(ct, SITEMAP), "markdown", None,
                                           "http", 1).content)
        assert "<urlset" in text and "<loc>" in text and "2026-09-27" in text, text[:160]

    def test_xhtml_is_still_an_html_document(self):
        """application/xhtml+xml 是 HTML 穿了 XML 的媒体类型，不能原样吐出去。"""
        from dhole_mcp.server import _is_xml_content_type

        assert not _is_xml_content_type("application/xhtml+xml")
        assert not _is_xml_content_type("image/svg+xml")
        assert not _is_xml_content_type("text/html; charset=utf-8")
        assert _is_xml_content_type("application/rss+xml; charset=utf-8")

    def test_the_structure_verdict_names_it(self):
        from dhole_mcp.server import _apply_envelope

        assert detect_page_type("", "https://a.test/sitemap.xml", "application/xml") == "xml"
        assert detect_page_type("", "https://a.test/f", "application/rss+xml") == "xml"
        assert detect_page_type("<html><body>x</body></html>", "https://a.test/f",
                               "application/xhtml+xml") != "xml"

        # The response's own verdict comes from _apply_envelope's content_type
        # override, not from the structural fallback above — patching only one of
        # the two leaves page_type=unknown next to a body full of tags.
        result = ResponseModel(url="https://a.test/sitemap.xml", status=200,
                               content=["<urlset/>"], content_type="application/xml")
        _apply_envelope(result)
        assert result.page_type == "xml"


class TestRotatedPdfTextIsNotBodyText:
    """P2-8：arxiv 页边那行旋转 90° 的标识被按阅读序读成 1v12052.9062:viXra。

    实测（报告自己那篇 2609.25021）：page 1 有 39 个 upright=False 的字符，正文
    3032 个 upright=True，而那 39 个正是报告引用的 guA / ]GL.sc[ / viXra 三行。
    """

    def _chars(self, upright_n: int, tilted_n: int):
        return ([{"object_type": "char", "upright": True, "text": "a"}] * upright_n
                + [{"object_type": "char", "upright": False, "text": "b"}] * tilted_n)

    def test_a_few_rotated_chars_are_the_artifact(self):
        from dhole_mcp.pdf_extractor import _rotated_is_artifact

        class Page:
            chars = self._chars(3032, 39)

        assert _rotated_is_artifact(Page) is True

    def test_a_page_that_IS_rotated_text_keeps_its_chars(self):
        """竖排中文 / 整页侧倒：旋转就是内容本身，剔除等于把文档删了。"""
        from dhole_mcp.pdf_extractor import _rotated_is_artifact

        class Page:
            chars = self._chars(20, 200)

        assert _rotated_is_artifact(Page) is False

    def test_a_page_without_chars_decides_nothing(self):
        from dhole_mcp.pdf_extractor import _rotated_is_artifact

        class Empty:
            chars = []

        class Broken:
            @property
            def chars(self):  # noqa: ANN401
                raise RuntimeError("no char layer")

        assert _rotated_is_artifact(Empty()) is False
        assert _rotated_is_artifact(Broken()) is False

    @pytest.mark.live
    def test_the_report_pdf_loses_its_sidebar_noise(self):
        """联网那条：拿报告引用的那篇真 PDF 验一遍（默认跑不执行）。"""
        import urllib.request

        from dhole_mcp.pdf_extractor import extract_pdf

        req = urllib.request.Request("https://arxiv.org/pdf/2609.25021",
                                     headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=120).read()
        text = "\n".join(extract_pdf(data, pages="1").content)

        for noise in ("viXra", "guA", "]GL.sc["):
            assert noise not in text, f"旋转页边仍被读进正文：{noise}"
        assert "Chat Template" in text


# ============================================================================
# 来源：test_wire_arg_type_regressions.py
# ============================================================================
# Regressions for the wire-boundary argument types (external report, 2026-09-24).
# 重名消歧：_dispatch → _dispatch__wire_arg_type_regressions、_envelope → _envelope__wire_arg_type_regressions、_record → _record__wire_arg_type_regressions

def _dispatch__wire_arg_type_regressions(tool: str, args: dict):
    return asyncio.run(MasterFetchServer()._dispatch(tool, args))


def _envelope__wire_arg_type_regressions(tool: str, args: dict) -> dict:
    _content, structured = _dispatch__wire_arg_type_regressions(tool, args)
    return structured


def _record__wire_arg_type_regressions(monkeypatch, method: str, result=None) -> dict:
    """Stub a tool method; return the dict it records its kwargs into."""
    seen: dict = {}

    async def fake(self, **kwargs):
        seen.update(kwargs)
        return result

    monkeypatch.setattr(server_mod.MasterFetchServer, method, fake)
    return seen


def _stub_search_layer(monkeypatch) -> dict:
    """Replace engine fetch + cache + reranker; return what they saw."""
    seen: dict = {}

    async def fake_multi(query, max_results=10, **kw):
        seen["max_results"] = max_results
        seen.update(kw)
        ranked = [
            RawResult(title=f"Knot DNS page {i}", url=f"https://knot-dns.example.org/{i}.html",
                      snippet="Knot DNS resolver manual", source="bing", position=i,
                      consensus=1, sources=("bing",))
            for i in range(1, 11)
        ]
        return ranked, [EngineReport(name="bing", ok=True, status="ok")]

    async def no_cache(query, extraction, css, *, ttl=None, **kw):
        seen["ttl"] = ttl
        return None

    async def fake_set(*a, **kw):
        return None

    monkeypatch.setattr(search_mod, "multi_search", fake_multi)
    monkeypatch.setattr(search_mod, "get_cached", no_cache)
    monkeypatch.setattr(search_mod, "set_cached", fake_set)
    monkeypatch.setattr(search_mod, "ensure_reranker",
                        lambda *a, **kw: asyncio.sleep(0))
    return seen


# ─── the reported crash ─────────────────────────────────────────────


class TestStringifiedNumbers:
    """A client that serializes numbers is a normal client, not a bad call."""

    def test_max_results_as_a_string_no_longer_raises_typeerror(self, monkeypatch):
        """The report's P1: every search answered with a bare Python traceback."""
        _stub_search_layer(monkeypatch)
        payload = _envelope__wire_arg_type_regressions("web_search", {"query": "Knot DNS", "max_results": "8"})

        assert "not supported between instances" not in payload["error"]
        assert payload["total_results"] == 8, "the caller's 8 has to be the 8 it gets"

    def test_page_as_a_string_selects_that_page(self, monkeypatch):
        """The report's `page=1 -> "Invalid page: '1'"`, for an integer 1.

        Rejecting it was also wrong at the layer below: `page` becomes
        `page + 1` for the backends, so it has to arrive as the int it means.
        """
        seen = _stub_search_layer(monkeypatch)
        payload = _envelope__wire_arg_type_regressions("web_search", {"query": "Knot DNS", "page": "1"})

        assert payload["error"] == ""
        assert seen["page"] == 1

    def test_a_null_takes_the_documented_default(self, monkeypatch):
        """`cache_ttl=null` means "not set", not "set to None".

        这条留着是因为它是老事故的另一半：袋子里的 null 会被原样转发，于是
        `if cache_ttl > 0` 读到一个 None 变成 TypeError。B4 拆平后只有一个通道，
        null 一律按"没传"处理，与顶层的读法一致。
        """
        seen = _stub_search_layer(monkeypatch)
        payload = _envelope__wire_arg_type_regressions("web_search",
                            {"query": "Knot DNS", "cache_ttl": None,
                             "max_results": None})

        assert "not supported between instances" not in payload["error"]
        assert seen["ttl"] == search_mod.SEARCH_CACHE_TTL
        assert payload["total_results"] == 6, "max_results fell back to its own default"

    def test_a_junk_number_names_the_argument_instead_of_tracebacking(self, monkeypatch):
        _stub_search_layer(monkeypatch)
        with pytest.raises(ValueError) as exc:
            _dispatch__wire_arg_type_regressions("web_search", {"query": "Knot DNS", "max_results": "abc"})

        assert "max_results must be an integer" in str(exc.value)
        assert "max_results=6" in str(exc.value)

    def test_the_boundary_still_leaves_ranges_to_the_tool(self, monkeypatch):
        """Clamping twice above the tool's own note would hide the note.

        `page` 0-10 belongs to _validate_filters and `max_results` 1-50 to
        search.py, both of which say out loud what they did.
        """
        _stub_search_layer(monkeypatch)
        assert "Invalid page: 99 (0-10)" in _envelope__wire_arg_type_regressions(
            "web_search", {"query": "Knot DNS", "page": 99})["error"]
        over = json.dumps(_envelope__wire_arg_type_regressions("web_search",
                                    {"query": "Knot DNS", "max_results": 100}))
        assert "max_results=100 is outside the supported 1-50" in over


class TestOtherToolsWithTheSameGap:
    """Every tool that read args.get() raw, not just the one that was reported."""

    def test_a_stringified_false_is_not_true_for_the_fetch_knobs(self, monkeypatch):
        """`include_media="false"` used to load the resources anyway.

        17.0 B5-13a 之前这里的样本是 `headless` / `hide_canvas`。那两颗已经进了
        `anti_detect` 档位、不再是入参，所以这一族的样本换成留下来的那几颗布尔，
        并把档位那颗字符串一起钉住：它走的是同一条边界，而大小写/空白是字符串客户端
        最容易带进来的两种噪声。B5-13d 退掉 `network_idle` 后，样本就是 web_read 在
        `_ARG_TYPES` 里仅剩的三颗布尔 —— 一颗读真、两颗读假，边界两个方向都还钉着。
        """
        seen = _record__wire_arg_type_regressions(monkeypatch, "web_read", ResponseModel(url="", status=0, content=[]))
        _dispatch__wire_arg_type_regressions("web_read", {"url": "https://example.test",
                                  "include_links": "true",
                                  "include_media": "false",
                                  "ignore_robots": "false",
                                  "anti_detect": " AGGRESSIVE "})

        assert seen["include_links"] is True
        assert seen["include_media"] is False
        assert seen["ignore_robots"] is False
        assert seen["anti_detect"] == "aggressive"

    def test_the_two_booleans_that_decide_something_are_read_as_booleans(self, monkeypatch):
        """`confirm="false"` 到闸门口是一个非空字符串 = 真。

        sys_state 的 confirm 栽过一次（"just look" 变成了清盘）。web_read 这两颗是同
        一类、而且后果更重：confirm 决定"要不要替用户授权一台真实 Chrome"（先问再记
        的那道闸），check_login 决定要不要跑那次 DOM 探测。schema 里两颗都写着
        boolean，调度器原先却没有它们的字符串写法路径 —— 守卫
        `test_every_boolean_property_has_a_string_spelling_reader` 现在盯着这一整族。
        """
        seen = _record__wire_arg_type_regressions(monkeypatch, "web_read", ResponseModel(url="", status=0, content=[]))
        _dispatch__wire_arg_type_regressions("web_read", {"url": "https://example.test",
                                  "confirm": "false", "check_login": "false"})
        assert seen["confirm"] is False
        assert seen["check_login"] is False

        seen = _record__wire_arg_type_regressions(monkeypatch, "web_read", ResponseModel(url="", status=0, content=[]))
        _dispatch__wire_arg_type_regressions("web_read", {"url": "https://example.test", "check_login": "true"})
        assert seen["check_login"] is True

    def test_screenshot_action_accepts_the_string_spellings(self):
        """A1：full_page/quality 的强制转换随工具下线离开顶层 coerce 表 ——
        字符串写法现在在动作归一化层拒绝（形状错误不能静默读真）。"""
        from dhole_mcp.actions import _validate_actions
        with pytest.raises(ValueError, match="full_page"):
            _validate_actions([{"screenshot": {"full_page": "false"}}])
        with pytest.raises(ValueError, match="quality"):
            _validate_actions([{"screenshot": {"selector": "img",
                                               "image_type": "jpeg",
                                               "quality": "80"}}])

    def test_crawl_caps_arrive_as_numbers_for_the_code_that_counts(self, monkeypatch):
        """`max_pages="5"` reaches `range(max_pages)`; `ignore_robots="false"`
        reaches `if ignore_robots:` and crawls a site the robots file forbids.

        这条原先拿 discover_only 举例（一个 "false" 会被读真，答案是那张地图而不是
        正文）。17.0 §4 把地图拆成 web_discover 之后，它不再是任何工具的入参，同一个
        bug 类改由 ignore_robots 钉住 —— 换的只是被读错的那个名字。"""
        seen = _record__wire_arg_type_regressions(monkeypatch, "web_crawl", ResponseModel(url="", status=0, content=[]))
        _dispatch__wire_arg_type_regressions("web_crawl", {"url": "https://example.test", "ignore_robots": "false",
                                  "max_pages": "5", "deadline_ms": "90000"})

        assert seen["max_pages"] == 5
        assert seen["deadline_ms"] == 90000
        assert seen["ignore_robots"] is False

    def test_a_list_option_as_a_bare_string_is_rejected_before_any_search(self, monkeypatch):
        """`engines="bing"` iterates into four one-character engine names.

        The list case with the worst outcome: every element is a plausible name, so
        one string becomes four queries against engines that do not exist.
        (`crawl_urls` was this test's subject until 17.0 §3 decision 31 deleted it;
        the boundary guard it exercised is what this test is really about.)
        """
        seen = _record__wire_arg_type_regressions(monkeypatch, "web_search")
        with pytest.raises(ValueError) as exc:
            _dispatch__wire_arg_type_regressions("web_search", {"query": "q", "engines": "bing"})

        assert "engines must be an array of strings" in str(exc.value)
        assert seen == {}, "a rejected call must not reach the search at all"

    def test_feed_and_resolve_timeouts_arrive_as_numbers(self, monkeypatch):
        seen = _record__wire_arg_type_regressions(monkeypatch, "feed_read", [])
        _dispatch__wire_arg_type_regressions("feed_read", {"urls": ["https://example.test/rss"],
                                 "max_items": "0", "timeout": "30"})
        assert seen["max_items"] == 0
        assert seen["timeout"] == 30


    def test_a_numeric_pdf_page_range_is_not_dropped(self, monkeypatch):
        """`pages` was read only when it was a str, so pages=3 meant no range at
        all — the whole file extracted, with nothing saying so."""
        seen = _record__wire_arg_type_regressions(monkeypatch, "web_read", ResponseModel(url="", status=0, content=[]))
        _dispatch__wire_arg_type_regressions("web_read", {"url": "https://example.test/paper.pdf",
                                  "pages": 3, "password": 1234})

        assert seen["pages"] == "3"
        assert seen["password"] == "1234"


class TestActionsAndEngineState:
    """The two things the report attributed to something else."""

    def test_actions_accept_a_serialized_list_and_a_stringified_count(self):
        """The array form several clients send, and {"wait": "1000"}.

        A client that serializes nested values sends `actions` as a JSON string;
        reading that as an iterable of characters was never going to work.
        """
        out = actions_mod._validate_actions(json.loads('[{"scroll": "2"}, {"wait": 500}]'))

        # scroll normalizes to one shape (steps/selector/ms_per_step) so the
        # executor has a single path; assert the digit STRING landed, not the
        # defaults that came with it.
        assert out[0]["scroll"]["steps"] == 2
        assert out[1] == {"wait": 500}
        assert actions_mod._validate_actions([{"wait": "1000"}]) == [{"wait": 1000}]

    def test_actions_still_want_one_key_per_object_and_say_so(self):
        """Not a defect — but the rule has to be on the wire, not only in code."""
        with pytest.raises(ValueError) as exc:
            actions_mod._validate_actions([{"scroll": 3, "wait": 1000}])
        assert "exactly one key" in str(exc.value)

        # 批次12：光有键名清单不够 —— 实测调用方写 {"index": 0, "fill": ...} 被拒后
        # 只能靠猜 index 放哪（正确形状藏在 examples.md 里）。报错要带可抄写的示例。
        assert '"fill": {"index": 0' in str(exc.value)

        # 17.0 §4 第 2 行：这条规矩的话现在由 web_act 说（actions 不再挂在读的那扇门上）。
        tool = next(td for td in MasterFetchServer._TOOL_DEFS if td["name"] == "web_act")
        assert "ONE action key per object" in tool["inputSchema"]["properties"]["actions"]["description"]

    def test_engine_state_reset_leaves_the_next_search_working(self, monkeypatch):
        """The report's restart-proof crash was never the engine pool.

        It filed the engine-state reset (now `sys_state(clear=["engine_state"])`)
        as the trigger and pointed at
        the cooldown tracker's `last` field. Resetting the pool and searching
        again is the exact sequence, and it works with the state files in the
        condition the reset leaves them in.
        """
        from dhole_mcp import search_metasearch as ms

        async def fake_clear_all():
            return 0

        monkeypatch.setattr(server_mod, "clear_all_cache", fake_clear_all)
        _stub_search_layer(monkeypatch)
        payload = _envelope__wire_arg_type_regressions("sys_state", {"clear": ["cache", "engine_state"]})
        assert payload["engine_state_reset"] is True
        assert ms.cooldowns() == {} and ms._ENGINE_YIELD == {}

        search = _envelope__wire_arg_type_regressions("web_search", {"query": "Knot DNS"})

        assert search["error"] == ""
        assert search["total_results"] == 6
