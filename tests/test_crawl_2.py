"""test_crawl_2.py —— 由 4 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import asyncio
import pytest
from dhole_mcp import server as server_mod
from dhole_mcp.crawl import CrawlResponseModel
from dhole_mcp.search import SearchResponseModel
from dhole_mcp.server import MasterFetchServer
from dhole_mcp.crawl import CrawlPage
from dhole_mcp.server import DISCOVER_FALLBACK_MIN_MS, DISCOVER_FALLBACK_PAGES
from dhole_mcp import crawl as crawl_mod
from dhole_mcp.crawl import plan_load, plan_parse
from dhole_mcp.server import ResponseModel
import http.server
import socket
import threading
from dhole_mcp import identity
from dhole_mcp import sitemap as sm


# ============================================================================
# 来源：test_top_level_args.py
# ============================================================================
# Top-level arguments either reach the tool or fail loudly - never vanish.

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

    def test_screenshot_capture_knobs_live_in_the_action_now(self):
        """A1：full_page/image_type 等随 web_screenshot 下线，不再是顶层参数 ——
        它们住进 web_act 截图动作的对象，由 actions 的归一化层守形状。"""
        from dhole_mcp.actions import _validate_actions
        (a,) = _validate_actions([{"screenshot": {"full_page": True,
                                                  "image_type": "jpeg"}}])
        assert a["screenshot"]["full_page"] is True
        assert a["screenshot"]["image_type"] == "jpeg"
        with pytest.raises(ValueError) as exc:
            _dispatch("web_act", {"url": "https://example.com/",
                                  "actions": [{"wait": 1}],
                                  "full_page": True})
        assert "full_page" in str(exc.value), "顶层传旧键要被指路拒绝"

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
    _ALLOW_PRIVATE_TOOLS = ("web_act", "feed_read")

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
        ("sys_state", {"show": ["cache"], "everything": True}),
        # `sheet=` 是调用方很自然会想点的那颗（挑一个工作表），而 doc_parse 不收它。
        # 这里曾经拿 `pages=` 当"未知键"的样例 —— 它现在是合法键了，再留着这行
        # 就是拿一颗真参数去冒充表外名，测的就不是这条守卫想测的东西了。
        ("doc_parse", {"file_path": "a.pdf", "sheet": 1}),
        ("feed_read", {"urls": ["https://example.com/f.xml"], "limit": 5}),
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
        """A property the tool advertises can never be a rejected call.

        2026-10-23 真的发生过一次（R3 `include_headers`）：两颗工具的 schema 都把
        它列了进去、签名也接了它、实现也读它 —— 唯独 `_SF_PARAMS` / `_REQUEST_PARAMS`
        没有它，于是 dispatch 按表外名把每一次调用都拒了。套件当时是绿的，因为那条
        用例只跑了子集：**一个只在全量里才绿的守卫，等于没有守卫**。

        这条断言因此把名字点出来，而不是只说工具名 —— "web_read 里有个东西不对" 要
        人去翻 31 个参数，而 "web_read: ['include_headers']" 是直接可动手的那一句。
        """
        for td in MasterFetchServer._TOOL_DEFS:
            declared = set((td.get("inputSchema") or {}).get("properties") or {})
            allowed = set(server_mod._TOP_LEVEL_ARGS[td["name"]])
            ghost = declared - allowed
            assert not ghost, (
                f"{td['name']}: {sorted(ghost)} 在 schema 里露着脸，却会被 dispatch "
                "当表外名拒掉 —— 补进 *_PARAMS，或者从 schema 里删掉")

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


# ============================================================================
# 来源：test_a_discover_map_that_kept_nothing_is_answered_from_page_text.py
# ============================================================================
# F5：`web_discover(search=)` 空手而归时，这一刀要替调用方把"内容级"那一问回答了。

BASE = "https://requests.readthedocs.io"
MAP_ROWS = 42
HIT = f"{BASE}/en/latest/user/advanced/"
MISS = f"{BASE}/en/latest/community/"
API = f"{BASE}/en/latest/api/"


class _Clock:
    """可推进的钟：`web_discover` 用 `server.now()` 量"地图那一轮花了多少秒"。"""

    def __init__(self) -> None:
        self.t = 1_700_000_000.0

    def __call__(self) -> float:
        return self.t


def _row(url: str, *, content: str = "", error: str = "") -> CrawlPage:
    return CrawlPage(url=url, title=url.rsplit("/", 1)[-1],
                     content=[content] if content else [],
                     content_ok=not error, error=error,
                     status=-1 if error else 200,
                     page_type="" if error else "article")


def _map(rows: int = MAP_ROWS, **kw) -> CrawlResponseModel:
    """那一轮返回的形状：地图有 `rows` 条 URL，`search` 一行没留下。"""
    kw.setdefault("summary", f"search='timeout' filtered to 0 URL(s); crawled 0 page(s) "
                             f"at {BASE} (depth <= 2)")
    kw.setdefault("next_action", "search='timeout' matched none of the crawled pages. "
                                 "It matches URL path + title only - widen it, drop it, "
                                 "or pass focus= to prioritize relevant links.")
    return CrawlResponseModel(start_url=BASE, pages=[], pages_crawled=0,
                              pages_discovered=rows, discover_only=True, **kw)


def _scan(*items: tuple) -> CrawlResponseModel:
    pages = [_row(i[0], content=i[1], error=(i[2] if len(i) > 2 else ""))
             for i in items]
    return CrawlResponseModel(start_url=BASE, pages=pages, pages_crawled=len(pages),
                              pages_discovered=len(pages) + 5, discover_only=False,
                              summary=f"crawled {len(pages)} page(s) at {BASE} "
                                      f"(depth <= 2), {len(pages)} content_ok")


@pytest.fixture
def stub(monkeypatch):
    """`web_crawl` 的替身：按队列交出结果、记下参数，并让钟按配置往前走。

    `advance` 以秒计，`"map"` 是地图那一轮的开销 —— 剩下多少时钟由它决定，所以"够不够
    补一轮扫描"这条边界是量出来的，不是碰巧的。
    """
    box: dict = {"results": [], "calls": [], "clock": _Clock(),
                 "advance": {"map": 0.0, "scan": 0.0}}
    monkeypatch.setattr("dhole_mcp.server.now", box["clock"])

    async def _fake(self, url, **kw):
        box["calls"].append(kw)
        side = "map" if kw.get("discover_only") else "scan"
        box["clock"].t += box["advance"].get(side, 0.0)
        return box["results"].pop(0)

    monkeypatch.setattr(MasterFetchServer, "web_crawl", _fake)
    return box


def _sent(box: dict, n: int = -1) -> dict:
    return box["calls"][n]


class TestTheScanRunsWhenTheMapHadUrls:

    @pytest.mark.asyncio
    async def test_page_text_answers_the_question_the_map_could_not(self, stub):
        stub["results"] = [_map(), _scan((HIT, "… set timeout=(5, 300) …"),
                                         (MISS, "Contributing and code of conduct"),
                                         (API, "Requests API, reference only"))]
        r = await MasterFetchServer().web_discover(BASE, search="timeout")
        assert _sent(stub, -1)["discover_only"] is False
        assert _sent(stub, -1)["focus"] == "timeout"
        assert _sent(stub, -1)["max_pages"] == DISCOVER_FALLBACK_PAGES
        # 只留带词的那一行，而"读了几页、其中几页带词"两个数都在句子里
        # （R01-039 之后同一句先说地图用了多少、补考又用了多少，那两个数跟着挪了位置，
        # 主张本身一字未减：仍是"读了 3 页、其中 1 页带这个词"）
        assert [p.url for p in r.pages] == [HIT], [p.url for p in r.pages]
        assert "read 3 page(s), 1 of them carrying the word" in r.summary, r.summary

    @pytest.mark.asyncio
    async def test_the_downgrade_admits_itself_in_the_summary(self, stub):
        stub["results"] = [_map(), _scan((HIT, "the timeout parameter"))]
        r = await MasterFetchServer().web_discover(BASE, search="timeout")
        assert "answered by CONTENT, not by the map" in r.summary, r.summary
        assert "URL path and the page title" in r.summary, r.summary
        # 地图那一轮的账要用**数**活下来（"筛掉 0 行"这句话唯一的用处就是那几个数），
        # 而扫描自己那行统计不许被替身抹掉 —— 接管是追加说明，不是重写两轮的账。
        assert f"kept none of the {MAP_ROWS} URL(s) the map found" in r.summary, r.summary
        assert "crawled 1 page(s) at" in r.summary, r.summary

    @pytest.mark.asyncio
    async def test_the_adopted_rows_are_page_rows_not_map_rows(self, stub):
        stub["results"] = [_map(), _scan((HIT, "connect timeout"))]
        r = await MasterFetchServer().web_discover(BASE, search="timeout")
        assert r.discover_only is False, r.discover_only
        assert r.pages[0].content, "内容行不该被剥成正文为空"
        assert r.pages_crawled == 1

    @pytest.mark.asyncio
    async def test_a_content_answer_is_not_stored_as_a_map_plan(self, stub):
        """接管的是正文，不是清单：给它一个 plan_id 等于承诺一次没人要的续传。"""
        stub["results"] = [_map(), _scan((HIT, "read timeout"))]
        r = await MasterFetchServer().web_discover(BASE, search="timeout")
        assert r.plan_id == "", r.plan_id
        assert "Plan " not in (r.next_action or ""), r.next_action

    @pytest.mark.asyncio
    async def test_the_next_action_names_a_lever_the_caller_can_reach(self, stub):
        stub["results"] = [_map(), _scan((HIT, "timeout=(5, 300)"))]
        r = await MasterFetchServer().web_discover(BASE, search="timeout")
        assert "web_read(url=" in r.next_action, r.next_action
        assert "focus='timeout'" in r.next_action, r.next_action


class TestTheScanSpendsOnlyWhatTheClockLeft:

    @pytest.mark.asyncio
    async def test_the_remaining_clock_bounds_the_scan(self, stub):
        stub["advance"]["map"] = 5.0
        stub["results"] = [_map(), _scan((HIT, "timeout"))]
        await MasterFetchServer().web_discover(BASE, search="timeout", timeout=25000)
        sent = _sent(stub)
        assert sent["deadline_ms"] == 20000, sent
        assert sent["timeout"] == sent["deadline_ms"], "每页预算与整轮帽必须是同一口钟"
        assert sent["max_total_chars"] == 60000

    @pytest.mark.asyncio
    async def test_too_little_clock_left_runs_no_scan_and_says_so(self, stub):
        """地图把时钟花光了：宁可空手回来讲清楚，也不交回一份被同一口钟砍过的爬取。"""
        stub["advance"]["map"] = 22.0
        stub["results"] = [_map()]
        r = await MasterFetchServer().web_discover(BASE, search="timeout", timeout=25000)
        assert len(stub["calls"]) == 1, "剩下 3000ms 低于门槛还补扫描"
        assert "No content scan was run" in r.summary, r.summary
        assert "3000ms of its 25000ms clock" in r.summary, r.summary
        assert f"{DISCOVER_FALLBACK_MIN_MS}ms floor" in r.summary, r.summary
        assert "web_search" in (r.next_action or ""), r.next_action

    @pytest.mark.asyncio
    async def test_the_map_answer_survives_when_nothing_was_scanned(self, stub):
        stub["advance"]["map"] = 22.0
        stub["results"] = [_map()]
        r = await MasterFetchServer().web_discover(BASE, search="timeout", timeout=25000)
        assert r.pages == []
        assert r.pages_discovered == MAP_ROWS, "地图多大这件事不能被降级说明改掉"
        assert r.discover_only is True

    @pytest.mark.asyncio
    async def test_a_clock_the_map_overspent_is_reported_as_none_left(self, stub):
        """复测（requests 文档站）量到的形状：地图把 60,000ms 花到比 0 还少。
        "还剩 -975 毫秒"是调用方没法用的数 —— 它说的是"一秒都不剩"，就该写成 0。"""
        stub["advance"]["map"] = 26.0
        stub["results"] = [_map()]
        r = await MasterFetchServer().web_discover(BASE, search="timeout", timeout=25000)
        assert "0ms of its 25000ms clock" in r.summary, r.summary
        assert "-975" not in r.summary and "-1000" not in r.summary, r.summary
        assert len(stub["calls"]) == 1, "花超了的那一口钟不许再开第二次网络"


class TestAnEmptyMapIsNotGivenAnInventedScan:

    @pytest.mark.asyncio
    async def test_a_map_that_held_nothing_stops_at_the_explanation_it_has(self, stub):
        """`pages_discovered` 是地图的大小。它是 0 就是"这站没链出来"，与筛掉无关。"""
        stub["results"] = [_map(rows=0, summary=f"no links found at {BASE}")]
        r = await MasterFetchServer().web_discover(BASE, search="timeout")
        assert len(stub["calls"]) == 1, stub["calls"]
        assert "answered by CONTENT" not in (r.summary or ""), r.summary
        assert "No content scan was run" not in (r.summary or ""), r.summary
        assert r.summary == f"no links found at {BASE}", "这一支一个字都不许多写"

    @pytest.mark.asyncio
    async def test_an_errored_call_does_not_start_a_second_fetch(self, stub):
        stub["results"] = [_map(rows=9, error="blocked by the site",
                                summary="crawl failed: blocked by the site")]
        r = await MasterFetchServer().web_discover(BASE, search="timeout")
        assert len(stub["calls"]) == 1, stub["calls"]
        assert r.error == "blocked by the site"

    @pytest.mark.asyncio
    async def test_a_map_that_kept_rows_is_left_alone(self, stub):
        """有行就没有降级这回事 —— 反向半边：不传 search 时一个字不许多写。"""
        stub["results"] = [CrawlResponseModel(
            start_url=BASE, pages=[_row(f"{BASE}/a/")], pages_crawled=1,
            pages_discovered=MAP_ROWS, discover_only=True, summary="mapped 1 URL(s)")]
        r = await MasterFetchServer().web_discover(BASE)
        assert len(stub["calls"]) == 1
        assert "search" not in (r.summary or "").lower(), r.summary
        assert r.plan_id, "地图照旧进计划"

    @pytest.mark.asyncio
    async def test_no_search_means_no_second_call_at_all(self, stub):
        stub["results"] = [_map()]
        await MasterFetchServer().web_discover(BASE)
        assert len(stub["calls"]) == 1, "没有关键词就没人问内容"


class TestAScanThatFindsNothingIsReportedAsAScan:

    @pytest.mark.asyncio
    async def test_no_content_hit_keeps_the_map_answer_and_adds_the_look(self, stub):
        stub["results"] = [_map(), _scan((MISS, "Contributing and code of conduct"),
                                         (API, "Requests API, reference only"))]
        r = await MasterFetchServer().web_discover(BASE, search="timeout")
        assert r.pages == [], "扫描没命中却把行交回来，等于替它们作证"
        assert r.discover_only is True
        assert "found none of it either" in r.summary, r.summary
        assert "A content scan of 2 page(s)" in r.summary, r.summary
        assert "web_search" in (r.next_action or ""), r.next_action
        assert r.plan_id == ""

    @pytest.mark.asyncio
    async def test_the_scan_own_error_is_quoted_not_swallowed(self, stub):
        stub["results"] = [_map(), CrawlResponseModel(
            start_url=BASE, pages=[], pages_crawled=0, pages_discovered=0,
            discover_only=False, error="robots.txt disallows every link",
            summary="crawled 0 page(s)")]
        r = await MasterFetchServer().web_discover(BASE, search="timeout")
        assert "robots.txt disallows every link" in r.summary, r.summary

    @pytest.mark.asyncio
    async def test_a_row_that_failed_to_read_is_not_counted_as_scanned(self, stub):
        stub["results"] = [_map(), _scan((MISS, "", "network_error: timeout"))]
        r = await MasterFetchServer().web_discover(BASE, search="timeout")
        assert "content scan of 0 page(s)" in r.summary, r.summary


# ============================================================================
# 来源：test_a_crawl_that_stops_early_leaves_a_way_back_in.py
# ============================================================================
# R02-009 · 一页一页爬的那一路早停时，也要留下一个按得回去的坐标。

SEED = "https://resume.example.com/"
ROOT = "resume.example.com"


class _FakeSite:
    """每页秒回，只记录被请求到的 URL —— 续跑的那一发要能数出"没再付的钱"。

    `delay` 让时钟那一闸能被造出来：靶站再快也要有真实的一发"抓到一半墙到了"。
    """

    def __init__(self, pages: dict[str, str] | None = None, delay: float = 0.0):
        self.fetched: list[str] = []
        self.pages = pages or {}
        self.delay = delay

    async def web_read(self, url, **kwargs):
        if self.delay:
            await asyncio.sleep(self.delay)
        self.fetched.append(url)
        body = self.pages.get(url, _PROSE)
        return ResponseModel(url=url, status=200, content=[body],
                             content_type="text/html", fetcher_used="http",
                             content_ok=True)


# 正文要有**分得开的**句子：塌陷/低质那两把尺会把重复句砍空，字符闸就永远走不到。
_PROSE = "<p>" + " ".join(
    f"Row {i} of the plan carries its own wording, so extraction returns it whole."
    for i in range(20)) + "</p>"


def _links(names: list[str]) -> str:
    return ("<html><body>" + "".join(f'<a href="{h}">l</a>' for h in names)
            + "</body></html>")


def _page(names: list[str]) -> str:
    """一页既有很长的正文（喂字符闸）又挂着站内链接（喂队列）。"""
    return ("<html><body>" + _PROSE
            + "".join(f'<a href="{h}">l</a>' for h in names) + "</body></html>")


def _queued_urls(out) -> list[str]:
    return list(plan_load(out.plan_id) or [])


def _quoted_never_sent(na: str) -> int:
    """句子里那个"never sent"的数：两种说法各带一个位置（`_shortfall_sentence` 的两个分支）。

    按位置数词会挑错（"6 URL(s) were discovered but never sent" 的倒数第二词是 but），
    所以锚在短语本身。
    """
    import re
    m = re.search(r"([\d,]+) URL\(s\) were discovered but never sent"
                  r"|;\s*([\d,]+) were never sent", na)
    assert m, na
    return int((m.group(1) or m.group(2)).replace(",", ""))


@pytest.fixture
def a_page_cap_stop():
    """seed 上挂着 6 条站内链接，帽 = 3：跑完 3 页（含 seed）后停，队列里还剩着没发的。"""
    async def _build():
        site = _FakeSite({SEED: _links([f"/p{i}" for i in range(6)])})
        out = await crawl_mod.web_crawl(site, SEED, max_pages=3, max_depth=1,
                                        cache_ttl=0)
        return site, out
    return asyncio.run(_build())


def test_a_page_cap_stop_leaves_a_cursor_back_into_the_queue(a_page_cap_stop):
    _site, out = a_page_cap_stop
    assert out.truncated_by_max_pages is True
    assert out.plan_id, "早停留下了没发的 URL，就必须留下一个 id"
    assert out.next_resume_token == f"{out.plan_id}:0"


def test_the_cursor_is_parsable_by_the_same_vocabulary_the_resume_path_reads(a_page_cap_stop):
    _site, out = a_page_cap_stop
    parsed = plan_parse(out.next_resume_token)
    assert parsed == (out.plan_id, 0), parsed


def test_the_plan_holds_exactly_the_urls_that_were_never_sent(a_page_cap_stop):
    site, out = a_page_cap_stop
    held = _queued_urls(out)
    sent = set(site.fetched)
    assert held, "队列里有货才有这一格"
    assert not (set(held) & sent), f"发过的不许再存一遍：{sorted(set(held) & sent)}"
    # 已经交回的页也不许在计划里（它们是"做完了的那一半"）
    returned = {p.url for p in out.pages}
    assert not (set(held) & returned)


def test_the_plan_size_is_the_number_the_reply_already_quoted(a_page_cap_stop):
    """算术锁：句子里报的 never-sent 条数 == 计划里的行数。两个数各说各话就是谎。"""
    _site, out = a_page_cap_stop
    assert len(_queued_urls(out)) == _quoted_never_sent(out.next_action), (
        out.next_action, _queued_urls(out))


def test_the_way_out_names_the_cursor_and_keeps_the_old_lever(a_page_cap_stop):
    _site, out = a_page_cap_stop
    na = out.next_action
    assert f"resume_token='{out.next_resume_token}'" in na, na
    assert "max_pages" in na, " raising 帽那条出路没被换掉，只是不再是唯一一条"
    assert "without re-fetching" in na or "without re-crawling" in na, na


def test_a_run_that_finished_its_queue_publishes_no_plan():
    """队列见底就不是早停：不许为不存在的东西留一份状态。"""
    async def _go():
        site = _FakeSite({SEED: _links(["/a"])})
        return await crawl_mod.web_crawl(site, SEED, max_pages=5, max_depth=1,
                                         cache_ttl=0)
    out = asyncio.run(_go())
    assert out.truncated_by_max_pages is not True
    assert not out.plan_id, out.plan_id
    assert "resume_token" not in out.next_action


def test_the_char_budget_stop_publishes_the_same_kind_of_cursor():
    async def _go():
        site = _FakeSite({SEED: _page([f"/p{i}" for i in range(8)])})
        # 帽的下限是 1,000 字符（`MIN_TOTAL_CHARS`），一页正文 ~1.7k ⇒ 交回 seed 那一页
        # 之后 while 顶上的字符闸就到点：这才是"预算 spent、队列还在"的形状。
        return await crawl_mod.web_crawl(site, SEED, max_pages=20, max_depth=1,
                                         max_total_chars=1_000, cache_ttl=0)
    out = asyncio.run(_go())
    assert out.truncated_by_total_chars is True, (out.summary, out.next_action)
    assert out.plan_id and out.next_resume_token == f"{out.plan_id}:0"
    assert len(_queued_urls(out)) == _quoted_never_sent(out.next_action), out.next_action


def test_the_deadline_stop_publishes_the_same_kind_of_cursor():
    async def _go():
        site = _FakeSite({SEED: _page([f"/p{i}" for i in range(8)])}, delay=0.05)
        return await crawl_mod.web_crawl(site, SEED, max_pages=20, max_depth=1,
                                         deadline_ms=120, cache_ttl=0)
    out = asyncio.run(_go())
    assert out.truncated_by_time is True, (out.summary, out.next_action)
    if _queued_urls(out):
        assert out.next_resume_token == f"{out.plan_id}:0"
        assert "without re-fetching" in out.next_action, out.next_action


def test_the_plan_is_written_by_the_one_author_the_resume_path_reads(monkeypatch):
    """不许有第二个存储：BFS 这一支也走 `crawl.plan_store`。"""
    seen: list[list[str]] = []
    real = crawl_mod.plan_store

    def spy(urls):
        seen.append(list(urls))
        return real(urls)

    monkeypatch.setattr(crawl_mod, "plan_store", spy)

    async def _go():
        site = _FakeSite({SEED: _links([f"/p{i}" for i in range(5)])})
        return await crawl_mod.web_crawl(site, SEED, max_pages=2, max_depth=1,
                                         cache_ttl=0)
    out = asyncio.run(_go())
    assert seen, "计划必须经唯一的存储写出去"
    assert out.plan_id and _queued_urls(out) == seen[-1]


def test_urls_filtered_out_before_queueing_never_reach_the_plan():
    """子树过滤在入队之前，所以计划里不许混进被剪掉的东西 —— 续跑不能变成走私。"""
    async def _go():
        site = _FakeSite({SEED: _links(["/work/1", "/work/2", "/other/3", "/other/4"])})
        return await crawl_mod.web_crawl(site, SEED, max_pages=2, max_depth=1,
                                         path_include=["/work"], cache_ttl=0)
    out = asyncio.run(_go())
    held = _queued_urls(out)
    assert all("/other/" not in u for u in held), held


def test_a_map_stop_hands_its_tail_to_the_same_plan_author():
    """discover_only 那一停**现在**把尾巴交出去：存的是整张地图（R02-057 的口径）。

    这一格原本钉的是相反的话（"这一支不交队列，那颗 `plan_id` 另有作者、另有含义"），
    它钉住的正是当时实测到的那条洞：`web_discover(max_pages=3)` 说 `mapped 42 URL(s)`，
    计划里只有已交出的 3 行，`next_resume_token` 空，照建议 `<id>:0` 再问一次读回的还是
    那 3 行，下一句 "Map exhausted: all 3 row(s) have shipped" —— 地图 42 行，"exhausted"
    说的是计划。口径定了（尾巴连着前半存、游标发在已交出的行数上）之后，旧的那句就成了
    反判据，所以**改写**这一格而不是删掉：形状仍由同一发问出，判据换成"两式都读得东西"。
    正文那一支的口径没动，仍只装一次都没发过的 URL（上面那几格守着）。
    """
    async def _go():
        site = _FakeSite({SEED: _links([f"/p{i}" for i in range(6)])})
        return await crawl_mod.web_crawl(site, SEED, max_pages=3, max_depth=1,
                                         discover_only=True, cache_ttl=0)
    out = asyncio.run(_go())
    assert out.truncated_by_max_pages is True, out.summary
    held = _queued_urls(out)
    assert held[:len(out.pages)] == [p.url for p in out.pages]
    assert len(held) == out.pages_discovered, (
        f"地图 {out.pages_discovered} 行，计划 {len(held)} 行：{held}")
    assert out.next_resume_token == f"{out.plan_id}:{len(out.pages)}"
    assert f"resume_token='{out.next_resume_token}'" in out.next_action, out.next_action


def test_the_two_field_names_now_cover_the_page_by_page_run_too():
    """字段自己的说法以前只讲地图（`plan_id` 那句），而游标那颗早就承诺了"帽/预算/时钟"。"""
    fields = crawl_mod.CrawlResponseModel.model_fields
    assert "max_pages" in fields["plan_id"].description, fields["plan_id"].description[:300]
    assert "without re-fetching" in fields["next_resume_token"].description or \
        "continue" in fields["next_resume_token"].description


def test_the_manual_says_a_stopped_crawl_can_be_picked_up():
    from dhole_mcp import guide
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))
    assert "resume_token" in corpus, "手册里没这把游标"
    assert "never sent" in corpus or "were never sent" in corpus, (
        "手册没写这条计划存的是**一次都没发过**的那一段")


# ============================================================================
# 来源：test_a_private_sitemap_is_read_when_the_door_is_open.py
# ============================================================================
# 私网站点的 sitemap：放行要放行到底，报告要说得出**真话**。

#: 真解析器必须在**导入时**抓住。conftest 的 `_offline_dns`（autouse）把
#: `socket.getaddrinfo` 钉成公网地址，好让套件不依赖本机 hosts；如果在夹具里写
#: `socket.getaddrinfo`，拿到的是**已经被钉住的那个**，于是 127.0.0.1 被判成
#: 93.184.216.34 —— 守卫放行，用例测的东西整个消失了（实测踩过）。
_REAL_GETADDRINFO = socket.getaddrinfo

SITEMAP_XML = (
    b"<?xml version='1.0' encoding='UTF-8'?>"
    b"<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
    b"<url><loc>__BASE__/a</loc></url>"
    b"<url><loc>__BASE__/b</loc></url>"
    b"<url><loc>__BASE__/c</loc></url>"
    b"</urlset>"
)

#: robots.txt 声明了 Sitemap，且 /sitemap.xml 是**合法**的 —— 站点这边完全没问题。
#: 唯一的问题在 dhole 那扇门上。
ROBOTS_WITH_SITEMAP = b"User-agent: *\nDisallow: /private\nSitemap: __BASE__/sitemap.xml\n"
#: 对照：robots.txt 真的没有 Sitemap 指令，且**连惯例路径也没有** sitemap。
ROBOTS_WITHOUT_SITEMAP = b"User-agent: *\nDisallow: /private\n"


def _make_site(robots_body: bytes, *, serve_conventional_sitemap: bool = True):
    """一个只会答 robots.txt / sitemap.xml / 一个普通页的最小站。"""

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # 静音
            pass

        def do_GET(self):
            base = f"http://127.0.0.1:{self.server.server_address[1]}"
            path = self.path.split("?", 1)[0]
            if path == "/robots.txt":
                body, ctype = robots_body.replace(b"__BASE__", base.encode()), \
                    "text/plain; charset=utf-8"
            elif path in ("/sitemap.xml", "/sitemap_index.xml"):
                if not serve_conventional_sitemap:
                    self.send_response(404)
                    self.end_headers()
                    return
                body, ctype = SITEMAP_XML.replace(b"__BASE__", base.encode()), "application/xml"
            elif path in ("/a", "/b", "/c", "/"):
                body, ctype = b"<html><body><h1>page</h1></body></html>", "text/html; charset=utf-8"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _serve(robots_body: bytes, *, serve_conventional_sitemap: bool = True):
    srv = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0),
        _make_site(robots_body, serve_conventional_sitemap=serve_conventional_sitemap))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


@pytest.fixture
def private_site_with_sitemap(monkeypatch):
    # 靶站是真 socket，守卫要看见真地址；测试进程自己的 DNS 替身会把它变成假 IP。
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    srv = _serve(ROBOTS_WITH_SITEMAP)
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


# ───────────────────────── 真 socket 组：门 ─────────────────────────

def _assert_the_door_starts_closed():
    """这颗用例的门必须是它自己开的，不是上一个用例留下的。

    这一组曾被记为"全量套件里绿、单独跑红"。今天量不到那个现场（文件单独跑绿、这两颗单独挑
    出来跑也绿，退回 D1 那一刀之前同样绿），但它防的形状是真的：一条没有前置检查的门守卫，
    可以因为别人留着的态而绿着测不到东西。证人：在下面的断言之前先
    `identity.set_value("allow_private", True)`，这条断言立刻红。

    这里只看见得上的那一条缝：identity 的连接是模块级单例，conftest 每颗用例换一份 tmp_path
    的 identity.db **并且** close() 断掉旧句柄（不 close 就等于什么都没隔离，旧句柄还指着
    上一颗用例那份文件）。门本身（`_ALLOW_PRIVATE` 那颗 ContextVar）由 conftest 的
    `_private_door_does_not_leak_across_tests` 逐颗复位，不在这里重复一道。
    """
    assert not identity.get("allow_private", ""), identity.all_rows()


@pytest.mark.asyncio
async def test_the_door_is_open_on_the_sitemap_too(private_site_with_sitemap):
    """存了 allow_private，sitemap 就必须**真被读到** —— 这是 D2 的正面判据。"""
    _assert_the_door_starts_closed()
    identity.set_value("allow_private", True)
    result = await MasterFetchServer().web_discover(
        private_site_with_sitemap, sitemap=True)
    assert result.sitemap_used is True, (
        "种子放行了、sitemap 却在守卫那里死掉 —— 同一扇门两个答案：\n"
        f"summary={result.summary!r}\nerror={result.error!r}")
    assert not result.error, result.error
    assert result.pages_discovered == 3, [p.url for p in result.pages]


@pytest.mark.asyncio
async def test_without_the_door_the_refusal_names_the_address(private_site_with_sitemap):
    """没放行时种子自己就该被拒 —— 而那句拒绝必须点得出**地址**与**出路**。

    这里判的不是"失败了"，是"失败说得清"：D2 的失实之处在于报告断言了没发生过的
    探测；种子的拒绝本来是说得清的（点名私网段 + 给出 allow_private 那条路），
    这条守卫把它钉住，防止"修失实摘要"时把这条正确的拒绝一起改坏。
    """
    _assert_the_door_starts_closed()
    result = await MasterFetchServer().web_discover(
        private_site_with_sitemap, sitemap=True)
    assert result.sitemap_used is False
    assert "private IP" in (result.error or ""), result.error
    assert "allow_private" in (result.next_action or ""), result.next_action
    # 一句都不许提"Sitemap 指令"：这条路上根本没走到读 robots.txt 的那一步。
    assert "Sitemap directive" not in (result.summary or ""), result.summary


@pytest.mark.asyncio
async def test_the_old_sentence_survives_where_it_is_true(monkeypatch):
    """robots.txt 真的没有指令、惯例路径也 404 时，那句老文案必须原样还在。

    修一句假话不等于把那句话删掉：它在**为真**的场合仍是最准的一句，而把它一并
    删掉，就等于用另一种含混替换了原来的自信 —— 两个方向都是"诊断答错"。
    """
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    srv = _serve(ROBOTS_WITHOUT_SITEMAP, serve_conventional_sitemap=False)
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        _assert_the_door_starts_closed()
        identity.set_value("allow_private", True)   # 让种子过门，走到 sitemap 那一段
        result = await MasterFetchServer().web_discover(base, sitemap=True)
        assert result.sitemap_used is False
        assert "had no Sitemap directive" in (result.summary or ""), result.summary
    finally:
        srv.shutdown()
        srv.server_close()


# ───────────────────────── 离线组：话 ─────────────────────────

def _discover_with_robots(robots_text: bytes):
    """假 transport：robots.txt 由调用方给，其余一律 404。"""
    def http_get(url: str):
        if url.endswith("/robots.txt"):
            return (200, robots_text)
        return None
    return sm.discover_sitemap("https://site.test/", http_get=http_get, max_urls=100)


def test_a_guarded_sitemap_directive_is_recorded_not_silently_dropped():
    """被守卫拦下的 Sitemap 指令必须**记账**：过去它是一句 `continue`。

    「index 里有 30 个子 sitemap、其中 12 个指向私网」与「index 只有 18 个」在
    报告里长得一模一样 —— 地图短了一截，没有任何一处能解释为什么。
    """
    res = _discover_with_robots(
        b"User-agent: *\nSitemap: http://169.254.169.254/sitemap.xml\n"
        b"Sitemap: https://site.test/ok.xml\n")
    assert res.robots_had_sitemap is True, "robots.txt 里确有 Sitemap 指令"
    assert res.guard_refused == ["http://169.254.169.254/sitemap.xml"], res.guard_refused
    # 公网那条仍然要留在候选里 —— 记账不是把整条指令一起废掉。
    assert "https://site.test/ok.xml" in res.sitemaps_used or res.via == "robots"


def test_a_guarded_directive_makes_the_summary_name_the_guard():
    """有指令、但指令指向私网 → 总结必须说「守卫拒了它」，不能说「没有指令」。"""
    from dhole_mcp.crawl import _no_sitemap_next_action, _no_sitemap_summary

    diag = {"robots_checked": True, "robots_had_sitemap": True,
            "guard_refused": ["http://169.254.169.254/sitemap.xml"], "via": "conventional"}
    s = _no_sitemap_summary("http://127.0.0.1:8399/", diag)
    assert "had no Sitemap directive" not in s, s
    assert "guard" in s and "169.254.169.254" in s, s
    # 出路指向那扇真门，不是「换个站」——那不是站的问题。
    assert "allow_private" in _no_sitemap_next_action(diag)


def test_a_directive_that_read_nothing_is_not_called_missing():
    """有指令、没被守卫拦、却什么都没读出来 → 也不能说「没有指令」。"""
    from dhole_mcp.crawl import _no_sitemap_summary

    diag = {"robots_checked": True, "robots_had_sitemap": True,
            "guard_refused": [], "via": "robots"}
    s = _no_sitemap_summary("https://site.test/", diag)
    assert "had no Sitemap directive" not in s, s
    assert "declares a Sitemap" in s, s


def test_the_summary_stays_hedged_when_robots_could_not_be_read():
    """robots.txt 根本没读到 → 不许断言它有没有指令。"""
    from dhole_mcp.crawl import _no_sitemap_summary

    diag = {"robots_checked": False, "robots_had_sitemap": False,
            "guard_refused": [], "via": "conventional"}
    s = _no_sitemap_summary("https://site.test/", diag)
    assert "had no Sitemap directive" not in s, s
    assert "could not be read" in s, s
