r"""`feed_read` 的回信也要有信封：几个源、几条、什么时候拿的、是不是旧的、下一步做什么。

现场（P02-05 邱洛 券商行业研究员 L3 · R02-015 · P2 · 组 F；S1 首读 + S7 重复订阅）：

    feed_read(urls=['http://127.0.0.1:8399/feed.xml'], max_items=3) 连发两遍，相隔 21s
      → 三封响应的顶层键集合恒为 ['feeds']
      → 对照同一次会话里 P02-05-s5b-read-again.json 的 `cached` + summary 里
        「cached copy, fetched 0.9 min ago」：**同一次会话里两个读取工具，对「旧不旧」
        这个问题的可答性不同**

她的刁难点是明写的：「如果拿了一份缓存旧版，必须明说这是旧的」。在 `feed_read` 这条路
上今天既不能判通过也不能判失败 —— **判据缺失本身就是缺陷**（与 R01-016 同构：
能问到 = 修好了，问不到 = 没修）。

动手前量的那张表（读 `_dispatch` 的每一个分支，不是猜）：

    web_read / web_request / web_act / doc_parse → ResponseModel / BulkResponseModel
        summary · content_ok · next_action · fetched_at · duration_ms · cached  都有
    web_search  → SearchResponseModel  summary(336) · next_action(337) · cached(327) · duration_ms(332)
    web_crawl / web_discover → CrawlResponseModel  summary(211) · next_action(212) · 每页 fetched_at(184)
    sys_state / cred_defaults → 自己的模型，message + next_action
    **feed_read → `{"feeds": [...]}` 一颗顶层键**（成功那一支）
    sys_guide   → 裸文本（设计如此：把 markdown 塞进 JSON 只是转义换行，换不来一颗可读字段）

所以缺的不是"某处代码坏了"，是**一条通道从来没请过这位作者**。这一把配齐三件事：

  1. 批次级 `summary`：几个源被点名、回来几行、共几条条目（newest-first）、几行报错、
     数字**从行里算**（不许在信封里另数一遍，另数就会与行 disagree）；
  2. 每行 `fetched_at`：**那份文档到达的时刻**（不是信封装配的时刻 —— 发现那一支先取网页
     再取 feed，盖前者就是假话），由 `_grab` 落点自己盖；失败的行没有文档就不盖戳
     （缺席即「这一行没有拿到文档」，比一个假的此刻诚实）；
  3. 「是不是缓存的旧版」这一问的**正面回答**：这一条路没有缓存（`feed.py` 模块自己的说明
     第一行就是 No caching），所以要说的是「它没有缓存、这一份就是刚才那一取」，
     而不是发一颗 `cached=false` 装作与 web_read 同一套语义。

`next_action` 只在**整批没交付任何可用条目**时出现（三条各自的原因：全部报错 /
全部空手 / `since` 筛完没有新的 —— 最后那条不是失败，措辞不许把它写成失败）。
一次正常成功不付这句话（与 11/16 那一族同一条纪律）。

契约（本文件钉住）：
  * 顶层三颗键的形状：`feeds` + `summary` 恒在，`next_action` 只在空手时在；
  * `summary` 里那颗数与 `feeds` 的行**必须能对上**（测试自己按行算一遍再比句子）；
  * 每行 `fetched_at` 等于该文档抓取那一刻（用可控的钟验证），发现那一支盖的是
    **第二个**文档的时刻；报错行不带这一颗；
  * 两次调用之间源换了内容，第二次必须交回新内容（无缓存的可执行证明），且那一封
    里要说得出「没有缓存」；
  * 被拒绝的那一发（ValueError 那一支）也要有同一套顶层形状：`feeds` / `error` /
    `next_action` / `summary`，不许再是第三种信封；
  * 逐行的既有字段一颗不动（`note` / `diagnosis` / 两张 `since` 收据 / `items`）——
    这一刀只加信封，不改交付。
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from dhole_mcp import feed as feed_mod
from dhole_mcp import guide
from dhole_mcp.server import MasterFetchServer

FEED = ("<?xml version='1.0'?><rss version='2.0'><channel><title>{title}</title>"
        "{items}</channel></rss>")
ITEM = ("<item><title>{t}</title><link>https://blog.test/{t}</link>"
        "<pubDate>Tue, 14 Oct 2026 09:00:00 GMT</pubDate><description>d</description></item>")


def _feed(titles: list[str], title: str = "Blog") -> bytes:
    return FEED.format(title=title, items="".join(ITEM.format(t=t) for t in titles)).encode()


PAGE = (b"<html><head><link rel='alternate' type='application/rss+xml' "
        b"href='https://blog.test/real-feed.xml'></head><body>hi</body></html>")


def _serve(monkeypatch, bodies: dict[str, object], clock: dict | None = None):
    """假网络：{url: 文档字节 或 错误句}。返回被取过的 URL 顺序（可重跑、零网络）。

    `clock` 是与 `patch_clock` **同一只**计数器：钟按「第几发抓取」走，所以文档到达的
    时刻与信封装配的时刻不是同一个数 —— 盖错了地方当场就看得出来（发现那一支先取网页）。
    """
    calls: list[str] = []

    async def fake_grab(url, timeout, headers=None):
        calls.append(url)
        if clock is not None:
            clock["grabbed"] = int(clock.get("grabbed", 0)) + 1
        got = bodies.get(url)
        if isinstance(got, str):
            return b"", 0, "", got, {}
        return got, 200, "utf-8", "", {"content-type": "application/rss+xml"}

    monkeypatch.setattr(feed_mod, "_grab", fake_grab)
    return calls


def _stamps() -> list[datetime]:
    """秒级分开的时刻：线上的 `fetched_at` 本来就被剪到一秒精度，用微秒区分两发
    只会让断言去比一个已经被剪掉的数量（那样这条锁就是在比剪子，不是比盖戳的地方）。
    """
    base = datetime(2026, 10, 15, 12, 0, 0, 500_000, tzinfo=timezone.utc)
    return [base.replace(microsecond=500_000) + timedelta(seconds=i) for i in range(20)]


@pytest.fixture()
def patch_clock(monkeypatch):
    """可控的钟：第 n **次抓取**读到第 n 个时刻（实现里唯一的取时刻来源）。"""
    stamps = _stamps()
    state = {"grabbed": 0}

    def _utc_now():
        # 抓取计数由 `_serve` 推进，所以这里吃的是"已经取了几发"，不是"被调了几次"。
        return stamps[max(0, int(state["grabbed"]) - 1)]

    monkeypatch.setattr(feed_mod, "_utc_now", _utc_now)
    return state


def _payload(tool: str, args: dict) -> dict:
    out = asyncio.run(MasterFetchServer()._dispatch(tool, args))
    return out[1] if isinstance(out, tuple) and len(out) == 2 else {}


def _feed_payload(args: dict) -> dict:
    return _payload("feed_read", args)


URL_A = "https://blog.test/a.xml"
URL_B = "https://blog.test/b.xml"
URL_DEAD = "https://blog.test/dead.xml"


# ─── 1 · 批次级信封 ─────────────────────────────────────────────

def test_a_feed_reply_names_how_many_sources_and_items(monkeypatch):
    _serve(monkeypatch, {URL_A: _feed(["one", "two"]), URL_B: _feed(["three"])})
    payload = _feed_payload({"urls": [URL_A, URL_B]})
    summary = payload.get("summary", "")
    assert summary, f"顶层仍只有 `feeds`：{sorted(payload)}"
    assert "2 URLs asked" in summary, summary
    assert "3 items" in summary, summary
    assert "holds no cache" in summary, summary


def test_the_summary_numbers_cannot_disagree_with_the_rows(monkeypatch):
    """三发里两发有条目、一发报错：信封上那几颗数必须能按行复核。"""
    _serve(monkeypatch, {URL_A: _feed(["one", "two"]), URL_B: _feed(["three"]),
                         URL_DEAD: "HTTP 410 Gone"})
    payload = _feed_payload({"urls": [URL_A, URL_B, URL_DEAD]})
    rows = payload["feeds"]
    summary = payload["summary"]
    items = sum(len(r.get("items") or []) for r in rows)
    errored = sum(1 for r in rows if r.get("error"))
    asked = re.search(r"(\d+) URLs asked", summary)
    got = re.search(r"(\d+) items", summary)
    answered = re.search(r"(\d+) rows answered", summary)
    assert asked and got and answered, summary
    assert int(asked.group(1)) == int(answered.group(1)) == len(rows) == 3, summary
    assert int(got.group(1)) == items == 3, (summary, items)
    assert f"{errored} rows errored" in summary, summary


def test_a_batch_that_delivered_items_pays_no_next_action(monkeypatch):
    """有交付就没有建议：一次干净成功不付这句话（11/16 那一族不付费）。"""
    _serve(monkeypatch, {URL_A: _feed(["one"])})
    payload = _feed_payload({"urls": [URL_A]})
    assert "next_action" not in payload, payload


def test_a_batch_where_every_source_failed_says_what_to_do_next(monkeypatch):
    _serve(monkeypatch, {URL_A: "HTTP 503 Service Unavailable",
                         URL_B: "timed out"})
    payload = _feed_payload({"urls": [URL_A, URL_B]})
    action = payload.get("next_action", "")
    assert action, payload
    assert "diagnosis" in action, action          # 逐行的结构化判据才是下一步的出处
    assert "web_read" in action, action           # feed 不在了就去读那一站的页面


def test_a_quiet_batch_says_the_sources_published_nothing(monkeypatch):
    """每一行都拿到了文档、条数却是 0：这一封要说「没有新东西」，不是「失败了」。"""
    _serve(monkeypatch, {URL_A: _feed([]), URL_B: _feed([])})
    payload = _feed_payload({"urls": [URL_A, URL_B]})
    action = payload.get("next_action", "")
    assert action, payload
    assert "error" not in action.lower(), action


def test_a_poll_with_nothing_new_is_not_reported_as_a_failure(monkeypatch):
    """`since` 筛空了是「没有增量」：这一格不许与「源报错」共用一句话。"""
    _serve(monkeypatch, {URL_A: _feed(["old"])})
    payload = _feed_payload({"urls": [URL_A], "since": "2027-01-01"})
    row = payload["feeds"][0]
    assert row["items_older_than_since"] == 1, row
    action = payload.get("next_action", "")
    assert action and "since" in action.lower(), payload
    assert "fail" not in action.lower(), action   # 不是失败，是安静


# ─── 2 · 每行的取回时刻 ─────────────────────────────────────────

def test_each_row_names_the_moment_its_document_arrived(monkeypatch, patch_clock):
    _serve(monkeypatch, {URL_A: _feed(["one"])}, clock=patch_clock)
    row = _feed_payload({"urls": [URL_A]})["feeds"][0]
    assert row.get("fetched_at") == "2026-10-15T12:00:00Z", row


def test_the_stamp_is_the_documents_moment_not_the_pages(monkeypatch, patch_clock):
    """发现那一支先取网页、再取 feed：盖在第一发就是假话（那一刻拿到的是 HTML）。"""
    calls = _serve(monkeypatch, {"https://blog.test/": PAGE,
                                 "https://blog.test/real-feed.xml": _feed(["one"])},
                   clock=patch_clock)
    row = _feed_payload({"urls": ["https://blog.test/"]})["feeds"][0]
    assert len(calls) == 2, calls
    assert row["discovered_from"] == "https://blog.test/", row
    assert row["fetched_at"] == "2026-10-15T12:00:01Z", row


def test_a_row_with_no_document_carries_no_stamp(monkeypatch, patch_clock):
    """没有文档就没有「它是什么时候拿的」：留空并被线压缩摘掉，比补一个此刻诚实。"""
    _serve(monkeypatch, {URL_DEAD: "HTTP 410 Gone"}, clock=patch_clock)
    row = _feed_payload({"urls": [URL_DEAD]})["feeds"][0]
    assert row.get("error"), row
    assert "fetched_at" not in row, row


def test_the_feed_path_holds_no_cache_and_says_so(monkeypatch):
    """她的刁难点：拿了一份缓存旧版必须明说。这一条路的真话是「没有缓存」——
    可执行证明：两次调用之间源换了内容，第二次必须交回新内容。"""
    _serve(monkeypatch, {URL_A: _feed(["one"])})
    first = _feed_payload({"urls": [URL_A]})
    assert "one" in json.dumps(first, ensure_ascii=False)

    _serve(monkeypatch, {URL_A: _feed(["one", "two"])})
    second = _feed_payload({"urls": [URL_A]})
    titles = [i["title"] for i in second["feeds"][0]["items"]]
    assert titles == ["two", "one"] or "two" in titles, titles
    assert "cache" in second["summary"].lower(), second["summary"]


def test_a_batch_that_came_back_short_says_so(monkeypatch):
    """空白 URL 被方法滤掉是旧行为，本刀改的是**可见性**：`asked` 高于 `answered`
    就是产品在自己说「有几颗没换成行」。说不出是哪一颗 —— 那一半另记 R02-053。
    """
    _serve(monkeypatch, {URL_A: _feed(["one"])})
    payload = _feed_payload({"urls": ["", URL_A, "   "]})
    summary = payload["summary"]
    assert "3 URLs asked" in summary, summary
    assert "1 rows answered" in summary, summary
    assert len(payload["feeds"]) == 1, payload


# ─── 3 · 同一套信封，被拒绝那一发也不例外 ───────────────────────

def test_a_refused_call_shares_the_same_top_level_shape(monkeypatch):
    """ValueError 那一支早就有 error/next_action，缺的是 summary：一发也没出的时候
    「几个源、几条」同样要说得出（不许多出一颗假的 fetched_at）。

    2026-10-19（R02-133 · F114）这一格从四格变五格：多出来的正是 `diagnosis` —— 手册
    （`references/fields.md:67`）对每副戴 `error` 的回执都承诺把手那一格，从前这一扇的门里面
    偏偏没有。这一枚钉的是"同一副骨架"，不是"恰好四格"，所以跟改的是名单而不是判据；
    「不许多出一颗假的 fetched_at」那一条一字未动，它量的还是那一件事。
    """
    payload = _feed_payload({"urls": []})
    assert set(payload) == {"feeds", "error", "next_action", "summary", "diagnosis"}, \
        sorted(payload)
    assert payload["feeds"] == []
    assert "no" in payload["summary"].lower(), payload["summary"]
    # 一发都没出去：不许有一颗"取回时刻"混进这一封（顶层与行都不该有）
    assert "fetched_at" not in payload, payload


# ─── 4 · 上了线就要在册 ─────────────────────────────────────────

def test_both_new_reads_are_documented():
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))
    assert "feed_read" in corpus
    # 每行的 fetched_at 在 feed 这条路上是什么意思（没有缓存可问），要写清
    assert "no cache" in corpus.lower() or "holds no cache" in corpus.lower(), (
        "手册没说 feed_read 这条路不缓存：读者会把每行的 fetched_at 当成回放时刻")
    assert "URLs asked" in corpus, (
        "批次级 summary 的形状没进手册：只读手册的人不知道有这一行")
