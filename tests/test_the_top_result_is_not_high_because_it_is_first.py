"""第一名不是第一名，所以它不配一个"值得抓"（体验修复刀2-S2/S3）。

`fetch_relevance` 此前由**集合内归一**的分数铸：`_apply_quality_boost` 除以最大值、
`reranker.rerank` 用 (s-min)/(max-min)。于是一整轮全都离题时，最离题的那条仍然是 1.0，
`_tier` 的第一条件恒真 —— 而 `or rank == 1` 是同一句谎的第二作者（B0 实测：删掉它
一个 badge 都不动，因为归一之后这两件事恒同真）。要改的不是那半句，是**问哪把尺**：
未归一的 raw 说得出"这条不相关"，归一分说不出（`reranker.py` 自己记着这条限制）。

判决数据（`evals/ranking_baseline.py --neural --tiers`，67 题冻结语料 / 756 行）：
发着 high 而 raw < 0.05 的行 14 条，逐条回查人工标注 **14 判不相关 / 0 判相关**；
其余 high 行 449 条里人工判相关 299（66.6%）—— 所以 `high` 的含义只能是"值得抓"，
不能是"一定相关"，这一句写进手册而不是写进阈值。
"""

from __future__ import annotations

import pytest

from dhole_mcp.search import (_quality_filter, _tier, compute_fetch_hint,
                              SearchResult)
from dhole_mcp.wire import _wire_dump


def _row(score: float, rank: int, total: int, raw=None, *, url=None) -> SearchResult:
    return SearchResult(
        title=f"row {rank}", url=url or f"https://s.test/{rank}",
        snippet="x", source="bing", position=rank,
        relevance_score=round(score, 4), relevance_raw=raw,
        fetch_relevance=_tier(score, rank, total, raw),
        engines_consensus="2 of 6")


OLD_RULE_HIGH = lambda s, r, t: s >= 0.5 or r == 1  # noqa: E731  前态，用来钉"只准降级"


class TestTheBadgeIsMintedFromTheEvidence:

    @pytest.mark.parametrize("raw,expected", [
        (0.9996, "high"), (0.5, "high"),          # 在题群（实测 p50 0.92、p90 0.9985）
        (0.49, "med"), (0.2, "med"), (0.05, "med"),
        (0.049, "low"), (0.0001, "low"),           # 离题群（p10 0.00013）
    ])
    def test_with_a_verdict_the_tier_follows_it_not_the_seat(self, raw, expected):
        """rank=1、归一分=1.0 的**同一个座位**，可以领到三种档位 —— 档位跟着模型的
        判决走。体验测试里那颗 `.cc` 黑料站就是这个形状：第 1 位、1.0、high。"""
        assert _tier(1.0, 1, 8, raw) == expected

    def test_the_row_carries_the_number_its_badge_was_minted_from(self):
        """badge 的原料必须和 badge 一起端出去：只给结论的调用方只能信或不信，
        给原料的调用方能自己复核"0.5 这条线对我够不够"。"""
        row = _row(1.0, 1, 8, 0.00012)
        assert row.relevance_raw == 0.00012
        assert row.relevance_score == 1.0
        assert row.fetch_relevance == "low"

    def test_no_row_is_high_because_it_is_first(self):
        row = _row(1.0, 1, 8, None)
        assert row.fetch_relevance != "high"


class TestWithoutAVerdictTheRulerMayOnlyDemote:

    @pytest.mark.parametrize("score,rank,total", [
        (1.0, 1, 20), (0.9, 2, 20), (0.6, 5, 20), (0.4, 6, 20), (0.2, 10, 20),
        (0.16, 14, 20), (0.1, 15, 20), (0.02, 19, 20), (0.0, 20, 20),
        (1.0, 1, 3), (0.0, 1, 1),
    ])
    def test_the_none_case_is_the_old_rule_capped_at_med(self, score, rank, total):
        """`raw is None` 那一支必须是**单调降档**：high→med，med/low 一字节不动。
        这条不变量就是"改档位规则不会把交付清空或缩窄"的证明 —— `_quality_filter`
        只看 low，所以 low 集合不动 = 交付不动（B0 实测：merge 臂 low 36→36）。"""
        old = "high" if OLD_RULE_HIGH(score, rank, total) else (
            "med" if (score >= 0.15 or rank <= max(2, total // 3)) else "low")
        new = _tier(score, rank, total, None)
        if old == "low":
            assert new == "low"
        elif old == "med":
            assert new == "med"
        else:
            assert new == "med", (score, rank, total)


class TestTheHintSaysWhyThereIsNoHigh:

    def test_a_round_with_no_verdict_names_the_missing_ruler(self):
        rows = [_row(1.0 - i / 6, i + 1, 6, None) for i in range(6)]
        hint = compute_fetch_hint(rows)
        assert "no reranker" in hint, hint
        assert "nothing is claimed 'high'" in hint, hint
        assert "0 high" in hint, hint

    def test_a_round_with_a_verdict_does_not_claim_to_have_no_reranker(self):
        """反向：有判决时不许把这句话端出来 —— 那是一次自我否证的假谦虚。"""
        rows = [_row(1.0, 1, 3, 0.97), _row(0.5, 2, 3, 0.2),
                _row(0.0, 3, 3, 0.0002)]
        hint = compute_fetch_hint(rows)
        assert "no reranker" not in hint, hint
        assert "1 high" in hint and "1 low" in hint, hint


class TestARelevanceRuleMustNotManufactureAnEmptyRound:

    def test_a_round_the_model_rejects_wholesale_still_delivers(self):
        """这一颗守着 S2 唯一真正可怕的方向：阈值把整轮判成 low，然后过滤器把人
        类问的 10 条变成 0 条。`min_keep` 就是为这个存在的，档位规则改的是它的输入，
        所以它必须被显式钉住。"""
        rows = [_row(1.0 - i / 10, i + 1, 10, 0.0001) for i in range(10)]
        assert all(r.fetch_relevance == "low" for r in rows)
        kept = _quality_filter(rows)
        assert len(kept) == 10, kept
        assert kept, "问 10 得 0 不是诚实，是故障"

    def test_a_mixed_round_drops_only_the_rows_the_model_rejected(self):
        rows = [_row(1.0, 1, 8, 0.99), _row(0.8, 2, 8, 0.6), _row(0.5, 3, 8, 0.2),
                _row(0.1, 4, 8, 0.0004), _row(0.05, 5, 8, 0.0002),
                _row(0.02, 6, 8, 0.0001), _row(0.01, 7, 8, 0.00005),
                _row(0.0, 8, 8, 0.00001)]
        kept = _quality_filter(rows)
        assert [r.relevance_raw for r in kept] == [0.99, 0.6, 0.2]


class TestTheWireOmitsWhatWasNeverMeasured:

    def test_an_unmeasured_row_dumps_no_relevance_raw_key(self):
        dumped = _wire_dump(_row(1.0, 1, 6, None))
        assert "relevance_raw" not in dumped, dumped

    def test_a_measured_row_puts_the_number_on_the_wire(self):
        dumped = _wire_dump(_row(1.0, 1, 6, 0.00013))
        assert dumped["relevance_raw"] == 0.00013, dumped

    def test_a_measured_zero_is_not_omitted_as_if_it_was_never_measured(self):
        """缺席必须只说"没量过"。模型给出 0.0 是一条**判决**（判为不相关），把它
        按默认值省掉，读侧就只剩"这一行没数据" —— 而这一行的数据恰恰是否定的那条。"""
        dumped = _wire_dump(_row(1.0, 1, 6, 0.0))
        assert "relevance_raw" in dumped, dumped
        assert dumped["relevance_raw"] == 0.0, dumped
