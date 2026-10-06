"""#8 共识感知扇出：让"够不够独立佐证"给自动取正文当预算分配器。

`fetch_content=true` 是这条链上唯一一处"dhole 自己决定去抓哪几页"的扇出。此前它按
`fetch_relevance` 取前三——而相关性是**单条结果自己**的分数，一家索引说了算。共识
（`engines_consensus`）是搜索里唯一免费的权威信号：它已经在每一行上，不花一次额外
请求、不引模型、确定性。这一颗参数把它接进扇出的选择里。

四件事要能被分开读，否则"少抓了"与"这页本来就没有"在字节上不可区分：

1. 地板 <2 是**无操作**（每行至少一个家族）→ 拒绝，不静默；
2. 没开 `fetch_content` 时地板没有作用对象 → 拒绝，不静默；
3. 池子只回一个家族时地板**不适用**（`1 of 1` 是"没人能同意"，不是"没人同意"）；
4. 地板清空全部候选时**照实抓 0 页并报数**，不回退到"反正都抓"。

纯函数 `gate_by_consensus` 承载全部判据，所以这里的单元测试直接钉它；集成测试再走
一遍 `server.web_search`，确认接线与回执。
"""

import json
from unittest.mock import AsyncMock

import pytest

from dhole_mcp import search as search_mod
from dhole_mcp.search import SearchResult, _parse_consensus, gate_by_consensus
from dhole_mcp.search_engines import EngineReport, RawResult
from dhole_mcp.server import MasterFetchServer, ResponseModel


def _row(url: str, consensus: str, tier: str = "high") -> SearchResult:
    return SearchResult(title=f"t {url}", url=url,
                        engines_consensus=consensus, fetch_relevance=tier)


class TestTheParser:

    def test_it_reads_the_ratio(self):
        assert _parse_consensus("2 of 6") == (2, 6)

    def test_it_reads_the_no_corroboration_spelling(self):
        assert _parse_consensus("1 of 1 (no corroboration)") == (1, 1)

    def test_an_unreadable_string_is_not_read_as_zero_agreement(self):
        """解析失败返回 (0, 0)，让闸门走"不适用"——不能把一次解析失败读成
        "零个家族同意"，那会丢掉每一行。"""
        assert _parse_consensus("") == (0, 0)
        assert _parse_consensus("garbage") == (0, 0)
        assert _parse_consensus(None) == (0, 0)


class TestTheGate:

    def test_off_by_default(self):
        rows = [_row("https://a/1", "1 of 6"), _row("https://a/2", "3 of 6")]
        kept, skipped, note = gate_by_consensus(rows, 0)
        assert kept == rows and skipped == [] and note == ""

    def test_a_floor_of_one_is_a_noop_and_says_nothing(self):
        """地板 1 由调用方那一侧拒绝；纯函数这里按"没设"处理。"""
        rows = [_row("https://a/1", "1 of 6")]
        kept, skipped, note = gate_by_consensus(rows, 1)
        assert kept == rows and skipped == [] and note == ""

    def test_it_keeps_only_corroborated_rows(self):
        rows = [_row("https://a/1", "1 of 6"), _row("https://a/2", "3 of 6"),
                _row("https://a/3", "2 of 6")]
        kept, skipped, note = gate_by_consensus(rows, 2)
        assert [r.url for r in kept] == ["https://a/2", "https://a/3"]
        assert [r.url for r in skipped] == ["https://a/1"]
        assert "fetched 2" in note and "skipped 1" in note

    def test_a_single_family_pool_is_not_read_as_disagreement(self):
        """`1 of 1 (no corroboration)` 是"没人**能**同意"——池子降级或太小。把这份
        沉默读成反对，会在坏天气里丢掉每一行。"""
        rows = [_row("https://a/1", "1 of 1 (no corroboration)"),
                _row("https://a/2", "1 of 1 (no corroboration)")]
        kept, skipped, note = gate_by_consensus(rows, 2)
        assert kept == rows and skipped == []
        assert "not applied" in note and "single index family" in note

    def test_clearing_every_candidate_fetches_nothing_and_says_so(self):
        rows = [_row("https://a/1", "1 of 6"), _row("https://a/2", "1 of 6")]
        kept, skipped, note = gate_by_consensus(rows, 2)
        assert kept == []
        assert [r.url for r in skipped] == ["https://a/1", "https://a/2"]
        assert "nothing was fetched" in note

    def test_no_candidates_is_a_noop(self):
        assert gate_by_consensus([], 2) == ([], [], "")


# ─── integration through server.web_search ─────────────────────────────────

def _fake_search(consensus: list[int], engines=("brave", "bing", "baidu", "yandex", "sogou", "so360")):
    async def fake_multi_search(query, max_results, **kwargs):
        rows = [RawResult(title=f"widgets guide {i}",
                          url=f"https://a{i}.example.test/widgets-guide",
                          snippet="widgets guide", source="brave",
                          position=i + 1, consensus=c)
                for i, c in enumerate(consensus)]
        return rows, [EngineReport(name=e, ok=True) for e in engines]
    return fake_multi_search


def _force_neural(monkeypatch, scores: list[float]):
    """让 _rank 走 neural 分支并交出可控分数。

    为什么必须动这一步：merge 模式下排序分数是"位置分 + 共识加成"，而共识加成
    (0.2/家族) 远大于标题+URL 的加成上限 (0.18)——于是单家族行永远排在多家族行之后，
    闸门在 merge 模式下结构性地不可达。闸门真正会咬的场合是**神经模型判断与共识
    不一致**时（高相关但单一家族 vs 低相关但多家佐证），那正是调用方会想覆盖的
    那一刻，也正是这里要复现的。
    """
    monkeypatch.setattr(search_mod, "ensure_reranker", AsyncMock(return_value=object()))
    monkeypatch.setattr(search_mod, "neural_rerank",
                        lambda q, ranked, **kw: [(r, s) for r, s in zip(ranked, scores)])


def _server() -> MasterFetchServer:
    server = MasterFetchServer()
    server.web_read = AsyncMock(return_value=ResponseModel(
        url="u", status=200, content=["page text"], content_ok=True))
    return server


class TestTheSearchCall:

    @pytest.mark.asyncio
    async def test_off_by_default_changes_nothing(self, monkeypatch):
        monkeypatch.setattr(search_mod, "multi_search", _fake_search([3, 3, 3]))
        _force_neural(monkeypatch, [0.9, 0.8, 0.7])
        server = _server()

        out = await server.web_search("widgets guide", fetch_content=True, cache_ttl=0)

        assert len(out.fetched_pages) == 3
        assert out.consensus_skipped == []

    @pytest.mark.asyncio
    async def test_the_floor_drops_the_uncorroborated_top_hit(self, monkeypatch):
        """排名第一但只有一家索引：开了地板就不花那一笔抓取。"""
        monkeypatch.setattr(search_mod, "multi_search", _fake_search([1, 3, 3]))
        _force_neural(monkeypatch, [0.90, 0.20, 0.18])
        server = _server()

        out = await server.web_search("widgets guide", fetch_content=True,
                                      fetch_min_consensus=2, cache_ttl=0)

        assert [p["url"] for p in out.fetched_pages] == [
            "https://a1.example.test/widgets-guide",
            "https://a2.example.test/widgets-guide"]
        assert out.consensus_skipped == ["https://a0.example.test/widgets-guide"]
        assert "fetched 2, skipped 1" in out.summary

    @pytest.mark.asyncio
    async def test_every_fetched_page_carries_its_own_consensus(self, monkeypatch):
        monkeypatch.setattr(search_mod, "multi_search", _fake_search([3, 3, 3]))
        _force_neural(monkeypatch, [0.9, 0.8, 0.7])
        server = _server()

        out = await server.web_search("widgets guide", fetch_content=True, cache_ttl=0)

        assert all(p["engines_consensus"] == "3 of 6" for p in out.fetched_pages)

    @pytest.mark.asyncio
    async def test_a_failed_fetch_still_carries_its_consensus(self, monkeypatch):
        """失败占位也带共识串——否则"这页没抓到"与"这页没被佐证"要分开查两次。"""
        monkeypatch.setattr(search_mod, "multi_search", _fake_search([3]))
        _force_neural(monkeypatch, [0.9])
        server = MasterFetchServer()
        server.web_read = AsyncMock(side_effect=RuntimeError("boom"))

        out = await server.web_search("widgets guide", fetch_content=True, cache_ttl=0)

        assert out.fetched_pages[0]["engines_consensus"] == "3 of 6"
        assert out.fetched_pages[0]["content_ok"] is False

    @pytest.mark.asyncio
    async def test_a_single_family_pool_leaves_the_floor_unapplied(self, monkeypatch):
        monkeypatch.setattr(search_mod, "multi_search", _fake_search([1, 1, 1], engines=("brave",)))
        _force_neural(monkeypatch, [0.9, 0.8, 0.7])
        server = _server()

        # engines=["brave"] 把池子收窄到一个家族 —— 分母因此是 1，每一行都成
        # "1 of 1 (no corroboration)"，地板无处施加。
        out = await server.web_search("widgets guide", fetch_content=True,
                                      engines=["brave"],
                                      fetch_min_consensus=2, cache_ttl=0)

        assert len(out.fetched_pages) == 3          # 一页没丢
        assert out.consensus_skipped == []
        assert "not applied" in out.summary

    @pytest.mark.asyncio
    async def test_a_floor_that_clears_everything_fetches_nothing(self, monkeypatch):
        """显式点了地板就得照它执行：静默回退到"反正都抓"是替调用方做了相反的决定。"""
        monkeypatch.setattr(search_mod, "multi_search", _fake_search([1, 1, 1]))
        _force_neural(monkeypatch, [0.9, 0.8, 0.7])
        server = _server()

        out = await server.web_search("widgets guide", fetch_content=True,
                                      fetch_min_consensus=2, cache_ttl=0)

        assert out.fetched_pages == []
        assert len(out.consensus_skipped) == 3
        assert "nothing was fetched" in out.summary


class TestTheRefusals:
    """一颗显式给出的参数不能被静默忽略——两条表外用法都在发请求前拒掉。"""

    @pytest.mark.asyncio
    async def test_a_floor_of_one_is_refused(self):
        server = _server()
        out = await server.web_search("q", fetch_content=True,
                                      fetch_min_consensus=1, cache_ttl=0)
        assert out.results == []
        assert "must be 0 (off) or >= 2" in out.error
        server.web_read.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_floor_without_fetch_content_is_refused(self):
        server = _server()
        out = await server.web_search("q", fetch_min_consensus=2, cache_ttl=0)
        assert out.results == []
        assert "only applies with fetch_content=true" in out.error


class TestTheWireAndTheManual:

    def test_the_parameter_is_on_the_wire(self):
        defs = {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}
        props = defs["web_search"]["inputSchema"]["properties"]
        assert props["fetch_min_consensus"]["type"] == "integer"
        assert "consensus_skipped" in props["fetch_min_consensus"]["description"]

    def test_the_parameter_is_accepted_by_the_dispatch_whitelist(self):
        from dhole_mcp.server import _SS_PARAMS
        assert "fetch_min_consensus" in _SS_PARAMS

    def test_a_string_floor_is_normalized(self):
        """字符串客户端写 "2" 时，`"2" >= 2` 会当场 TypeError——这颗进归一化表。"""
        from dhole_mcp.params import _ARG_TYPES
        assert _ARG_TYPES["web_search"]["fetch_min_consensus"] == "int"

    def test_an_empty_skip_list_is_omitted_from_the_wire(self):
        from dhole_mcp.search import SearchResponseModel
        from dhole_mcp.wire import _wire_dump
        clean = _wire_dump(SearchResponseModel(query="q", results=[]))
        assert "consensus_skipped" not in clean
        dirty = _wire_dump(SearchResponseModel(
            query="q", results=[], consensus_skipped=["https://a/1"]))
        assert dirty["consensus_skipped"] == ["https://a/1"]

    def test_the_manual_documents_the_floor_and_its_boundaries(self):
        from dhole_mcp import guide
        text = (guide._ROOT / "references" / "tools.md").read_text(encoding="utf-8")
        assert "fetch_min_consensus" in text
        assert "consensus_skipped" in text
        assert "not applied" in text          # 单家族池那条边界
        assert "nothing is fetched" in text   # 清空候选那条边界
