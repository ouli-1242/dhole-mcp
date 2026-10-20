"""验收刀9-5（F4）：一批 URL 共用一次返回预算，这笔账从前没人算过。

报告 S6 的形状是"粘 9 个链接，要一句话摘要 + 对比表"。`web_read(urls=9)` 每一页都各拿满
默认的 40,000 字符 = 360,000，宿主在自己的载荷上限处把整份截断 —— **只剩下前 6 条**，
助手必须再发一次才补齐后 3 条。那次返工既更慢，又把"我要了 9 条"这件事变成了调用方的
活儿：一批的总预算明明是一次调用就能算出来的数。

现在按页数摊：`BULK_TOTAL_CONTENT_CHARS // len(urls)`，默认那一档 9 页各拿 5,333 ——
一页摘要长度的正文还剩得下，而 9 条齐全。三条例外都是刻意的：

* **单条不压**。一次一条本来就用不满那份预算，压它只是把默认变差（行为逐字不变）。
* **点了名不压**。`max_content_chars=` 是显式决定，替他改等于把他的参数当成建议 ——
  这一族对显式值的规矩写在 `web_read` 里那一行 "An explicit value still wins"。
* **压了必须逐行说**。落点是每一行自己的 `summary`，说清三个数（每页压到几、为什么是
  这个数、出路是什么），并且给两条出路：点名 `max_content_chars=` 多花，或单独读那一页
  回到默认。静默把 40,000 变成 5,333 是本仓库列为最坏的那一类形状。

失败的那一行不追加这句话 —— 它一个字都没被截，加上就成了一句关于没发生过的事的断言。
"""

from __future__ import annotations

import asyncio

import pytest

from dhole_mcp import server as SM
from dhole_mcp.server import (BULK_TOTAL_CONTENT_CHARS, DEFAULT_MAX_CONTENT_CHARS,
                              MasterFetchServer)

BODY = "Real body text so the extractor has something to chew on. " * 2000
URLS9 = [f"https://site{i}.example.com/post" for i in range(9)]
NEEDLE = "this batch shares one return budget"


def _stub(monkeypatch, *, failing=()):
    """每一页都回一大坨正文：真实的截断发生在 `mc` 那一步，不在抓取层。"""

    async def fake(self, url, **kwargs):
        if any(url.startswith(f) for f in failing):
            return SM.ResponseModel(url=url, status=0, content=[],
                                    fetcher_used="none", error="connection refused")
        return SM.ResponseModel(url=url, status=200, content=[BODY],
                                fetcher_used="http", content_ok=True,
                                metadata={"content_type": "text/html"},
                                total_size_bytes=len(BODY))

    monkeypatch.setattr(MasterFetchServer, "get", fake)


def _call(args):
    out = asyncio.run(MasterFetchServer()._dispatch("web_read", args))
    if isinstance(out, tuple) and len(out) == 2:
        return out[1]
    return {}


def _chars(row) -> int:
    return sum(len(c) for c in row.get("content") or [])


@pytest.mark.parametrize("n", [2, 3, 9, 12])
def test_a_batch_splits_one_budget_instead_of_losing_the_tail(monkeypatch, n):
    _stub(monkeypatch)
    urls = URLS9[:n] if n <= 9 else URLS9 + [f"https://extra{i}.example.com/"
                                             for i in range(n - 9)]
    out = _call({"urls": urls, "force_fetcher": "http", "cache_ttl": 0})
    rows = out.get("results") or []
    assert len(rows) == n, f"要了 {n} 条只回 {len(rows)} 条"
    assert all(r["status"] == 200 for r in rows), [r.get("error") for r in rows]
    share = BULK_TOTAL_CONTENT_CHARS // n
    # 页首那几行（标题/出处）是另算的，所以这里量的是"落在那一页份额上"而不是一个
    # 逐字符的等式：这条主张是关于总载荷装不装得下，不是关于 markdown 的长度。
    for r in rows:
        assert abs(_chars(r) - share) <= 500, (_chars(r), share)
    assert sum(_chars(r) for r in rows) <= BULK_TOTAL_CONTENT_CHARS + 500 * n, \
        sum(_chars(r) for r in rows)


def test_every_row_says_it_was_capped_and_how_to_get_more(monkeypatch):
    _stub(monkeypatch)
    out = _call({"urls": URLS9, "force_fetcher": "http", "cache_ttl": 0})
    rows = out["results"]
    per = BULK_TOTAL_CONTENT_CHARS // 9
    for r in rows:
        summary = r.get("summary") or ""
        assert NEEDLE in summary, summary
        assert f"capped at {per} content chars" in summary, summary
        assert "9 x the 40000 default is 360000" in summary, summary
        assert "max_content_chars=" in summary and "read one URL alone" in summary, summary


def test_a_failed_row_is_not_told_it_was_cut(monkeypatch):
    """截断的账只写给真被截过的那一行：给一行没发出去的正文解释预算就是编故事。"""
    _stub(monkeypatch, failing=("https://site3.example.com",))
    out = _call({"urls": URLS9, "force_fetcher": "http", "cache_ttl": 0})
    rows = out["results"]
    dead = [r for r in rows if r["status"] == 0]
    assert len(dead) == 1, dead
    assert all(NEEDLE not in (r.get("summary") or "") for r in dead), dead
    assert all(NEEDLE in (r.get("summary") or "") for r in rows if r["status"] == 200)


def test_one_url_is_left_alone(monkeypatch):
    """单条不压：一次一条本来就用不满那份预算，压它只是把默认变差。"""
    _stub(monkeypatch)
    out = _call({"url": URLS9[0], "force_fetcher": "http", "cache_ttl": 0})
    assert abs(_chars(out) - DEFAULT_MAX_CONTENT_CHARS) <= 500, _chars(out)
    assert NEEDLE not in (out.get("summary") or ""), out.get("summary")


@pytest.mark.parametrize("named", [1500, DEFAULT_MAX_CONTENT_CHARS, 200000])
def test_a_named_budget_is_the_callers_own(monkeypatch, named):
    """点了数就不替他改 —— 哪怕点的是一个会把批次挤出宿主载荷的数。

    这一条不是为那个后果辩护，是把它钉成"显式值赢"的既有规矩：想少花就点名小值，想多花
    就点名大值，两种都不该被一个自动份额拦下来。
    """
    _stub(monkeypatch)
    urls = URLS9 if named <= DEFAULT_MAX_CONTENT_CHARS else URLS9[:2]
    out = _call({"urls": urls, "force_fetcher": "http", "cache_ttl": 0,
                 "max_content_chars": named})
    cap = min(named, 200000)
    for r in out["results"]:
        assert NEEDLE not in (r.get("summary") or ""), r.get("summary")
        if cap <= len(BODY):
            assert abs(_chars(r) - cap) <= 500, (_chars(r), cap)


def test_the_share_grows_when_the_batch_is_small_and_stops_at_the_floor(monkeypatch):
    """份额随页数走，但地板不许跌破 500：一页只剩 500 字符的话这条批读就没有意义了。

    100 是 `urls=` 的上限（`MAX_BULK_URLS`），那正是份额最小的一发 —— 48,000 / 100 = 480
    落在地板以下，所以每一页拿到 500 而不是 480：宁肯总载荷超出预算一点，也不交回一页
    读不出东西的正文。超出那部分是刻意的，且每一行都照样说清了压到了几。

    R01-024 之后这一条的数钉在"这一页自己的字"上：切口退到子句末时退回去的那几个字随
    `next_offset` 走下一窗，所以带内的正文可以少于 500 —— 只要 `chars_snapped_back` 报出
    差额、两者相加正好是 500。原来那句 `>= 500` 一直绿着，是因为 dhole 自己那句
    "[Truncated: …]"（实测 173 字）就挂在正文尾巴上把它垫过了头：地板是被缺陷垫出来的，
    而那一行 `summary` 明明写着 "capped at 500 content chars"。
    """
    _stub(monkeypatch)
    two = _call({"urls": URLS9[:2], "force_fetcher": "http", "cache_ttl": 0})
    nine = _call({"urls": URLS9, "force_fetcher": "http", "cache_ttl": 0})
    assert _chars(two["results"][0]) > _chars(nine["results"][0]), (
        _chars(two["results"][0]), _chars(nine["results"][0]))
    hundred = [f"https://h{i}.example.com/" for i in range(100)]
    out = _call({"urls": hundred, "force_fetcher": "http", "cache_ttl": 0})
    assert len(out["results"]) == 100, out.get("total")
    for r in out["results"]:
        body = _chars(r)
        snap = r.get("chars_snapped_back") or 0
        assert body + snap == 500, (body, snap)
        assert "capped at 500 content chars" in (r.get("summary") or ""), r.get("summary")
