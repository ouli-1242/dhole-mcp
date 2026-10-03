"""17.0 B4：参数只有顶层这一个家，于是"传了就得抵达"成为唯一的契约。

这个文件接替 `test_options_are_canonical_g22.py`（G22 守的是"两种放法一模一样"，
袋子删了就没有第二种放法可比较了）。它守的是同一族事故的另一半，也是历史上真的
翻过车的那一半：**一个能被接受的键被管道丢掉**。

- 顶层 `max_pages=3` 曾经爬了 10 页，一句警告都没有（调度器不读它）；
- `options.urls="https://x"` 曾经被当成 19 个单字符 URL；
- 一个没被 sent 的键如果被递成 `None`，会盖掉方法自己那个**不是 None** 的默认
  （`extraction_type="markdown"` 就是这么被 None 顶掉、缓存 INSERT 撞在 NOT NULL 列上的）。

覆盖面是从 `_TOP_LEVEL_ARGS` **算出来**的：以后任何新参数进了这张表，它自动被这一组
用例覆盖，不需要谁记得再写一条。
"""

from __future__ import annotations

import asyncio

import pytest

from dhole_mcp import server as server_mod
from dhole_mcp.crawl import CrawlResponseModel
from dhole_mcp.search import SearchResponseModel
from dhole_mcp.server import MasterFetchServer, ResponseModel

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
    # screenshot 回的是内容片段列表，不是模型；这里只看桩被用什么参数调用。
    "web_screenshot": ({"url": URL}, []),
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
    # B5-13a：档位是字符串枚举，探针值要和默认值 "auto" 不同才探得出丢没丢。
    "anti_detect": "aggressive",
    "max_links": 5, "session_id": "probe-session",
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
    # web_crawl
    "max_pages": 3, "max_depth": 1, "path_include": "/a", "path_exclude": "/b",
    "max_content_chars_per": 2000, "concurrency": 2,
    "deadline_ms": 60000, "sitemap": True, "search": "topic",
    # web_search
    "max_results": 5, "mode": "auto", "engines": ["bing"], "site": "example.com",
    "exclude_sites": ["other.example"], "location": "cn", "language": "zh",
    "region": "cn-zh", "page": 1, "freshness": "week", "after": "2026-09-20",
    "before": "2026-01-01", "url": URL, "fetch_content": True,
    "fetch_schema": {"properties": {"t": {"selector": "p"}}},
    "min_relevance": 0.2, "min_raw_relevance": 0.1,
    # screenshot
    "quality": 80, "save_to": "shot.png", "image_type": "png",
    "wait_selector": ".item", "full_page": True,
    # 17.0 多模态：只验"到没到"，不真截屏
    "selector": ".qr", "decode_qr": True,
}
# 布尔类共用一个值；列出来是为了让"表里查不到"这条守卫说得准。
# §3-17：allow_private 原来是这张表里的一颗（每次调用放行内网），现在它只能从
# cred_defaults 落盘，所以按上面 VALUES 的同一处理从表里拿掉。
# B5-13d：network_idle 同样拿掉 —— 渲染时机不再是调用方能说的话。
BOOL_KEYS = {
    "include_media", "include_links", "ignore_robots",
    "full_page", "sitemap", "check_login",
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
        正好相反：能力全在，只是位置换了。所以这句话必须点名 17.0 与能搬出去的键。
        """
        with pytest.raises(ValueError) as ei:
            _dispatch("web_search", {"query": "q", "options": {"max_results": 5}})
        msg = str(ei.value)
        assert "removed in 17.0" in msg, msg
        assert "max_results" in msg, msg

    def test_a_bag_key_that_could_not_move_anywhere_says_so(self):
        """袋子里装了一个哪儿都不存在的键：不能假装它"可以搬出来"。"""
        with pytest.raises(ValueError) as ei:
            _dispatch("web_search", {"query": "q", "options": {"frobnicate": 1}})
        assert "frobnicate" not in str(ei.value).split("removed in 17.0")[1]


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
        ("web_screenshot", "allow_private"),
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
