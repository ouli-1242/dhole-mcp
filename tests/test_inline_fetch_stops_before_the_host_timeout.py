"""R2刀1（P2-5）：`fetch_content=true` 的内联扇出没有总量闸，两次复现把整调用撞死在宿主的 30s 超时上。

预算结构（修前）：搜索 2–6s + 3 页 × `timeout=15000` —— 理论上限 ≈50s，而 stdio 宿主
在 30s 就把请求杀了，连已经拿到的搜索结果一起丢。修后：搜索完成即按「宿主 30s」分内联
预算，每页拿 `min(15000, 剩余/剩余页数)`，到点即停；被预算砍掉的页仍占一行，带
`error="inline_fetch_budget"`。

同一段代码的第二颗洞：注释承诺"every hole carries a reason"，但 web_read 正常返回
一条 content_ok=false、正文为空的页时（没抛异常），那一行没有 error 字段——洞开着，
reason 缺席。这里一并钉住：每个洞必带 reason。
"""

import time
from unittest.mock import AsyncMock

import pytest

from dhole_mcp import search as search_mod
from dhole_mcp import server as server_mod
from dhole_mcp.models import ResponseModel
from dhole_mcp.search_engines import EngineReport, RawResult
from dhole_mcp.server import MasterFetchServer

HOST_MS = 30_000
RESERVE_MS = 2_000


class _Clock:
    """替身时钟：server_mod.now 是从全局 time 借来的名字，换模块属性而不是 patch
    time 模块本身，否则别的模块的 now() 会调到同一根针上。起点用真实 epoch，
    免得谁拿它去减一个真实文件 mtime 得出一个荒谬的年龄。"""

    def __init__(self):
        self.t0 = time.time()
        self.t = self.t0

    def __call__(self) -> float:
        return self.t

    def advance(self, ms: float) -> None:
        self.t += ms / 1000.0

    @property
    def elapsed_ms(self) -> float:
        return (self.t - self.t0) * 1000


def _wire(monkeypatch, n_results: int = 3, search_ms: int = 3_000):
    """装一条可控的搜索链：结果数、搜索本身花掉多少时间，都由测试说。"""
    clock = _Clock()

    async def fake_multi_search(query, max_results, **kwargs):
        clock.advance(search_ms)
        rows = [RawResult(title=f"widgets doc {i}",
                          url=f"https://a{i}.example.test/widgets-{i}",
                          snippet=f"widgets guide number {i} of {n_results}",
                          source="brave", position=i + 1)
                for i in range(n_results)]
        return rows, [EngineReport(name="brave", ok=True)]

    monkeypatch.setattr(search_mod, "multi_search", fake_multi_search)
    # fetch_content 只抓 fetch_relevance=="high" 的前 3 条。merge 打分下第三行会掉到
    # med，扇出因此少一页、预算算术就对不上；这里钉住三行全 high（与共识扇出那份
    # 夹具同一手法），让"几页"由测试说了算。
    monkeypatch.setattr(search_mod, "ensure_reranker", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        search_mod, "neural_rerank",
        lambda q, ranked, **kw: [(r, 0.9 - 0.05 * i) for i, r in enumerate(ranked)])
    monkeypatch.setattr(server_mod, "now", clock)
    return clock


def _ok_page(url: str) -> ResponseModel:
    return ResponseModel(url=url, status=200, content=["real page text"],
                         content_ok=True)


def _server_with_slow_pages(clock: _Clock, spend_ms: list[int],
                            spend_full: bool = False):
    """每页按 spend_ms 消耗预算（0 = 秒回），并把收到的 timeout 留在 call_timeouts 上。

    spend_full=True 让每页花满「配额 + 兜底宽限」——一页实际占用的时间不止它的
    timeout，最坏情况那条不变量要按这个口径量。
    """
    server = MasterFetchServer()
    state = {"call_timeouts": [], "n": 0}

    async def fake_web_read(**kwargs):
        i = state["n"]
        quota = int(kwargs.get("timeout") or 0)
        state["call_timeouts"].append(quota)
        state["n"] += 1
        if spend_full:
            clock.advance(quota + server_mod.INLINE_PAGE_GRACE_MS)
        else:
            clock.advance(spend_ms[i] if i < len(spend_ms) else 0)
        return _ok_page(kwargs.get("url", "u"))

    server.web_read = AsyncMock(side_effect=fake_web_read)
    return server, state


class TestTheInlineBudget:

    @pytest.mark.asyncio
    async def test_worst_case_spending_still_lands_inside_the_host_budget(self, monkeypatch):
        """每页都花满配额 + 兜底宽限（一页实际占用的时间）时，整条内联链仍落在 30s 内。

        这条是不变量测试，不重复实现里的算式：配额可以调，「最坏情况不许超宿主的耐心」
        不许变。修前 3 页 × 15s = 45s，宿主在 30s 就杀。
        """
        clock = _wire(monkeypatch, n_results=3, search_ms=2_000)
        server, state = _server_with_slow_pages(clock, [], spend_full=True)

        out = await server.web_search("widgets doc", fetch_content=True, cache_ttl=0)

        spent_ms = clock.elapsed_ms
        assert spent_ms <= HOST_MS - 1_000, (
            f"内联扇出最坏花了 {spent_ms:.0f}ms，宿主 30s 已到")
        assert len(out.fetched_pages) == 3, "每一页都要有一行"
        assert all(t <= server_mod.INLINE_PAGE_CAP_MS
                   for t in state["call_timeouts"])

    @pytest.mark.asyncio
    async def test_each_page_gets_a_share_not_the_whole_budget(self, monkeypatch):
        """3 页 × 15s = 45s 的结构超支：修后第一页的上限是「剩余预算 ÷ 剩余页数」。

        这里断言的是**配额**而不是配额的加总：配额按实时余额逐页重算，秒回的页不会
        把自己那份花掉，于是后一页的配额可以变大。真正兜住时间的是上面那条最坏情况。
        """
        clock = _wire(monkeypatch, n_results=3, search_ms=3_000)
        server, state = _server_with_slow_pages(clock, [0, 0, 0])

        out = await server.web_search("widgets doc", fetch_content=True, cache_ttl=0)

        assert len(state["call_timeouts"]) == 3
        left = HOST_MS - 3_000 - RESERVE_MS
        assert state["call_timeouts"][0] <= left // 3, \
            "搜索只花了 3s，第一页还拿到整份 15s —— 总量闸没生效"
        assert state["call_timeouts"][0] >= server_mod.INLINE_PAGE_FLOOR_MS
        assert all(p["content_ok"] is True for p in out.fetched_pages), \
            "配额收紧不该让秒回的成功页变成洞"

    @pytest.mark.asyncio
    async def test_a_slow_page_leaves_the_rest_the_leftover(self, monkeypatch):
        """预算按实时余额重算：第一页烧掉 14s，第二页的配额只剩余额的一份。"""
        clock = _wire(monkeypatch, n_results=3, search_ms=1_000)
        server, state = _server_with_slow_pages(clock, [14_000, 0, 0])

        out = await server.web_search("widgets doc", fetch_content=True, cache_ttl=0)

        left_after_page1 = HOST_MS - 1_000 - RESERVE_MS - 14_000
        assert state["call_timeouts"][1] <= left_after_page1 // 2
        assert state["call_timeouts"][1] < 15_000, \
            "第一页烧掉 14s 之后，第二页还拿到整份 15s —— 余额没跟着走"
        assert len(out.fetched_pages) == 3, "三页都该有行，成功的页不许被静默丢掉"

    @pytest.mark.asyncio
    async def test_the_run_stops_when_the_budget_is_out(self, monkeypatch):
        """到点即停：剩下的页不再发包，但每页仍占一行并说明为什么是洞。"""
        clock = _wire(monkeypatch, n_results=3, search_ms=20_000)
        server, state = _server_with_slow_pages(clock, [6_000, 0, 0])

        out = await server.web_search("widgets doc", fetch_content=True, cache_ttl=0)

        assert server.web_read.await_count == 1, "预算已尽还在发第 2 页"
        assert len(out.fetched_pages) == 3
        assert out.fetched_pages[0]["content_ok"] is True
        for row in out.fetched_pages[1:]:
            assert row["content_ok"] is False
            assert row["content"] == ""
            assert row["error"] == "inline_fetch_budget"
            assert row["url"], "被砍的页也要报是哪一条"
        assert "inline_fetch_budget" in out.summary

    @pytest.mark.asyncio
    async def test_a_search_that_ate_the_budget_fetches_nothing(self, monkeypatch):
        """搜索本身超时（28s）时不再追发包：一发就必然被宿主杀，搜索结果也一起丢。"""
        clock = _wire(monkeypatch, n_results=3, search_ms=28_000)
        server, state = _server_with_slow_pages(clock, [0, 0, 0])

        out = await server.web_search("widgets doc", fetch_content=True, cache_ttl=0)

        server.web_read.assert_not_awaited()
        assert [r["error"] for r in out.fetched_pages] == ["inline_fetch_budget"] * 3
        assert out.results, "搜索结果本身必须还在——被杀的是抓取，不是整次调用"

    @pytest.mark.asyncio
    async def test_healthy_pages_are_untouched(self, monkeypatch):
        """快路径行为不变：三页秒回就三页正文，没有洞、没有注记。"""
        clock = _wire(monkeypatch, n_results=3, search_ms=500)
        server, state = _server_with_slow_pages(clock, [0, 0, 0])

        out = await server.web_search("widgets doc", fetch_content=True, cache_ttl=0)

        assert len(out.fetched_pages) == 3
        assert all(p["content_ok"] is True for p in out.fetched_pages)
        assert all("error" not in p for p in out.fetched_pages)
        assert "inline_fetch_budget" not in out.summary


class TestEveryHoleCarriesAReason:
    """失败占位的 error 字段此前只在「web_read 抛异常」时才有；正常返回一条空正文的
    失败页时那一行 content_ok=false + content="" 且**无 error**，与注释的承诺矛盾。"""

    @pytest.mark.asyncio
    async def test_a_page_that_returns_empty_without_error_still_gets_a_reason(
            self, monkeypatch):
        _wire(monkeypatch, n_results=1, search_ms=500)
        server = MasterFetchServer()
        server.web_read = AsyncMock(return_value=ResponseModel(
            url="https://a0.example.test/widgets", status=200, content=[],
            content_ok=False))

        out = await server.web_search("widgets doc", fetch_content=True, cache_ttl=0)

        row = out.fetched_pages[0]
        assert row["content_ok"] is False
        assert row["content"] == ""
        assert row.get("error"), "洞开着却没有 reason"

    @pytest.mark.asyncio
    async def test_the_reason_names_what_the_page_reported(self, monkeypatch):
        """页侧已经说出来的原因要原样带上来，不要在组装处再编一句空话。"""
        _wire(monkeypatch, n_results=1, search_ms=500)
        server = MasterFetchServer()
        server.web_read = AsyncMock(return_value=ResponseModel(
            url="https://a0.example.test/widgets", status=200, content=[],
            content_ok=False, page_type="js_shell",
            diagnosis={"code": "js_shell", "retryable": True, "recover_with": ""}))

        out = await server.web_search("widgets doc", fetch_content=True, cache_ttl=0)

        assert "js_shell" in out.fetched_pages[0]["error"]

    @pytest.mark.asyncio
    async def test_a_page_error_wins_over_the_fallback(self, monkeypatch):
        _wire(monkeypatch, n_results=1, search_ms=500)
        server = MasterFetchServer()
        server.web_read = AsyncMock(return_value=ResponseModel(
            url="https://a0.example.test/widgets", status=503, content=[],
            content_ok=False, error="http 503: upstream gave up"))

        out = await server.web_search("widgets doc", fetch_content=True, cache_ttl=0)

        assert out.fetched_pages[0]["error"] == "http 503: upstream gave up"

    @pytest.mark.asyncio
    async def test_a_clean_page_still_carries_no_error_key(self, monkeypatch):
        """反向不许漂：给成功行硬塞一个空 error 会让调用方把"没问题"读成"有问题"。"""
        _wire(monkeypatch, n_results=1, search_ms=500)
        server = MasterFetchServer()
        server.web_read = AsyncMock(return_value=_ok_page("u"))

        out = await server.web_search("widgets doc", fetch_content=True, cache_ttl=0)

        assert "error" not in out.fetched_pages[0]
