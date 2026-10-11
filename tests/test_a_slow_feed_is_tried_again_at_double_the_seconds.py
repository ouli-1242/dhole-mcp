"""验收刀9-6a（F3）：慢站不是坏站 —— 超时的那一发在同一次调用里以双倍秒数再走一次。

报告 S5 的形状：`feed_read(ifanr + 阮一峰 + 少数派)`，ifanr 8 条昨日增量、阮一峰 0 条
（周更，合理）、少数派 timeout；重试 → 再 timeout；第三次**手动 `timeout=60000`** 才成。
那第三发不是用户想到的 —— 是助手知道有这颗旋钮。用户的话是"刚超时？再来一次"，而他真正
要的是那条源的新鲜条目，不是学会一个 60000。

所以放宽发生在**这一发**里，而不是发生在下一次调用上。四条边界：

* 只认 `timeout` 这一类失败。DNS / TLS / 被拒 / 限流把秒数加倍不会改变任何东西，而再敲
  一次同一扇门正是 `MetaBlockedException` 那份文档要避免的 hammering —— 熔断存在的理由
  也是它。
* 翻倍不许越过 `validate_timeout` 的门（`security.TIMEOUT_MAX_MS` = 120,000ms）。越过
  等于这一刀自己造了一个非法值。
* 已经在门上时不重试：`timeout=120000` 没有"更多"可给，再发一次只是把同一记超时烧两遍。
* 说两句，且两句都别说错。加倍之后**成功**：这一行慢过，出路是点名 `timeout=` 少烧第一发；
  加倍之后**仍失败**：那句"再等久一点"已经作废，账上必须留着这句，否则下一次调用还会照旧
  多烧 40 秒去试一件已经试过没用的事。
"""

from __future__ import annotations

import asyncio

import pytest

from dhole_mcp import feed as F

RSS = (b'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>'
       b'<item><title>new post</title><link>https://x.test/1</link>'
       b'<pubDate>Mon, 16 Sep 2026 00:00:00 GMT</pubDate></item></channel></rss>')
TIMEOUT_ERR = "network_error: timeout - 上游应答太慢"


class _Fake:
    """按次序交回预设的 (_body, _status, _enc, _err, _hdrs)，并把每次的秒数记下来。"""

    def __init__(self, script):
        self.script = list(script)
        self.timeouts: list[int] = []

    async def __call__(self, url, timeout, headers=None):
        self.timeouts.append(timeout)
        item = self.script.pop(0) if self.script else self.script_default()
        return item

    @staticmethod
    def script_default():
        return (RSS, 200, "utf-8", "", {})


def _patch(monkeypatch, script):
    fake = _Fake(script)
    monkeypatch.setattr(F, "_grab", fake)
    return fake


def _read(timeout_ms=20_000):
    return asyncio.run(F.fetch_feed("https://sspai.com/feed",
                                    timeout=timeout_ms // 1000))


def test_a_slow_feed_is_read_in_the_same_call_instead_of_failing_twice(monkeypatch):
    fake = _patch(monkeypatch, [(b"", 0, "", TIMEOUT_ERR, {}), (RSS, 200, "utf-8", "", {})])
    out = _read()
    assert out.error == "", out.error
    assert len(out.items) == 1, out.items
    assert fake.timeouts == [20, 40], fake.timeouts
    assert "timed out at 20s" in out.note and "retried once at 40s" in out.note, out.note
    # 出路要说得出可用的那颗数：feed_read 的 `timeout=` 是毫秒。
    assert "timeout=40000" in out.note, out.note
    assert "slow, not down" in out.note, out.note


def test_a_source_that_times_out_twice_is_not_still_offered_a_longer_wait(monkeypatch):
    fake = _patch(monkeypatch, [(b"", 0, "", TIMEOUT_ERR, {}),
                                (b"", 0, "", TIMEOUT_ERR, {})])
    out = _read()
    assert out.error == TIMEOUT_ERR, out.error
    assert fake.timeouts == [20, 40], fake.timeouts
    assert "timed out again" in out.note, out.note
    assert "timeout=40000" not in out.note, out.note


@pytest.mark.parametrize("err", ["network_error: dns_failure - 名字解析不出",
                                 "network_error: tls_error - 证书或拦截",
                                 "HTTP 403"])
def test_a_failure_that_more_seconds_cannot_fix_gets_no_second_try(monkeypatch, err):
    """加倍治不了的那些族，一发都不许多发：敲门两遍是熔断要避免的那件事。"""
    fake = _patch(monkeypatch, [(b"", 0, "", err, {})])
    out = _read()
    assert out.error == err, out.error
    assert fake.timeouts == [20], fake.timeouts
    assert out.note == "", out.note


def test_the_doubling_never_walks_through_the_door_it_just_validated(monkeypatch):
    """`validate_timeout` 的上限是 120,000ms —— 翻倍不许造出一个非法秒数。"""
    fake = _patch(monkeypatch, [(b"", 0, "", TIMEOUT_ERR, {}), (RSS, 200, "utf-8", "", {})])
    out = _read(timeout_ms=100_000)
    assert fake.timeouts == [100, 120], fake.timeouts
    assert "retried once at 120s" in out.note, out.note


def test_a_call_already_at_the_ceiling_owns_its_timeout(monkeypatch):
    """点名 120 秒的人已经给了这一族最多能给的：再发一次只是把同一记超时烧两遍。"""
    fake = _patch(monkeypatch, [(b"", 0, "", TIMEOUT_ERR, {}), (RSS, 200, "utf-8", "", {})])
    out = _read(timeout_ms=120_000)
    assert fake.timeouts == [120], fake.timeouts
    assert out.error == TIMEOUT_ERR, out.error
    assert out.note == "", out.note


@pytest.mark.asyncio
async def test_the_retry_note_is_per_row_not_batch_wide(monkeypatch):
    """一批里每一行各走各的抓取：慢的是这一台，话就说在这一行上。

    第二条源一次就答 —— 它必须**不**带上那句解释，否则读者会以为 ifanr 也慢过，
    而下一次调用就会为了一个不存在的原因把 timeout 抬上去。
    """
    _patch(monkeypatch, [(b"", 0, "", TIMEOUT_ERR, {}), (RSS, 200, "utf-8", "", {}),
                         (RSS, 200, "utf-8", "", {})])
    slow = await F.fetch_feed("https://sspai.com/feed", timeout=20)
    fast = await F.fetch_feed("https://ifanr.com/feed", timeout=20)
    assert "retried once" in slow.note, slow.note
    assert fast.note == "", fast.note
