"""B5 拆分的守卫：一张工具表一次只搬一件事。

17.0 §4 的目标是 17 颗带域前缀的工具，而 web_read / web_crawl 现在是把它们都装在一
颗里的样子（`discover_only` 就是其中一件：它把「抓正文」的那颗临时变成「只出地图」）。
一个参数换一个工具的好处是 agent 少猜一次：它不必先想起要把某个开关打开，才能拿到
它其实想要的那个东西。

这里给每次拆分钉三件事，缺一条就不算拆完：
1. 新名字在注册面上，参数不超过 §4 给它的那一份；
2. 旧工具不再接受被搬走的那个参数（搬完还留着就是两个家）；
3. 走新名字真的能拿到和以前一样的结果（行为不变，只是入口变了）。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer

URL = "https://example.com/"


def _defs() -> dict:
    return {td["name"]: td for td in MasterFetchServer._TOOL_DEFS}


def _run(coro):
    return asyncio.run(coro)


# ─── web_discover：从 web_crawl 搬出「只出 URL 地图」──────────────────

DISCOVER_PARAMS = {"url", "sitemap", "path_include", "path_exclude",
                   "max_pages", "search", "timeout"}


def test_web_discover_is_registered_with_its_own_param_set():
    defs = _defs()
    assert "web_discover" in defs, sorted(defs)
    props = set(defs["web_discover"]["inputSchema"]["properties"])
    assert props == DISCOVER_PARAMS, (
        f"§4 给 web_discover 的是这 7 个，多出的是还没搬走的旧旋钮：{props}")
    assert props == set(server_mod._TOP_LEVEL_ARGS["web_discover"])


def test_web_discover_says_what_it_returns_and_what_it_does_not():
    desc = _defs()["web_discover"]["description"].lower()
    assert "no page" in desc and "content" in desc, desc
    # 拆出去的工具必须自己说清下一步是谁，否则 agent 拿着地图不知道找谁读正文
    assert "web_read" in desc, desc


def test_web_crawl_no_longer_accepts_discover_only():
    assert "discover_only" not in server_mod._TOP_LEVEL_ARGS["web_crawl"], (
        "地图搬走了，开关还留在爬取那颗上：同一个能力两个入口")
    assert "discover_only" not in _defs()["web_crawl"]["inputSchema"]["properties"]
    with pytest.raises(ValueError) as exc:
        _run(MasterFetchServer()._dispatch("web_crawl",
                                           {"url": URL, "discover_only": True}))
    assert "Unsupported argument" in str(exc.value)


def test_a_map_call_over_the_new_name_asks_the_engine_for_a_map(monkeypatch):
    """行为账：入口换成 web_discover 之后，引擎收到的仍然是「只出地图」那档。

    断的是接线而不是地图内容 —— 引擎本身（crawl.web_crawl 的 discover_only 模式）
    已由爬取那批测试逐条钉过，这里重钉一遍只是把同一件事写两处。
    """
    from dhole_mcp.crawl import CrawlResponseModel

    seen: dict = {}

    async def fake_engine(self, url, **kwargs):
        seen.update(kwargs)
        seen["url"] = url
        return CrawlResponseModel(start_url=url, summary="map",
                                  pages=[], pages_crawled=0,
                                  pages_discovered=0, discover_only=True)

    monkeypatch.setattr(MasterFetchServer, "web_crawl", fake_engine)
    _run(MasterFetchServer()._dispatch(
        "web_discover", {"url": URL, "max_pages": 5, "sitemap": True}))
    assert seen.get("discover_only") is True, \
        "新入口没把地图模式带上：它其实还在抓正文"
    assert seen.get("max_pages") == 5 and seen.get("sitemap") is True


# ─── sys_state：把 cache_clear 与 close_session 合成一颗 ───────────────
#
# 两颗旧工具做的是同一件事的两半：「本地还存着什么」和「把它抹掉」。分开摆的代价
# 是踩过的：cache_clear(all=true) 连 cookie jar 一起抹，而它自己的描述里没这句；
# close_session 看不见缓存。合成一颗之后 token 词表把「抹哪一类」说明白，
# §3-32 的闸（抹掉后用户不会立刻知道的那一类要先 confirm）才有地方落。

STATE_PARAMS = {"show", "clear", "confirm"}
VOCAB = {"cache", "engine_state", "sessions", "watch", "recipes", "all"}

ME = "https://shop.example.com/account"
SECRET = "session-value-DO-NOT-LEAK"


def _defs_by_name() -> dict:
    return {td["name"]: td for td in MasterFetchServer._TOOL_DEFS}


def test_sys_state_is_registered_with_three_params():
    defs = _defs_by_name()
    assert "sys_state" in defs, sorted(defs)
    props = set(defs["sys_state"]["inputSchema"]["properties"])
    assert props == STATE_PARAMS, (
        f"§4 给 sys_state 的是这 3 个，多出的是没搬干净的旧旋钮：{props}")
    assert props == set(server_mod._TOP_LEVEL_ARGS["sys_state"])


def test_the_two_housekeeping_tools_are_gone():
    """合并不能留两个家：旧名字必须在三个注册面上都消失。"""
    for old in ("cache_clear", "close_session"):
        assert old not in _defs_by_name(), f"{old} 还在连接期的表里"
        assert old not in server_mod._TOP_LEVEL_ARGS, f"{old} 仍可被分发"
        with pytest.raises(ValueError, match="Unknown tool") as exc:
            _run(MasterFetchServer()._dispatch(old, {}))
        # 报错会回显调用方打的那个名字，那是对的；不能有的部分是"建议名单"——
        # 服务端不许把一个不存在的名字推荐给人。
        assert old not in str(exc.value).split("Registered names:")[1], \
            "报错的建议名单里还留着旧名字"


def test_a_bare_call_shows_the_census_and_changes_nothing():
    from dhole_mcp import sessions
    from dhole_mcp.cache import set_cached

    sessions.store("alpha", ME, {"sid": SECRET})
    _run(set_cached(URL, "markdown", ["body"], 200))
    _content, structured = _run(MasterFetchServer()._dispatch("sys_state", {}))
    assert [r["session_id"] for r in structured["sessions"]] == ["alpha"]
    assert structured["open_count"] == 1
    assert structured["cache"]["entries"] >= 1
    assert sessions.header_for("alpha", ME) == {"sid": SECRET}, "看一看不该有任何副作用"
    assert SECRET not in json.dumps(structured, ensure_ascii=False)


def test_clearing_every_session_waits_for_a_confirm():
    """§3-32：抹掉之后用户不会立刻知道自己失去了什么 —— 登录态正是这一类。"""
    from dhole_mcp import sessions

    sessions.store("alpha", ME, {"sid": SECRET})
    _content, paused = _run(MasterFetchServer()._dispatch(
        "sys_state", {"clear": ["sessions"]}))
    assert paused["pending_approval"] is True, paused
    assert sessions.header_for("alpha", ME) == {"sid": SECRET}, \
        "没批准就不能动手"
    assert "confirm" in paused["next_action"]

    _content, ok = _run(MasterFetchServer()._dispatch(
        "sys_state", {"clear": ["sessions"], "confirm": True}))
    assert sessions.header_for("alpha", ME) == {}
    assert ok["cleared"] == ["sessions"] and ok["cookies_forgotten"] == 1


def test_naming_one_session_forgets_it_without_a_confirm():
    """点名关是调用方自己写出来的意图，闸只拦「你没点名的那些也一起没了」。"""
    from dhole_mcp import sessions

    sessions.store("target", ME, {"sid": SECRET})
    sessions.store("untouched", "https://elsewhere.example.org/x", {"sid": SECRET})
    _content, structured = _run(MasterFetchServer()._dispatch(
        "sys_state", {"clear": ["target"]}))
    assert sessions.header_for("target", ME) == {}
    assert sessions.header_for("untouched", "https://elsewhere.example.org/x") == \
        {"sid": SECRET}
    assert structured["session_id"] == "target"


def test_clear_cache_does_not_touch_the_jar():
    """旧 cache_clear(all=true) 顺手抹掉 cookie，是这条合并要修的谎。
    现在缓存与凭据是两个 token，谁都不再是另一个的隐藏副作用。"""
    from dhole_mcp import sessions
    from dhole_mcp.cache import get_cached, set_cached

    sessions.store("alpha", ME, {"sid": SECRET})
    _run(set_cached(URL, "markdown", ["body"], 200))
    _content, structured = _run(MasterFetchServer()._dispatch(
        "sys_state", {"clear": ["cache"]}))
    assert _run(get_cached(URL, "markdown")) is None, structured
    assert structured["purged"] >= 1
    assert sessions.header_for("alpha", ME) == {"sid": SECRET}, \
        "清缓存不该顺手把人登出"


def test_an_unknown_token_names_the_vocabulary_and_its_neighbour():
    """词表打错不能静悄悄：既不落会话分支，也不能只回一句 not found。"""
    _content, structured = _run(MasterFetchServer()._dispatch(
        "sys_state", {"clear": ["chache"]}))
    assert structured["error"], structured
    for token in VOCAB:
        assert token in structured["error"] or token in structured["next_action"], token
    assert structured.get("cleared", []) == []
