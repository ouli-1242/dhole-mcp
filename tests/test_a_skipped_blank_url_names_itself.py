r"""被滤掉的空白 URL 要**报得出是哪几颗**：数对了名字仍缺（R02-053）。

现场（P02-02 温其 · L2 · 复制粘贴订阅清单最常见的一发）：

    feed_read(urls=["", "http://127.0.0.1:8399/feed.xml", "   "])
      → summary = `3 URLs asked · 1 rows answered · 1 rows carried 1 items, newest first`
      → `feeds` 只有 1 行，而**没有任何一格说得出被跳过的是第几颗**

`server.feed_read` 的第一行活就是 `urls = [u for u in urls if u and u.strip()]`（src/dhole_mcp/server.py:9587）：
它把空白项丢掉，却不在任何地方登记丢了几颗、丢的是第几颗。R02-F32 那把刀补上了「asked != answered
看得见的数」，所以今天 *少了几颗* 说得出来，*少了哪一颗* 说不出来 —— 半件活。

三种形状各归各账，不许混：
  * `asked` 数的是调用方点名了几颗（3）—— 这一格 F32 已经对了，本刀一字不动；
  * 行数是 `feeds` 的行（1）—— 也不动；**不许**为被跳过的那两颗编出假的 feed 行；
  * 中间那格（谁没被去取）今天整个缺失 —— 本刀只补这一格。

判据（本文件钉住）：
  * 有空白项 ⇒ 回执顶层多一颗 `skipped_blank_urls`（真实个数），`summary` 里那句话点名**位置**
    （第几颗），不回显内容（空白项的内容就是空与空白，点名内容没有信息量，位置才有）；
  * 算式锁：`asked == len(feeds) + skipped_blank_urls` —— 两处数必须能对上，对不上就是两处各数一遍；
  * 沉默锁：一批里没有空白项 ⇒ 那句话与那颗标量都不许出现（不给付没错的批次加客套）；
  * 全空白那一支（ValueError）也要说得出丢了几颗，且**保留原来那句拒绝的话**；
  * 结构锁：滤与报必须是**同一位作者**（`_blank_url_split`），方法体里那条内联列表推导不许留下 ——
    留下就是两处各数一遍（R02-046 / R02-050 那一族的反面）。
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from dhole_mcp import feed as feed_mod
from dhole_mcp.server import MasterFetchServer, _feed_batch_envelope

FEED = ("<?xml version='1.0'?><rss version='2.0'><channel><title>Blog</title>"
        "<item><title>One</title><link>https://blog.test/one</link>"
        "<pubDate>Tue, 14 Oct 2026 09:00:00 GMT</pubDate></item></channel></rss>")
GOOD = "https://blog.test/feed.xml"


@pytest.fixture()
def stub(monkeypatch):
    async def fake_grab(url, timeout, headers=None):
        return FEED.encode(), 200, "utf-8", "", {"content-type": "application/rss+xml"}

    monkeypatch.setattr(feed_mod, "_grab", fake_grab)


def _payload(args: dict) -> dict:
    out = asyncio.run(MasterFetchServer()._dispatch("feed_read", args))
    return out[1] if isinstance(out, tuple) and len(out) == 2 else {}


# ─── 1 · 有空白项：标量 + 点名位置 ──────────────────────────────────────────

def test_blank_entries_are_counted_and_positioned(stub):
    p = _payload({"urls": ["", GOOD, "   "], "max_items": 3})
    assert len(p["feeds"]) == 1, p["feeds"]              # 不许为跳过的那两颗编行
    assert p["skipped_blank_urls"] == 2, sorted(p)
    s = p["summary"]
    assert "positions 1, 3" in s, s
    # 算式锁：点名的、去取的、跳过的三颗数必须能对上
    assert p["skipped_blank_urls"] + len(p["feeds"]) == 3, s


def test_the_asked_number_keeps_counting_what_the_caller_named(stub):
    """`3 URLs asked` 这一格是 R02-F32 修好的，本刀不许把它改成「取了几颗」。"""
    p = _payload({"urls": [GOOD, "", ""], "max_items": 3})
    assert "1 URLs asked" not in p["summary"], p["summary"]
    assert p["summary"].startswith("3 URLs asked"), p["summary"]


def test_a_single_blank_is_named_with_one_position(stub):
    p = _payload({"urls": [GOOD, ""], "max_items": 3})
    assert p["skipped_blank_urls"] == 1, sorted(p)
    assert "position 2" in p["summary"], p["summary"]


# ─── 2 · 沉默锁：没空白项就不许多说 ─────────────────────────────────────────

def test_a_clean_batch_says_nothing_about_blanks(stub):
    p = _payload({"urls": [GOOD, "https://blog.test/feed2.xml"], "max_items": 3})
    assert "skipped_blank_urls" not in p, sorted(p)
    assert "blank" not in p["summary"], p["summary"]


def test_envelope_author_omits_the_scalar_when_nothing_was_skipped():
    env = _feed_batch_envelope(2, [{"source_url": "x", "items": [1]}], blank_positions=[])
    assert "skipped_blank_urls" not in env, sorted(env)
    assert "blank" not in env["summary"], env["summary"]
    env2 = _feed_batch_envelope(3, [{"source_url": "x", "items": [1]}], blank_positions=[1, 3])
    assert env2["skipped_blank_urls"] == 2, sorted(env2)
    assert "positions 1, 3" in env2["summary"], env2["summary"]


# ─── 3 · 全空白那一支：拒绝也要说得出丢了几颗，原话保留 ────────────────────

def test_the_all_blank_refusal_still_refuses_and_says_the_number(stub):
    """拒绝那一支走的不是异常而是同一套信封（R02-F32 立的形状），所以这里读回执的三个落点。"""
    p = _payload({"urls": ["", "   ", "\t"]})
    said = " ".join(str(p.get(k) or "") for k in ("error", "summary", "next_action"))
    assert "requires at least one URL" in said, p
    assert "3" in said, p


# ─── 4 · 结构锁：滤与报同一位作者，内联那条不许留下 ─────────────────────────

def test_the_filter_and_the_report_share_one_author():
    from dhole_mcp import server as S
    src = inspect.getsource(S)
    assert "if u and u.strip()" not in src, "方法体里那条内联滤法必须换成共用的那一位作者"
    assert src.count("_blank_url_split(") >= 3, "定义 + 滤那一处 + 报那一处，缺一处就是两处各数一遍"
    kept, blank = S._blank_url_split(["", GOOD, "  ", None])
    assert kept == [GOOD] and blank == [1, 3, 4], (kept, blank)
