"""Regressions for the wire-boundary argument types (external report, 2026-09-24).

The report filed smart_search as "completely broken, and an MCP restart does not
recover it", with `'<' not supported between instances of 'int' and 'str'` on
every call, and diagnosed the engine cooldown tracker. The state files were
innocent: `engine_state_reset()` followed by a search works (pinned below,
because the wrong diagnosis points at the wrong fix).

What actually raised was `max(1, min(max_results, 50))` in search.py. `min()`
compares its SECOND operand against the first, so a `max_results` that arrived as
the string "6" produces that message verbatim — int on the left, str on the
right. smart_fetch and cache_clear already coerced their arguments at the
dispatcher because "several MCP clients stringify numbers"; the other six tools
read `args.get()` raw, so a number-typed client that serializes (or echoes a
schema property as null) was one keystroke away from a bare Python traceback.

The same gap has three other outcomes on the same surface, each pinned here: a
boolean a client sends as "false" reading as True, a list sent as one bare string
iterating character by character, and a PDF page range dropped on the floor.

Range policy is deliberately NOT part of this: the boundary converts types only,
and the documented caps stay where they are applied together with the note that
tells the caller the value moved.
"""

import asyncio
import json

import pytest

from dhole_mcp import actions as actions_mod
from dhole_mcp import search as search_mod
from dhole_mcp import server as server_mod
from dhole_mcp.search_engines import EngineReport, RawResult
from dhole_mcp.server import MasterFetchServer, ResponseModel


def _dispatch(tool: str, args: dict):
    return asyncio.run(MasterFetchServer()._dispatch(tool, args))


def _envelope(tool: str, args: dict) -> dict:
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
        payload = _envelope("smart_search", {"query": "Knot DNS", "max_results": "8"})

        assert "not supported between instances" not in payload["error"]
        assert payload["total_results"] == 8, "the caller's 8 has to be the 8 it gets"

    def test_page_as_a_string_selects_that_page(self, monkeypatch):
        """The report's `page=1 -> "Invalid page: '1'"`, for an integer 1.

        Rejecting it was also wrong at the layer below: `page` becomes
        `page + 1` for the backends, so it has to arrive as the int it means.
        """
        seen = _stub_search_layer(monkeypatch)
        payload = _envelope("smart_search", {"query": "Knot DNS", "page": "1"})

        assert payload["error"] == ""
        assert seen["page"] == 1

    def test_a_null_in_the_options_bag_takes_the_documented_default(self, monkeypatch):
        """A top-level null already means "not set"; in the bag it meant TypeError.

        _promote_options skips nulls, but _strict_options forwards them, so
        `options={"cache_ttl": null}` reached `if cache_ttl > 0` as None.
        """
        seen = _stub_search_layer(monkeypatch)
        payload = _envelope("smart_search",
                            {"query": "Knot DNS", "options": {"cache_ttl": None,
                                                              "max_results": None}})

        assert "not supported between instances" not in payload["error"]
        assert seen["ttl"] == search_mod.SEARCH_CACHE_TTL
        assert payload["total_results"] == 6, "max_results fell back to its own default"

    def test_a_junk_number_names_the_argument_instead_of_tracebacking(self, monkeypatch):
        _stub_search_layer(monkeypatch)
        with pytest.raises(ValueError) as exc:
            _dispatch("smart_search", {"query": "Knot DNS", "max_results": "abc"})

        assert "max_results must be an integer" in str(exc.value)
        assert "max_results=6" in str(exc.value)

    def test_the_boundary_still_leaves_ranges_to_the_tool(self, monkeypatch):
        """Clamping twice above the tool's own note would hide the note.

        `page` 0-10 belongs to _validate_filters and `max_results` 1-50 to
        search.py, both of which say out loud what they did.
        """
        _stub_search_layer(monkeypatch)
        assert "Invalid page: 99 (0-10)" in _envelope(
            "smart_search", {"query": "Knot DNS", "page": 99})["error"]
        over = json.dumps(_envelope("smart_search",
                                    {"query": "Knot DNS", "max_results": 100}))
        assert "max_results=100 is outside the supported 1-50" in over


class TestOtherToolsWithTheSameGap:
    """Every tool that read args.get() raw, not just the one that was reported."""

    def test_a_stringified_false_is_not_true_for_the_browser_knobs(self, monkeypatch):
        """`headless="false"` used to keep the browser headless anyway."""
        seen = _record(monkeypatch, "smart_fetch", ResponseModel(url="", status=0, content=[]))
        _dispatch("smart_fetch", {"url": "https://example.test",
                                  "options": {"headless": "false",
                                              "main_content_only": "false",
                                              "include_links": "true"}})

        assert seen["headless"] is False
        assert seen["main_content_only"] is False
        assert seen["include_links"] is True

    def test_screenshot_full_page_accepts_the_string_spellings(self, monkeypatch):
        seen = _record(monkeypatch, "screenshot", [])
        _dispatch("screenshot", {"url": "https://example.test",
                                 "options": {"full_page": "false", "quality": "80"}})

        assert seen["full_page"] is False
        assert seen["quality"] == 80

    def test_crawl_caps_arrive_as_numbers_for_the_code_that_counts(self, monkeypatch):
        """`max_pages="5"` reaches `range(max_pages)`; discover_only="false"
        reached `if discover_only:` and answered with a URL map instead."""
        seen = _record(monkeypatch, "smart_crawl", ResponseModel(url="", status=0, content=[]))
        _dispatch("smart_crawl", {"url": "https://example.test", "discover_only": "false",
                                  "options": {"max_pages": "5", "deadline_ms": "90000"}})

        assert seen["max_pages"] == 5
        assert seen["deadline_ms"] == 90000
        assert seen["discover_only"] is False

    def test_crawl_urls_as_a_bare_string_is_rejected_before_any_fetch(self, monkeypatch):
        """`crawl_urls="https://x"` iterated into ten one-character URLs.

        The list case with the worst outcome, because each character is handed to
        the fetcher as a candidate URL.
        """
        seen = _record(monkeypatch, "smart_crawl")
        with pytest.raises(ValueError) as exc:
            _dispatch("smart_crawl", {"url": "https://example.test",
                                      "options": {"crawl_urls": "https://example.test/a"}})

        assert "crawl_urls must be an array of strings" in str(exc.value)
        assert seen == {}, "a rejected call must not reach the crawl at all"

    def test_feed_and_resolve_timeouts_arrive_as_numbers(self, monkeypatch):
        seen = _record(monkeypatch, "feed_fetch", [])
        _dispatch("feed_fetch", {"urls": ["https://example.test/rss"],
                                 "max_items": "0", "timeout": "30"})
        assert seen["max_items"] == 0
        assert seen["timeout"] == 30

        seen2 = _record(monkeypatch, "resolve_url", {})
        _dispatch("resolve_url", {"url": "https://example.test", "timeout": "1.5"})
        assert seen2["timeout"] == 1.5

    def test_a_numeric_pdf_page_range_is_not_dropped(self, monkeypatch):
        """`pages` was read only when it was a str, so pages=3 meant no range at
        all — the whole file extracted, with nothing saying so."""
        seen = _record(monkeypatch, "smart_fetch", ResponseModel(url="", status=0, content=[]))
        _dispatch("smart_fetch", {"url": "https://example.test/paper.pdf",
                                  "options": {"pages": 3, "password": 1234}})

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

        assert out == [{"scroll": 2}, {"wait": 500}]
        assert actions_mod._validate_actions([{"wait": "1000"}]) == [{"wait": 1000}]

    def test_actions_still_want_one_key_per_object_and_say_so(self):
        """Not a defect — but the rule has to be on the wire, not only in code."""
        with pytest.raises(ValueError) as exc:
            actions_mod._validate_actions([{"scroll": 3, "wait": 1000}])
        assert "exactly one key" in str(exc.value)

        tool = next(td for td in MasterFetchServer._TOOL_DEFS if td["name"] == "smart_fetch")
        assert "ONE action key per object" in tool["inputSchema"]["properties"]["actions"]["description"]

    def test_engine_state_reset_leaves_the_next_search_working(self, monkeypatch):
        """The report's restart-proof crash was never the engine pool.

        It filed `cache_clear(engine_state=true)` as the trigger and pointed at
        the cooldown tracker's `last` field. Resetting the pool and searching
        again is the exact sequence, and it works with the state files in the
        condition the reset leaves them in.
        """
        from dhole_mcp import search_metasearch as ms

        async def fake_clear_all():
            return 0

        monkeypatch.setattr(server_mod, "clear_all_cache", fake_clear_all)
        _stub_search_layer(monkeypatch)
        payload = _envelope("cache_clear", {"all": True, "engine_state": True})
        assert payload["engine_state_reset"] is True
        assert ms.cooldowns() == {} and ms._ENGINE_YIELD == {}

        search = _envelope("smart_search", {"query": "Knot DNS"})

        assert search["error"] == ""
        assert search["total_results"] == 6
