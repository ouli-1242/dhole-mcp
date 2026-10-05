"""Regressions for the wire-boundary argument types (external report, 2026-09-24).

The report filed web_search as "completely broken, and an MCP restart does not
recover it", with `'<' not supported between instances of 'int' and 'str'` on
every call, and diagnosed the engine cooldown tracker. The state files were
innocent: `engine_state_reset()` followed by a search works (pinned below,
because the wrong diagnosis points at the wrong fix).

What actually raised was `max(1, min(max_results, 50))` in search.py. `min()`
compares its SECOND operand against the first, so a `max_results` that arrived as
the string "6" produces that message verbatim — int on the left, str on the
right. web_read and cache_clear already coerced their arguments at the
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
        payload = _envelope("web_search", {"query": "Knot DNS", "max_results": "8"})

        assert "not supported between instances" not in payload["error"]
        assert payload["total_results"] == 8, "the caller's 8 has to be the 8 it gets"

    def test_page_as_a_string_selects_that_page(self, monkeypatch):
        """The report's `page=1 -> "Invalid page: '1'"`, for an integer 1.

        Rejecting it was also wrong at the layer below: `page` becomes
        `page + 1` for the backends, so it has to arrive as the int it means.
        """
        seen = _stub_search_layer(monkeypatch)
        payload = _envelope("web_search", {"query": "Knot DNS", "page": "1"})

        assert payload["error"] == ""
        assert seen["page"] == 1

    def test_a_null_takes_the_documented_default(self, monkeypatch):
        """`cache_ttl=null` means "not set", not "set to None".

        这条留着是因为它是老事故的另一半：袋子里的 null 会被原样转发，于是
        `if cache_ttl > 0` 读到一个 None 变成 TypeError。B4 拆平后只有一个通道，
        null 一律按"没传"处理，与顶层的读法一致。
        """
        seen = _stub_search_layer(monkeypatch)
        payload = _envelope("web_search",
                            {"query": "Knot DNS", "cache_ttl": None,
                             "max_results": None})

        assert "not supported between instances" not in payload["error"]
        assert seen["ttl"] == search_mod.SEARCH_CACHE_TTL
        assert payload["total_results"] == 6, "max_results fell back to its own default"

    def test_a_junk_number_names_the_argument_instead_of_tracebacking(self, monkeypatch):
        _stub_search_layer(monkeypatch)
        with pytest.raises(ValueError) as exc:
            _dispatch("web_search", {"query": "Knot DNS", "max_results": "abc"})

        assert "max_results must be an integer" in str(exc.value)
        assert "max_results=6" in str(exc.value)

    def test_the_boundary_still_leaves_ranges_to_the_tool(self, monkeypatch):
        """Clamping twice above the tool's own note would hide the note.

        `page` 0-10 belongs to _validate_filters and `max_results` 1-50 to
        search.py, both of which say out loud what they did.
        """
        _stub_search_layer(monkeypatch)
        assert "Invalid page: 99 (0-10)" in _envelope(
            "web_search", {"query": "Knot DNS", "page": 99})["error"]
        over = json.dumps(_envelope("web_search",
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
        seen = _record(monkeypatch, "web_read", ResponseModel(url="", status=0, content=[]))
        _dispatch("web_read", {"url": "https://example.test",
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
        seen = _record(monkeypatch, "web_read", ResponseModel(url="", status=0, content=[]))
        _dispatch("web_read", {"url": "https://example.test",
                                  "confirm": "false", "check_login": "false"})
        assert seen["confirm"] is False
        assert seen["check_login"] is False

        seen = _record(monkeypatch, "web_read", ResponseModel(url="", status=0, content=[]))
        _dispatch("web_read", {"url": "https://example.test", "check_login": "true"})
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
        seen = _record(monkeypatch, "web_crawl", ResponseModel(url="", status=0, content=[]))
        _dispatch("web_crawl", {"url": "https://example.test", "ignore_robots": "false",
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
        seen = _record(monkeypatch, "web_search")
        with pytest.raises(ValueError) as exc:
            _dispatch("web_search", {"query": "q", "engines": "bing"})

        assert "engines must be an array of strings" in str(exc.value)
        assert seen == {}, "a rejected call must not reach the search at all"

    def test_feed_and_resolve_timeouts_arrive_as_numbers(self, monkeypatch):
        seen = _record(monkeypatch, "feed_read", [])
        _dispatch("feed_read", {"urls": ["https://example.test/rss"],
                                 "max_items": "0", "timeout": "30"})
        assert seen["max_items"] == 0
        assert seen["timeout"] == 30


    def test_a_numeric_pdf_page_range_is_not_dropped(self, monkeypatch):
        """`pages` was read only when it was a str, so pages=3 meant no range at
        all — the whole file extracted, with nothing saying so."""
        seen = _record(monkeypatch, "web_read", ResponseModel(url="", status=0, content=[]))
        _dispatch("web_read", {"url": "https://example.test/paper.pdf",
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
        payload = _envelope("sys_state", {"clear": ["cache", "engine_state"]})
        assert payload["engine_state_reset"] is True
        assert ms.cooldowns() == {} and ms._ENGINE_YIELD == {}

        search = _envelope("web_search", {"query": "Knot DNS"})

        assert search["error"] == ""
        assert search["total_results"] == 6
