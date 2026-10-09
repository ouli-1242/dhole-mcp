r"""R4：一次缓存回放给自己盖了个"刚刚抓的"时间戳 —— 再核一遍看起来像真核了一遍。

实测形状（15 场景那轮，C 组"你再确认一遍"）：第二次调用回来 `cached: true`、
`duration_ms: 0`、`fetched_at` 与第一次真抓的那发**逐位形状相同**。调用方读到的是
"我刚核验过"，而实际发生的是"我读了本机 SQLite 里 400 秒前的那一行"。三个字段各自
没错，合在一起是一句谎：

* `cache.get_cached()` 的 SQL 里 `fetched_at` 是**主键之外的第一列**（TTL 判定就靠它），
  返回的 dict 却把它丢了 —— 存的时候有，读的时候没有。
* `_with_agent_hints` 是无条件的最后一步封装（live 抓取、缓存命中、robots 拦、档案兜底
  都过它），而它每次都盖 `datetime.now()`。
* `duration_ms` 在普通命中上写死 0，于是"这一发花了多久"的答案是"零"，而查缓存 + 还原
  信封是几毫秒的活。

这三条合起来的后果比任何一条单独看都大：`content_age_days` / `is_stale` 是从
`fetched_at` 算的，时间戳被盖成当下，**过期判定就一起改口** —— 一份 400 秒前的副本永远
不会显得旧。

顺手钉一件相邻的混淆：summary 里那句回放年龄是 `fetched_at → now`，**不是**
`content_age_days`（那一颗说的是"这一页发布于多久之前"）。两个问句共用一个"age old"
就开始互相冒充，而这一刀要修的正是冒充。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import dhole_mcp.cache as cache_mod
import dhole_mcp.server as server_mod
from dhole_mcp.cache import fetched_at_iso, get_cached, set_cached
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _replay_age_seconds, _with_agent_hints
from dhole_mcp.server import MasterFetchServer

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
