"""源级的那颗数不许在没点名 `since` 时静默成 0：`items_without_date` 说的是源，不是你收了了几条。

现场（2026-10-15 · P02-05 邱洛（券商行业研究员 L3）· S7 相隔 21s 的重复订阅 · R02-014 · P2 · 组 D）：
入账时记的是"同一颗源 `max_items=8` 给 2、`max_items=3` 给 0"。P4 复验时在这台机器上重新量，
形状比那句话更糟一档（`feed.xml` = 6 条带 pubDate + 2 条不带）：

    fetch_feed(feed.xml, max_items=8)                    → items 8 条、其中 2 条没有日期、
                                                            items_without_date=0
    fetch_feed(feed.xml, max_items=3, since='2020-01-01') → items 3 条、items_without_date=2

也就是说这条计数**只有点了 `since` 才有人算**（`_keep_newer` 是唯一的写者，而那一路只在有
cutoff 时才走）。不点 `since` 的那一发把两条没有日期的条目交回手里，同时报 `0` —— 字段自己的
说明写的是 "Entries KEPT despite having no usable publish date"，0 就成了一句关于源的假话。
而点了 `since` 的那一发里，那颗数是**整源口径**（不随 `max_items` 变），返回的三条里其实一条
无日期的都没有 —— 两个口径共用一个字段名，而响应里没有任何一句"共 N 条 / 交回 M 条"。

契约（本文件钉住）：
  * 两条路都在**切之前**数无日期那族：`items_without_date` 说的是这个源有多少条没日期；
  * 上限切掉过条目时必须申报"源共 N 条 / 交回 M 条，那颗数是全源的"（`note` 那一格）；
  * **反面锁**：`since` 那一路的两笔账（`items_older_than_since` 与整源口径的
    `items_without_date`）一位不动；没切掉任何一条时不多打一句 cap 申报；
  * 判决位不动：`max_items` 仍然只管交回多少条（本刀不改交付量，只改这封响应说的是什么）。
"""

from __future__ import annotations

import pytest

from dhole_mcp import feed as F

_DATED = [(f"条目 {i}", f"https://x.test/{i}", f"Mon, 1{i} Sep 2026 00:00:00 GMT")
          for i in range(6)]
_UNDATED = [("无日期 A", "https://x.test/a", ""), ("无日期 B", "https://x.test/b", "")]
ALL = _DATED + _UNDATED

RSS = ("<?xml version='1.0'?><rss version='2.0'><channel><title>dhole 靶站订阅源</title>"
       + "".join(f"<item><title>{t}</title><link>{u}</link>"
                 + (f"<pubDate>{d}</pubDate>" if d else "") + "</item>"
                 for t, u, d in ALL)
       + "</channel></rss>").encode()


class _OneShot:
    async def __call__(self, url, timeout, headers=None):
        return RSS, 200, "utf-8", "", {}, ""


@pytest.fixture(autouse=True)
def _local_feed_source(monkeypatch):
    monkeypatch.setattr(F, "_grab_with_retry", _OneShot())


async def _fetch(**kw):
    return await F.fetch_feed("https://x.test/feed.xml", timeout=10, **kw)


# ─── 1 · 不点 since 也要数 ───

@pytest.mark.asyncio
async def test_undated_entries_are_counted_without_a_since_filter():
    r = await _fetch(max_items=8)
    assert len(r.items) == 8
    assert r.items_without_date == 2, (r.items_without_date,
                                       "源里有两条没有日期，而这一封说 0")


@pytest.mark.asyncio
async def test_the_count_is_about_the_source_not_about_the_slice():
    """同一个事实不许随 `max_items` 换答案：两条路都是整源口径。"""
    wide = await _fetch(max_items=8)
    narrow = await _fetch(max_items=3)
    assert wide.items_without_date == narrow.items_without_date == 2
    assert len(narrow.items) == 3, "本刀不改交付量"


# ─── 2 · 切掉过条目要说出来 ───

@pytest.mark.asyncio
async def test_a_cut_says_the_count_is_for_the_whole_source():
    r = await _fetch(max_items=3)
    assert "8" in r.note and "3" in r.note, r.note
    assert "without_date" in r.note or "counts" in r.note, r.note


@pytest.mark.asyncio
async def test_no_cap_sentence_when_nothing_was_cut():
    r = await _fetch(max_items=8)
    assert "max_items" not in (r.note or ""), r.note


# ─── 3 · 反面锁：since 那一路的账一位不动 ───

@pytest.mark.asyncio
async def test_the_since_ledger_is_unchanged():
    r = await _fetch(max_items=8, since="2020-01-01")
    assert r.items_without_date == 2
    assert r.items_older_than_since == 0
    old = await _fetch(max_items=8, since="2035-01-01")
    assert old.items_older_than_since == 6, old.items_older_than_since
    assert old.items_without_date == 2


@pytest.mark.asyncio
async def test_a_since_read_that_the_cap_cut_also_declares_it():
    r = await _fetch(max_items=3, since="2020-01-01")
    assert r.items_without_date == 2, "since 那一路的整源口径不许被这一刀改成子集口径"
    assert "max_items" in (r.note or ""), r.note


# ─── 4 · 一位作者 ───

def test_the_cap_is_applied_in_one_place_only():
    import inspect

    src = inspect.getsource(F)
    assert src.count("result.items[:max_items]") == 1, "上限在两处各自切，申报就只覆盖一处"
