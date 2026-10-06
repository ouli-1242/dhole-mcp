"""cache.py 没有命中计数 —— cache_ref 要不要上 wire 的判决依据「读不到」。

症状（2026-10-06 F-B-J1 判决原文）：cache.py 无命中计数，频率证据「读不到」
—— cache_ref（复读句柄）的增量价值集中在 PDF 与凭据调用，但没人知道普通页
的复读频率，棘轮无从谈起。

判决：两步走的第一步 —— 先给 sys_state 的 cache 读数加 hits/misses（进程级，
与 census 同生命周期，重启归零不是缺陷），零连接期代价（cache 是响应侧的
自由字典，不进 tools/list 面）；有了一周读数再决定走不走显式上抬 ＋341。
"""

from __future__ import annotations

import pytest

from dhole_mcp import cache as cache_mod

pytestmark = pytest.mark.asyncio


@pytest.fixture()
def fresh_cache(tmp_path, monkeypatch):
    # raising=False：实现落地前该属性还不存在 —— 红要红在行为上，不红在夹具上。
    monkeypatch.setattr(cache_mod, "_hit_counters", {"hits": 0, "misses": 0},
                        raising=False)
    return tmp_path


async def test_a_lookup_that_finds_nothing_counts_as_a_miss(fresh_cache):
    before = dict(cache_mod._hit_counters)
    got = await cache_mod.get_cached("https://example.com/never", "markdown",
                                     cache_dir=fresh_cache)
    assert got is None
    assert cache_mod._hit_counters["misses"] == before["misses"] + 1
    assert cache_mod._hit_counters["hits"] == before["hits"]


async def test_a_repeat_read_counts_as_a_hit(fresh_cache):
    await cache_mod.set_cached("https://example.com/twice", "markdown",
                               content=["hello"], status=200,
                               content_type="text/html", total_size_bytes=5,
                               cache_dir=fresh_cache)
    assert await cache_mod.get_cached("https://example.com/twice", "markdown",
                                      cache_dir=fresh_cache) is not None
    before = dict(cache_mod._hit_counters)
    await cache_mod.get_cached("https://example.com/twice", "markdown",
                               cache_dir=fresh_cache)
    assert cache_mod._hit_counters["hits"] == before["hits"] + 1


async def test_the_census_reports_the_counters(fresh_cache):
    await cache_mod.get_cached("https://example.com/absent", "markdown",
                               cache_dir=fresh_cache)
    view = await cache_mod.cache_stats(cache_dir=fresh_cache)
    assert "hits" in view and "misses" in view, (
        "cache 读数没有命中计数 —— cache_ref 的判决依据仍然读不到")
    assert view["misses"] >= 1
