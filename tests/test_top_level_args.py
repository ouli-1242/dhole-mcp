"""Top-level arguments either reach the tool or fail loudly - never vanish.

Found while re-testing the third report's crawl item live. The dispatcher read
only url/discover_only/focus/crawl_urls/search + options, so every other
top-level key was dropped on the floor: `max_pages=3` crawled 10 pages and
`path_include="/tag/"` filtered nothing, with no error, no warning and nothing
in the reply to say the cap never applied. The tool descriptions name those
params without saying "in options" ("Caps: max_pages(10), max_depth(2)...").

Two guards, same principle as _strict_options one level up:

* a key the tool documents may be passed top-level (top-level wins over the
  options bag, which stays the documented home), and
* a key it does not accept raises, listing what is supported.
"""

import asyncio

import pytest

from dhole_mcp import server as server_mod
from dhole_mcp.crawl import CrawlResponseModel
from dhole_mcp.search import SearchResponseModel
from dhole_mcp.server import MasterFetchServer

_CRAWL_OK = CrawlResponseModel(start_url="https://example.com/", pages=[])
_SEARCH_OK = SearchResponseModel(query="q", results=[])


def _stub(monkeypatch, method: str, result) -> dict:
    """Stub a tool method; return the dict it records its kwargs into."""
    seen: dict = {}

    async def fake(self, **kwargs):
        seen.update(kwargs)
        return result

    monkeypatch.setattr(server_mod.MasterFetchServer, method, fake)
    return seen


def _dispatch(tool: str, args: dict):
    return asyncio.run(MasterFetchServer()._dispatch(tool, args))


# A page whose anchors are same-domain links, no assets to trip the filter.
_LINKED_PAGE = (
    "<html><head><title>Start</title></head><body>"
    "<p>" + "Real body text so the extractor has something to chew on. " * 3 + "</p>"
    '<a href="/a">Alpha</a> <a href="/b">Beta</a> <a href="/c">Gamma</a>'
    "</body></html>"
)


class TestCrawlTopLevelArgs:
    """The report's wire shape: caps and path filters passed top-level."""

    def test_a_top_level_cap_reaches_the_crawl(self, monkeypatch):
        seen = _stub(monkeypatch, "smart_crawl", _CRAWL_OK)
        _dispatch("smart_crawl", {"url": "https://example.com/", "max_pages": 3,
                                  "max_depth": 1, "cache_ttl": 0})
        assert seen["max_pages"] == 3
        assert seen["max_depth"] == 1
        assert seen["cache_ttl"] == 0

    def test_a_top_level_string_filter_reaches_the_crawl(self, monkeypatch):
        seen = _stub(monkeypatch, "smart_crawl", _CRAWL_OK)
        _dispatch("smart_crawl", {"url": "https://example.com/", "path_exclude": "/what/"})
        assert seen["path_exclude"] == "/what/"

    def test_top_level_wins_over_the_options_bag(self, monkeypatch):
        seen = _stub(monkeypatch, "smart_crawl", _CRAWL_OK)
        _dispatch("smart_crawl", {
            "url": "https://example.com/", "max_pages": 3,
            "options": {"max_pages": 9, "max_depth": 1},
        })
        assert seen["max_pages"] == 3, "顶层应优先（与 smart_fetch 的 promote 先例一致）"
        assert seen["max_depth"] == 1, "options 里的其余键照旧生效"

    def test_the_options_bag_alone_still_works(self, monkeypatch):
        seen = _stub(monkeypatch, "smart_crawl", _CRAWL_OK)
        _dispatch("smart_crawl", {
            "url": "https://example.com/", "options": {"sitemap": True, "max_pages": 5},
        })
        assert seen["sitemap"] is True and seen["max_pages"] == 5


class TestOtherToolsTakeTheirDocumentedKeysTopLevel:
    def test_search_filters(self, monkeypatch):
        seen = _stub(monkeypatch, "smart_search", _SEARCH_OK)
        _dispatch("smart_search", {"query": "x", "max_results": 8, "engines": ["brave"],
                                   "freshness": "week", "site": "example.com"})
        assert (seen["max_results"], seen["engines"], seen["freshness"], seen["site"]) == (
            8, ["brave"], "week", "example.com")

    def test_screenshot_options(self, monkeypatch):
        seen = _stub(monkeypatch, "screenshot", [])
        _dispatch("screenshot", {"url": "https://example.com/", "full_page": True,
                                 "image_type": "jpeg"})
        assert seen["full_page"] is True and seen["image_type"] == "jpeg"

    def test_fetch_bag_options_that_used_to_be_dropped(self, monkeypatch):
        from dhole_mcp.server import ResponseModel

        seen: dict = {}

        async def fake(self, **kwargs):
            seen.update(kwargs)
            return ResponseModel(url=kwargs.get("url", ""), status=200, content=["x"])

        monkeypatch.setattr(server_mod.MasterFetchServer, "smart_fetch", fake)
        _dispatch("smart_fetch", {"url": "https://example.com/", "include_links": True,
                                  "cookies": "sessionid=abc123"})
        assert seen["include_links"] is True
        assert seen["cookies"] == "sessionid=abc123"


class TestUnknownTopLevelArgsRaise:
    """A key that is not a param must not look like it applied."""

    def test_a_typo_names_the_key_and_the_supported_set(self):
        with pytest.raises(ValueError) as exc:
            _dispatch("smart_crawl", {"url": "https://example.com/", "path_exlucde": "/what/"})
        message = str(exc.value)
        assert "path_exlucde" in message
        assert "max_pages" in message and "path_exclude" in message

    @pytest.mark.parametrize("tool,args", [
        ("smart_crawl", {"url": "https://example.com/", "paths": ["/a"]}),
        ("smart_search", {"query": "x", "sites": ["example.com"]}),
        ("smart_fetch", {"url": "https://example.com/", "max_chars": 100}),
        ("screenshot", {"url": "https://example.com/", "width": 800}),
        ("cache_clear", {"all": True, "everything": True}),
        ("parse", {"file_path": "a.pdf", "pages": "1-2"}),
        ("feed_fetch", {"urls": ["https://example.com/f.xml"], "limit": 5}),
        ("resolve_url", {"url": "https://example.com/", "follow": True}),
    ])
    def test_every_tool_rejects_its_own_unknown_keys(self, tool, args):
        with pytest.raises(ValueError) as exc:
            _dispatch(tool, args)
        assert "Unsupported argument" in str(exc.value)

    def test_unknown_tool_still_says_unknown_tool(self):
        with pytest.raises(ValueError) as exc:
            _dispatch("smart_frobnicate", {"url": "https://example.com/"})
        assert "Unknown tool" in str(exc.value)

    def test_null_valued_keys_mean_not_set(self, monkeypatch):
        """Clients that echo every schema property as null must not be rejected."""
        seen = _stub(monkeypatch, "smart_crawl", _CRAWL_OK)
        _dispatch("smart_crawl", {"url": "https://example.com/", "focus": None,
                                  "crawl_urls": None, "nonsense": None})
        assert seen["focus"] is None and seen["crawl_urls"] is None

    def test_null_valued_options_alone_is_fine(self, monkeypatch):
        seen = _stub(monkeypatch, "smart_crawl", _CRAWL_OK)
        _dispatch("smart_crawl", {"url": "https://example.com/", "options": None,
                                  "max_pages": 2})
        assert seen["max_pages"] == 2


class TestAcceptedSetCoversTheContract:
    def test_every_declared_schema_property_is_accepted(self):
        """A property the tool advertises can never be a rejected call."""
        for td in MasterFetchServer._TOOL_DEFS:
            declared = set((td.get("inputSchema") or {}).get("properties") or {})
            allowed = set(server_mod._TOP_LEVEL_ARGS[td["name"]])
            assert declared <= allowed, td["name"]

    def test_every_documented_option_is_accepted(self):
        assert server_mod._SC_OPTIONS <= server_mod._TOP_LEVEL_ARGS["smart_crawl"]
        assert server_mod._SS_OPTIONS <= server_mod._TOP_LEVEL_ARGS["smart_search"]
        assert server_mod._SHOT_OPTIONS <= server_mod._TOP_LEVEL_ARGS["screenshot"]
        assert server_mod._SF_OPTIONS_ALLOWED <= server_mod._TOP_LEVEL_ARGS["smart_fetch"]

    def test_every_tool_in_the_schema_is_covered(self):
        names = {td["name"] for td in MasterFetchServer._TOOL_DEFS}
        assert names == set(server_mod._TOP_LEVEL_ARGS)


class TestParseCwdReachesTheTool:
    """parse 的 cwd 是 15.0 新增的顶层参数：它必须真的抵达方法。

    这个文件存在的理由就是「顶层键静默消失」那类事故 —— 新参数要走同一条守卫，
    否则 agent 传了 cwd、相对路径照样解析到宿主安装目录，且答复里没有任何异常。
    """

    def test_cwd_is_forwarded(self, monkeypatch):
        seen = _stub(monkeypatch, "parse",
                     server_mod.ResponseModel(url="file:///x", status=0, content=[]))
        _dispatch("parse", {"file_path": "notes.csv", "cwd": "/tmp/session"})
        assert seen["file_path"] == "notes.csv"
        assert seen["cwd"] == "/tmp/session"

    def test_omitted_cwd_stays_none(self, monkeypatch):
        """不传就是 None（继续走 DHOLE_WORKDIR / 进程 cwd），不是空串或报错。"""
        seen = _stub(monkeypatch, "parse",
                     server_mod.ResponseModel(url="file:///x", status=0, content=[]))
        _dispatch("parse", {"file_path": "notes.csv"})
        assert seen["cwd"] is None


class TestTopLevelFilterAppliesEndToEnd:
    """Dispatch -> crawl -> link filter, with the filter passed top-level."""

    @staticmethod
    def _crawl(monkeypatch, **top_level):
        async def fake_smart_fetch(self, url=None, **ignored):
            return server_mod.ResponseModel(
                url=url or "", status=200, content=[_LINKED_PAGE],
                fetcher_used="http", content_ok=True, content_type="text/html")

        monkeypatch.setattr(server_mod.MasterFetchServer, "smart_fetch", fake_smart_fetch)
        text, structured = _dispatch("smart_crawl", {
            "url": "https://example.com/", "max_pages": 5, "max_depth": 1,
            "force_fetcher": "http", **top_level})
        return structured

    def test_string_exclude_top_level_drops_the_path(self, monkeypatch):
        got = self._crawl(monkeypatch, path_exclude="/a")
        urls = [p["url"] for p in got["pages"]]
        assert got["pages_crawled"] == 3
        assert not any(u.endswith("/a") for u in urls)
        assert any(u.endswith("/b") for u in urls)

    def test_top_level_and_options_forms_agree(self, monkeypatch):
        top = self._crawl(monkeypatch, path_exclude="/a")
        bag = self._crawl(monkeypatch, options={"path_exclude": "/a"})
        assert top["pages_crawled"] == bag["pages_crawled"] == 3
        assert [p["url"] for p in top["pages"]] == [p["url"] for p in bag["pages"]]
