"""badge 背后的那把尺必须活着走到铸 badge 那一步（体验修复刀2-S1）。

链上流转的 `relevance_score` 是**集合内归一**的数：`_apply_quality_boost` 除以集合
最大值、`reranker.rerank` 用 (s-min)/(max-min)，min/max 相同时全体记 1.0。于是"集合
里最好的一条"永远是 1.0 —— 这个数**按构造**说不出"这一整轮没有一条相关"，而它正是
今天 `fetch_relevance="high"` 的原料（B0 量的前态：67 题冻结语料上 60.1% 的行领到
high，其中"仅因 rank==1"的是 0 条，因为归一之后这两件事恒同真 —— 位置穿着证据的外
衣，不需要那半句 `or rank == 1` 也穿）。

未归一的交叉编码器 sigmoid（下称 raw）说得出那句话（`reranker.py` 记着自己的标定：
离题文档 ≈1e-4、在题 0.93+，两群几乎不相触）。S1 只把 raw 送到能用的地方：盖在行上
（`RawResult.neural_raw`，None = 这一轮没有模型量过），跟着走完 `_rank` →
`_apply_quality_boost` → `_diversify` → `_build_results`。**判决位在 S2**，这一刀不
改任何 badge —— 它改的是一条证据的可得性。

为什么带在行上而不是把元组加宽成三元：`rerank()` 交回 `(result, score)` 是它
docstring 明写的兼容契约，五处测试桩按二元组喂。加宽把"多送一条证据"变成一次破坏性
改动，而那是这一轮最不该付的账。
"""

from __future__ import annotations

import pytest

from dhole_mcp import reranker as RR
from dhole_mcp import search as S
from dhole_mcp.search_engines import RawResult


class _FakeModel:
    """按 title 查表的假交叉编码器：让"哪条行拿到哪个分"在断言里可核对。"""

    def __init__(self, by_title: dict[str, float]):
        self.by_title = by_title
        self.calls: list[list[str]] = []

    def score(self, query, docs):
        self.calls.append(list(docs))
        out = []
        for d in docs:
            title = d.split(" ", 1)[0]
            if title not in self.by_title:
                raise AssertionError(f"unmapped doc title: {title!r}")
            out.append(self.by_title[title])
        return out


def _row(url: str, title: str, *, consensus: int = 1, position: int = 1) -> RawResult:
    return RawResult(title=title, url=url, snippet=f"{title} body", source="bing",
                     position=position, consensus=consensus, sources=("bing",))


@pytest.fixture
def model(monkeypatch):
    def _install(by_title: dict[str, float]) -> _FakeModel:
        fake = _FakeModel(by_title)
        monkeypatch.setattr(RR, "get_reranker", lambda: fake)
        return fake
    return _install


class TestTheRawScoreTravelsWithItsRow:

    def test_the_number_a_row_got_is_the_number_that_row_carries(self, model):
        """对齐性：raw 必须跟着**它自己那条行**，不是跟着位置。"""
        rows = [_row("https://a.test/1", "alpha", position=1),
                _row("https://b.test/2", "beta", position=2),
                _row("https://c.test/3", "gamma", position=3)]
        model({"alpha": 0.9996, "beta": 0.00012, "gamma": 0.7})
        ranked, scores, used, _note = S._rank("alpha beta gamma", rows, "neural")
        assert used == "neural"
        by_url = {r.url: r for r in ranked}
        assert by_url["https://a.test/1"].neural_raw == 0.9996
        assert by_url["https://b.test/2"].neural_raw == 0.00012
        assert by_url["https://c.test/3"].neural_raw == 0.7
        # 归一分数仍在它原来的路上（这一刀不动它）。
        assert max(scores) == 1.0 and min(scores) == 0.0

    def test_the_boost_reorders_rows_but_moves_no_raw(self, model):
        """`_apply_quality_boost` 会重排并把分数除以最大值 —— 它**不许**顺手把 raw
        也归一化：1e-4 一旦变成 1.0，"这条其实没被任何证据支持过"就再也读不出来。"""
        rows = [_row("https://a.test/1", "alpha", position=1),
                _row("https://z.test/9", "widgetry", consensus=8, position=2)]
        model({"alpha": 0.9, "widgetry": 0.0001})
        ranked, scores, _used, _n = S._rank("alpha widgetry", rows, "neural")
        ranked, scores = S._apply_quality_boost(ranked, scores, "alpha widgetry")
        ranked, scores = S._diversify(ranked, scores, max_per_domain=2)
        top = ranked[0]
        assert top.url == "https://z.test/9", scores  # 共识把离题那条抬到了第一
        assert top.neural_raw == 0.0001
        assert scores[0] == 1.0
        # 两把尺此刻给出相反的话，而这两句话**都**是真的：归一说"这条最靠前"，
        # raw 说"模型从没支持过它"。S2 要铸的 badge 只能问第二把。
        assert top.neural_raw != scores[0]

    def test_no_model_leaves_the_evidence_absent_not_zero(self, monkeypatch):
        """量不到 ≠ 量为 0：`neural_raw is None` 是"这一轮没有证据"，而 0.0 是
        "模型说它不相关"。把前者写成后者就是本轮 R4 刚拆掉的那类谎。"""
        monkeypatch.setattr(RR, "get_reranker", lambda: None)
        rows = [_row(f"https://a.test/{i}", f"t{i}", position=i + 1) for i in range(3)]
        ranked, scores, used, _note = S._rank("anything", rows, "auto")
        assert used == "merge"
        assert [r.neural_raw for r in ranked] == [None, None, None]
        assert scores == [1.0, 1.0 - 1 / 3, 1.0 - 2 / 3]  # 位置-derived，明写在注释里

    def test_the_raw_is_the_same_number_the_floor_uses(self, model):
        """一处判定、两个用途：`min_raw_relevance` 地板与行上的 stamp 必须同源。
        两套推导迟早会互相否决 —— 而"地板丢掉的分数"与"badge 引用的分数"对不上时，
        调用方拿到的是一条自己无法核对的数字。"""
        rows = [_row("https://a.test/1", "alpha", position=1),
                _row("https://b.test/2", "beta", position=2)]
        fake = model({"alpha": 0.2, "beta": 0.9})
        stats: dict = {}
        pairs = RR.rerank("alpha beta", rows, min_raw_relevance=0.5, stats=stats)
        assert [r.url for r, _s in pairs] == ["https://b.test/2"]
        assert pairs[0][0].neural_raw == 0.9
        assert (stats["raw_min"], stats["raw_max"]) == (0.2, 0.9)
        # 被丢的那条仍带着它自己的量法（可复盘"这轮丢掉了什么"），没被清零也没被改写。
        assert rows[0].neural_raw == 0.2
        assert fake.by_title["alpha"] == 0.2


class TestThePairContractIsNotWidened:

    def test_rerank_still_returns_result_score_pairs(self, model):
        """五处测试桩与 `_rank` 都按二元组解包 —— 加宽元组会把一次"多送一条证据"
        变成破坏性改动。这一颗守的是那个契约，不是实现细节。"""
        model({"alpha": 0.4, "beta": 0.6})
        pairs = RR.rerank("alpha beta",
                          [_row("https://a.test/1", "alpha"),
                           _row("https://b.test/2", "beta")])
        assert pairs is not None
        for pair in pairs:
            assert len(pair) == 2, pair
            r, s = pair
            assert isinstance(r, RawResult)
            assert isinstance(s, float)

    def test_rows_that_never_went_through_a_model_do_not_carry_a_stale_raw(self, model):
        """行是跨调用重用的对象时（引擎适配器、评测重放），上一轮的量法不能冒充本轮。
        S2 的 `raw is None` 分支只有在这条成立时才说得出"这一轮没量过"。"""
        rows = [_row("https://a.test/1", "alpha")]
        model({"alpha": 0.88})
        RR.rerank("alpha", rows)
        assert rows[0].neural_raw == 0.88
        fresh = _row("https://a.test/1", "alpha")
        assert fresh.neural_raw is None, "新造的行必须从「没量过」开始"
