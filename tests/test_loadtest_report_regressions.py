"""Regressions for the 2026-09-24 load test (dhole_压力测试报告.md, v15.1).

That report ran 91 scenarios against v15.1 through an out-of-process stdio
driver and numbered its findings D-01..D-14. Each test here names the symptom it
locks down rather than the number, following the convention of
test_bug_report2_regressions.py — the numbers belong to that report and the next
report will reuse them for different defects.

Three of the report's items needed no code change and are pinned as behaviour
instead, because the report's diagnosis was partly wrong and the wrong fix would
have been worse than none:

* D-06 (``attribute`` returns only the first match) — the capability already
  exists: ``"type": "array"`` returns every match. What was missing was the
  contract on the wire, so the schema description now states it and the test
  pins both halves. The report's suggested ``{"all": true}`` would have added a
  second way to say what ``"type": "array"`` already says.
* D-13 (``required`` empty on smart_fetch *and* cache_clear) — true for
  smart_fetch, which now declares ``anyOf``. cache_clear genuinely has no
  required argument, so it stays empty; adding a fabricated ``required`` there
  would have been the bug.
* D-14 (no ``original_url``/``final_url``/``status_code``) — only
  ``original_url`` was actually missing. ``url`` is documented as the FINAL url
  and ``status`` IS the status code, so ``final_url``/``status_code`` would be
  duplicate names for the same two fields.

Two items are environment, not code, and have no test here:

* E-01 (fake-IP TUN makes the SSRF check reject public hosts) — the tool already
  emits the diagnosis plus the ``DHOLE_SSRF_DNS_RECHECK=0`` escape hatch. The
  README now documents it.
* E-02 (plaintext GitHub PAT in ``~/.config/opencode/opencode.json``) — outside
  this repo.
"""

import asyncio
import json

import pytest

from dhole_mcp import server as server_mod
from dhole_mcp import search as search_mod
from dhole_mcp.focus import focus_content, _tokens
from dhole_mcp.search import SearchResult
from dhole_mcp.server import MasterFetchServer, ResponseModel
from dhole_mcp.structured import extract_structured

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
        seen = _record(monkeypatch, "smart_fetch", _REJECTED)
        payload = _envelope("smart_fetch", {"urls": "https://example.com"})

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
        seen = _record(monkeypatch, "smart_fetch", _REJECTED)
        payload = _envelope("smart_fetch", {"urls": ["https://example.com", 123]})

        assert "urls[1]" in payload["error"]
        assert "int" in payload["error"]
        assert "validation error" not in payload["error"].lower()
        assert seen == {}

    def test_a_json_encoded_array_is_still_accepted(self, monkeypatch):
        """Clients that stringify nested structures must keep working.

        ``_coerce_options`` exists for the same reason; an array literal has
        exactly one reading, so it is honoured rather than rejected with the
        bare string.
        """
        seen = _record(monkeypatch, "smart_fetch", _REJECTED)
        _dispatch("smart_fetch", {"urls": '["https://a.test", "https://b.test"]'})
        assert seen["urls"] == ["https://a.test", "https://b.test"]

    def test_a_url_list_of_strings_still_reaches_the_tool(self, monkeypatch):
        """The legitimate path is untouched."""
        seen = _record(monkeypatch, "smart_fetch", _REJECTED)
        _dispatch("smart_fetch", {"urls": ["https://a.test", "https://b.test"]})
        assert seen["urls"] == ["https://a.test", "https://b.test"]

    @pytest.mark.parametrize("value", ["false", "no", "0", "off", False, 0])
    def test_cache_clear_only_expired_when_all_is_falsy(self, monkeypatch, value):
        """D-03: ``all="false"`` cleared the WHOLE cache.

        ``args.get("all", False)`` handed the string straight to ``if all:``,
        and every non-empty string is truthy — so a caller asking for the
        expired-only sweep got a full wipe, silently.
        """
        calls: list[str] = []

        async def fake_clear_cache(*a, **kw):
            calls.append("expired")
            return 0

        async def fake_clear_all_cache(*a, **kw):
            calls.append("all")
            return 0

        monkeypatch.setattr(server_mod, "clear_cache", fake_clear_cache)
        monkeypatch.setattr(server_mod, "clear_all_cache", fake_clear_all_cache)

        _dispatch("cache_clear", {"all": value})
        assert calls == ["expired"], f"all={value!r} must not wipe everything"

    @pytest.mark.parametrize("value", ["true", "yes", "1", "on", True, 1])
    def test_cache_clear_wipes_all_when_all_is_truthy(self, monkeypatch, value):
        """The other direction keeps working, including stringified."""
        calls: list[str] = []

        async def fake_clear_cache(*a, **kw):
            calls.append("expired")
            return 0

        async def fake_clear_all_cache(*a, **kw):
            calls.append("all")
            return 0

        monkeypatch.setattr(server_mod, "clear_cache", fake_clear_cache)
        monkeypatch.setattr(server_mod, "clear_all_cache", fake_clear_all_cache)

        _dispatch("cache_clear", {"all": value})
        assert calls == ["all"], f"all={value!r} should wipe everything"

    def test_an_unlisted_boolean_string_is_rejected(self):
        """Neither true nor false: guessing would be the original defect again."""
        with pytest.raises(ValueError, match="must be a boolean"):
            server_mod._validate_tool_args("cache_clear", {"all": "maybe"})

    def test_a_missing_boolean_keeps_its_default(self):
        assert server_mod._validate_tool_args("cache_clear", {})["all"] is False

    def test_force_fetcher_unlisted_value_is_rejected(self, monkeypatch):
        """D-02: ``force_fetcher="magic"`` silently ran the stealthy browser.

        The signature declares ``Literal["http", "dynamic", "stealthy"]``, but
        the value only has to miss ``== "http"`` to land in the stealthy
        ``else`` — the heaviest tier, ~5s plus anti-detect overhead, reported as
        a normal success.
        """
        seen = _record(monkeypatch, "smart_fetch", _REJECTED)
        payload = _envelope("smart_fetch", {"url": "https://example.com", "force_fetcher": "magic"})

        assert "not a fetcher tier" in payload["error"]
        assert seen == {}

    @pytest.mark.parametrize("value,expected", [
        ("http", "http"),
        ("stealthy", "stealthy"),
        ("dynamic", "dynamic"),   # documented legacy alias, kept verbatim
        ("HTTP", "http"),
        (" Stealthy ", "stealthy"),
    ])
    def test_force_fetcher_valid_values_are_preserved(self, value, expected):
        assert server_mod._validate_tool_args(
            "smart_fetch", {"force_fetcher": value},
        )["force_fetcher"] == expected

    def test_an_absent_force_fetcher_stays_none(self):
        assert server_mod._validate_tool_args("smart_fetch", {})["force_fetcher"] is None

    def test_smart_crawl_options_force_fetcher_is_validated_too(self):
        """crawl.py forwards it straight to smart_fetch — same silent fallback."""
        with pytest.raises(ValueError, match="not a fetcher tier"):
            _dispatch("smart_crawl", {
                "url": "https://example.com/", "options": {"force_fetcher": "magic"},
            })


# ─── D-13: the schema says what is required ───────────────────────────


class TestToolSchemaRequired:
    def _schema(self, name: str) -> dict:
        for tool in MasterFetchServer._TOOL_DEFS:
            if tool["name"] == name:
                return tool["inputSchema"]
        raise AssertionError(f"no tool def for {name}")

    def test_smart_fetch_declares_one_of_url_or_urls(self):
        """The schema said nothing; the tool rejects a call with neither."""
        schema = self._schema("smart_fetch")
        assert "required" not in schema, "either one alone satisfies the call"
        assert schema["anyOf"] == [{"required": ["url"]}, {"required": ["urls"]}]

    def test_cache_clear_requires_nothing(self):
        """The report listed cache_clear here too; both its args are optional."""
        assert "required" not in self._schema("cache_clear")

    def test_every_other_tool_still_declares_its_required_arg(self):
        for name, required in (
            ("smart_crawl", ["url"]), ("screenshot", ["url"]),
            ("smart_search", ["query"]), ("parse", ["file_path"]),
            ("feed_fetch", ["urls"]), ("resolve_url", ["url"]),
        ):
            assert self._schema(name)["required"] == required


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
        resp = asyncio.run(MasterFetchServer().smart_search("clamp note", max_results=51))

        assert resp.cached is True
        assert "outside the supported 1-50 range" in resp.fetch_hint

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
        resp = asyncio.run(MasterFetchServer().smart_search("clamp note", max_results=10))
        assert "outside the supported" not in resp.fetch_hint

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
        out = asyncio.run(MasterFetchServer().smart_fetch(
            "https://example.com", max_content_chars=499, cache_ttl=0,
        ))
        assert "clamped 499->500" in out.summary

    def test_an_in_range_max_content_chars_is_not_reported(self, monkeypatch):
        _stub_fetch_layer(monkeypatch, ResponseModel(
            url="https://example.com", status=200, content=["short body"],
            content_type="text/html", total_extracted_chars=10,
        ))
        out = asyncio.run(MasterFetchServer().smart_fetch(
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
        asyncio.run(srv.smart_fetch("https://example.com", max_content_chars=499, cache_ttl=0))
        second = asyncio.run(srv.smart_fetch("https://example.com", cache_ttl=0))
        assert "clamped" not in second.summary


# ─── D-11 / D-12: the feed contract ───────────────────────────────────


class TestFeedContract:
    def test_a_rejected_call_is_an_envelope_not_an_mcp_error(self, monkeypatch):
        """D-12: feed raised ValueError while every other tool used an envelope.

        ``call_tool`` turned that into an is_error result whose payload was a
        bare ``{"error": ...}`` — a second error shape for callers to detect,
        with no next_action.
        """
        payload = _envelope("feed_fetch", {"urls": []})
        assert payload["feeds"] == []
        assert payload["error"]
        assert payload["next_action"]

    def test_both_channels_carry_the_same_shape(self, monkeypatch):
        """D-11: content[0].text was a bare array, structured_content a dict."""
        async def fake_feed(self, **kwargs):
            return []

        monkeypatch.setattr(server_mod.MasterFetchServer, "feed_fetch", fake_feed)
        content, structured = _dispatch("feed_fetch", {"urls": ["https://example.test/feed"]})

        assert structured == {"feeds": []}
        assert json.loads(content[0].text) == structured

    def test_a_missing_urls_argument_is_reported_as_an_envelope(self):
        payload = _envelope("feed_fetch", {})
        assert payload["feeds"] == []
        assert "at least one URL" in payload["error"]


# ─── D-10: cache_clear payload ────────────────────────────────────────


class TestCacheClearPayload:
    def test_a_plain_cache_clear_carries_no_engine_health(self, monkeypatch):
        """D-10: every cache_clear shipped ~1KB of per-engine pool state.

        The default call is about the content cache and says nothing about the
        search pool, so the snapshot is now taken only when engine_state=true.
        """
        from dhole_mcp import search_metasearch as ms

        called: list[str] = []

        def fake_snapshot():
            called.append("snapshot")
            return {"brave": {"n": 3, "mean": 0.5}}

        monkeypatch.setattr(ms, "engine_state_snapshot", fake_snapshot, raising=False)

        async def fake_clear_cache(*a, **kw):
            return 0

        monkeypatch.setattr(server_mod, "clear_cache", fake_clear_cache)

        _content, structured = _dispatch("cache_clear", {})
        assert structured["engine_health"] == {}
        assert called == [], "the pool snapshot must not even be taken"

    def test_engine_state_true_still_reports_health(self, monkeypatch):
        from dhole_mcp import search_metasearch as ms

        monkeypatch.setattr(
            ms, "engine_state_snapshot",
            lambda: {"brave": {"n": 3, "mean": 0.5}}, raising=False,
        )
        monkeypatch.setattr(
            ms, "engine_state_reset",
            lambda: {"engines_forgotten": 2, "released_cooldowns": {}}, raising=False,
        )

        async def fake_clear_cache(*a, **kw):
            return 0

        monkeypatch.setattr(server_mod, "clear_cache", fake_clear_cache)

        _content, structured = _dispatch("cache_clear", {"engine_state": True})
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
        out = asyncio.run(MasterFetchServer().smart_fetch(
            "https://example.com/start", cache_ttl=0,
        ))
        assert out.url == "https://example.com/final"
        assert out.original_url == "https://example.com/start"

    def test_an_unchanged_url_leaves_the_field_empty(self, monkeypatch):
        _stub_fetch_layer(monkeypatch, ResponseModel(
            url="https://example.com/page", status=200, content=["body"],
            content_type="text/html", total_extracted_chars=4,
        ))
        out = asyncio.run(MasterFetchServer().smart_fetch(
            "https://example.com/page", cache_ttl=0,
        ))
        assert out.original_url == ""

    def test_a_trailing_slash_is_not_a_redirect(self, monkeypatch):
        """The fetcher adds one itself; reporting that as a redirect is noise."""
        _stub_fetch_layer(monkeypatch, ResponseModel(
            url="https://example.com/page/", status=200, content=["body"],
            content_type="text/html", total_extracted_chars=4,
        ))
        out = asyncio.run(MasterFetchServer().smart_fetch(
            "https://example.com/page", cache_ttl=0,
        ))
        assert out.original_url == ""

    def test_a_host_case_change_is_not_a_redirect(self, monkeypatch):
        _stub_fetch_layer(monkeypatch, ResponseModel(
            url="https://EXAMPLE.com/page", status=200, content=["body"],
            content_type="text/html", total_extracted_chars=4,
        ))
        out = asyncio.run(MasterFetchServer().smart_fetch(
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

    def test_the_wire_description_states_both_halves(self):
        schema_desc = next(
            t["inputSchema"]["properties"]["schema"]["description"]
            for t in MasterFetchServer._TOOL_DEFS if t["name"] == "smart_fetch"
        )
        assert "FIRST match" in schema_desc
        assert "array" in schema_desc
