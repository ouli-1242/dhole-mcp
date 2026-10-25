"""R09-002：recall 的 limit 越界要 clamp-and-name，不许静默改写。

现场（round-09 P10）：limit=21 与 limit=-5 都被 `recall.py` 的
`max(1, min(20, int(limit or 8)))` 静默改写——回执无任何注记。行为指南的在册
绝对句是「An out-of-range number is either clamped-and-named, or refused —
never silently rewritten」；同轮 web_search 的 max_results=51 走的是具名
clamp（"outside the supported 1-50 range; returning at most 50"）。

修后合同：越界的 clamp 在回执带 `clamped: ["limit -> N (1-20)"]`（形状同
web_research 的 clamped）；未越界（含缺省 8）一个字不加。
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from dhole_mcp.cache import set_cached
from dhole_mcp.recall import recall_pages


@pytest_asyncio.fixture
async def cache_dir(tmp_path):
    return tmp_path


async def _seed(cache_dir, url, text):
    await set_cached(url, "markdown", [text], 200, ttl=3600, cache_dir=cache_dir,
                     scope="fetch")


def _no_network(monkeypatch):
    import socket

    def _explode(*a, **k):
        raise AssertionError("web_recall touched the network - it must not")

    monkeypatch.setattr(socket, "create_connection", _explode)
    monkeypatch.setattr(socket.socket, "connect", _explode)


@pytest.mark.asyncio
async def test_limit_above_range_is_clamped_and_named(cache_dir, monkeypatch):
    _no_network(monkeypatch)
    for i in range(3):
        await _seed(cache_dir, f"https://a.example/p{i}", f"注意力机制第 {i} 篇。")
    out = await recall_pages("注意力机制", limit=21, cache_dir=cache_dir)
    assert out.get("clamped") == ["limit -> 20 (1-20)"], out.get("clamped")


@pytest.mark.asyncio
async def test_negative_limit_is_clamped_and_named(cache_dir, monkeypatch):
    _no_network(monkeypatch)
    await _seed(cache_dir, "https://a.example/p0", "注意力机制第 0 篇。")
    out = await recall_pages("注意力机制", limit=-5, cache_dir=cache_dir)
    assert out.get("clamped") == ["limit -> 1 (1-20)"], out.get("clamped")


@pytest.mark.asyncio
async def test_in_range_limit_stays_quiet(cache_dir, monkeypatch):
    _no_network(monkeypatch)
    await _seed(cache_dir, "https://a.example/p0", "注意力机制第 0 篇。")
    out = await recall_pages("注意力机制", limit=8, cache_dir=cache_dir)
    assert "clamped" not in out
    out = await recall_pages("注意力机制", cache_dir=cache_dir)
    assert "clamped" not in out
