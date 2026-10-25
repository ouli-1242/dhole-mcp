"""R01-008：不带 timeout 的 feed_read 得到的是完整的 20 秒，不是 1 秒。

现场（2026-10-25 · P10 Helen · S3 订阅链 · review/round-01）：
`feed_read urls=[https://hnrss.org/frontpage]`（不带 timeout）→
「the first try timed out at 1s, so this call retried once at 2s」——两发全挂，
而 wire 的括号里印着 default 20000。

根因：分发层 `timeout=args.get("timeout", 20)` 在调用方没传时塞了 **20**，而
方法层把 wire 上的数字当**毫秒**换算（`_feed_timeout_s(20)` → `max(1, 0)` = 1
秒）。那个 20 是 `_feed_timeout_s` 出生前 wire 的旧默认（20 **秒**）；换算落地
时这一处没跟上，于是每个不带 timeout 的调用都顶着 1 秒的首发预算在跑。

契约（本文件钉住）：
  * 分发层缺参时**不塞数**：None 传进方法，方法层自己的默认（20s）生效；
  * 显式毫秒照旧换算：timeout=60000 → 60 秒。
"""

from __future__ import annotations

import asyncio

import pytest

from dhole_mcp import feed as feed_mod
from dhole_mcp.server import MasterFetchServer


@pytest.fixture
def seen(monkeypatch):
    """替身抓 `fetch_feeds` 收到的 timeout（秒）——不发出任何真实请求。"""
    box: dict = {}

    async def fake_fetch_feeds(urls, **kwargs):
        box["urls"] = urls
        box["timeout"] = kwargs.get("timeout")
        return []

    monkeypatch.setattr(feed_mod, "fetch_feeds", fake_fetch_feeds)
    return box


def test_a_call_without_a_timeout_keeps_the_twenty_second_default(seen):
    asyncio.run(MasterFetchServer()._dispatch(
        "feed_read", {"urls": ["https://hnrss.org/frontpage"]}))
    assert seen["timeout"] == 20, (
        f"缺参那一发拿到的预算是 {seen['timeout']}s —— 分发层又塞了一个会被当成"
        f"毫秒的旧默认；1 秒的首发预算杀光所有慢源")


def test_an_explicit_timeout_still_arrives_in_seconds(seen):
    asyncio.run(MasterFetchServer()._dispatch(
        "feed_read", {"urls": ["https://hnrss.org/frontpage"], "timeout": 60000}))
    assert seen["timeout"] == 60, seen
