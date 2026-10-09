"""分母怎么来的，必须站在分母旁边（体验修复刀2-S9）。

`engines_consensus` 读起来是一个比值，实际上它的**含义**随出处变：`2 of 5` 与
`2 of 3 (degraded_pool)` 是两种强弱完全不同的佐证，前者是"五家里两家同意"，后者是
"三家被墙了还剩三家，其中两家同意"。出处此前只写在响应顶层的 `consensus_basis` 上 ——
而读行的人就在那一行决定要不要信这条结果，等他抬头去看顶层那颗，这行他已经按
"web 不同意"读完了。同一句话的两个部分分在两个地方，等于少了一半。

顶层那颗照旧存在（一次 glance 判整轮），这一刀把出处**也**放进串行。
"""

from __future__ import annotations

import pytest

from dhole_mcp import search as S
from dhole_mcp.search_engines import EngineReport, RawResult


def _row(url: str, i: int, consensus: int) -> RawResult:
    return RawResult(title=f"t{i}", url=url, snippet="s", source="bing",
                     position=i, consensus=consensus,
                     sources=("bing", "baidu") if consensus > 1 else ("bing",))


def _built(families: int, basis: str, consensus: int = 1) -> str:
    built = S._build_results("q", [_row("https://a.test/1", 1, consensus)],
                             [1.0], total_families=families, basis=basis)
    return built[0].engines_consensus


class TestTheProvenanceRidesTheRow:

    def test_a_degraded_denominator_says_it_was_degraded(self):
        assert _built(4, "degraded_pool") == "1 of 4 (degraded_pool)"

    def test_a_narrowed_pool_says_it_was_narrowed(self):
        assert _built(4, "partial_pool", consensus=2) == "2 of 4 (partial_pool)"

    def test_a_full_pool_adds_nothing(self):
        """健康池不许被加一层噪音 —— 每一行都挂一个 "(full)" 就是把 0 信息摊成 N 份。"""
        assert _built(6, "full", consensus=3) == "3 of 6"

    def test_single_family_is_not_parenthesized_twice(self):
        """"一家都没有别家"那句已经自带说明了，再叠第二个括号是重复而不是诚实。"""
        assert _built(1, "single_family") == "1 of 1 (no corroboration)"

    def test_a_caller_that_passes_no_basis_gets_the_old_string(self):
        """默认值必须维持旧形状：`basis=""` 是"我不知道出处"，不是"出处是 full"。"""
        assert _built(6, "", consensus=2) == "2 of 6"


@pytest.mark.asyncio
async def test_the_row_and_the_envelope_agree(monkeypatch):
    """同一颗事实只有一个作者：串行的括号必须等于顶层的 `consensus_basis`。

    端到端跑（不是再调一次 `_build_results`），因为它守的是**两个调用点**里有人忘了
    传 basis 那种漂移 —— 顶层照常说 degraded_pool、行里干干净净不带出处，那正是
    这一刀要消灭的形状，而它只能在真路径上被看见。
    """
    rows = [RawResult(title=f"doc {i}", url=f"https://e{i}.test/{i}", snippet="dns resolution",
                      source="bing", position=i, consensus=1, sources=("bing",))
            for i in range(1, 4)]
    reports = [EngineReport(name="bing", ok=True, status="ok"),
               EngineReport(name="sogou", blocked=True, status="blocked", error="429"),
               EngineReport(name="baidu", blocked=True, status="blocked", error="timeout")]

    async def fake_multi(query, max_results, **kwargs):
        return list(rows), list(reports)

    async def pass_through(*a, **k):
        return None

    monkeypatch.setattr(S, "multi_search", fake_multi)
    monkeypatch.setattr(S, "get_cached", pass_through)
    monkeypatch.setattr(S, "set_cached", pass_through)
    monkeypatch.setattr(S, "ensure_reranker", pass_through)
    out = await S.web_search(None, "dns resolution", 6, cache_ttl=0)
    assert out.consensus_basis == "degraded_pool", out.consensus_basis
    for r in out.results:
        assert r.engines_consensus.endswith("(degraded_pool)"), r.engines_consensus
