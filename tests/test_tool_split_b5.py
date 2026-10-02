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
