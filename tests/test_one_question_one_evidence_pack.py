"""一问一证据包：`web_research`（提升计划 3.2）。

「搜索 → 取前 N → 证据包 + 引用」从前要调用方自己串：N 次往返、自己拼 [n]。
这颗门是**薄编排层**——搜索与抓取全部复用 web_search / web_read 自己的机器，
它只负责「串」与引用格式。

钉住五件事：每条证据带 [n] 与 URL；一个源失败说明为什么且**从不拖垮整包**；
预算按剩余量逐源分摊、耗尽前先声明；夹取（clamp）在回执里点名；空查询一个请求
都不发。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from dhole_mcp.models import ResponseModel
from dhole_mcp.server import MasterFetchServer

import dhole_mcp.server as server_mod


def _wire(monkeypatch, read_impl=None, rows=6):
    """假搜索 + 受控假读取；read_impl 可按 URL 定制成败。"""
    async def fake_search(self, query="", **kw):
        return SimpleNamespace(
            results=[SimpleNamespace(url=f"https://s{i}.test/a", title=f"t{i}")
                     for i in range(rows)],
            error="", next_action="")

    async def fake_read(self, url, focus=None, max_content_chars=4000,
                        timeout=None, **kw):
        if read_impl is not None:
            return read_impl(url, focus)
        return ResponseModel(url=url, status=200,
                             content=[f"{url} 说：这是一段与 {focus} 相关的证据。"
                                      "它带着句号，过得了形状闸。" * 20],
                             fetcher_used="http", content_ok=True)

    monkeypatch.setattr(MasterFetchServer, "web_search", fake_search)
    monkeypatch.setattr(MasterFetchServer, "web_read", fake_read)


def test_each_evidence_carries_its_number_and_url(monkeypatch):
    _wire(monkeypatch)
    out = asyncio.run(MasterFetchServer().web_research(query="注意力机制"))
    assert len(out["evidence"]) == 3
    for n, block in enumerate(out["evidence"], start=1):
        assert block.startswith(f"[{n}] https://s{n - 1}.test/a\n"), block[:60]
    assert [s["n"] for s in out["sources"]] == [1, 2, 3]
    assert out["failed"] == []
    assert len(out["more_hits"]) == 3, "没读的那几行该列出来供跟进"


def test_a_failed_source_says_why_and_never_fails_the_pack(monkeypatch):
    def flaky(url, focus):
        if url.endswith("s1.test/a"):
            return ResponseModel(url=url, status=403, content=[],
                                 fetcher_used="http",
                                 error="bot_wall_detected: the page answered 403")
        return ResponseModel(url=url, status=200,
                             content=["一段正常的证据正文，带句号。" * 40],
                             fetcher_used="http", content_ok=True)

    _wire(monkeypatch, read_impl=flaky)
    out = asyncio.run(MasterFetchServer().web_research(query="q"))
    assert len(out["evidence"]) == 2, "一个源倒下，整包照交"
    (why,) = out["failed"]
    assert why["n"] == 2 and why["url"].endswith("s1.test/a")
    assert "bot_wall" in why["why"] or "403" in why["why"], why


def test_budget_exhaustion_is_declared_not_silently_skipped(monkeypatch):
    _wire(monkeypatch)
    clock = {"t": 0.0}

    def fake_now():
        return clock["t"]

    real_read = MasterFetchServer.web_read

    async def slow_read(self, url, **kw):
        # 第一源读完就把钟拨过预算：剩下的源必须在 failed 里说明"预算没了"
        out = await real_read(self, url, **kw)
        clock["t"] += 40.0
        return out

    monkeypatch.setattr(MasterFetchServer, "web_read", slow_read)
    monkeypatch.setattr(server_mod, "now", fake_now)
    out = asyncio.run(MasterFetchServer().web_research(query="q", timeout=30_000))
    assert len(out["evidence"]) == 1
    assert len(out["failed"]) == 2
    assert all("budget ran out" in f["why"] for f in out["failed"]), out["failed"]


def test_clamps_are_announced_in_the_receipt(monkeypatch):
    _wire(monkeypatch)
    out = asyncio.run(MasterFetchServer().web_research(
        query="q", max_sources=9, per_source_chars=99, timeout=999_999))
    assert out["clamped"] == [
        "max_sources -> 5 (1-5)",
        "per_source_chars -> 500 (500-20000)",
        "timeout -> 300000ms (30000-300000)",
    ]
    assert len(out["evidence"]) == 5


def test_an_empty_query_makes_no_request(monkeypatch):
    def boom(*a, **k):  # 任何一腿都不该发生
        raise AssertionError("empty query must not search or read")

    monkeypatch.setattr(MasterFetchServer, "web_search", boom)
    monkeypatch.setattr(MasterFetchServer, "web_read", boom)
    with pytest.raises(ValueError, match="needs a query"):
        asyncio.run(MasterFetchServer().web_research(query="  "))
