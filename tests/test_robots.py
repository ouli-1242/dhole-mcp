"""robots.txt 合规（G3）测试 —— 单元 + 接线，全程不碰网络。

v16.0 之前 dhole 从不看 robots.txt（README 里明说了）。现在默认遵从，所以这个
模块是「行为变更」的证据所在：

* fail-open 分级（200 执行 / 401-403 视为全禁 / 其他 4xx 放行 / 5xx+网络错误放行
  但只缓存 60s）；
* 每 origin 一条、TTL 1h、并发单飞；
* 两条豁免通道（per-call ``ignore_robots`` / 进程级 ``DHOLE_IGNORE_ROBOTS``）；
* ``cached()`` 同步 peek —— crawl 靠它免费丢掉已知被禁的候选 URL；
* 接线：``smart_fetch`` 被拒时**一个请求都不发**，``smart_crawl`` 在 seed 就报错。
"""

from __future__ import annotations

import asyncio
import types

import pytest

from dhole_mcp import robots
from dhole_mcp.robots import (
    DISABLED,
    DISALLOWED,
    UNAVAILABLE,
    RobotsCache,
    check,
    env_disabled,
    get_robots_cache,
    reset_robots_cache,
    robots_url_for,
)

PAGE = "https://example.com/deny"
OTHER = "https://example.com/public"
RULES = "User-agent: *\nDisallow: /deny\n"


def _cache_with(status, body, **kwargs):
    """A RobotsCache whose fetcher answers ``(status, body)`` and counts calls.

    ``status=None`` means "no answer at all" - the shape ``_blocking_fetch``
    returns when nothing came back, which is a different claim from any HTTP
    status. The body is encoded because that is the injected-fetcher contract
    (``(int, bytes)``); passing text used to be swallowed into an empty rules
    body, which reads as "no rules" and allows everything.
    """
    calls: list[str] = []

    async def _fetch(url, proxy):
        calls.append(url)
        if status is None:
            return None
        return (status, body.encode("utf-8") if isinstance(body, str) else body)

    return RobotsCache(fetch=_fetch, **kwargs), calls


@pytest.fixture
def frozen_clock(monkeypatch):
    """Controllable ``robots.time.monotonic``.

    ``robots`` uses the time module for exactly one thing (entry expiry), so a
    stub namespace is enough and is far less fragile than sleeping in a test.
    """
    now = [1000.0]
    monkeypatch.setattr(robots, "time", types.SimpleNamespace(monotonic=lambda: now[0]))
    return now


# ─── 规则本身 ────────────────────────────────────────────────────────

class TestRuleEvaluation:

    @pytest.mark.asyncio
    async def test_200_rules_are_enforced(self):
        cache, calls = _cache_with(200, RULES)

        assert (await cache.allowed(PAGE)).allowed is False
        assert (await cache.allowed(OTHER)).allowed is True
        assert calls == ["https://example.com/robots.txt"], "一个 origin 只读一次"

    @pytest.mark.asyncio
    async def test_403_is_read_as_disallow_all(self):
        """RFC 9309 的保守读法：host 不肯把规则给这个客户端 = 不给访问。

        这条容易写成「403 就拿不到规则，那就放行」，那等于用一次拒绝换来对所有
        路径的通行证。
        """
        cache, _ = _cache_with(403, "")

        verdict = await cache.allowed(OTHER)
        assert verdict.allowed is False and verdict.reason == DISALLOWED

    @pytest.mark.asyncio
    async def test_404_means_no_rules_so_everything_is_allowed(self):
        cache, _ = _cache_with(404, "")

        verdict = await cache.allowed(PAGE)
        assert verdict.allowed is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [500, 502, 503, 429])
    async def test_server_errors_fail_open(self, status):
        """5xx/429 是「没查到」，不是「合规通过」—— reason 要把这个区别留住。

        因为 /robots.txt 瞬时 5xx 就拒绝用户点名要的那一页，会被 agent 归错因
        （当成站点挂了）。
        """
        cache, _ = _cache_with(status, "")

        verdict = await cache.allowed(PAGE)
        assert verdict.allowed is True
        assert verdict.reason == UNAVAILABLE, "放行必须标成 unavailable，不能标成 allowed"

    @pytest.mark.asyncio
    async def test_no_answer_at_all_fails_open(self):
        cache, _ = _cache_with(None, "")

        verdict = await cache.allowed(PAGE)
        assert verdict.allowed is True and verdict.reason == UNAVAILABLE

    @pytest.mark.asyncio
    async def test_unparseable_rules_fail_open(self):
        """规则解析不出来 = 没有可执行的规则，不是「全禁」。"""
        cache, _ = _cache_with(200, "this is not a robots file\n\x00\xff")

        assert (await cache.allowed(PAGE)).allowed is True

    def test_robots_url_is_only_defined_for_http(self):
        assert robots_url_for("https://example.com/a/b?c=1") == "https://example.com/robots.txt"
        assert robots_url_for("http://example.com:8080/a") == "http://example.com:8080/robots.txt"
        assert robots_url_for("ftp://example.com/x") == ""
        assert robots_url_for("") == ""


# ─── 缓存与 TTL ──────────────────────────────────────────────────────

class TestCacheLifecycle:

    @pytest.mark.asyncio
    async def test_successful_rules_are_cached_for_the_long_ttl(self, frozen_clock):
        cache, calls = _cache_with(200, RULES, ttl=3600.0, unavailable_ttl=60.0)

        await cache.allowed(PAGE)
        frozen_clock[0] += 3599          # still inside the hour
        await cache.allowed(OTHER)
        assert len(calls) == 1

        frozen_clock[0] += 2             # now past it
        await cache.allowed(OTHER)
        assert len(calls) == 2, "TTL 到期必须重读"

    @pytest.mark.asyncio
    async def test_unavailable_rules_are_retried_after_the_short_ttl(self, frozen_clock):
        """失败只缓存 60s：一次瞬时 5xx 不能把「无规则」锁在 host 上一小时。"""
        cache, calls = _cache_with(503, "", ttl=3600.0, unavailable_ttl=60.0)

        await cache.allowed(PAGE)
        frozen_clock[0] += 61
        await cache.allowed(PAGE)
        assert len(calls) == 2, "失败态没按 60s 的短 TTL 重试"

    @pytest.mark.asyncio
    async def test_concurrent_checks_share_one_fetch(self):
        """单飞：一个 100 页的 crawl 不该读 100 次 robots.txt。"""
        calls: list[str] = []

        async def _slow_fetch(url, proxy):
            calls.append(url)
            await asyncio.sleep(0.01)
            return (200, RULES)

        cache = RobotsCache(fetch=_slow_fetch)
        results = await asyncio.gather(*(cache.allowed(OTHER) for _ in range(10)))

        assert all(v.allowed for v in results)
        assert calls == ["https://example.com/robots.txt"]

    @pytest.mark.asyncio
    async def test_cached_peek_is_synchronous_and_knows_nothing_before_the_first_read(self):
        """crawl 用它免费过滤候选 URL —— 未知必须是 None，不能当成 False。"""
        cache, _ = _cache_with(200, RULES)

        assert cache.cached(PAGE) is None, "没读过之前不能猜"

        await cache.allowed(PAGE)
        assert cache.cached(PAGE) is False
        assert cache.cached(OTHER) is True

    def test_prime_seeds_rules_without_a_fetch(self):
        cache, calls = _cache_with(200, "")

        cache.prime(PAGE, RULES)
        assert calls == []
        assert cache.cached(PAGE) is False

    @pytest.mark.asyncio
    async def test_clear_drops_every_origin(self):
        cache, calls = _cache_with(200, RULES)

        await cache.allowed(PAGE)
        cache.clear()
        assert cache.cached(PAGE) is None
        await cache.allowed(PAGE)
        assert len(calls) == 2


# ─── 豁免通道 ────────────────────────────────────────────────────────

class TestEscapes:

    @pytest.fixture
    def exploding_cache(self, monkeypatch):
        """Install a cache whose fetch fails loudly: proves the escape is taken
        BEFORE any network attempt, not merely that no verdict came back."""
        async def _boom(url, proxy):
            raise AssertionError("豁免通道仍去读了 robots.txt")

        monkeypatch.setattr(robots, "_CACHE", RobotsCache(fetch=_boom))

    @pytest.mark.asyncio
    async def test_per_call_ignore_bypasses_the_check(self, exploding_cache):
        verdict = await check(PAGE, ignore=True)
        assert verdict.allowed is True and verdict.reason == DISABLED

    @pytest.mark.asyncio
    async def test_env_var_disables_the_check_process_wide(self, monkeypatch, exploding_cache):
        monkeypatch.setenv("DHOLE_IGNORE_ROBOTS", "1")
        assert env_disabled() is True

        verdict = await check(PAGE)
        assert verdict.allowed is True and verdict.reason == DISABLED

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "  "])
    def test_env_var_falsey_values_keep_compliance_on(self, monkeypatch, value):
        monkeypatch.setenv("DHOLE_IGNORE_ROBOTS", value)
        assert env_disabled() is False

    @pytest.mark.asyncio
    async def test_a_fresh_process_cache_is_created_on_demand(self):
        reset_robots_cache()
        first = get_robots_cache()
        assert get_robots_cache() is first, "单例：每个 fetch 路径共用同一份规则"


# ─── 接线：smart_fetch / smart_crawl ─────────────────────────────────

@pytest.fixture
def robots_rules(monkeypatch):
    """Point the PROCESS-WIDE robots cache at a fake fetcher.

    smart_fetch and crawl both resolve the cache through get_robots_cache(), so
    replacing the module singleton is what makes these integration tests
    network-free. The autouse DHOLE_IGNORE_ROBOTS from conftest is removed here:
    these tests exist precisely to exercise compliance.
    """
    def _install(status=200, body=RULES):
        calls: list[str] = []

        async def _fetch(url, proxy):
            calls.append(url)
            return (status, body.encode("utf-8") if isinstance(body, str) else body)

        monkeypatch.setattr(robots, "_CACHE", RobotsCache(fetch=_fetch))
        monkeypatch.delenv("DHOLE_IGNORE_ROBOTS", raising=False)
        return calls
    return _install


class TestSmartFetchIntegration:

    @pytest.mark.asyncio
    async def test_disallowed_url_is_refused_without_any_request(self, robots_rules):
        """「起飞前的拒绝」：不发请求、status 0、空正文，且不能像站点故障。"""
        from dhole_mcp.server import MasterFetchServer

        robots_rules()

        async def _never(*a, **k):
            raise AssertionError("robots 拒绝的 URL 不该发出任何抓取请求")

        server = MasterFetchServer()
        server.get = _never
        server.stealthy_fetch = _never

        out = await server.smart_fetch(PAGE)

        assert out.status == 0
        assert out.content == []
        assert out.error.startswith("robots_disallowed")
        assert out.fetcher_used == "none"
        assert "robots" in out.next_action.lower()

    @pytest.mark.asyncio
    async def test_allowed_url_still_goes_through(self, robots_rules):
        """守卫不能把放行的路径一起挡掉。"""
        from dhole_mcp.server import MasterFetchServer

        robots_rules()
        hit: list[str] = []

        async def _ok(url, *a, **k):
            from dhole_mcp.server import ResponseModel
            hit.append(url)
            return ResponseModel(url=url, status=200, content=["body"],
                                 content_type="text/html", fetcher_used="http")

        server = MasterFetchServer()
        server.get = _ok
        # cache_ttl=0: without it a hit from the shared fetch cache would answer
        # the call and the tier under test would never run.
        out = await server.smart_fetch(OTHER, cache_ttl=0)

        assert hit == [OTHER]
        assert out.status == 200
        assert not out.error

    @pytest.mark.asyncio
    async def test_ignore_robots_true_still_fetches_a_disallowed_url(self, robots_rules):
        from dhole_mcp.server import MasterFetchServer, ResponseModel

        robots_rules()
        hit: list[str] = []

        async def _ok(url, *a, **k):
            hit.append(url)
            return ResponseModel(url=url, status=200, content=["body"],
                                 content_type="text/html", fetcher_used="http")

        server = MasterFetchServer()
        server.get = _ok
        out = await server.smart_fetch(PAGE, ignore_robots=True, cache_ttl=0)

        assert hit == [PAGE]
        assert out.status == 200


class TestSmartCrawlIntegration:

    @pytest.mark.asyncio
    async def test_seed_disallowed_fails_the_crawl_before_spending_budget(self, robots_rules):
        from dhole_mcp.crawl import smart_crawl

        robots_rules()
        out = await smart_crawl(None, PAGE)

        assert out.pages == []
        assert out.error.startswith("robots_disallowed")
        assert out.robots_skipped == 0
        assert "no page was fetched" in out.error

    @pytest.mark.asyncio
    async def test_discovered_disallowed_links_are_counted_and_skipped(self, robots_rules):
        """发现到的已知被禁链接在入队前就被丢掉，并计入 robots_skipped。"""
        from dhole_mcp import crawl as crawl_mod
        from dhole_mcp.server import ResponseModel

        robots_rules(200, "User-agent: *\nDisallow: /blocked\n")

        seed = "https://example.com/"
        html = (
            '<a href="/blocked/a">a</a>'
            '<a href="/blocked/b">b</a>'
            '<a href="/open">open</a>'
        )

        class _FakeServer:
            async def smart_fetch(self, url, **kwargs):
                body = html if url.rstrip("/") == seed.rstrip("/") else "<p>leaf</p>"
                return ResponseModel(url=url, status=200, content=[body],
                                     content_type="text/html", fetcher_used="http")

        out = await crawl_mod.smart_crawl(_FakeServer(), seed, max_pages=3, max_depth=1)

        assert out.robots_skipped == 2, out.summary
        assert "2 URL(s) skipped by robots.txt" in out.summary
        assert all("/blocked/" not in p.url for p in out.pages)

