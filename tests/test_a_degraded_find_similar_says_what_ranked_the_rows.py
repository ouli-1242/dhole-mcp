"""find_similar 排不了"相似"的时候，必须说清这次是谁排的（2026-10-08 验收 · D-19）

现场（真实调用，只把 `(源页, 候选)` 那一刀的 `rer.score` 换成抛异常，其余全是本机
的活路径：`python /tmp/repro_d19.py`）：

    web_search(mode="find_similar", url="https://clig.dev/", max_results=4)
      rerank_mode : find_similar          <-- 相似度这一步根本没跑
      summary     : ... rerank=find_similar
      scores      : [1.0, 0.9887, 0.9821, 0.9731]   <-- 出自对派生查询词的神经相关度
      fetch_hint  : "... | find_similar to https://clig.dev/ (searched: 'Command Line
                    Interface Guidelines') | ..."   <-- 全篇没有一个字提到这次降级

`rerank_mode` 的字段描述写着 "Rerank used"，而这一轮真正 used 的重排是把每一行拿去
和**那页的标题**比 —— 词面/主题重合，不是页面相似。调用方只读结构化字段时读到的是
"按我点的那页排过相似"，于是把一份主题榜当相似榜用。

修法：降级那两条路（`score` 抛异常、重排器压根不在）不再硬写 `"find_similar"`，
`rerank_mode` 交回 `_rank` 实际用的那个名字（merge / neural），并由
`_similarity_missed_note` 说两半 —— 相似度这步为什么没跑、这次的顺序和分数实际出自
哪里。反向锁同样要紧：相似度这步**跑成了**的时候，一个字都不许多说。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from dhole_mcp import search as search_mod
from dhole_mcp.search import _similarity_missed_note
from dhole_mcp.search_engines import EngineReport, RawResult
from dhole_mcp.server import MasterFetchServer

SOURCE = "https://clig.dev/"
NOTE_MARK = "did NOT run"


def _row(url: str, pos: int) -> RawResult:
    return RawResult(title=f"page at {url}", url=url,
                     snippet="command line interface guidelines",
                     source="brave", position=pos)


class _Reranker:
    """A warm cross-encoder: `score` either answers or raises, as the test wants."""

    def __init__(self, scores=None, raises=None):
        self._scores = scores
        self._raises = raises

    def score(self, _a, docs):
        if self._raises is not None:
            raise self._raises
        return list(self._scores)[:len(docs)]


def _stub(monkeypatch, urls, *, reranker=None, neural=None):
    """引擎与取源都不发真请求。`reranker=None` = 这台机器上没有暖着的重排器。"""
    rows = [_row(u, i) for i, u in enumerate(urls, 1)]

    async def fake_multi_search(query, max_results, **kwargs):
        return rows, [EngineReport(name="brave", ok=True)]

    monkeypatch.setattr(search_mod, "multi_search", fake_multi_search)
    monkeypatch.setattr(search_mod, "ensure_reranker", AsyncMock(return_value=None))
    monkeypatch.setattr(search_mod, "fetch_source_for_similar",
                        AsyncMock(return_value={
                            "title": "The Command Line Interface Guidelines",
                            "body": "an open-source guide to help you write better "
                                    "command-line interfaces",
                            "status": 200, "reason": "", "detail": ""}))
    monkeypatch.setattr(search_mod, "get_reranker", lambda: reranker)
    monkeypatch.setattr(search_mod, "unavailable_reason",
                        lambda: "reranker model download failed (offline?)")
    if neural is not None:
        monkeypatch.setattr(search_mod, "neural_rerank", neural)


async def _find_similar(monkeypatch, *, urls, reranker=None, neural=None):
    _stub(monkeypatch, urls, reranker=reranker, neural=neural)
    return await MasterFetchServer().web_search(mode="find_similar", url=SOURCE,
                                                max_results=5, cache_ttl=0)


def _neural_pairs(query, ranked, **kwargs):
    """The cross-encoder DOES run here - against the derived query words."""
    return [(r, 1.0 - i / 10) for i, r in enumerate(ranked)]


class TestTheFieldNamesTheRerankThatRan:

    @pytest.mark.asyncio
    async def test_a_failed_similarity_stage_stops_claiming_the_mode(self, monkeypatch):
        """症状本体：`score` 抛了，`rerank_mode` 却仍写 find_similar。"""
        r = await _find_similar(
            monkeypatch,
            urls=["https://other.test/a", "https://other.test/b"],
            reranker=_Reranker(raises=RuntimeError("onnx session refused this pair")),
            neural=_neural_pairs)
        assert r.rerank_mode != "find_similar", r.rerank_mode
        assert r.rerank_mode == "neural", f"字段要说这次实际跑了谁: {r.rerank_mode}"

    @pytest.mark.asyncio
    async def test_the_summary_line_agrees_with_the_field(self, monkeypatch):
        r = await _find_similar(
            monkeypatch,
            urls=["https://other.test/a", "https://other.test/b"],
            reranker=_Reranker(raises=TimeoutError("encoder timed out")),
            neural=_neural_pairs)
        assert f"rerank={r.rerank_mode}" in r.summary, r.summary
        assert "rerank=find_similar" not in r.summary, r.summary

    @pytest.mark.asyncio
    async def test_no_reranker_at_all_reports_the_order_it_used(self, monkeypatch):
        r = await _find_similar(
            monkeypatch,
            urls=["https://other.test/a", "https://other.test/b"],
            reranker=None,
            neural=lambda *a, **k: None)
        assert r.rerank_mode == "merge", r.rerank_mode


class TestTheProseNamesTheBasis:

    @pytest.mark.asyncio
    async def test_the_note_says_both_halves_when_the_encoder_still_ran(self, monkeypatch):
        r = await _find_similar(
            monkeypatch,
            urls=["https://other.test/a", "https://other.test/b"],
            reranker=_Reranker(raises=RuntimeError("onnx session refused this pair")),
            neural=_neural_pairs)
        hint = r.fetch_hint
        assert NOTE_MARK in hint, f"相似度这步没跑却没说: {hint}"
        assert "topical match, not page similarity" in hint, hint
        assert "Command Line Interface Guidelines" in hint, \
            f"要说出分数是拿什么比的: {hint}"
        assert "RuntimeError" in hint, f"为什么没跑也要给: {hint}"

    @pytest.mark.asyncio
    async def test_the_note_says_nothing_was_scored_when_no_model_ran(self, monkeypatch):
        r = await _find_similar(
            monkeypatch,
            urls=["https://other.test/a", "https://other.test/b"],
            reranker=None,
            neural=lambda *a, **k: None)
        hint = r.fetch_hint
        assert NOTE_MARK in hint, hint
        assert "no model scored anything" in hint, hint
        assert "reranker model download failed (offline?)" in hint, hint

    @pytest.mark.asyncio
    async def test_a_note_never_invents_an_exception(self, monkeypatch):
        """重排器缺席那条路的起因是"没有模型"，不是"抛了异常"。"""
        r = await _find_similar(
            monkeypatch,
            urls=["https://other.test/a", "https://other.test/b"],
            reranker=None,
            neural=lambda *a, **k: None)
        assert "raised" not in r.fetch_hint, r.fetch_hint


class TestASuccessfulRoundStaysQuiet:
    """反向锁：相似度真跑成了，这刀一个字都不许多说。"""

    @pytest.mark.asyncio
    async def test_the_mode_still_names_find_similar_when_it_ran(self, monkeypatch):
        r = await _find_similar(
            monkeypatch,
            urls=["https://other.test/a", "https://other.test/b"],
            reranker=_Reranker(scores=[0.9, 0.2]))
        assert r.rerank_mode == "find_similar", r.rerank_mode

    @pytest.mark.asyncio
    async def test_a_clean_round_carries_no_degradation_note(self, monkeypatch):
        r = await _find_similar(
            monkeypatch,
            urls=["https://other.test/a", "https://other.test/b"],
            reranker=_Reranker(scores=[0.9, 0.2]))
        assert NOTE_MARK not in r.fetch_hint, r.fetch_hint


class TestTheFieldAndTheProseAnswerTogether:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reranker,neural,wanted", [
        (_Reranker(scores=[0.9, 0.2]), None, "find_similar"),
        (_Reranker(raises=RuntimeError("boom")), _neural_pairs, "neural"),
        (None, lambda *a, **k: None, "merge"),
    ], ids=["similarity-ran", "similarity-failed-encoder-ran", "no-model"])
    async def test_the_field_and_the_note_are_neither_both_silent_nor_both_loud(
            self, monkeypatch, reranker, neural, wanted):
        r = await _find_similar(monkeypatch,
                                urls=["https://other.test/a", "https://other.test/b"],
                                reranker=reranker, neural=neural)
        assert r.rerank_mode == wanted, r.rerank_mode
        assert (NOTE_MARK in r.fetch_hint) == (wanted != "find_similar"), r.fetch_hint
        # 字段在 wire 上"等于默认就省略"（merge 是默认值），省略不等于说谎：
        # 那一句必须替字段把降级说到。
        if wanted == "merge":
            assert NOTE_MARK in r.fetch_hint

    def test_a_long_reason_does_not_fold_into_a_pair_of_parentheses(self):
        """`_rerank_absent_reason()` 自己带括号与句号 —— 拼进去不能变成括号套括号。"""
        note = _similarity_missed_note("merge", "Some Title",
                                       "no reason recorded - the reranker has not "
                                       "run in this process yet (the first search "
                                       "may still be loading it).")
        assert "((" not in note and "))" not in note, note
        assert "Reason: no reason recorded" in note, note
        assert note.endswith("loading it)."), note
