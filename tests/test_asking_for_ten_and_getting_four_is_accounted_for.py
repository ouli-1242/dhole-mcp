""""要 10 条回了 4 条"要对得起这道问句（体验修复刀2-S4）。

此前的形状：`total_results: 4` 是唯一的数，调用方要自己记住我要了几条、再去数回来的
条数，而"少掉的那几去哪了"在部分丢弃时被压成一句 `dropped as off-topic/low-relevance`
—— 三个因（相关性地板、被判 low、没命中查询词）各有各的解药，合并成一句就等于让人猜：
抬 `min_relevance` 治不了"没命中查询词"，换 `engines=` 治不了被判 low。

S2 之后 low 有**两个可能的作者**：模型的判决，或没有模型时那条只准降级的位置尺。
两句的含义相反（前者="模型认为不相关"，后者="没人判断过，它只是排得靠后"），所以
点名的话必须把尺子一起带出来。
"""

from __future__ import annotations

import pytest

from dhole_mcp import search as S
from dhole_mcp.search_engines import EngineReport, RawResult
from dhole_mcp.wire import _wire_dump


def _raw(title: str, url: str, snippet: str = "", source: str = "bing") -> RawResult:
    return RawResult(title=title, url=url, snippet=snippet, source=source,
                     position=1, consensus=1, sources=(source,))


def _rows(n: int, *, topic: str = "dns resolution") -> list[RawResult]:
    return [_raw(f"{topic} doc {i}", f"https://e{i}.example/",
                 f"{topic} walkthrough number {i}") for i in range(n)]


def _patch(monkeypatch, results, *, scores=None, used="merge", raws=None):
    """引擎、缓存、`_rank` 三处桩：把这一轮变成可完全指定的输入。

    `_rank` 被打桩时 `used` 就是"这把尺到底跑没跑"的声明 —— 被测的那句话
    （`the model scored them off-topic` / `no reranker ran`）的正反两向都由它驱动。
    """
    rows = list(results)
    if raws:
        for r, v in zip(rows, raws):
            r.neural_raw = v

    async def fake_multi(query, max_results, **kwargs):
        return list(rows), [EngineReport(name="bing", ok=True, status="ok")]

    async def pass_through(*a, **k):
        return None

    monkeypatch.setattr(S, "multi_search", fake_multi)
    monkeypatch.setattr(S, "get_cached", pass_through)
    monkeypatch.setattr(S, "set_cached", pass_through)
    monkeypatch.setattr(S, "ensure_reranker", pass_through)
    if scores is not None:
        monkeypatch.setattr(S, "_rank", lambda q, ranked, mode, **kw: (
            ranked, scores[:len(ranked)], used, ""))
    return S


class TestThePairIsEmittedOnlyWhenItSaysSomething:

    @pytest.mark.asyncio
    async def test_asking_for_ten_and_getting_fewer_names_both_numbers(self, monkeypatch):
        srv = _patch(monkeypatch, _rows(4))
        out = await srv.web_search(None, "dns resolution", 10, cache_ttl=0)
        assert (out.requested, out.delivered) == (10, 4), out.requested
        # 这对数不许替调用方做减法，也不许让他从 `total_results` 反推。
        assert out.total_results == 4

    @pytest.mark.asyncio
    async def test_a_round_that_delivered_what_was_asked_says_nothing(self, monkeypatch):
        """相等时这一对不携带信息，所以它必须**缺席**而不是写两个 10。"""
        srv = _patch(monkeypatch, _rows(12))
        out = await srv.web_search(None, "dns resolution", 6, cache_ttl=0)
        assert out.requested is None and out.delivered is None
        dumped = _wire_dump(out)
        assert "requested" not in dumped and "delivered" not in dumped, list(dumped)

    @pytest.mark.asyncio
    async def test_the_pair_reaches_a_cache_replay_too(self, monkeypatch):
        """两个入口共用一处判定 —— 否则第二次问同一句就换了套话（R4/S7 同一条病）。"""
        srv = _patch(monkeypatch, _rows(3))
        stored: dict = {}

        async def capture(url, extraction_type, content, *a, **k):
            stored["payload"] = content

        monkeypatch.setattr(S, "set_cached", capture)
        out = await srv.web_search(None, "dns resolution", 9, cache_ttl=60)
        assert (out.requested, out.delivered) == (9, 3)
        assert "payload" in stored, "这一轮没被存下来，缓存命中无从重放这对数"

        async def hit(url, extraction_type, css_selector, ttl, scope):
            return {"status": 200, "content": stored["payload"], "url": url,
                    "content_type": "application/json", "total_size_bytes": 1,
                    "envelope": "{}"}

        monkeypatch.setattr(S, "get_cached", hit)
        again = await srv.web_search(None, "dns resolution", 9, cache_ttl=60)
        assert again.cached is True, "没走到命中支，这对数就没被复核过"
        assert (again.requested, again.delivered) == (9, 3), again.fetch_hint


class TestEachCauseCarriesItsOwnCount:

    @pytest.mark.asyncio
    async def test_a_low_label_is_attributed_to_the_ruler_that_made_it(self, monkeypatch):
        """没有模型时，"low" 只是"排得靠后" —— 这句话不许省，否则调用方读到的是
        "模型认为这条不相关"，而模型根本没跑。"""
        srv = _patch(monkeypatch, _rows(6),
                     scores=[1.0, 0.9, 0.8, 0.02, 0.01, 0.005], used="merge")
        out = await srv.web_search(None, "dns resolution", 6, cache_ttl=0)
        note = out.fetch_hint
        assert "labelled low" in note, note
        assert "no reranker ran" in note, note
        assert "dropped as off-topic/low-relevance" not in note, note

    @pytest.mark.asyncio
    async def test_a_verdict_backed_low_says_the_model_scored_it(self, monkeypatch):
        """反向：模型跑了就不许说"没跑" —— 同一句话在两种轮次里各自成立才算点名。"""
        srv = _patch(monkeypatch, _rows(6),
                     scores=[1.0, 0.9, 0.8, 0.02, 0.01, 0.005], used="neural",
                     raws=[0.99, 0.97, 0.95, 0.0001, 0.0002, 0.0003])
        out = await srv.web_search(None, "dns resolution", 6, cache_ttl=0)
        assert "the model scored them off-topic" in out.fetch_hint, out.fetch_hint
        assert "no reranker ran" not in out.fetch_hint, out.fetch_hint

    @pytest.mark.asyncio
    async def test_two_causes_are_two_clauses_not_one_slash(self, monkeypatch):
        """地板丢的与被判 low 的是两笔账，合并句里那个总数是唯一能对上的数，而它
        刚好什么都不说：抬 `min_relevance` 治不了后者，动 `min_raw_relevance` 治不了前者。"""
        rows = _rows(6)
        srv = _patch(monkeypatch, rows)

        def truncated_rank(q, ranked, mode, **kw):
            # 6 个候选里地板先吃掉 1 个；剩下的 5 个再被档位判掉 2 个（留 3 条，
            # 刚好过 `min_keep` —— 不足 3 条时过滤器会全留，两因就凑不到一起了）。
            return list(ranked[:5]), [1.0, 0.9, 0.8, 0.02, 0.01], "merge", ""

        monkeypatch.setattr(S, "_rank", truncated_rank)
        out = await srv.web_search(None, "dns resolution", 6, cache_ttl=0)
        note = out.fetch_hint
        assert "below the relevance floor you set" in note, note
        assert "labelled low" in note, note
        # 逐因各带自己的数，分隔符用 ";" 而不是 "," —— 括号里的说明本身带逗号，
        # 用逗号分家的话这句话就再也解析不开了（人读也一样）。
        assert "3 engine result(s) dropped: 1 below the relevance floor you set; " \
               "2 labelled low (no reranker ran" in note, note
        assert len(out.results) == 3, out.results
        assert (out.requested, out.delivered) == (6, 3), (out.requested, out.delivered)

    def test_the_off_topic_clear_is_never_partial(self):
        """"没命中查询词"这一因**结构上不可能只吃一半**：它是整轮全有或全无的守卫
        （`_filter_irrelevant_results` 对 blob 里一个词都没命中时才清场）。
        写下来是因为不看这条就会去测一个不存在的组合。"""
        from dhole_mcp.search import SearchResult as _SR
        on_topic = [_SR(title="dns resolution doc", url="https://a.test/1",
                        snippet="how dns resolution works")]
        off_topic = [_SR(title="Zhihu", url="https://www.zhihu.com/", snippet="首页推荐")]
        assert S._filter_irrelevant_results(on_topic, "dns resolution") == on_topic
        assert S._filter_irrelevant_results(off_topic, "dns resolution") == []

    @pytest.mark.asyncio
    async def test_an_emptied_round_uses_the_same_cause_list(self, monkeypatch):
        """两个分支不许各自演化出两套说法（同一件事两个作者 = 迟早对不上）。
        清空的那一支今天还得带上地板的名字与 raw 跨度 —— 清空一轮的人最需要下一个数。"""
        srv = _patch(monkeypatch, _rows(4))

        def empty_rank(q, ranked, mode, **kw):
            return [], [], "neural", ""

        monkeypatch.setattr(S, "_rank", empty_rank)
        out = await srv.web_search(None, "dns resolution", 6, cache_ttl=0,
                                   min_raw_relevance=0.5)
        assert out.results == []
        assert "dhole dropped them rather than return noise" in out.error, out.error
        assert "4 below min_raw_relevance=0.5" in out.error, out.error
        assert "no reranker ran" not in out.error, out.error


class TestTheCauseListHasOneAuthor:

    def test_zero_counts_are_not_rendered_as_zero_items(self):
        """没发生的因不该在句子里占一格 —— "0 below the floor" 是一条没人问过的账。"""
        from dhole_mcp.search import _drop_causes
        assert _drop_causes(rank_dropped=0, low_dropped=2, off_topic=0,
                            floors=[], verdict_ran=True) == (
            "2 labelled low (the model scored them off-topic)")
        assert _drop_causes(rank_dropped=3, low_dropped=0, off_topic=1,
                            floors=["min_relevance=0.5"], verdict_ran=False) == (
            "3 below min_relevance=0.5; 1 matched no query term")
