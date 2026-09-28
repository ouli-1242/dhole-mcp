"""G27 回归：爬取礼貌度 —— 一台主机多久被打一次，和「选项到底有没有到位」。

`concurrency` 限的是**同时在飞**几个请求，它回答不了「同一台服务器每秒被敲几下」：
3 路并发对着一个慢主机就是每秒 3 发，持续到天荒地老。所以 `delay` 是按主机的请求
间隔，和并发是两个旋钮。站方在 robots.txt 里写的 `Crawl-delay` 会把间隔**抬高**
（那是它明确提出的要求），但不会把调用方自己要的间隔压低。

顺带这一族里最贵的那个 bug：`ignore_robots` 在 smart_crawl 的选项集合里，却不在
`MasterFetchServer.smart_crawl` 的形参表里 —— 于是从 wire 上传进来就是 TypeError，
被兜底的 except 变成「Internal error, not a blocked site」。robots 那个选项从接上
wire 的那天起就没通过。下面第一条断言守的是整类问题，不是这一个键。
"""

from __future__ import annotations

import inspect
import time

import pytest

from dhole_mcp import robots
from dhole_mcp import crawl as crawl_mod
from dhole_mcp.robots import MAX_CRAWL_DELAY_S, cached_crawl_delay, get_robots_cache
from dhole_mcp.server import (
    MasterFetchServer,
    ResponseModel,
    _SC_OPTIONS_FORWARDED,
    _SF_OPTIONS_FORWARDED,
    _SS_OPTIONS,
    _SHOT_OPTIONS,
)

SEED = "https://paced.example.com/"
LEAVES = [f"https://paced.example.com/p{i}" for i in range(4)]


class _TimingServer:
    """Answers every page instantly and records when each request was dialed."""

    def __init__(self, urls=LEAVES):
        self.at: list[tuple[str, float]] = []
        self._html = ('<html><body>'
                      + "".join(f'<a href="/p{i}">p{i}</a>' for i in range(len(urls)))
                      + '</body></html>')

    async def smart_fetch(self, url, **kwargs):
        self.at.append((url, time.monotonic()))
        body = self._html if url.rstrip("/") == SEED.rstrip("/") else "<p>leaf page</p>"
        return ResponseModel(url=url, status=200, content=[body],
                             content_type="text/html", fetcher_used="http")

    @property
    def page_times(self) -> list[float]:
        return [t for u, t in self.at if u.rstrip("/") != SEED.rstrip("/")]


def _spacings(times: list[float]) -> list[float]:
    return [b - a for a, b in zip(times, times[1:])]


@pytest.fixture(autouse=True)
def _fresh_robots_cache(monkeypatch):
    """Two things this file has to undo about the default suite environment.

    conftest sets ``DHOLE_IGNORE_ROBOTS=1`` so unrelated tests never dial a real
    /robots.txt; here the site's own ask IS the behaviour under test, so it has to
    be on. Which means the test origin needs rules in the cache already, or every
    crawl spends seconds failing a real robots.txt lookup — so a blank ruleset is
    seeded here and the cases that care overwrite it with `prime()`.

    ``prime()`` writes the process-wide cache; without a reset one test's rules
    leak into the next, in both directions.
    """
    monkeypatch.delenv("DHOLE_IGNORE_ROBOTS", raising=False)
    robots.reset_robots_cache()
    get_robots_cache().prime(SEED, "User-agent: *\nDisallow:\n")
    yield
    robots.reset_robots_cache()


class TestTheWiringThatWasMissing:

    @pytest.mark.parametrize("tool,forwarded", [
        ("smart_fetch", _SF_OPTIONS_FORWARDED),
        ("smart_crawl", _SC_OPTIONS_FORWARDED),
        ("screenshot", _SHOT_OPTIONS),
        ("smart_search", _SS_OPTIONS),
    ])
    def test_every_forwarded_option_is_a_parameter_the_method_accepts(self, tool, forwarded):
        """选项袋的键最终是 `self.<tool>(**kw)`。少一个形参不是「忽略这个选项」，
        而是一记 TypeError —— 被宽泛的 except 吞掉后，调用方看到的是一句「不是被
        站方挡了」的内错，恰好把真正的故障类别说反了。"""
        accepted = set(inspect.signature(getattr(MasterFetchServer, tool)).parameters)
        missing = sorted(set(forwarded) - accepted)
        assert not missing, f"{tool} does not accept {missing} but forwards them"

    @pytest.mark.asyncio
    async def test_the_crawl_options_actually_reach_the_crawl_layer(self, monkeypatch):
        seen: dict = {}

        async def fake_crawl(server, url, **kwargs):
            seen.update(kwargs)
            from dhole_mcp.crawl import CrawlResponseModel
            return CrawlResponseModel(start_url=url, pages=[])

        monkeypatch.setattr(crawl_mod, "smart_crawl", fake_crawl)

        await MasterFetchServer().smart_crawl(
            SEED, ignore_robots=True, delay=2.5)

        assert seen["ignore_robots"] is True
        assert seen["delay"] == 2.5


class TestTheIntervalIsPerHost:

    @pytest.mark.asyncio
    async def test_a_delay_spaces_the_requests_it_covers(self):
        server = _TimingServer()

        out = await crawl_mod.smart_crawl(server, SEED, max_pages=4, max_depth=1,
                                          concurrency=3, delay=0.08, cache_ttl=0)

        # page_times excludes the seed: what is under test is the interval between
        # requests to one host, and three gaps make a min() worth asserting.
        assert len(server.page_times) >= 3, out.summary
        assert min(_spacings(server.page_times)) >= 0.06, server.page_times

    @pytest.mark.asyncio
    async def test_without_a_delay_the_crawl_is_not_spaced(self):
        server = _TimingServer()

        await crawl_mod.smart_crawl(server, SEED, max_pages=4, max_depth=1,
                                    concurrency=3, cache_ttl=0)

        # The claim is "nothing was inserted between requests", so measure the
        # gaps, not the wall clock: the crawl's own retry path sleeps ~1s on a
        # transient failure, and a total-time bound would blame this feature for
        # that.
        assert max(_spacings(server.page_times), default=0.0) < 0.5, server.page_times

    @pytest.mark.asyncio
    async def test_the_summary_says_the_crawl_was_spacing_itself(self):
        """一个 10 页 40 秒的 crawl 看起来就像卡住了。不说「这是故意的」，调用方
        唯一的办法就是调大 deadline 或者砍掉并发 —— 两个都在解决不存在的问题。"""
        server = _TimingServer()

        out = await crawl_mod.smart_crawl(server, SEED, max_pages=2, max_depth=1,
                                          delay=0.05, cache_ttl=0)

        assert "between requests per host" in out.summary, out.summary
        assert "0.05s" in out.summary, out.summary


class TestTheSitesOwnAskCounts:

    @pytest.mark.asyncio
    async def test_a_crawl_delay_in_robots_raises_the_interval(self):
        # Whole seconds only: urllib reads `Crawl-delay` behind an isdigit() guard
        # (3.14), so 0.1 would silently mean "the host asked for nothing" and this
        # test would prove nothing. See robots._delay_of's docstring.
        get_robots_cache().prime(SEED, "User-agent: *\nCrawl-delay: 1\n")
        server = _TimingServer()

        out = await crawl_mod.smart_crawl(server, SEED, max_pages=3, max_depth=1,
                                          concurrency=3, delay=0.0, cache_ttl=0)

        assert min(_spacings(server.page_times)) >= 0.9, server.page_times
        assert "robots Crawl-delay" in out.summary, out.summary

    @pytest.mark.asyncio
    async def test_a_caller_who_opted_out_of_compliance_is_not_rate_limited_by_it(self):
        """ignore_robots 说「别按这个站家的规则办」。这时候再悄悄按它的
        Crawl-delay 限速，就是拿一个调用方已经关掉的策略去解释它的耗时。"""
        get_robots_cache().prime(SEED, "User-agent: *\nCrawl-delay: 5\n")
        server = _TimingServer()

        out = await crawl_mod.smart_crawl(server, SEED, max_pages=4, max_depth=1,
                                          ignore_robots=True, cache_ttl=0)

        assert max(_spacings(server.page_times), default=0.0) < 0.5, server.page_times
        assert "between requests per host" not in out.summary

    def test_an_absurd_ask_is_clamped_where_it_is_read(self):
        """间隔在 robots 层就被截断：往 crawl 里递一个 3600 秒的「礼貌」，等于
        没有人会把它跑完的一次挂起。"""
        get_robots_cache().prime(SEED, "User-agent: *\nCrawl-delay: 3600\n")
        assert cached_crawl_delay(SEED) == MAX_CRAWL_DELAY_S

    def test_no_crawl_delay_is_none_not_zero(self):
        """0 会被读成「站家要求立刻」，和「站家没提要求」不是一回事。"""
        get_robots_cache().prime(SEED, "User-agent: *\nDisallow:\n")
        assert cached_crawl_delay(SEED) is None

    def test_reading_the_spacing_needs_no_request(self):
        """逐页问「这台主机要求隔多久」必须是免费的：规则已经在缓存里。"""
        async def explode(url, proxy):
            raise AssertionError("a robots.txt was fetched for a spacing question")
        cache = robots.RobotsCache(fetch=explode)
        cache.prime(SEED, "User-agent: *\nCrawl-delay: 1\n")

        assert cache.cached_delay(SEED) == 1.0
        assert cache.cached_delay("https://never-read.example/") is None

    @pytest.mark.asyncio
    async def test_a_requested_delay_past_the_ceiling_is_clamped_and_said(self):
        server = _TimingServer()

        out = await crawl_mod.smart_crawl(server, SEED, max_pages=1, delay=9999,
                                          cache_ttl=0)

        assert "clamped" in out.summary, out.summary
        assert f"{MAX_CRAWL_DELAY_S:g}s" in out.summary, out.summary
