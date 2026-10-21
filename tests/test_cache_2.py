"""test_cache_2.py —— 由 2 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import asyncio
import pytest
from dhole_mcp.cache import get_cached, set_cached, clear_cache, clear_all_cache, _cache_key
from datetime import datetime, timedelta, timezone
import dhole_mcp.cache as cache_mod
import dhole_mcp.server as server_mod
from dhole_mcp.cache import fetched_at_iso
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _replay_age_seconds, _with_agent_hints
from dhole_mcp.server import MasterFetchServer


# ============================================================================
# 来源：test_cache.py
# ============================================================================
# Cache tests: round-trip set/get, TTL expiry, clear expired vs all,
# cache key determinism, MAX_CACHE_ENTRIES eviction.

@pytest.fixture
def cache_dir(tmp_path):
    return tmp_path / "cache"


# ─── Round-trip ────────────────────────────────────────────────────

class TestCacheRoundTrip:

    @pytest.mark.asyncio
    async def test_set_then_get_returns_content(self, cache_dir):
        await set_cached("https://example.com", "markdown", ["Hello world"],
                         200, cache_dir=cache_dir)
        result = await get_cached("https://example.com", "markdown",
                                  cache_dir=cache_dir)
        assert result is not None
        assert result["content"] == ["Hello world"]
        assert result["status"] == 200

    @pytest.mark.asyncio
    async def test_different_extraction_types_get_different_keys(self, cache_dir):
        await set_cached("https://example.com", "markdown", ["md"], 200, cache_dir=cache_dir)
        await set_cached("https://example.com", "html", ["<html>"], 200, cache_dir=cache_dir)
        md = await get_cached("https://example.com", "markdown", cache_dir=cache_dir)
        html = await get_cached("https://example.com", "html", cache_dir=cache_dir)
        assert md["content"] == ["md"]
        assert html["content"] == ["<html>"]

    @pytest.mark.asyncio
    async def test_css_selector_differentiates_keys(self, cache_dir):
        await set_cached("https://example.com", "markdown", ["all"],
                         200, css_selector=".main", cache_dir=cache_dir)
        await set_cached("https://example.com", "markdown", ["filtered"],
                         200, css_selector=".sidebar", cache_dir=cache_dir)
        main = await get_cached("https://example.com", "markdown",
                                css_selector=".main", cache_dir=cache_dir)
        sidebar = await get_cached("https://example.com", "markdown",
                                   css_selector=".sidebar", cache_dir=cache_dir)
        assert main["content"] == ["all"]
        assert sidebar["content"] == ["filtered"]

    @pytest.mark.asyncio
    async def test_content_type_round_trips(self, cache_dir):
        await set_cached("https://example.com", "markdown", ["x"], 200,
                        cache_dir=cache_dir, content_type="text/html",
                        total_size_bytes=12345)
        result = await get_cached("https://example.com", "markdown", cache_dir=cache_dir)
        assert result["content_type"] == "text/html"
        assert result["total_size_bytes"] == 12345

    @pytest.mark.asyncio
    async def test_envelope_round_trips(self, cache_dir):
        env = {"metadata": {"title": "Test"}, "page_type": "article"}
        await set_cached("https://example.com", "markdown", ["x"], 200,
                        cache_dir=cache_dir, envelope=env)
        result = await get_cached("https://example.com", "markdown", cache_dir=cache_dir)
        assert result["envelope"]["metadata"]["title"] == "Test"
        assert result["envelope"]["page_type"] == "article"


# ─── TTL behavior ──────────────────────────────────────────────────

class TestCacheTTL:

    @pytest.mark.asyncio
    async def test_expired_entry_not_returned(self, cache_dir):
        await set_cached("https://example.com", "markdown", ["x"], 200,
                        cache_dir=cache_dir, ttl=1)
        await asyncio.sleep(1.1)
        result = await get_cached("https://example.com", "markdown",
                                  cache_dir=cache_dir)
        assert result is None

    @pytest.mark.asyncio
    async def test_caller_ttl_can_request_fresher(self, cache_dir):
        # Stored with TTL=3600, caller requests TTL=1
        await set_cached("https://example.com", "markdown", ["x"], 200,
                        cache_dir=cache_dir, ttl=3600)
        await asyncio.sleep(1.1)
        result = await get_cached("https://example.com", "markdown",
                                  ttl=1, cache_dir=cache_dir)
        assert result is None  # lesser of stored (3600) and requested (1) = 1, expired

    @pytest.mark.asyncio
    async def test_fresh_entry_returned(self, cache_dir):
        await set_cached("https://example.com", "markdown", ["x"], 200,
                        cache_dir=cache_dir, ttl=3600)
        result = await get_cached("https://example.com", "markdown", cache_dir=cache_dir)
        assert result is not None


# ─── Cache clearing ────────────────────────────────────────────────

class TestCacheClearing:

    @pytest.mark.asyncio
    async def test_clear_cache_removes_expired_only(self, cache_dir):
        await set_cached("https://fresh.com", "markdown", ["fresh"], 200,
                        cache_dir=cache_dir, ttl=3600)
        await set_cached("https://stale.com", "markdown", ["stale"], 200,
                        cache_dir=cache_dir, ttl=1)
        await asyncio.sleep(1.1)
        purged = await clear_cache(cache_dir=cache_dir)
        assert purged == 1
        # Fresh entry still there
        fresh = await get_cached("https://fresh.com", "markdown", cache_dir=cache_dir)
        assert fresh is not None
        # Stale entry gone (already expired, then purged)
        stale = await get_cached("https://stale.com", "markdown", cache_dir=cache_dir)
        assert stale is None

    @pytest.mark.asyncio
    async def test_clear_all_cache_nukes_everything(self, cache_dir):
        await set_cached("https://a.com", "markdown", ["a"], 200, cache_dir=cache_dir)
        await set_cached("https://b.com", "markdown", ["b"], 200, cache_dir=cache_dir)
        purged = await clear_all_cache(cache_dir=cache_dir)
        assert purged == 2
        assert await get_cached("https://a.com", "markdown", cache_dir=cache_dir) is None
        assert await get_cached("https://b.com", "markdown", cache_dir=cache_dir) is None


# ─── Cache key ─────────────────────────────────────────────────────

class TestCacheKey:

    def test_same_params_same_key(self):
        k1 = _cache_key("https://example.com", "markdown", ".main", "1-5")
        k2 = _cache_key("https://example.com", "markdown", ".main", "1-5")
        assert k1 == k2

    def test_different_url_different_key(self):
        assert _cache_key("https://a.com", "markdown") != _cache_key("https://b.com", "markdown")

    def test_different_source_different_key(self):
        assert _cache_key("https://x.com", "markdown", source="live") != \
               _cache_key("https://x.com", "markdown", source="archive.org")

    def test_key_is_24_chars(self):
        assert len(_cache_key("https://x.com", "markdown")) == 24


# ─── MAX_CACHE_ENTRIES eviction ────────────────────────────────────

class TestCacheEviction:

    @pytest.mark.asyncio
    async def test_eviction_when_over_cap(self, cache_dir, monkeypatch):
        # Lower the cap for testing
        monkeypatch.setattr("dhole_mcp.cache.MAX_CACHE_ENTRIES", 20)
        # Insert 25 entries
        for i in range(25):
            await set_cached(f"https://example.com/{i}", "markdown", [f"content-{i}"],
                            200, cache_dir=cache_dir)
        # Should have evicted oldest down to ~90% of cap (18)
        # Check some old entries are gone
        old = await get_cached("https://example.com/0", "markdown", cache_dir=cache_dir)
        assert old is None
        # Check newest entry is still there
        new = await get_cached("https://example.com/24", "markdown", cache_dir=cache_dir)
        assert new is not None


# ─── 初始化锁不许跨事件循环活着 ────────────────────────────────────

class TestTheInitLockBelongsToOneLoop:
    """`asyncio.Lock` 绑在**创建它的那个循环**上，而 `acquire()` 只在**要排队**时
    才去问是哪个循环 —— 不争用的时候它一路畅通。于是一个模块级锁看起来完全正确，
    直到同一个进程里的**第二个**循环去争它。

    实测（2026-10-09，做 D8 并发合流时踩到的，与本类无关但同属一个形状）：

        两轮 asyncio.run()，每轮 6 个并发首次读（每轮换一个全新缓存目录）
          第 1 轮：ok
          第 2 轮：RuntimeError: <asyncio.Lock …> is bound to a different event loop

    生产里一个进程一个循环，所以它不砸产品；它砸嵌入方，以及"前一个用例已经把锁
    建出来了、这个用例又跑了并发"的套件 —— 那不是崩溃，那是随机红。
    """

    def test_a_new_loop_gets_a_new_lock(self):
        import asyncio as _asyncio
        from dhole_mcp import cache as cache_mod

        seen = []

        async def grab():
            seen.append(cache_mod._get_db_lock())

        _asyncio.run(grab())
        _asyncio.run(grab())
        assert seen[0] is not seen[1], "初始化锁跨事件循环活了下来"

    def test_a_second_loop_can_still_contend_for_it(self, tmp_path):
        """判据不是"锁换了个对象"，是"第二轮的并发读能跑完"。"""
        import asyncio as _asyncio
        from dhole_mcp import cache as cache_mod

        async def one(d, i):
            return await cache_mod.get_cached(f"https://x/{i}", "markdown",
                                              ttl=3600, cache_dir=d)

        def burst(n):
            d = tmp_path / f"round{n}"
            assert not d.exists(), "每一轮都要一个全新的目录，否则走的是已初始化快路"

            async def main():
                return await _asyncio.gather(*[one(d, i) for i in range(6)])

            return _asyncio.run(main())

        burst(1)
        burst(2)      # 修复前炸在这一轮：bound to a different event loop
        burst(3)


# ============================================================================
# 来源：test_a_cache_replay_that_stamped_now_was_not_a_fresh_check.py
# ============================================================================
# R4：一次缓存回放给自己盖了个"刚刚抓的"时间戳 —— 再核一遍看起来像真核了一遍。

PAGE = "https://docs.example.com/replay"


class _FakeClock:
    """只替 `cache.py` 命名空间里的 `time`，不碰全局时钟。"""

    def __init__(self, at: float):
        self._at = at

    def time(self) -> float:
        return self._at


def _pin_cache_clock(monkeypatch, at: float):
    """只替 `cache.py` 命名空间里的时钟，并交回一个还原动作。

    这里**不用** `monkeypatch.undo()`：pytest 的 `monkeypatch` 实例在本测试里与 conftest
    的 autouse 夹具共用同一个，`undo()` 会连"把 cache.db 重定向进 tmp_path"一起撤销 ——
    种子写进临时库、读侧回到真 ~/.dhole，于是这一发什么都没量到（第一次踩到时是这样）。
    """
    real = cache_mod.time
    monkeypatch.setattr(cache_mod, "time", _FakeClock(at))

    def restore() -> None:
        monkeypatch.setattr(cache_mod, "time", real)

    return restore


def _body() -> list[str]:
    return ["the page as stored, four hundred seconds ago " * 12]


def _result(**updates) -> ResponseModel:
    out = ResponseModel(url=PAGE, status=200, content=_body(), content_ok=True,
                        total_extracted_chars=sum(len(c) for c in _body()),
                        **updates)
    return _with_agent_hints(out)


class TestTheStoredFetchTimeComesBackOutOfTheStore:

    @pytest.mark.asyncio
    async def test_a_hit_carries_the_instant_the_content_was_fetched(self, tmp_path):
        """存的时候就有这一列，读的时候不能把它丢掉 —— 用的就是写进去的那个瞬间。"""
        before = datetime.now(timezone.utc).timestamp()
        await set_cached(PAGE, "markdown", ["body"], status=200,
                         cache_dir=tmp_path, ttl=3600)
        got = await get_cached(PAGE, "markdown", None, ttl=3600,
                               cache_dir=tmp_path)
        assert got is not None
        assert before <= got["fetched_at"] <= datetime.now(timezone.utc).timestamp() + 1

    @pytest.mark.asyncio
    async def test_the_stored_instant_round_trips_through_the_iso_helper(self, tmp_path,
                                                                        monkeypatch):
        """`fetched_at_iso` 把那一条翻回 ISO —— 信封上的形状与列里的形状不是同一个。"""
        long_ago = datetime.now(timezone.utc) - timedelta(seconds=400)
        _pin_cache_clock(monkeypatch, long_ago.timestamp())
        await set_cached(PAGE, "markdown", ["body"], status=200,
                         cache_dir=tmp_path, ttl=600)
        got = await get_cached(PAGE, "markdown", None, ttl=600, cache_dir=tmp_path)
        assert fetched_at_iso(got).startswith(long_ago.isoformat()[:19]), fetched_at_iso(got)
        assert fetched_at_iso(None) == ""
        assert fetched_at_iso({"fetched_at": "not a number"}) == ""

    @pytest.mark.asyncio
    async def test_ttl_still_decides_on_the_same_column(self, tmp_path, monkeypatch):
        """把时间带回来不许顺手改变过期语义：同一行过期后仍然不命中。"""
        _pin_cache_clock(monkeypatch, 1_760_000_000.0)
        await set_cached(PAGE, "markdown", ["body"], status=200,
                         cache_dir=tmp_path, ttl=60)
        _pin_cache_clock(monkeypatch, 1_760_000_000.0 + 120)
        assert await get_cached(PAGE, "markdown", None, ttl=600,
                                cache_dir=tmp_path) is None


class TestTheReplayDoesNotStampNow:

    def test_a_cached_result_keeps_the_time_its_copy_was_fetched(self):
        when = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()
        out = _result(cached=True, fetcher_used="cache", fetched_at=when)
        assert out.fetched_at == when, (out.fetched_at, when)

    def test_a_live_result_still_gets_the_stamp(self):
        """反向：不缓存的时候这仍然是"此刻"，不许为了对称把它留空。"""
        before = datetime.now(timezone.utc)
        out = _result(cached=False, fetcher_used="http")
        assert before <= datetime.fromisoformat(out.fetched_at) <= datetime.now(
            timezone.utc) + timedelta(seconds=5), out.fetched_at

    def test_the_summary_says_how_old_the_copy_is(self):
        when = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()
        out = _result(cached=True, fetcher_used="cache", fetched_at=when)
        assert "cached copy, fetched 6.7 min ago" in out.summary, out.summary

    def test_a_cached_row_with_no_stored_time_says_nothing_rather_than_now(self):
        """存量老行没有这一列时留**空** —— 补一个 now 就是这一刀要删的那句谎。"""
        out = _result(cached=True, fetcher_used="cache", fetched_at="")
        assert out.fetched_at == "", out.fetched_at
        assert "cached" in out.summary, out.summary
        assert "ago" not in out.summary, out.summary

    def test_the_replay_age_is_not_the_publication_age(self):
        """两颗"多久"不许互相冒充：发布很久、抓取刚刚 —— 说的是抓取。"""
        long_ago = (datetime.now(timezone.utc) - timedelta(days=900)).isoformat()
        fresh = datetime.now(timezone.utc).isoformat()
        out = _result(cached=True, fetcher_used="cache", fetched_at=fresh,
                      metadata={"published_time": long_ago})
        assert out.content_age_days and out.content_age_days > 800, out.content_age_days
        assert "min ago" in out.summary, out.summary
        assert "days" not in out.summary.split("cached copy")[-1], out.summary

    def test_clock_skew_never_reads_as_the_future(self):
        """本机时钟比写缓存那一刻慢几秒时，年龄钳到 0 而不是负数。"""
        future = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
        assert _replay_age_seconds(future) == 0.0
        assert _replay_age_seconds("not a timestamp") is None


class TestTheRealCacheHitThroughWebRead:
    """走完整 `web_read`：那一发一个包都不发，而回执必须自己说清它是回放。"""

    @pytest.fixture()
    def stored_copy(self, monkeypatch):
        monkeypatch.setattr(server_mod, "_cache_context", lambda *a, **k: "")
        return PAGE

    @pytest.mark.asyncio
    async def test_the_hit_reports_the_stored_time_and_a_nonzero_duration(
            self, stored_copy, monkeypatch):
        """种一行 400 秒前的记录，`cache_ttl=600` 仍然命中 —— 而它必须说自己是回放。"""
        four_hundred_s_ago = (datetime.now(timezone.utc)
                              - timedelta(seconds=400)).timestamp()
        restore = _pin_cache_clock(monkeypatch, four_hundred_s_ago)
        await set_cached(stored_copy, "markdown", _body(), status=200, ttl=600)
        restore()
        seeded = await get_cached(stored_copy, "markdown", None, ttl=600)
        assert seeded is not None, "种子没落进本次调用的那份缓存 —— 下面的断言就什么都没量"

        out = await MasterFetchServer().web_read(url=stored_copy, cache_ttl=600)

        assert out.cached is True and out.fetcher_used == "cache", out.summary
        assert out.duration_ms > 0, "查缓存 + 还原信封不是零成本，报 0 是账不对"
        stamp = datetime.fromisoformat(out.fetched_at)
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
        assert 300 < age < 500, (out.fetched_at, age)
        assert "cached copy, fetched" in out.summary and "min ago" in out.summary, \
            out.summary

    @pytest.mark.asyncio
    async def test_a_fresh_write_does_not_look_stale(self, stored_copy):
        """正向对照：刚抓完的命中说"0.0 min ago"，不是一句"过期"。"""
        await set_cached(stored_copy, "markdown", _body(), status=200, ttl=600)
        out = await MasterFetchServer().web_read(url=stored_copy, cache_ttl=600)
        assert out.cached is True, out.summary
        assert "fetched 0.0 min ago" in out.summary, out.summary
        assert out.is_stale is False, out.summary
