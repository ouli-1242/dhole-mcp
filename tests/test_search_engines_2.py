"""test_search_engines_2.py —— 由 6 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import pytest
from dhole_mcp import search
from dhole_mcp.search_engines import EngineReport, RawResult
from dhole_mcp import search as S
from dhole_mcp.wire import _wire_dump
from dhole_mcp import reranker as RR
import time
from types import SimpleNamespace
from dhole_mcp import search_metasearch as m
from dhole_mcp.search_engines import cn_index_engines
import inspect
import json
from unittest.mock import AsyncMock
from dhole_mcp import search as search_mod
from dhole_mcp.server import _DISPATCH_REQUIRED, MasterFetchServer


# ============================================================================
# 来源：test_a_retry_promise_is_only_made_where_a_retry_can_help.py
# ============================================================================
# 一句 "retry shortly for more recall" 对三桶里的两桶是空头承诺（体验修复刀2-S7）。

def _row(name: str = "t", url: str = "https://a.test") -> search.SearchResult:
    return search.SearchResult(title=name, url=url, fetch_relevance="high")


def _na(**kw) -> str:
    """直调 `_search_next_action`，把可变参数按桶给齐。"""
    results = kw.pop("results", None)
    return search._search_next_action(
        results if results is not None else [_row()],
        kw.pop("engine_blocked", []) or [],
        kw.pop("error", ""),
        kw.pop("engines_used", ["bing"]),
        **kw)


class TestTheDenominatorCountsOnlyEnginesThatWereAsked:

    def test_preempted_names_do_not_dilute_the_diversity_warning(self):
        """实测那一轮：2 家被墙 + 4 家被取消 + 1 家应答。

        preempted 进分母 → 2/7 = 0.29 → 闭嘴；preempted 出局 → 2/3 → 警告。
        """
        got = _na(engine_blocked=["brave", "yahoo"], engines_used=["bing"],
                  engine_preempted=["duckduckgo", "startpage", "qwant", "mojeek"])
        assert "2 of the 3 engines it asked didn't contribute" in got, got
        assert "LOW diversity" in got, got

    def test_preempted_never_appear_in_the_warning_s_counts(self):
        """警告那句只数"被问了却没答上来"的：`3` 里不许混进被取消的三家。"""
        got = _na(engine_blocked=["brave"], engine_empty=["yahoo"], engines_used=["bing"],
                  engine_preempted=["duckduckgo", "mojeek", "qwant"])
        assert "2 of the 3 engines it asked didn't contribute" in got, got
        assert "of 4" not in got and "of 5" not in got and "of 6" not in got, got


class TestPreemptionIsItsOwnBucket:

    def test_a_healthy_fast_pool_says_so_instead_of_implying_damage(self):
        """只有早停、零故障：这一句必须出现（`engine_preempted` 非空而每个结论位都
        闭嘴，读的人只能自己猜那几家去哪了），而且必须说它不是故障。"""
        got = _na(engine_preempted=["duckduckgo", "mojeek"],
                  engines_used=["bing", "brave", "yandex"])
        assert "were cancelled once enough indexes had answered" in got, got
        assert "the diversity quorum, not a failure" in got, got

    def test_the_promise_not_to_wait_is_its_own_claim(self):
        """早停的杠杆是"改参数"不是"等一会儿" —— 等待必然复现同一个停止点。"""
        got = _na(engine_preempted=["duckduckgo"], engines_used=["bing", "brave", "yandex"])
        assert "waiting and retrying reaches the same stop point" in got, got
        assert "raise max_results" in got and "engines=[...]" in got, got
        assert "retry shortly" not in got, got

    def test_a_pool_where_everyone_answered_says_nothing_about_preemption(self):
        """反向：没取消任何人时不许凭空承诺"有引擎被跳过"。"""
        got = _na(engines_used=["bing", "brave", "yandex"])
        assert "cancelled" not in got and "quorum" not in got, got


class TestEachSilentCauseCarriesItsOwnLever:

    def test_a_refused_engine_gets_the_cooldown_lever(self):
        got = _na(engine_blocked=["brave"], engines_used=["bing", "yandex", "so360"])
        assert "retry after the cooldown clears" in got, got
        assert "60s" in got and "600s" in got, got
        assert "DHOLE_SEARCH_PROXY" in got, got

    def test_an_engine_that_answered_with_nothing_is_not_offered_a_wait(self):
        """它答了，200，一条没有。等 60s 治不好一个已经开口的引擎。"""
        got = _na(engine_empty=["brave"], engines_used=["bing", "yandex", "so360"])
        assert "retry after the cooldown clears" not in got, got
        assert "rephrase if the query is narrow" in got, got

    def test_two_causes_get_two_clauses(self):
        got = _na(engine_blocked=["brave"], engine_empty=["yandex"],
                  engines_used=["bing", "so360", "mojeek"])
        assert "1 blocked" in got and "1 answered but parsed nothing usable" in got, got
        assert "retry after the cooldown clears" in got, got
        assert "rephrase if the query is narrow" in got, got
        # 一个因一句解药：两桶都点名时不早停桶不该被拿来顶包
        assert "cancelled" not in got, got


class TestThePoolNoteInFetchHint:
    """`fetch_hint` 里那句池子健康：报"谁没贡献、属于哪一桶"，不抄 `next_action` 的药方。

    一处一遍（体验修复刀2-S7 的落地形状）：杠杆句只由 `next_action` 说；回放的**出处**
    是事实不是建议，两个结论位都要带 —— 否则第二次问同一句就长得像一次重新核验。
    """

    def test_the_two_causes_are_named_separately(self):
        got = search._pool_health_notes([_row()], ["brave"], 1, engine_empty=["yandex"])
        assert "1 blocked" in got and "1 answered but parsed nothing usable" in got, got
        assert "For more recall" not in got, "解药归 next_action，一句话不许有两个作者"

    def test_a_replayed_round_says_no_engine_was_asked_again(self):
        got = search._pool_health_notes([_row()], ["brave"], 1, replay=True)
        assert "replayed from the cache" in got, got
        assert "retry after the cooldown clears" not in got, got

    def test_preemption_is_not_recounted_here(self):
        """早停由 `next_action` 说**一遍**：健康快池每轮都会取消几家，两个结论位各说
        一次同一句话，读者的训练结果是整段跳过。"""
        got = search._pool_health_notes([_row()], ["brave"], 1)
        assert "quorum" not in got and "cancelled" not in got, got


class TestTheCacheReplayStopsPromisingAFreshRound:

    @pytest.fixture
    def pool(self, monkeypatch):
        """1 家应答 + 1 家被墙 + 1 家被早停，缓存层是真的（live 写完再命中）。"""
        cache: dict = {}
        ranked = [RawResult(title="sqlite WAL mode explained", url="https://sqlite.org/wal",
                            snippet="sqlite WAL journaling", source="bing", position=1)]

        async def fake_multi(query_, max_results, **kwargs):
            return list(ranked), [
                EngineReport(name="bing", ok=True, status="ok"),
                EngineReport(name="brave", blocked=True, status="timeout", error="timed out"),
                EngineReport(name="duckduckgo", preempted=True, status="preempted"),
            ]

        async def _none():
            return None

        async def no_get(key_, cache_type, css_selector, **kwargs):
            return cache.get((key_, cache_type))

        async def no_set(key_, cache_type, content, status, css_selector, ttl, **kwargs):
            cache[(key_, cache_type)] = {"content": content}

        monkeypatch.setattr(search, "multi_search", fake_multi)
        monkeypatch.setattr(search, "ensure_reranker", _none)
        monkeypatch.setattr(search, "get_cached", no_get)
        monkeypatch.setattr(search, "set_cached", no_set)
        return cache

    @pytest.mark.asyncio
    async def test_the_live_round_promises_the_cooldown_because_it_can_be_kept(self, pool):
        r = await search.web_search(None, "sqlite WAL mode", 6, cache_ttl=60, mode="auto")
        assert r.cached is False
        assert "retry after the cooldown clears" in r.next_action, r.next_action

    @pytest.mark.asyncio
    async def test_the_full_prescription_has_one_owner_per_round(self, pool):
        """整条响应里"再等等会有更多召回"只出现一次（`next_action`）。两个结论位各写
        一遍是原形状，而它换来的是读者整段跳过。"""
        r = await search.web_search(None, "sqlite WAL mode", 6, cache_ttl=60, mode="auto")
        assert "For more recall" in r.next_action, r.next_action
        assert "For more recall" not in r.fetch_hint, r.fetch_hint
        assert "didn't contribute" in r.fetch_hint, r.fetch_hint

    @pytest.mark.asyncio
    async def test_a_replay_names_itself_as_a_replay_in_both_conclusion_slots(self, pool):
        """`next_action` 与 `fetch_hint` 是同一句承诺的两个作者，两个都得改口。"""
        await search.web_search(None, "sqlite WAL mode", 6, cache_ttl=60, mode="auto")
        hit = await search.web_search(None, "sqlite WAL mode", 6, cache_ttl=60, mode="auto")
        assert hit.cached is True
        assert "replayed from the cache" in hit.next_action, hit.next_action
        assert "replayed from the cache" in hit.fetch_hint, hit.fetch_hint

    @pytest.mark.asyncio
    async def test_a_replay_offers_the_levers_that_actually_reach_a_fresh_round(self, pool):
        await search.web_search(None, "sqlite WAL mode", 6, cache_ttl=60, mode="auto")
        hit = await search.web_search(None, "sqlite WAL mode", 6, cache_ttl=60, mode="auto")
        assert "cache_ttl=0" in hit.next_action, hit.next_action
        assert "part of the cache identity" in hit.next_action, hit.next_action
        assert "retry after the cooldown clears" not in hit.next_action, hit.next_action

    @pytest.mark.asyncio
    async def test_a_replay_still_separates_the_three_buckets(self, pool):
        """命中缓存不许把降级包装成健康：三桶各自的成员与早停那句都要在。"""
        live = await search.web_search(None, "sqlite WAL mode", 6, cache_ttl=60, mode="auto")
        hit = await search.web_search(None, "sqlite WAL mode", 6, cache_ttl=60, mode="auto")
        for field in ("engines_used", "engine_blocked", "engine_empty", "engine_preempted"):
            assert getattr(hit, field) == getattr(live, field), field
        assert "duckduckgo were cancelled once enough indexes had answered" in hit.next_action


# ============================================================================
# 来源：test_asking_for_ten_and_getting_four_is_accounted_for.py
# ============================================================================
# "要 10 条回了 4 条"要对得起这道问句（体验修复刀2-S4）。

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


# ============================================================================
# 来源：test_the_evidence_behind_a_relevance_badge_survives_the_pipeline.py
# ============================================================================
# badge 背后的那把尺必须活着走到铸 badge 那一步（体验修复刀2-S1）。
# 重名消歧：_row → _row__the_evidence_behind_a_re_5c23

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


def _row__the_evidence_behind_a_re_5c23(url: str, title: str, *, consensus: int = 1, position: int = 1) -> RawResult:
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
        rows = [_row__the_evidence_behind_a_re_5c23("https://a.test/1", "alpha", position=1),
                _row__the_evidence_behind_a_re_5c23("https://b.test/2", "beta", position=2),
                _row__the_evidence_behind_a_re_5c23("https://c.test/3", "gamma", position=3)]
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
        rows = [_row__the_evidence_behind_a_re_5c23("https://a.test/1", "alpha", position=1),
                _row__the_evidence_behind_a_re_5c23("https://z.test/9", "widgetry", consensus=8, position=2)]
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
        rows = [_row__the_evidence_behind_a_re_5c23(f"https://a.test/{i}", f"t{i}", position=i + 1) for i in range(3)]
        ranked, scores, used, _note = S._rank("anything", rows, "auto")
        assert used == "merge"
        assert [r.neural_raw for r in ranked] == [None, None, None]
        assert scores == [1.0, 1.0 - 1 / 3, 1.0 - 2 / 3]  # 位置-derived，明写在注释里

    def test_the_raw_is_the_same_number_the_floor_uses(self, model):
        """一处判定、两个用途：`min_raw_relevance` 地板与行上的 stamp 必须同源。
        两套推导迟早会互相否决 —— 而"地板丢掉的分数"与"badge 引用的分数"对不上时，
        调用方拿到的是一条自己无法核对的数字。"""
        rows = [_row__the_evidence_behind_a_re_5c23("https://a.test/1", "alpha", position=1),
                _row__the_evidence_behind_a_re_5c23("https://b.test/2", "beta", position=2)]
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
                          [_row__the_evidence_behind_a_re_5c23("https://a.test/1", "alpha"),
                           _row__the_evidence_behind_a_re_5c23("https://b.test/2", "beta")])
        assert pairs is not None
        for pair in pairs:
            assert len(pair) == 2, pair
            r, s = pair
            assert isinstance(r, RawResult)
            assert isinstance(s, float)

    def test_rows_that_never_went_through_a_model_do_not_carry_a_stale_raw(self, model):
        """行是跨调用重用的对象时（引擎适配器、评测重放），上一轮的量法不能冒充本轮。
        S2 的 `raw is None` 分支只有在这条成立时才说得出"这一轮没量过"。"""
        rows = [_row__the_evidence_behind_a_re_5c23("https://a.test/1", "alpha")]
        model({"alpha": 0.88})
        RR.rerank("alpha", rows)
        assert rows[0].neural_raw == 0.88
        fresh = _row__the_evidence_behind_a_re_5c23("https://a.test/1", "alpha")
        assert fresh.neural_raw is None, "新造的行必须从「没量过」开始"


# ============================================================================
# 来源：test_a_chinese_query_waits_for_the_index_that_has_chinese.py
# ============================================================================
# 验收刀9-2（F2）：一条中文查询不许因为境外索引先填满配额就收工。

def _install(monkeypatch, spec: dict):
    """spec: backend -> (delay_seconds, result_count)，替掉真索引，时钟由用例定。"""
    for name, (delay, count) in spec.items():
        def _make(name=name, delay=delay, count=count):
            class _Fake:
                disabled = False

                def __init__(self, **kwargs):
                    pass

                def search(self, *a, **k):
                    time.sleep(delay)
                    return [SimpleNamespace(title=f"{name}-{i}",
                                            href=f"https://{name}.test/{i}", body="b")
                            for i in range(count)]
            _Fake.name = name
            return _Fake
        monkeypatch.setitem(m._TEXT_ENGINES, name, _make())
    monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
    monkeypatch.setattr(m, "_BACKEND_HEALTH", {}, raising=False)
    monkeypatch.setattr(m, "_CHALLENGE_COUNTS", {}, raising=False)
    monkeypatch.setattr(m, "_SOFT_DEADLINE", 0.0)


# ─────────────── 判据本身：只有字符，没有语言猜测 ───────────────

@pytest.mark.parametrize("query", [
    "PCIe 7.0 规范 发布 128GT/s",       # 报告 S3 的原始查询：混着术语
    "缓存 过期 时间",
    "日本語のクエリです",               # 假名
    "한국어 질의입니다",                 # 谚文
])
def test_a_query_written_with_cjk_characters_names_the_engines_to_wait_for(query):
    assert cn_index_engines(query) == frozenset(
        {"baidu", "sogou", "so360", "shenma"}), query


@pytest.mark.parametrize("query", [
    "cache expiry time", "PCIe 7.0 specification release", "", None, "128 GT/s",
])
def test_a_query_without_those_characters_waits_for_nobody(query):
    """英文那一侧不许因为这一刀多等一秒：交回空集，门上的判断因此短路。"""
    assert cn_index_engines(query) == frozenset(), repr(query)


# ─────────────── 门：中文等，英文照旧收工 ───────────────

@pytest.mark.asyncio
async def test_a_chinese_query_waits_for_the_engine_that_still_owes_it(monkeypatch,
                                                                       tmp_path):
    """bing 0 秒填满配额、baidu 1.6 秒才回：中文这一轮拿到 baidu 的答案。

    软截止钉在 0 秒，所以"境外一家答完就该收工"这个条件在第一次循环就成立 —— 唯一拦住
    它的就是这一条查询写着中文。慢的那一家给 1.6 秒，比 `_LONE_FAMILY_GRACE`(1.2s) 长：
    没有这道门时它是在短等到点那一刻被取消的，不是自己没答。
    """
    monkeypatch.setattr(m, "_circuit_state_file", lambda: str(tmp_path / "cb.json"))
    _install(monkeypatch, {"bing": (0.0, 10), "baidu": (1.6, 4)})
    results, status = await m.metasearch("PCIe 7.0 规范 发布 128GT/s", 6,
                                         engines=["bing", "baidu"])
    assert status["baidu"] == "ok", status
    assert any(r["backend"] == "baidu" for r in results), results


@pytest.mark.asyncio
async def test_the_same_clock_on_an_english_query_still_preempts(monkeypatch, tmp_path):
    """对照组：同一条路、同一个时钟，纯英文查询里 baidu 仍被记 `preempted`。

    这一条是本刀的"没动别的东西"。摘掉门上的判断它照样绿；把它摘掉而上一条也照样绿，
    才说明中文那一条测的是别的事。
    """
    monkeypatch.setattr(m, "_circuit_state_file", lambda: str(tmp_path / "cb.json"))
    _install(monkeypatch, {"bing": (0.0, 10), "baidu": (1.6, 4)})
    results, status = await m.metasearch("pcie specification release", 6,
                                        engines=["bing", "baidu"])
    assert status["baidu"] == "preempted", status
    assert all(r["backend"] != "baidu" for r in results), results


@pytest.mark.asyncio
async def test_a_dead_engine_is_called_timeout_not_a_decision_we_made(monkeypatch,
                                                                       tmp_path):
    """硬期限到点还没等到：记 `timeout`。把这两桶写反就是编出一句"baidu 挂了"。

    `preempted` 进 engine_preempted（健康快池的正常现象，主张"再问一家"），`timeout` 进
    engine_blocked（主张"这一家今天别问了"）。这里把硬期限压到 1 秒、让 baidu 飞 5 秒，
    量的就是"到点"那一句而不是"等到"那一句。
    """
    monkeypatch.setattr(m, "_circuit_state_file", lambda: str(tmp_path / "cb.json"))
    _install(monkeypatch, {"bing": (0.0, 10), "baidu": (5.0, 4)})
    monkeypatch.setattr(m, "_SEARCH_DEADLINE", 1.0)
    _t0 = time.monotonic()
    _results, status = await m.metasearch("缓存 过期 时间", 6, engines=["bing", "baidu"])
    assert status["baidu"] == "timeout", status
    assert time.monotonic() - _t0 < 3.0, "等的是硬期限，不是慢引擎自己的秒数"


@pytest.mark.asyncio
async def test_nothing_is_waited_for_when_the_cn_engine_answered_first(monkeypatch,
                                                                       tmp_path):
    """baidu 先答完：门上没有可等的对象，本轮立刻收工（不许为"中文"二字白等）。"""
    monkeypatch.setattr(m, "_circuit_state_file", lambda: str(tmp_path / "cb.json"))
    _install(monkeypatch, {"baidu": (0.0, 10), "brave": (5.0, 4)})
    _t0 = time.monotonic()
    _results, status = await m.metasearch("缓存 过期 时间", 6,
                                         engines=["baidu", "brave"])
    assert status["baidu"] == "ok", status
    assert status["brave"] == "preempted", status
    assert time.monotonic() - _t0 < 2.5, "brave 是决定不等的那一家"


# ============================================================================
# 来源：test_find_similar_takes_url_and_no_query.py
# ============================================================================
# R2刀4（P2-7）：`query` 在 wire 上是全局必填，可 `mode='find_similar'` 整个忽略它。

SOURCE = "https://example.com/widgets"


def _schema(name: str) -> dict:
    return next(td for td in MasterFetchServer._TOOL_DEFS
                if td["name"] == name)["inputSchema"]


def _fake_search(monkeypatch):
    """引擎与 find_similar 的取源都不发真请求：这里测的是参数门，不是抓取。"""
    from dhole_mcp.search_engines import EngineReport, RawResult

    async def fake_multi_search(query, max_results, **kwargs):
        return ([RawResult(title="similar doc", url="https://other.test/x",
                           snippet="x", source="brave", position=1)],
                [EngineReport(name="brave", ok=True)])

    monkeypatch.setattr(search_mod, "multi_search", fake_multi_search)
    monkeypatch.setattr(search_mod, "ensure_reranker", AsyncMock(return_value=None))
    monkeypatch.setattr(search_mod, "fetch_source_for_similar",
                        AsyncMock(return_value={
                            "title": "Widgets", "body": "widget body text",
                            "status": 200, "reason": "", "detail": ""}))


def _payload(result) -> dict:
    content, _ = result
    return json.loads(content[0].text)


class TestTheWireNoLongerDemandsAQuery:

    def test_query_is_not_required_on_the_wire(self):
        assert "query" not in _schema("web_search").get("required", [])

    def test_the_handler_gives_query_a_default(self):
        """没有默认值时，缺 query 会在方法签名上炸成 TypeError 而不是信封。"""
        param = inspect.signature(MasterFetchServer.web_search).parameters["query"]
        assert param.default == ""

    def test_the_dispatch_table_hands_the_decision_to_the_method(self):
        """dispatch 的静态必需键表不认识 mode，所以这颗从表里出来（R1刀2 的信封
        由方法体给同款：见 test_a_missing_query_is_refused_server_side）。"""
        assert "web_search" not in _DISPATCH_REQUIRED


class TestFindSimilarWithoutAQuery:

    @pytest.mark.asyncio
    async def test_find_similar_reaches_the_host_without_a_query(self, monkeypatch):
        _fake_search(monkeypatch)
        out = await MasterFetchServer()._dispatch(
            "web_search", {"mode": "find_similar", "url": SOURCE})
        payload = _payload(out)
        assert not str(payload.get("error", "")).startswith("invalid_request"), payload
        assert payload.get("results"), payload

    @pytest.mark.asyncio
    async def test_the_url_still_names_the_response(self, monkeypatch):
        """query 缺席时，响应里那栏写的是这次真正寻源用的东西，不是一句谎话。"""
        _fake_search(monkeypatch)
        out = await MasterFetchServer().web_search(
            mode="find_similar", url=SOURCE, cache_ttl=0)
        assert SOURCE in (out.query or "") or out.results


class TestTheQueryIsStillRequiredWhereItIsNeeded:

    @pytest.mark.asyncio
    async def test_a_missing_query_is_refused_server_side(self, monkeypatch):
        _fake_search(monkeypatch)
        out = await MasterFetchServer()._dispatch("web_search", {})
        payload = _payload(out)
        err = payload["error"]
        assert err.startswith("invalid_request"), err
        assert "query" in err, err

    @pytest.mark.asyncio
    async def test_an_empty_or_blank_query_is_refused_the_same_way(self, monkeypatch):
        _fake_search(monkeypatch)
        server = MasterFetchServer()
        a = await server.web_search("", cache_ttl=0)
        b = await server.web_search("   ", cache_ttl=0)
        assert a.error.startswith("invalid_request"), a.error
        assert b.error == a.error, "缺席与空串同罪：dispatch 那张表就是这么判的"

    @pytest.mark.asyncio
    async def test_the_refusal_names_the_one_mode_that_does_not_need_it(
            self, monkeypatch):
        """报错要说清下一步：find_similar 是唯一能不带查询词的那条路，而它要 url=。"""
        _fake_search(monkeypatch)
        out = await MasterFetchServer().web_search("", cache_ttl=0)
        assert "find_similar" in out.error or "find_similar" in out.next_action
        assert "url=" in out.error + out.next_action

    @pytest.mark.asyncio
    async def test_find_similar_without_a_url_still_says_what_it_needs(
            self, monkeypatch):
        """缺 url 的 find_similar 不是"缺 query"：那句要说的是它真正缺的东西。"""
        _fake_search(monkeypatch)
        out = await MasterFetchServer().web_search(mode="find_similar", cache_ttl=0)
        assert "url" in out.error, out.error
        assert not out.error.startswith("invalid_request"), (
            "这颗模式本来就不需要 query，不许拿缺 query 去拒它")

    @pytest.mark.asyncio
    async def test_no_engine_is_called_when_the_query_is_missing(self, monkeypatch):
        calls = {"n": 0}

        async def counting(*a, **kw):
            calls["n"] += 1
            return [], []

        monkeypatch.setattr(search_mod, "multi_search", counting)
        await MasterFetchServer().web_search("", cache_ttl=0)
        assert calls["n"] == 0


# ============================================================================
# 来源：test_a_denominator_that_changed_says_so_in_the_row.py
# ============================================================================
# 分母怎么来的，必须站在分母旁边（体验修复刀2-S9）。
# 重名消歧：_row → _row__a_denominator_that_chang_b80d

def _row__a_denominator_that_chang_b80d(url: str, i: int, consensus: int) -> RawResult:
    return RawResult(title=f"t{i}", url=url, snippet="s", source="bing",
                     position=i, consensus=consensus,
                     sources=("bing", "baidu") if consensus > 1 else ("bing",))


def _built(families: int, basis: str, consensus: int = 1) -> str:
    built = S._build_results("q", [_row__a_denominator_that_chang_b80d("https://a.test/1", 1, consensus)],
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
