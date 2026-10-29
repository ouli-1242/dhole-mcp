"""`terse=true` 只丢逐后端的尸检，不丢任何一个可行动的判决。

症状（2026-10-29 · 一次真实的 3 行搜索）：响应约 5.5KB，其中 `engine_detail` 12 行
约 1.6KB、逐行 `why` 与 `status` 大面积重复。批量搜索的 agent 只要链接，却按每发都在
为这份"谁为什么没答"付费；而需要它的回合（池子降级、解析器漂移）恰恰是少数。

修法是一颗 opt-in，不是改默认：`terse=true` 清空 `engine_detail` / `source_detail`，
留下结果行、四个桶、家族计数、`consensus_basis`、日期账、`fetch_hint`、`summary`、
`next_action`。四个桶仍然说得出**谁**没贡献；被省略的只有逐家的**为什么** ——
那正是"冷却 / 解析器漂移 / 这个索引真没有"三种互斥修法的分诊台，所以默认仍在。

契约：
  * 默认（false）一个字节都不变；
  * terse 只清那两张逐后端表，桶名单、共识、日期与两句建议原样保留；
  * 缓存回放与实时搜索走同一个收尾（两处 return 都接同一颗助手）；
  * wire 广告这颗参数，dispatch 也接受它。
"""

from __future__ import annotations

import pytest

from dhole_mcp import search as search_mod
from dhole_mcp.search import RawResult, SearchResponseModel
from dhole_mcp.search_engines import EngineReport
from dhole_mcp.server import MasterFetchServer, _TOP_LEVEL_ARGS


def _stub_round(monkeypatch):
    async def fake_multi(*a, **kw):
        return ([RawResult(title="Python packaging guide",
                           url="https://docs.test/packaging",
                           snippet="A practical guide to Python packaging and wheels.",
                           source="brave", position=1)],
                [EngineReport(name="brave", ok=True, status="ok",
                              item_nodes=5, usable=1),
                 EngineReport(name="bing", ok=False, blocked=True,
                              status="blocked", error="HTTP 403")])

    async def fake_none(*a, **k):
        return None

    monkeypatch.setattr(search_mod, "multi_search", fake_multi)
    monkeypatch.setattr(search_mod, "ensure_reranker", fake_none)
    monkeypatch.setattr(search_mod, "get_cached", fake_none)
    monkeypatch.setattr(search_mod, "set_cached", fake_none)
    monkeypatch.setattr(
        search_mod, "_rank",
        lambda query, ranked, mode, **kw: (ranked, [1.0] * len(ranked), "merge", ""))


@pytest.mark.asyncio
async def test_terse_drops_the_autopsy_and_keeps_every_lever(monkeypatch):
    _stub_round(monkeypatch)

    full = await search_mod.web_search(None, "python packaging guide")
    assert full.engine_detail, "默认那一发必须仍有逐家的账"
    assert full.engine_blocked == ["bing"], full.engine_blocked
    assert full.results, full.error

    lean = await search_mod.web_search(None, "python packaging guide", terse=True)

    assert lean.results and len(lean.results) == len(full.results)
    assert lean.engine_detail == [], lean.engine_detail
    assert lean.source_detail == [], lean.source_detail
    # 桶名单还在：谁没贡献仍然一眼可见。
    assert lean.engine_blocked == ["bing"], lean.engine_blocked
    assert lean.engines_used == full.engines_used
    # 共识、日期与两句行动建议一个不少。
    assert lean.families_answered_of_asked == full.families_answered_of_asked
    assert lean.consensus_basis == full.consensus_basis
    assert lean.fetch_hint and lean.next_action
    assert lean.summary == full.summary


def test_the_helper_clears_only_the_two_per_backend_tables():
    resp = SearchResponseModel(
        query="q", results=[], engine_detail=[{"name": "bing"}],
        source_detail=[{"source": "news"}], engine_blocked=["bing"],
        fetch_hint="hint", next_action="next")
    out = search_mod._terse_response(resp)
    assert out.engine_detail == [] and out.source_detail == []
    assert out.engine_blocked == ["bing"]
    assert out.fetch_hint == "hint" and out.next_action == "next"


def test_the_wire_advertises_terse_and_the_dispatch_accepts_it():
    entry = [d for d in MasterFetchServer._TOOL_DEFS if d["name"] == "web_search"][0]
    assert "terse" in entry["inputSchema"]["properties"], entry["inputSchema"]["properties"]
    assert "terse" in _TOP_LEVEL_ARGS["web_search"], _TOP_LEVEL_ARGS["web_search"]
