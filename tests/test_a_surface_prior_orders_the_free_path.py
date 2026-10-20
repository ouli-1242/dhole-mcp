"""On the no-model path, rows carrying a query term outrank rows that carry none.

Measured on the 67-query frozen SERP corpus (the same chain
`evals/ranking_baseline.py` replays): agent-a P@5 0.370 -> 0.525, MRR 0.550 ->
0.731, nDCG@10 0.646 -> 0.759. Fourteen queries changed, every one upward. The
prior NEVER runs when a model scored the round: a synonym-only row the model
called relevant must not be pushed down because the words do not literally match.
"""

from dhole_mcp.search import _apply_quality_boost
from dhole_mcp.search_engines import RawResult


def _row(title, url="https://example.com/x", snippet="", position=1,
         consensus=1, raw=None):
    r = RawResult(title=title, url=url, snippet=snippet, source="bing",
                  position=position, consensus=consensus, sources=("bing",))
    if raw is not None:
        r.neural_raw = raw
    return r


def test_a_generic_row_yields_to_a_term_matching_row_without_a_model():
    generic = _row("Welcome to Python.org", "https://python.org/", position=1)
    match = _row("asyncio.Semaphore usage", "https://example.com/asyncio-semaphore",
                 position=9)
    ranked, _scores = _apply_quality_boost(
        [generic, match], [0.95, 0.10], "asyncio semaphore")
    assert [r.title for r in ranked] == [match.title, generic.title]


def test_a_weak_one_term_match_yields_to_a_two_term_row():
    """q001 的形状：泛化页标题带一个宽词（python），真正答案带两个具体词。"""
    weak = _row("Python tutorial", "https://example.com/python", position=1)
    strong = _row("asyncio.Semaphore usage", "https://example.com/asyncio-semaphore",
                  position=9)
    ranked, _scores = _apply_quality_boost(
        [weak, strong], [0.95, 0.10], "python asyncio semaphore")
    assert [r.title for r in ranked] == [strong.title, weak.title]


def test_a_model_verdict_keeps_the_order():
    """模型已判过就不许词面接管：泛化行 raw 更高时仍排第一。"""
    generic = _row("Welcome to Python.org", "https://python.org/", position=1, raw=0.9)
    match = _row("asyncio.Semaphore usage", "https://example.com/asyncio-semaphore",
                 position=9, raw=0.1)
    ranked, _scores = _apply_quality_boost(
        [generic, match], [0.95, 0.10], "asyncio semaphore")
    assert [r.title for r in ranked] == [generic.title, match.title]


def test_one_model_verdict_is_enough_to_skip_the_prior():
    """混血轮（只有部分行带 raw）也不覆盖：有判决就按判决。"""
    generic = _row("Welcome to Python.org", "https://python.org/", position=1, raw=0.9)
    match = _row("asyncio.Semaphore usage", "https://example.com/asyncio-semaphore",
                 position=9)
    ranked, _scores = _apply_quality_boost(
        [generic, match], [0.95, 0.10], "asyncio semaphore")
    assert [r.title for r in ranked] == [generic.title, match.title]


def test_a_url_term_keeps_the_row_in_the_matched_group():
    """URL 里的词面也算这条行自己带的证据（标题/摘要都没写时）。"""
    generic = _row("Welcome", "https://python.org/", position=1)
    by_url = _row("Article", "https://example.com/asyncio-semaphore", position=9)
    ranked, _scores = _apply_quality_boost(
        [generic, by_url], [0.95, 0.10], "asyncio semaphore")
    assert ranked[0] is by_url


def test_no_query_terms_leaves_order_alone():
    generic = _row("Welcome", "https://python.org/", position=1)
    match = _row("asyncio.Semaphore usage", "https://example.com/asyncio", position=9)
    ranked, _scores = _apply_quality_boost(
        [generic, match], [0.95, 0.10], "!!!")
    assert [r.title for r in ranked] == [generic.title, match.title]


def test_cjk_terms_are_visible_in_the_surface():
    generic = _row("Python 教程", "https://example.com/python", position=1)
    match = _row("asyncio.Semaphore 信号量用法", "https://example.com/a", position=9)
    ranked, _scores = _apply_quality_boost(
        [generic, match], [0.95, 0.10], "信号量 用法")
    assert ranked[0] is match


def test_the_prior_never_drops_or_duplicates_a_row():
    rows = [
        _row("Welcome", "https://example.com/1", position=1),
        _row("asyncio guide", "https://example.com/2", position=2),
        _row("unrelated", "https://example.com/3", position=3),
    ]
    ranked, scores = _apply_quality_boost(rows, [0.9, 0.5, 0.4], "asyncio")
    assert len(ranked) == len(rows) == len(scores)
    assert {id(r) for r in ranked} == {id(r) for r in rows}
