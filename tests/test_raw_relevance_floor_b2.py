"""B2 回归：原始分下限（`min_raw_relevance`）——让「这一轮全都不相关」说得出口。

归一化分数的下限做不到这件事，而且不是「还没实现」，是**量纲上不可能**：min-max 归一
把 top 定义成 1.0、worst 定义成 0.0，于是一组全是垃圾的结果里，最好的那条照样是 1.0，
任何 <1.0 的下限都留不下"空"这个答案；原始分全相等时更糟——全部归一成 1.0。

所以阈值必须挂在 cross-encoder 的**原始 sigmoid** 上。挂上去之前得先知道它长什么样，
实测（bge-zh，真搜索结果）：

    离题文档      p50 1e-4，最大值 0.0104 / 0.0173（两轮各不同）
    同主题不同侧面  p50 0.005，p90 0.18   ← 硬负例，所以下限不能往上抬太多
    引擎自己返回的  p50 0.97，但最小值 1e-4（那 13% 就是报告抱怨的离题填充）

0.1 落在「离题上限」和「勉强相关」之间的空档里。下面钉的是这个操作性事实，以及每条
降级路径都有话说（过滤了多少、这一轮原始分跨了多远）——否则调用方只能猜下一个数。
"""

from __future__ import annotations

import pytest

from dhole_mcp import reranker as R
from dhole_mcp import search as S
from dhole_mcp.reranker import rerank
from dhole_mcp.search import EngineReport, RawResult

class _FakeReranker:
    """A cross-encoder stand-in with a scripted score per document text."""

    def __init__(self, table):
        self.table = table
        self.calls = 0

    def score(self, query, docs):
        self.calls += 1
        # rerank() hands the model "title snippet"; every fixture title here is a
        # single token, so that is what the table is keyed by.
        return [self.table.get(d.split(" ", 1)[0], 0.0) for d in docs]


def _results(texts):
    return [RawResult(title=t, url=f"https://s{i}.test/", snippet="", source="bing",
                      position=i + 1) for i, t in enumerate(texts)]


# 一组「全垃圾」：引擎确实返回了 3 条，但原始分全在离题带里。
JUNK = {"a": 0.0002, "b": 0.0001, "c": 0.0173}
MIXED = {"good": 0.9811, "marginal": 0.2043, "junk": 0.0004}


@pytest.fixture
def wired(monkeypatch):
    """Install the fake reranker behind rerank()'s peek-only getter."""
    def _install(table):
        fake = _FakeReranker(table)
        monkeypatch.setattr(R, "_reranker", fake)
        monkeypatch.setattr(R, "get_reranker", lambda: fake)

        async def _no_real_load(*a, **k):
            return fake

        # search.smart_search awaits ensure_reranker() before ranking; without this
        # it would load the real 288 MB model to answer a unit test.
        monkeypatch.setattr(S, "ensure_reranker", _no_real_load)
        return fake
    return _install


class TestTheNormalizedFloorCannotDoThis:

    def test_a_normalized_floor_always_keeps_the_best_of_a_bad_set(self, wired):
        wired(JUNK)
        results = _results(list(JUNK))

        kept = rerank("q", results, min_relevance=0.99)

        assert kept, "top 恒为 1.0，所以它永远活下来"
        assert len(kept) == 1
        assert kept[0][1] == 1.0


class TestTheRawFloorCanSayNothingIsRelevant:

    def test_an_all_junk_round_comes_back_empty_not_reassuring(self, wired):
        wired(JUNK)
        results = _results(list(JUNK))

        kept = rerank("q", results, min_raw_relevance=0.1)

        # [] = "模型跑了，并且把每一条都判为不相关"；None = "没有重排器"。
        # 把这两件事混成一个，调用方就分不清「查无结果」和「结果全是垃圾」。
        assert kept == []

    def test_the_measured_operating_point_keeps_every_real_hit(self, wired):
        """0.1 是从实测空档里取的：它必须放行勉强相关的（0.2043），也放行真命中的
        （≥0.93），只砍掉 1e-4 那一团。"""
        wired(MIXED)
        results = _results(list(MIXED))

        kept = rerank("q", results, min_raw_relevance=0.1)

        assert [r.title for r, _ in kept] == ["good", "marginal"]

    def test_a_junk_only_query_drops_at_the_floor_it_asked_for(self, wired):
        wired(JUNK)
        results = _results(list(JUNK))

        no_floor = rerank("q", results)
        floored = rerank("q", results, min_raw_relevance=0.1)

        assert len(no_floor) == 3 and floored == []

    def test_the_span_it_saw_comes_back_with_the_verdict(self, wired):
        """过滤掉东西却不报原始分范围，调用方下一步只能瞎猜；报了这个数，0.1→0.05
        这种决定才有依据。"""
        wired(MIXED)
        stats: dict = {}

        kept = rerank("q", _results(list(MIXED)), min_raw_relevance=0.1, stats=stats)

        assert len(kept) == 2
        assert stats["raw_min"] == pytest.approx(0.0004)
        assert stats["raw_max"] == pytest.approx(0.9811)
        assert stats["dropped_raw"] == 1

    def test_no_floor_means_no_change_for_anyone(self, wired):
        wired(MIXED)
        before = rerank("q", _results(list(MIXED)))
        after = rerank("q", _results(list(MIXED)), min_raw_relevance=0.0, stats={})

        assert [r.title for r, _ in before] == [r.title for r, _ in after]


@pytest.mark.asyncio
class TestTheSearchLayerReportsIt:

    async def test_an_emptied_round_says_which_floor_emptied_it(self, wired, monkeypatch):
        wired(JUNK)
        raw = _results(list(JUNK))

        async def fake_multi(query, max_results, **kwargs):
            return list(raw), [EngineReport(name="bing", ok=True)]

        monkeypatch.setattr(S, "multi_search", fake_multi)
        out = await S.smart_search(None, "任意查询", max_results=5, cache_ttl=0,
                                   engines=["bing"], min_raw_relevance=0.1)

        assert out.results == []
        assert "min_raw_relevance=0.1" in out.error, out.error
        # The span is in the same sentence so the next number is not a guess.
        assert "spanned" in out.error, out.error

    async def test_a_partial_drop_is_counted_in_the_hint(self, wired, monkeypatch):
        wired(MIXED)
        raw = _results(list(MIXED))

        async def fake_multi(query, max_results, **kwargs):
            return list(raw), [EngineReport(name="bing", ok=True)]

        monkeypatch.setattr(S, "multi_search", fake_multi)
        out = await S.smart_search(None, "任意查询", max_results=5, cache_ttl=0,
                                   engines=["bing"], min_raw_relevance=0.1)

        titles = [r.title for r in out.results]
        assert "junk" not in titles and "good" in titles
        assert "min_raw_relevance=0.1" in out.fetch_hint, out.fetch_hint
        assert "spanned" in out.fetch_hint, out.fetch_hint

    async def test_the_floor_is_part_of_the_cache_identity(self, wired, monkeypatch):
        """同一个查询带下限与不帯下限必须是两条缓存，否则 0.1 的那次调用会拿到一份
        没过滤的历史结果，看起来像「下限没生效」。"""
        wired(MIXED)
        raw = _results(list(MIXED))
        cache: dict = {}

        async def fake_get_cached(query, cache_type, css_selector, **kwargs):
            return cache.get((query, cache_type))

        async def fake_set_cached(query, cache_type, content, status, css_selector, ttl, **kw):
            cache[(query, cache_type)] = {"content": content}

        async def fake_multi(query, max_results, **kwargs):
            return list(raw), [EngineReport(name="bing", ok=True)]

        monkeypatch.setattr(S, "get_cached", fake_get_cached)
        monkeypatch.setattr(S, "set_cached", fake_set_cached)
        monkeypatch.setattr(S, "multi_search", fake_multi)

        loose = await S.smart_search(None, "同一查询", max_results=5, engines=["bing"])
        strict = await S.smart_search(None, "同一查询", max_results=5, engines=["bing"],
                                       min_raw_relevance=0.1)

        assert len(strict.results) < len(loose.results)
        assert any("minraw=0.1" in k[1] for k in cache), list(cache)

    async def test_an_out_of_range_floor_is_clamped_and_said(self, wired, monkeypatch):
        wired(MIXED)
        raw = _results(list(MIXED))

        async def fake_multi(query, max_results, **kwargs):
            return list(raw), [EngineReport(name="bing", ok=True)]

        monkeypatch.setattr(S, "multi_search", fake_multi)
        out = await S.smart_search(None, "q", max_results=5, cache_ttl=0,
                                   engines=["bing"], min_raw_relevance=5)

        assert "outside 0-1" in out.fetch_hint, out.fetch_hint
        assert "1.0" in out.fetch_hint, out.fetch_hint

@pytest.mark.live
@pytest.mark.asyncio
async def test_a_full_floor_empties_a_real_round_instead_of_passing_it_off():
    """真模型、真搜索：下限设成原始分几乎不可能到的 1.0，正确的答复是「这轮没有一条
    够相关」，而不是把最高分那条当结果端出去。"""
    from dhole_mcp.server import MasterFetchServer

    srv = MasterFetchServer()
    loose = await S.smart_search(srv, "kubernetes liveness probe 配置", max_results=5,
                                 cache_ttl=0)
    if not loose.results:
        pytest.skip(f"no live engine answer: {loose.error[:80]}")

    strict = await S.smart_search(srv, "kubernetes liveness probe 配置", max_results=5,
                                  cache_ttl=0, min_raw_relevance=1.0)

    assert strict.rerank_mode == "neural", strict.fetch_hint
    assert strict.results == [], [r.url for r in strict.results]
    assert "min_raw_relevance=1.0" in strict.error, strict.error
    assert "spanned" in strict.error, strict.error
