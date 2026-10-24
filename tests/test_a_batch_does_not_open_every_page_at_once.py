"""一批 web_read(urls=[...]) 不能把每一页同时塞给浏览器。

现场（2026-10，11 站冷缓存批量）：n=5 全活（10.7s），n=11 只剩 5/11 —— 同批五六个
渲染任务在共享浏览器上争抢，维基与 GitHub 以 status 0 死掉，而同一 URL 单发全部
成功（维基 134,420 字 / GitHub 15,683 字）。临界点量在 5-6 个渲染任务，所以批量里的
浏览器档任务现在走一根每批 `_BATCH_BROWSER_CONCURRENCY`（4）格的车道（信号量）。

本文件钉住五件事：

  * 一批 6 个渲染行，任一时刻在渲染的不超过 4 个，且一行不丢；
  * 批次把**同一根**车道放进上下文，每一行都读得到，收尾后摘干净；
  * 批次的行以单 URL 走到浏览器档时，排的是调用方那根车道；
  * 不在任何批次里的单发调用一行不多等（车道不在场）；
  * 排队的等待不写进那一行的 `duration_ms` —— 那颗数喂 per-host 延迟学习。

外加一条结构守卫：HTTP 档的批量（`bulk_get`）不碰车道 —— 它的瓶颈是网络。
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import dhole_mcp.server as server_mod
from dhole_mcp.fetcher import Response
from dhole_mcp.models import ResponseModel
from dhole_mcp.server import (
    _BATCH_BROWSER_CONCURRENCY,
    MasterFetchServer,
    _batch_browser_lane,
)


def _page(url: str) -> Response:
    return Response(url=url, body=b"<html><body><p>ok</p></body></html>",
                    status=200, headers={"content-type": "text/html; charset=utf-8"})


def _tracking_fetch(hold_s: float = 0.05):
    """假 session.fetch：记录任一时刻在跑的抓取数（峰值）与完成的行数。"""
    state = {"live": 0, "peak": 0, "done": 0}

    async def fetch(url, **kwargs):
        state["live"] += 1
        state["peak"] = max(state["peak"], state["live"])
        try:
            await asyncio.sleep(hold_s)
            state["done"] += 1
            return _page(url)
        finally:
            state["live"] -= 1

    return fetch, state


def _wire(monkeypatch, fetch) -> None:
    """把 bulk_stealthy_fetch 之下的那一层换成假的：真入口 + 假 session.fetch。"""

    async def _get_session(self, session_id, expected_type=None):
        return SimpleNamespace(session=SimpleNamespace(
            fetch=fetch, cdp_attached=False, launch_note=""))

    monkeypatch.setattr(MasterFetchServer, "_get_session", _get_session)
    monkeypatch.setattr("dhole_mcp.server._browser_deps_available", lambda: True)
    # 身份两问必须同答，否则命中的是"这一趟自己起一台浏览器"那条支路
    monkeypatch.setattr("dhole_mcp.server._launch_identity", lambda **k: ("same",))
    monkeypatch.setattr("dhole_mcp.server._session_identity", lambda s: ("same",))


# ─── 1 · 批量里的渲染上限 ──────────────────────────────────────────────

def test_six_render_rows_never_render_more_than_four_at_once(monkeypatch):
    fetch, state = _tracking_fetch()
    _wire(monkeypatch, fetch)
    bulk = asyncio.run(MasterFetchServer().bulk_stealthy_fetch(
        urls=[f"https://site.test/{i}" for i in range(6)], session_id="s"))
    assert state["peak"] == _BATCH_BROWSER_CONCURRENCY, state
    assert bulk.total == 6 and bulk.successful == 6, "限流丢了行 —— 车道只该排队，不该吞"


def test_the_queue_wait_is_not_written_into_the_rows_own_duration(monkeypatch):
    """前 4 行各占一格 0.25s；第 5 行排队等位、自己的抓取瞬间完成。

    它的 `duration_ms` 必须只算自己那一程：让站点替 dhole 的排队付账，per-host
    延迟学习会学出一个假的 ceiling。
    """

    async def fetch(url, **kwargs):
        await asyncio.sleep(0.25 if url.endswith(("/0", "/1", "/2", "/3")) else 0)
        return _page(url)

    _wire(monkeypatch, fetch)
    bulk = asyncio.run(MasterFetchServer().bulk_stealthy_fetch(
        urls=[f"https://site.test/{i}" for i in range(6)], session_id="s"))
    assert bulk.results[0].duration_ms >= 200, bulk.results[0].duration_ms
    queued = bulk.results[4].duration_ms
    assert queued < 150, f"第 5 行自己只抓了一瞬，却背了 {queued}ms —— 排队的账进来了"


# ─── 2 · 批次与行之间那根车道 ──────────────────────────────────────────

def test_a_batch_sets_one_lane_for_all_its_rows_and_clears_it_after(monkeypatch):
    seen: list[object] = []

    async def _fake_read(self, url=None, **kwargs):
        seen.append(_batch_browser_lane.get())
        return ResponseModel(url=url, status=200, content=["ok"], fetcher_used="http")

    monkeypatch.setattr(MasterFetchServer, "web_read", _fake_read)
    asyncio.run(MasterFetchServer()._smart_fetch_bulk(
        urls=[f"https://site.test/{i}" for i in range(3)],
        extraction_type="markdown", css_selector=None, cache_ttl=0,
        force_fetcher=None, timeout=30000))
    assert len(seen) == 3 and all(s is not None for s in seen), seen
    assert all(s is seen[0] for s in seen), "三行必须排同一根车道，各自一根等于没限"
    assert _batch_browser_lane.get() is None, "批次收尾后车道必须摘掉，别漏给后来的单发"


def test_rows_of_a_batch_queue_on_the_callers_lane(monkeypatch):
    """批次的行以单 URL 进浏览器档：排的是上下文里那根（这里故意只给 2 格）。"""
    fetch, state = _tracking_fetch()
    _wire(monkeypatch, fetch)
    token = _batch_browser_lane.set(asyncio.Semaphore(2))
    try:
        async def go():
            srv = MasterFetchServer()
            return await asyncio.gather(*[
                srv.bulk_stealthy_fetch(urls=[f"https://site.test/{i}"], session_id="s")
                for i in range(5)])
        asyncio.run(go())
    finally:
        _batch_browser_lane.reset(token)
    assert state["peak"] == 2, state


def test_single_calls_outside_any_batch_are_not_gated(monkeypatch):
    """车道不在场（单发路径）时三发全并发 —— 2.1 的边界：信号量只在批量上生效。"""
    fetch, state = _tracking_fetch()
    _wire(monkeypatch, fetch)

    async def go():
        srv = MasterFetchServer()
        return await asyncio.gather(*[
            srv.bulk_stealthy_fetch(urls=[f"https://site.test/{i}"], session_id="s")
            for i in range(3)])
    asyncio.run(go())
    assert state["peak"] == 3, state


# ─── 3 · HTTP 档不碰车道 ──────────────────────────────────────────────

def test_the_http_tier_bulk_never_touches_the_browser_lane():
    """结构守卫：`bulk_get` 的函数体里不许出现车道的任何一个名字。

    HTTP 档的瓶颈是网络不是浏览器 —— 把它一起限流，纯 HTTP 的批量就为渲染档的
    拥挤白付等待，而那正是 2.1 明确不做的事。
    """
    tree = ast.parse(Path(server_mod.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == "bulk_get":
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            assert "_batch_browser_lane" not in names
            assert "_lane_gated" not in names
            return
    raise AssertionError("bulk_get not found in server.py")
