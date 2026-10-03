"""Top-level arguments either reach the tool or fail loudly - never vanish.

Found while re-testing the third report's crawl item live. The dispatcher read
only url/discover_only/focus/search + the options bag, so every other
top-level key was dropped on the floor: `max_pages=3` crawled 10 pages and
`path_include="/tag/"` filtered nothing, with no error, no warning and nothing
in the reply to say the cap never applied. The tool descriptions name those
params without saying "in options" ("Caps: max_pages(10), max_depth(2)...").

Two guards, same principle as `_reject_unknown_args` one level up:

* a key the tool documents is passed top-level — 17.0 removed the bag, so that
  is now the only channel, and
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
        seen = _stub(monkeypatch, "web_crawl", _CRAWL_OK)
        _dispatch("web_crawl", {"url": "https://example.com/", "max_pages": 3,
                                  "max_depth": 1, "cache_ttl": 0})
        assert seen["max_pages"] == 3
        assert seen["max_depth"] == 1
        assert seen["cache_ttl"] == 0

    def test_a_top_level_string_filter_reaches_the_crawl(self, monkeypatch):
        seen = _stub(monkeypatch, "web_crawl", _CRAWL_OK)
        _dispatch("web_crawl", {"url": "https://example.com/", "path_exclude": "/what/"})
        assert seen["path_exclude"] == "/what/"

    def test_a_top_level_bool_reaches_the_crawl(self, monkeypatch):
        seen = _stub(monkeypatch, "web_crawl", _CRAWL_OK)
        _dispatch("web_crawl", {
            "url": "https://example.com/", "sitemap": True, "max_pages": 5,
        })
        assert seen["sitemap"] is True and seen["max_pages"] == 5


class TestOtherToolsTakeTheirDocumentedKeysTopLevel:
    def test_search_filters(self, monkeypatch):
        seen = _stub(monkeypatch, "web_search", _SEARCH_OK)
        _dispatch("web_search", {"query": "x", "max_results": 8, "engines": ["brave"],
                                   "freshness": "week", "site": "example.com"})
        assert (seen["max_results"], seen["engines"], seen["freshness"], seen["site"]) == (
            8, ["brave"], "week", "example.com")

    def test_screenshot_keys(self, monkeypatch):
        seen = _stub(monkeypatch, "web_screenshot", [])
        _dispatch("web_screenshot", {"url": "https://example.com/", "full_page": True,
                                 "image_type": "jpeg"})
        assert seen["full_page"] is True and seen["image_type"] == "jpeg"

    def test_fetch_keys_that_used_to_be_dropped(self, monkeypatch):
        from dhole_mcp.server import ResponseModel

        seen: dict = {}

        async def fake(self, **kwargs):
            seen.update(kwargs)
            return ResponseModel(url=kwargs.get("url", ""), status=200, content=["x"])

        # 17.0 §3-17 之前这条用的是 `cookies`：它和 auth/proxy/extra_headers/
        # allow_private 一起从每次调用里搬走了，现在住在 cred_defaults（读回的路径
        # 由 test_cred_defaults_* 那组守）。这里换成一颗留下来的字符串键，守卫的
        # 形状不变：一个顶层名字要么抵达方法，要么当场拒绝。
        monkeypatch.setattr(server_mod.MasterFetchServer, "web_read", fake)
        _dispatch("web_read", {"url": "https://example.com/", "include_links": True,
                                  "css_selector": "h1"})
        assert seen["include_links"] is True
        assert seen["css_selector"] == "h1"


class TestTheIdentityKeysAreGoneFromTheSurface:
    """17.0 §3-17：五颗身份参数搬进 cred_defaults，旧写法必须以名字被拒。

    「删掉了」的可验收形式不是 schema 少一行，而是老调用当场报错：一个被静默忽略的
    `cookies=` 等于让调用方以为自己带着登录态取到了那一页。留在每次调用里的只有
    `cdp_url` / `session_id` / `anti_detect` 三颗（前者要逐页授权，后两者是按站判断）。
    """

    _IDENTITY_KEYS = ("auth", "cookies", "proxy", "extra_headers", "allow_private")
    _ALLOW_PRIVATE_TOOLS = ("web_screenshot", "feed_read", "web_recipe", "web_watch",
                            "cred_login")

    @pytest.mark.parametrize("key", _IDENTITY_KEYS)
    def test_web_read_refuses_its_old_identity_keys_by_name(self, key):
        with pytest.raises(ValueError) as exc:
            _dispatch("web_read", {"url": "https://example.com/", key: "x"})
        msg = str(exc.value)
        assert "Unsupported argument" in msg and key in msg, msg
        assert key not in server_mod._TOP_LEVEL_ARGS["web_read"], msg

    @pytest.mark.parametrize("tool", _ALLOW_PRIVATE_TOOLS)
    def test_the_other_tools_refuse_allow_private(self, tool):
        with pytest.raises(ValueError) as exc:
            _dispatch(tool, {"url": "https://example.com/", "allow_private": True})
        msg = str(exc.value)
        assert "Unsupported argument" in msg and "allow_private" in msg, msg
        assert "allow_private" not in server_mod._TOP_LEVEL_ARGS[tool], msg

    def test_the_three_names_that_stayed_are_still_accepted(self):
        allowed = server_mod._TOP_LEVEL_ARGS["web_read"]
        assert {"cdp_url", "session_id", "anti_detect"} <= allowed
        # 落盘的默认身份只能从这一颗工具进来。
        assert server_mod._TOP_LEVEL_ARGS["cred_defaults"] == frozenset(
            {"set", "unset", "show"})


class TestUnknownTopLevelArgsRaise:
    """A key that is not a param must not look like it applied."""

    def test_a_typo_names_the_key_and_the_supported_set(self):
        with pytest.raises(ValueError) as exc:
            _dispatch("web_crawl", {"url": "https://example.com/", "path_exlucde": "/what/"})
        message = str(exc.value)
        assert "path_exlucde" in message
        assert "max_pages" in message and "path_exclude" in message

    @pytest.mark.parametrize("tool,args", [
        ("web_crawl", {"url": "https://example.com/", "paths": ["/a"]}),
        ("web_search", {"query": "x", "sites": ["example.com"]}),
        ("web_read", {"url": "https://example.com/", "max_chars": 100}),
        ("web_screenshot", {"url": "https://example.com/", "width": 800}),
        ("sys_state", {"show": ["cache"], "everything": True}),
        ("doc_parse", {"file_path": "a.pdf", "pages": "1-2"}),
        ("feed_read", {"urls": ["https://example.com/f.xml"], "limit": 5}),
        ("web_resolve", {"url": "https://example.com/", "follow": True}),
    ])
    def test_every_tool_rejects_its_own_unknown_keys(self, tool, args):
        with pytest.raises(ValueError) as exc:
            _dispatch(tool, args)
        assert "Unsupported argument" in str(exc.value)

    def test_unknown_tool_still_says_unknown_tool(self):
        """改名之后，一个还带着 16.x 名字的客户端要能从报错里读到现在的名字。

        这里传的就是旧名：Unknown tool 之外什么都不说的话，它只知道「有个工具不
        存在」，得自己去翻 tools/list。
        """
        with pytest.raises(ValueError) as exc:
            _dispatch("smart_fetch", {"url": "https://example.com/"})
        msg = str(exc.value)
        assert "Unknown tool" in msg
        assert "web_read" in msg, "报错没列注册名，改了名的调用方无处可查"

    def test_null_valued_keys_mean_not_set(self, monkeypatch):
        """Clients that echo every schema property as null must not be rejected."""
        seen = _stub(monkeypatch, "web_crawl", _CRAWL_OK)
        _dispatch("web_crawl", {"url": "https://example.com/", "focus": None,
                                  "path_include": None, "nonsense": None})
        # 没传 = 不写进 kwargs，让方法自己的默认值生效。显式传 None 会盖掉一个
        # 不是 None 的默认值（extraction_type 的教训，见 _dispatch 里的注释）。
        assert "focus" not in seen and "path_include" not in seen

    def test_a_null_valued_call_is_not_rejected(self, monkeypatch):
        seen = _stub(monkeypatch, "web_crawl", _CRAWL_OK)
        _dispatch("web_crawl", {"url": "https://example.com/", "max_pages": 2,
                                  "sitemap": None})
        assert seen["max_pages"] == 2


class TestAcceptedSetCoversTheContract:
    def test_every_declared_schema_property_is_accepted(self):
        """A property the tool advertises can never be a rejected call."""
        for td in MasterFetchServer._TOOL_DEFS:
            declared = set((td.get("inputSchema") or {}).get("properties") or {})
            allowed = set(server_mod._TOP_LEVEL_ARGS[td["name"]])
            assert declared <= allowed, td["name"]

    def test_every_accepted_name_is_advertised(self):
        """17.0 B4 的那半边：能被读的键就必须能在 schema 里看到 —— 参数只有一个家，
        而家要看得见。

        反过来的事故真实存在过：袋子的散文写着"method 只发一次"，而顶层的 method
        一直能用、从来没被声明过，于是任何按 schema 写调用的客户端都不知道自己有这条能力。
        拆平之后没有豁免项了：能被接受又看不见 = 说谎。
        """
        for td in MasterFetchServer._TOOL_DEFS:
            declared = set((td.get("inputSchema") or {}).get("properties") or {})
            allowed = set(server_mod._TOP_LEVEL_ARGS[td["name"]])
            hidden = allowed - declared
            assert not hidden, (td["name"], sorted(hidden))

    def test_every_documented_param_set_is_accepted(self):
        assert server_mod._SC_PARAMS <= server_mod._TOP_LEVEL_ARGS["web_crawl"]
        assert server_mod._SS_PARAMS <= server_mod._TOP_LEVEL_ARGS["web_search"]
        assert server_mod._SHOT_PARAMS <= server_mod._TOP_LEVEL_ARGS["web_screenshot"]
        assert server_mod._SF_PARAMS <= server_mod._TOP_LEVEL_ARGS["web_read"]

    def test_every_tool_in_the_schema_is_covered(self):
        names = {td["name"] for td in MasterFetchServer._TOOL_DEFS}
        assert names == set(server_mod._TOP_LEVEL_ARGS)


class TestParseCwdReachesTheTool:
    """parse 的 cwd 是 15.0 新增的顶层参数：它必须真的抵达方法。

    这个文件存在的理由就是「顶层键静默消失」那类事故 —— 新参数要走同一条守卫，
    否则 agent 传了 cwd、相对路径照样解析到宿主安装目录，且答复里没有任何异常。
    """

    def test_cwd_is_forwarded(self, monkeypatch):
        seen = _stub(monkeypatch, "doc_parse",
                     server_mod.ResponseModel(url="file:///x", status=0, content=[]))
        _dispatch("doc_parse", {"file_path": "notes.csv", "cwd": "/tmp/session"})
        assert seen["file_path"] == "notes.csv"
        assert seen["cwd"] == "/tmp/session"

    def test_omitted_cwd_stays_none(self, monkeypatch):
        """不传就是 None（继续走进程 cwd / home），不是空串或报错。"""
        seen = _stub(monkeypatch, "doc_parse",
                     server_mod.ResponseModel(url="file:///x", status=0, content=[]))
        _dispatch("doc_parse", {"file_path": "notes.csv"})
        assert seen["cwd"] is None


class TestTopLevelFilterAppliesEndToEnd:
    """Dispatch -> crawl -> link filter, with the filter passed top-level."""

    @staticmethod
    def _crawl(monkeypatch, **top_level):
        async def fake_smart_fetch(self, url=None, **ignored):
            return server_mod.ResponseModel(
                url=url or "", status=200, content=[_LINKED_PAGE],
                fetcher_used="http", content_ok=True, content_type="text/html")

        monkeypatch.setattr(server_mod.MasterFetchServer, "web_read", fake_smart_fetch)
        text, structured = _dispatch("web_crawl", {
            "url": "https://example.com/", "max_pages": 5, "max_depth": 1,
            "force_fetcher": "http", **top_level})
        return structured

    def test_string_exclude_top_level_drops_the_path(self, monkeypatch):
        got = self._crawl(monkeypatch, path_exclude="/a")
        urls = [p["url"] for p in got["pages"]]
        assert got["pages_crawled"] == 3
        assert not any(u.endswith("/a") for u in urls)
        assert any(u.endswith("/b") for u in urls)
