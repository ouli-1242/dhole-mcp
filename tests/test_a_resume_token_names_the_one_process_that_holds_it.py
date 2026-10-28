"""计划只活在一个进程的内存里 —— 拒绝句必须这么说。

现场（2026-10-27 全量 MCP 覆盖）：进程 A 里 `web_discover` 拿到的 token，在进程 B
里续爬时回来 `plan … is unknown or expired (plans live 1h server-side …)`。
"server-side" 读起来像"服务器上有一份，可能过期了"，而真相是**这个进程里根本
没有**：`crawl._PLANS` 是模块级内存，重启、第二个进程、另一个 MCP 会话都从空
存储开始。措辞把范围说小了，调用方就会去检查 TTL（一个它无法改变的东西），
而不是在**当前**进程重跑 discover。

这个文件钉两件事：
* 一个本进程没有的 token 被拒时，句子点名"this server process"与重启，
  不再把成因说成只有"过期"；
* 本进程真的持有计划时，discover 的出路那句同样限定在"this server process"。
"""

from __future__ import annotations

import pytest

import dhole_mcp.crawl as crawl_mod
from dhole_mcp.crawl import CrawlPage, CrawlResponseModel, plan_load, plan_store
from dhole_mcp.server import MasterFetchServer

URL = "https://example.com/"


@pytest.mark.asyncio
async def test_a_token_this_process_never_held_is_not_called_expired():
    """`plan_store` 之后清掉存储 = 一次重启；拒绝句要说"这个进程没有"。"""
    plan_id = plan_store([f"{URL}a"])
    crawl_mod._PLANS.pop(plan_id, None)  # 重启（或另一个进程）看到的就是这样

    out = await MasterFetchServer().web_crawl(URL, resume_token=f"{plan_id}:0",
                                              cache_ttl=0)

    assert out.error.startswith("invalid_request"), out.error
    low = out.error.lower()
    assert "unknown or expired" not in low, (
        "旧句把'这个进程没有'说成了'过期'，调用方会去查一个它改不了的 TTL")
    assert f"no plan '{plan_id}' is held by this server process" in out.error, out.error
    assert "memory" in low and "restart" in low and "second process" in low, out.error
    assert out.summary == "plan not held by this process"
    assert out.next_action.startswith("Nothing was fetched."), out.next_action
    assert "in THIS process" in out.next_action, out.next_action


@pytest.mark.asyncio
async def test_the_discover_receipt_scopes_the_plan_to_this_process(monkeypatch):
    """留下一张地图时，那句"held … for 1h"必须同时说清它不会跨进程。"""
    async def _fake_crawl(self, url, **kwargs):
        return CrawlResponseModel(start_url=url,
                                  pages=[CrawlPage(url=f"{URL}a")],
                                  pages_discovered=1)

    monkeypatch.setattr(MasterFetchServer, "web_crawl", _fake_crawl)

    out = await MasterFetchServer().web_discover(URL)

    assert not out.error, out.error
    assert out.plan_id, "这份地图该被留下"
    assert plan_load(out.plan_id), "计划真的进了本进程的存储"
    assert ("held in this server process for 1h" in out.next_action), out.next_action
    assert "restart or another process cannot resume it" in out.next_action, (
        out.next_action)
