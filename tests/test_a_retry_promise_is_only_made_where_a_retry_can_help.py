"""一句 "retry shortly for more recall" 对三桶里的两桶是空头承诺（体验修复刀2-S7）。

没贡献的引擎有三桶，各自含义与解药都不同：

  · `engine_blocked`     拒答/被墙/超时 —— 冷却会自己清（硬 block 60s，反复吃挑战页
                         或连不上 600s），所以"过一会儿再问"是**唯一**能被兑现的那句
  · `engine_empty`       HTTP 200 答了、解析出 0 条 —— 再问一次还是同样的空
  · `engine_preempted`   配额一到就取消（合并结果 ≥ `max_results+4` 且已有 3 家贡献
                         或 2s 软截止已过）—— 它根本没被问过，重放同一个调用必然停在
                         同一个点

此前三桶共用一句话，于是响应同时写着"再等等会有更多召回"和"这些引擎其实答了/根本没
被问"。缓存命中还要再加一层：那一轮**一个引擎都没重新问**，原样重试在 TTL 内逐字节
返回同一批行 —— 承诺的兑现能力比 live 更弱，说的却是同一句话。

分母另有第二宗罪：`engine_preempted` 曾被算进 "N of M engines didn't contribute" 的
M。实测 8 家池子里 2 家被墙 + 4 家被取消 + 1 家应答 → 2/7 = 0.29，多样性警告永不
触发，而那一轮真正能佐证的只有 1 家。
"""

from __future__ import annotations

import pytest

from dhole_mcp import search
from dhole_mcp.search_engines import EngineReport, RawResult


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
