"""G22（可做的那一半）：`options` 是规范位置，两种放法必须一模一样。

报告的原始抱怨是**参数放置不一致**——一半参数顶层、一半只能在 options 里，而
`wait_selector` 这类写在文档里的键放进 options 还会被拒。这一半的修复不需要大版本：

* 文档把 `options` 说成规范位置（顶层是兼容读法，同一参数两种放法等价）；
* 这条等价性**由测试钉住**，而且覆盖面是从四个工具的选项集合**算出来**的：以后
  任何新选项进了 `_SF_OPTIONS_ALLOWED` / `_SC_OPTIONS` / `_SS_OPTIONS` /
  `_SHOT_OPTIONS`，它就自动被这一组用例覆盖 —— 不需要谁记得再写一条。

破坏性的那一半（移除顶层通道）留在大版本：见 REPLAN 的 B4，本轮不动。
"""

from __future__ import annotations

import asyncio

import pytest

from dhole_mcp import server as server_mod
from dhole_mcp.crawl import CrawlResponseModel
from dhole_mcp.search import SearchResponseModel
from dhole_mcp.server import (
    MasterFetchServer,
    _SC_OPTIONS,
    _SC_OPTIONS_FORWARDED,
    _SF_OPTIONS_ALLOWED,
    _SF_OPTIONS_FORWARDED,
    _SHOT_OPTIONS,
    _SS_OPTIONS,
    ResponseModel,
)

_CRAWL_OK = CrawlResponseModel(start_url="https://example.com/", pages=[])
_SEARCH_OK = SearchResponseModel(query="q", results=[])
_FETCH_OK = ResponseModel(url="https://example.com/", status=200, content=["body"],
                          fetcher_used="http")

# tool -> (base args, allowed keys, forwarded keys, the result the stub returns)
TOOLS: dict[str, tuple[dict, frozenset, frozenset, object]] = {
    "smart_fetch": ({"url": "https://example.com/"}, _SF_OPTIONS_ALLOWED,
                    _SF_OPTIONS_FORWARDED, _FETCH_OK),
    "smart_crawl": ({"url": "https://example.com/"}, _SC_OPTIONS,
                    _SC_OPTIONS_FORWARDED, _CRAWL_OK),
    "smart_search": ({"query": "python asyncio"}, _SS_OPTIONS, _SS_OPTIONS,
                     _SEARCH_OK),
    # screenshot answers with a list of content parts, not a model; the stub's
    # return value is never read here, only the kwargs it was called with.
    "screenshot": ({"url": "https://example.com/"}, _SHOT_OPTIONS, _SHOT_OPTIONS, []),
}

URL = "https://example.com/"

# One probe value per option key. Values have to survive the wire-level coercion
# in `_validate_tool_args`, so they are real shapes rather than `"x"` everywhere.
VALUES: dict[str, object] = {
    # smart_fetch
    "css_selector": "p",
    "max_content_chars": 5000,
    "timeout": 20000,
    "pages": "1-2",
    "password": "pw",
    "schema": {"properties": {"t": {"selector": "p"}}},
    "proxy": "http://proxy.test:8080",
    "cookies": {"a": "1"},
    "extra_headers": {"X-Probe": "1"},
    "useragent": "dhole-probe/1",
    "wait": 100,
    "max_links": 5,
    "session_id": "probe-session",
    "method": "GET",
    "body": {"a": 1},
    "content_type": "application/json",
    "auth": {"type": "basic", "username": "u", "password": "p"},
    "if_modified_since": "2026-09-20",
    "if_none_match": "probe-etag",
    "focus": "topic",
    "actions": [{"wait": 1}],
    "cache_ttl": 300,
    # Probes that a dropped value would still pass are no probe: an
    # extraction_type/offset equal to the tool's own default is invisible in the
    # comparison, so these carry values the default could not produce.
    "extraction_type": "html",
    "force_fetcher": "http",
    "offset": 100,
    # Bulk mode: the one parameter whose drop changes which code runs at all.
    "urls": [URL + "a", URL + "b"],
    # smart_crawl
    "max_pages": 3,
    "max_depth": 1,
    "path_include": "/a",
    "path_exclude": "/b",
    "max_content_chars_per": 2000,
    "max_total_chars": 20000,
    "concurrency": 2,
    "deadline_ms": 60000,
    "sitemap": True,
    "search": "topic",
    "crawl_urls": [URL + "a"],
    "delay": 1.0,
    # smart_search
    "max_results": 5,
    "mode": "auto",
    "engines": ["bing"],
    "site": "example.com",
    "exclude_sites": ["other.example"],
    "location": "cn",
    "language": "zh",
    "region": "cn-zh",
    "page": 1,
    "freshness": "week",
    "after": "2026-09-20",
    "before": "2026-01-01",
    "url": URL,
    "fetch_content": True,
    "fetch_schema": {"properties": {"t": {"selector": "p"}}},
    "min_relevance": 0.2,
    "min_raw_relevance": 0.1,
    # screenshot
    "quality": 80,
    "save_to": "shot.png",
    "image_type": "png",
    "wait_selector": ".item",
    "full_page": True,
}
# Plain booleans share one value; listing them keeps the coverage check honest.
BOOL_KEYS = {
    "network_idle", "headless", "real_chrome", "main_content_only", "use_trafilatura",
    "solve_cloudflare", "block_webrtc", "hide_canvas", "include_media",
    "include_links", "ignore_robots", "allow_private", "discover_only",
    "full_page", "sitemap",
}
for _k in BOOL_KEYS:
    VALUES.setdefault(_k, True)


def _all_keys(tool: str) -> frozenset:
    return TOOLS[tool][1]


def _capture(monkeypatch, tool: str) -> list[dict]:
    """Stub `tool` on the server and record the kwargs it is actually called with.

    The request-context fingerprint travels too: it is computed by the decorator
    from the promoted arguments, so two forms that differed anywhere the fetch
    cares about (headers, cookies, session, credential) would differ here.
    """
    calls: list[dict] = []
    result = TOOLS[tool][3]

    async def fake(self, **kwargs):
        snap = dict(kwargs)
        if tool == "smart_fetch":
            snap["__ctx__"] = server_mod._CACHE_CTX.get()
        calls.append(snap)
        return result

    monkeypatch.setattr(MasterFetchServer, tool, fake)
    return calls


def _dispatch(tool: str, args: dict):
    return asyncio.run(MasterFetchServer()._dispatch(tool, args))


class TestTheValueTableCoversTheSurface:

    def test_every_option_key_has_a_probe_value(self):
        """新选项进了集合就必须进这张表，否则它躲过整组等价性用例。"""
        missing = sorted({k for tool in TOOLS for k in _all_keys(tool)} - set(VALUES))
        assert not missing, f"add probe values for {missing} to VALUES"

    def test_the_probe_table_names_no_key_that_does_not_exist(self):
        """表里不许留着没有工具接受的键：它会让人以为某个拼写还有效。

        Subtracted set is the union of every tool's ACCEPTED top-level keys, which
        is wider than the option bags: `crawl_urls` / `discover_only` / `focus` /
        `actions` are promoted first-class parameters, and a probe value for them
        belongs here even though they are not bag keys.
        """
        from dhole_mcp.server import _TOP_LEVEL_ARGS

        accepted = set().union(*_TOP_LEVEL_ARGS.values())
        extra = sorted(set(VALUES) - accepted)
        assert not extra, f"VALUES names keys no tool accepts: {extra}"


class TestTheTwoFormsAgree:

    @pytest.mark.parametrize("tool,key",
                             [(tool, key) for tool in sorted(TOOLS)
                              for key in sorted(_all_keys(tool))])
    def test_top_level_and_options_arrive_identically(self, monkeypatch, tool, key):
        base = TOOLS[tool][0]
        value = VALUES[key]
        calls = _capture(monkeypatch, tool)

        _dispatch(tool, {**base, key: value})
        top_form = calls[-1]
        _dispatch(tool, {**base, "options": {key: value}})
        bag_form = calls[-1]

        assert top_form == bag_form, (
            f"{tool}.{key}: top-level reached {top_form}, the options bag reached "
            f"{bag_form}")

    def test_top_level_wins_when_both_are_given(self, monkeypatch):
        """两种放法同时给：顶层是明确的那一个，文档承诺的就是这个次序。"""
        calls = _capture(monkeypatch, "smart_crawl")
        _dispatch("smart_crawl", {"url": URL, "max_pages": 2,
                                  "options": {"max_pages": 9}})
        assert calls[-1]["max_pages"] == 2


class TestTheSeamItself:
    """纯函数层面的等价：不经过桩，直接看两个通道的合并结果。"""

    @pytest.mark.parametrize("tool", sorted(TOOLS))
    def test_promotion_produces_the_same_kw_dict(self, tool):
        base, allowed, forwarded = TOOLS[tool][0], TOOLS[tool][1], TOOLS[tool][2]
        from dhole_mcp.server import _promote_options, _strict_options

        for key in sorted(allowed):
            if key not in forwarded:
                continue
            value = VALUES[key]
            via_top = _strict_options(
                _promote_options({**base, key: value}, {}, forwarded),
                allowed, forwarded, tool)
            via_bag = _strict_options(
                _promote_options(dict(base), {key: value}, forwarded),
                allowed, forwarded, tool)
            assert via_top == via_bag == {key: value}, f"{tool}.{key}"


class TestAbsentMeansTheToolsOwnDefault:
    """「没传」不能翻成「显式传了 None」。

    复测报的形状：不带 ``cache_ttl=0`` 的 ``smart_fetch`` 全部失败，
    ``NOT NULL constraint failed: cache.extraction_type``。参数提升到分支里用一张表
    统一挑之后，没传的键也各自拿到一个 None，于是顶掉了签名里那些**不是 None** 的
    默认值 —— ``extraction_type`` 的默认是 "markdown"，None 一路进到缓存 INSERT。
    整组等价性用例全绿，因为它们都显式给了被测的那个键；而缓存的实测用例是直接调
    ``smart_fetch`` 的，绕开了 dispatcher 这道缝。
    """

    def test_nothing_unsupplied_arrives_as_an_explicit_none(self, monkeypatch):
        import inspect

        # The real signature, read BEFORE the stub replaces the method: taking it
        # afterwards yields **kwargs, so every lookup misses and the assertion can
        # never fail.
        defaults = inspect.signature(MasterFetchServer.smart_fetch).parameters
        calls = _capture(monkeypatch, "smart_fetch")
        _dispatch("smart_fetch", {"url": URL})
        offenders = {}
        for key, value in calls[-1].items():
            param = defaults.get(key)
            if value is None and param is not None and param.default is not None:
                offenders[key] = param.default
        assert not offenders, (
            f"这些参数没被传，却以 None 到达了方法，顶掉了默认值: {offenders}")

    def test_extraction_type_and_offset_keep_their_documented_defaults(
            self, monkeypatch):
        calls = _capture(monkeypatch, "smart_fetch")
        _dispatch("smart_fetch", {"url": URL})
        arrived = calls[-1]
        assert arrived.get("extraction_type", "markdown") == "markdown"
        assert arrived.get("offset", 0) == 0
        # cache_ttl 是这条规则的例外：没传就是「让服务端决定」，所以它不该出现
        assert "cache_ttl" not in arrived, "顶掉了 --cache-ttl 配置"

    def test_a_default_fetch_through_the_dispatcher_writes_the_cache(self):
        """报告的复现：默认 cache_ttl 下两次同样的调用，第二次必须命中缓存而不是报错。"""
        with _served_page() as url:
            def call() -> dict:
                return asyncio.run(MasterFetchServer()._dispatch(
                    "smart_fetch", {"url": url, "allow_private": True}))[1]

            first, second = call(), call()

        assert first["error"] == "", f"默认（缓存）抓取失败：{first['error'][:120]}"
        assert not first.get("cached", False)
        assert second["cached"] is True, (
            f"缓存没写进去或没命中：{second['error'][:120]}")


def _served_page() -> "_ServedPage":
    """A loopback page with real text, so nothing else fails the fetch first."""
    return _ServedPage()


class _ServedPage:
    def __enter__(self):
        import http.server
        import socketserver
        import threading

        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = (b"<html><body><article><h1>Probe</h1><p>"
                        + b"loopback probe page body text. " * 30
                        + b"</p></article></body></html>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                outer.hits += 1
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        outer.hits = 0
        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.srv.daemon_threads = True
        self.srv_thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.srv_thread.start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/probe"
        return self.url

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.srv.server_close()
        return False


class TestTheDocumentationSaysWhichIsCanonical:

    def test_the_wire_table_says_options_carries_every_parameter_except_url(self):
        """`url` 是「这次调用抓哪一页」，不是选项：放进 options 会被当陌生键拒掉。

        其余顶层参数两处都接受，所以表里必须这么说 —— 这句就是客户端唯一能看到的
        放置规则说明（逐参数表已删）。
        """
        tools = {t["name"]: t for t in MasterFetchServer._TOOL_DEFS}
        bag = tools["smart_fetch"]["inputSchema"]["properties"]["options"]["description"]
        for key in ("cache_ttl", "offset", "focus", "actions", "extraction_type",
                    "force_fetcher", "urls"):
            assert key in bag, f"options 表里查不到 {key}，调用方就只会继续往顶层放"
        assert "Every parameter of this tool except url" in bag

    def test_the_server_default_cache_ttl_survives_the_dispatcher(self, monkeypatch):
        """``--cache-ttl`` 在 MCP 这条路上必须是活的。

        分支曾把 cache_ttl 硬写成模块默认值（3600）再传下去，于是服务端配置的 TTL
        永远读不到 —— 正是 16.0 在方法入口用 None 哨兵修掉的那个毛病，从另一头又焊
        了回来。不传就是「让服务端决定」，这一条不能被 dispatcher 代答。
        """
        seen: dict = {}

        async def fake(self, url, *a, **kw):
            seen.update(kw)
            return ResponseModel(url=url, status=200, content=["x"])

        monkeypatch.setattr(MasterFetchServer, "_auto_escalate", fake)
        server = MasterFetchServer(cache_ttl=7)
        asyncio.run(server._dispatch("smart_fetch", {"url": URL}))
        assert seen["cache_ttl"] == 7, "服务端配置的 TTL 被 dispatcher 代答成了模块默认值"

        seen.clear()
        asyncio.run(server._dispatch("smart_fetch",
                                     {"url": URL, "options": {"cache_ttl": 0}}))
        assert seen["cache_ttl"] == 0, "显式的 cache_ttl=0 必须仍然绕过缓存"


    def test_readme_says_where_options_live_and_which_form_wins(self):
        """README 要说清三件事：袋是谁的选项家、顶层是兼容读法且顶层优先、
        以及另五个工具根本没有袋。

        逐参数抄一份表已经删掉了 —— 那份表的内容客户端从 `tools/list` 本来就拿得到，
        抄一次就漂一次；这里守的是那句**结论**，不是某个小节标题。守的还是结论
        而不是措辞：把「每个工具的选项都写在 options 里」写成普适规则曾经就是错的，
        所以「只有顶层这一层」这一句和袋的那一句同等重要。
        """
        from pathlib import Path
        text = (Path(__file__).resolve().parents[1] / "README.md").read_text(
            encoding="utf-8")
        assert "顶层优先" in text
        assert "`options` 对象里" in text, "四个带袋的工具没说清"
        assert "只有顶层这一层" in text, "另五个工具不接受 options 袋这件事没写"
        assert "discover_only" in text, "只能放顶层的反向例外（smart_crawl）没点名"

    def test_the_wire_text_for_each_tool_points_at_options(self):
        tools = {t["name"]: t for t in MasterFetchServer._TOOL_DEFS}
        for tool in ("smart_fetch", "smart_crawl", "smart_search"):
            assert "options" in tools[tool]["description"], tool


class TestARejectionNamesTheOtherChannel:
    """袋里放了一个只能放顶层的参数时，报错要负责指路。

    smart_crawl 的 `discover_only` 是真的能力，只是不住在袋里。只回一句
    "Unsupported option key(s) ... Supported keys: [...]" 的话，读的人唯一的
    结论就是「这工具没有地图模式」——于是这条守的不是拒绝本身，而是那句指路。
    """

    def test_a_top_level_parameter_in_the_bag_is_pointed_at_the_top_level(self):
        with pytest.raises(ValueError) as got:
            asyncio.run(MasterFetchServer()._dispatch(
                "smart_crawl", {"url": "https://example.com/",
                                "options": {"discover_only": True}}))
        msg = str(got.value)
        assert "discover_only" in msg, msg
        assert "top-level parameter" in msg and "move it out" in msg, \
            f"报错列了支持的键，却没说这个键其实住在哪：{msg}"

    def test_a_key_that_lives_nowhere_does_not_get_a_fake_address(self):
        """指路必须是准的：不存在的键不能被说成顶层参数。"""
        with pytest.raises(ValueError) as got:
            asyncio.run(MasterFetchServer()._dispatch(
                "smart_crawl", {"url": "https://example.com/",
                                "options": {"frobnicate": True}}))
        assert "top-level parameter" not in str(got.value)
