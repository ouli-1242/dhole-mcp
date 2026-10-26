"""`since` 收「3d / 12h / 2w」这类相对档位词（E8，四轮复现）。

现场（manual + R01 + R02 + R03）：每次轮询都有人先写 `since='3d'`，每次都拿到
"not a date - use ISO-8601 … or an RFC-822 stamp" 的具名拒绝。拒绝本身可自愈
（示例就在句子里），但同一个直觉四轮被拒四次，说明缺的是这颗词，不是文档。

相对窗口从**这一发的时刻**往回量；回执里的 `since` 仍是规范化后的绝对 ISO，
所以"3d"到底切在哪一刻，读回执就能核。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from dhole_mcp import feed as feed_mod

FIXED = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(feed_mod, "_utc_now", lambda: FIXED)


@pytest.mark.parametrize("value,delta", [
    ("3d", timedelta(days=3)),
    ("12h", timedelta(hours=12)),
    ("2w", timedelta(weeks=2)),
    (" 3D ", timedelta(days=3)),
], ids=["days", "hours", "weeks", "case-and-space"])
def test_a_relative_window_is_measured_back_from_now(frozen, value, delta):
    stamp, iso, why = feed_mod._since_cutoff(value)
    assert why == "", why
    assert stamp == FIXED - delta
    assert iso == (FIXED - delta).isoformat(), iso


@pytest.mark.parametrize("bad", [
    "3m",          # m 是月还是分？两种读数都改语义 —— 拒绝，不猜
    "0d",          # 零窗口不是"从现在起"，是一次没有说出口的空筛选
    "-3d",
    "3",
    "d",
    "3 days",
    "100000d",     # 超过相对窗口的边界（100 年）
])
def test_a_window_that_is_not_a_clean_positive_count_is_refused(bad):
    stamp, iso, why = feed_mod._since_cutoff(bad)
    assert stamp is None and iso == "", (stamp, iso)
    assert why.startswith("invalid_request: since="), why
    assert "relative" in why, why
    assert "3d" in why, why


@pytest.mark.asyncio
async def test_a_relative_poll_filters_entries_and_stamps_the_absolute_cutoff(frozen):
    xml = _feed_xml([
        "Sun, 25 Oct 2026 10:00:00 +0000",   # 1 day ago -> kept
        "Tue, 20 Oct 2026 10:00:00 +0000",   # 6 days ago -> dropped
    ])
    out = await _fetch_with(xml, since="3d")
    assert [i.title for i in out.items] == ["entry 0"], out.items
    assert out.items_older_than_since == 1
    assert out.since == (FIXED - timedelta(days=3)).isoformat()
    assert out.error == ""


def _feed_xml(dates: list[str]) -> str:
    items = "".join(
        f"<item><title>entry {i}</title><link>https://f.example/{i}</link>"
        f"<pubDate>{d}</pubDate></item>"
        for i, d in enumerate(dates))
    return ('<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
            + items + "</channel></rss>")


async def _fetch_with(xml: str, **kw):
    async def fake_grab(url, timeout, headers=None):
        return xml.encode(), 200, "utf-8", "", {}

    orig = feed_mod._grab
    feed_mod._grab = fake_grab
    try:
        return await feed_mod.fetch_feed("https://f.example/feed", **kw)
    finally:
        feed_mod._grab = orig
