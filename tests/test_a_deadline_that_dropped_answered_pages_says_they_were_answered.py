"""deadline 越线那一发里被丢掉的行，站点其实已经答完了 —— 句子不许说"没发过"。

现场（2026-10-14 · P4 写 R02-F4 时的定时取样 · 本地靶 + 服务端日志双证 · R02-038）：

    web_crawl(url=<本地靶>, max_pages=3, deadline_ms=1000, timeout=5000, cache_ttl=0)
      → rows=1、truncated_by_time=true
      → next_action：「2 URL(s) were discovered but not fetched」
      → 同一时刻靶子的日志：/post-2 在 0.80s、/post-3 在 1.98s **各打了一行** —— 都收到、
        都答了。

一句话假在何处：那两颗**发过也答回来了**，只是回到时帽子已经过点，行被丢在批次里。计数来自
一个代数式 `not_fetched = pages_discovered - pages_crawled`，它把两种"少"合成一个数：
  * 队列里根本没轮到发的（真没发）；
  * 这一批已经付过请求、答案在手而没吐出来的（付了没返回）。
字符预算那一支早就分过这两种（那一支的计数与那句 "N fetched in the last batch
and not returned"），时钟这一支没分 —— 于是 summary 有时说出"付了没返回"，next_action
却一律写成"没 fetch 过"。同一封响应里两句话互相矛盾，而假的那句才是调用方照着行动的那句
（它决定抬不抬 `deadline_ms`、要不要重爬）。

按 §6.2 这条不是网络问题：请求发出去了，站点答了，是 dhole 把答案丢了又说没发。独立旁证是
**服务方自己的日志**（本文件里就是靶页的请求计数），不是被审对象的自述。

契约（本文件钉住）：
  * 「少了的那几颗」这一句只把"没发过"说给真的没发出去过的；
  * 发过而答案被丢的那几颗要按"had already been requested … dropped rather than returned"
    报，并给出两个数；
  * 三处 break（`max_pages` / 字符帽 / 时钟）共用同一位作者 —— 同一句话不许有三个版本；
  * 字符预算那一支走同一位作者；
  * 反面锁：帽子落在两批**之间**（本文件用 `max_pages` 那一格，它是同一条代数式的确定
    形状）时，全部差额都是"没发过"，句子不许反过来吹成"已经付过钱了"；
  * 判决一位不动：`truncated_by_time` / `pages_crawled` / `pages_discovered` 的语义与
    数值都不因这一刀改变 —— 本刀修的是那句假话，不是那次数账。
"""

from __future__ import annotations

import http.server
import socketserver
import threading
import time

import pytest

from dhole_mcp.server import MasterFetchServer

NEWLINE = chr(10).encode()

# 每一颗页的延迟是钉出来的：慢的那几颗保证 gather 全部返回时帽子已经过点，
# 于是"发过、答了、行被丢"那一格必然出现；而快的与慢的分在两侧，余量都留着一大段。
#
# 2026-10-20 CI 实测：原来这一格钉的是 300ms 帽 + 0.45s 慢页，而满载的 runner 上
# 「起点页 + 首次 robots.txt + 抽取器冷启动」自己就吃掉 300ms 以上 —— 四颗链接根本没
# 轮到发出，靶站日志只剩 `['/']`，这一发测的就不再是"付了没返回"，而是"这台机器多快"。
# 判据要的只是**快批赶得上帽子、慢批赶不上**这一个相对形状，所以两边一起放大：帽子
# 1000ms、慢页 2.0s、快页 0.05s（比值与原来同一），并在计时之前先预热一发，把一次性
# 的 robots 抓取与冷启动挪出帽子之外。
DELAY = {"/slow-start": 0.55, "/fast": 0.05, "/p1": 0.05, "/p2": 0.05,
         "/p3": 2.0, "/p4": 2.0, "/p5": 2.0, "/p6": 2.0}
LINKS = "".join(f'<a href="/p{i}">p{i}</a>' for i in range(1, 7))
HITS: list[str] = []

PROSE = ("<p>The claims describe a method for ranking patent families by priority "
         "date, and the sentence is long enough to read as real prose.</p>")


def _page(path: str) -> bytes:
    body = ("<html><head><title>Post</title></head><body><article>"
            f"<h1>A post about {path}</h1>" + PROSE * 3 + LINKS
            + "</article></body></html>")
    return body.encode()


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path.endswith("robots.txt"):
            self.send_response(200)
            self.send_header("content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"User-agent: *" + NEWLINE + b"Allow: /")
            return
        HITS.append(path)
        time.sleep(DELAY.get(path, 0.02))
        self.send_response(200)
        self.send_header("content-type", "text/html")
        body = _page(path)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def origin():
    HITS.clear()
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _the_test_origin_is_fetchable():
    from dhole_mcp import identity
    identity.set_value("allow_private", True)


async def _crawl(origin, path="/p1", **kw):
    return await MasterFetchServer().web_crawl(url=origin + path, cache_ttl=0, **kw)


async def _warm(origin):
    """把一次性开销（首次 robots.txt、抽取器冷启动）挪到帽子之外，然后把日志清空。

    这两发钉的是"快批赶得上帽子、慢批赶不上"这一个相对形状；让冷启动留在计时窗口里，
    测出来的就是这台机器的负载而不是那个形状（2026-10-20 CI 实测：四颗链接一颗都没发出，
    日志只剩 `['/']`）。预热之后 `HITS.clear()`，日志证据仍然只属于被审的那一发。
    """
    await MasterFetchServer().web_read(url=origin + "/", cache_ttl=0)
    HITS.clear()


# ─── 1 · 付过请求的那几颗，不许被说成没发过 ───


@pytest.mark.asyncio
async def test_pages_the_site_answered_are_not_called_unsent(origin):
    """现场那一格：四颗都发出去了，帽子在 gather 回来之后才过点。

    1000ms 的帽子落在第二批的飞行途中：p1/p2 各 0.05s、p3/p4 各 2.0s，四颗同一批
    发出，gather 要等最慢的那颗 —— 回到循环时点已过，四行一起被丢在批次里。
    """
    await _warm(origin)
    r = await _crawl(origin, "/", max_pages=5, concurrency=4, deadline_ms=1000)

    assert r.truncated_by_time, f"这一发应当被时钟砍：{r.summary}"
    dials = [h for h in HITS if not h.endswith("robots.txt")]
    assert dials.count("/p1") + dials.count("/p2") + dials.count("/p3") \
        + dials.count("/p4") == 4, f"靶站日志证明四颗都发过：{dials}"

    text = f"{r.summary} {r.next_action}"
    assert "discovered but not fetched" not in text, (
        f"句子把已答完的页说成没发过：{text[:260]}")
    assert "already been requested" in text, (
        f"没有把「付了没返回」那一格说出来：{text[:260]}")


@pytest.mark.asyncio
async def test_the_two_counts_are_separated_not_merged_into_one_number(origin):
    """两种"少"各有其数：丢了几个已答的、还有几个压根没发。

    靶上四颗链接都在同一批里发出，行数 = 起点那一页 + 来得及吐出来的那些，所以
    「答了却被丢下」的颗数是可以从回执自己算出来的（不必信句子）。
    """
    await _warm(origin)
    r = await _crawl(origin, "/", max_pages=5, concurrency=4, deadline_ms=1000)

    dropped = 4 - (len(r.pages) - 1)
    never_sent = max(r.pages_discovered - r.pages_crawled, 0) - dropped
    assert (dropped, never_sent) == (4, 2), (
        f"这一格应当是 4 行付了没返回、2 行压根没发：dropped={dropped} "
        f"never_sent={never_sent} pages={len(r.pages)} "
        f"discovered={r.pages_discovered} crawled={r.pages_crawled}")
    text = f"{r.summary} {r.next_action}"
    assert f"{dropped} URL(s) of those had already been requested" in text, text[:300]
    assert f"{never_sent} were never sent" in text, text[:300]


# ─── 2 · 反面锁：没有一行被丢下时，不许反过来吹成"已经付过钱了" ───


@pytest.mark.asyncio
async def test_a_run_cut_between_rounds_names_the_unsent_as_unsent(origin):
    """`max_pages` 正好落在批与批之间：差额里一颗也没发过。

    这一格是确定形状的（不靠毫秒级的窗口）：`min(concurrency, remaining)` 让每一批
    不超出剩余名额，所以 `len(batch) - _bi` 一直是 0 —— 句子必须只报"没发过"那一个数。
    """
    r = await _crawl(origin, "/", max_pages=2, concurrency=4)

    assert r.truncated_by_max_pages, r.summary
    assert len(r.pages) == 2, r.summary
    text = f"{r.summary} {r.next_action}"
    assert "already been requested" not in text, (
        f"没发过的被说成付过钱了：{text[:260]}")
    assert f"{r.pages_discovered - r.pages_crawled} URL(s) were discovered " \
        "but never sent" in text, text[:260]


# ─── 3 · 字符预算那一支：同一句话只有一个作者 ───


@pytest.mark.asyncio
async def test_the_character_budget_branch_says_the_same_thing(origin):
    """summary 早就分过"付了没返回"，next_action 却一律写"没 fetch 过" —— 一句话两个版本。"""
    r = await _crawl(origin, "/", max_pages=5, concurrency=4, max_total_chars=1000)

    assert r.truncated_by_total_chars, r.summary
    assert "fetched in the last batch and not returned" in (r.summary or ""), r.summary
    text = f"{r.summary} {r.next_action}"
    assert "discovered but not fetched" not in text, text[:320]
    assert "URL(s) of those had already been requested" in text, (
        f"同一封响应里两句话对不上：{text[:320]}")


def test_the_false_phrase_has_no_author_left():
    """那句假话不许在源码里留第二个版本：三处 break 共用一位句子作者。"""
    import inspect
    import re

    from dhole_mcp import crawl as crawl_mod

    src = inspect.getsource(crawl_mod)
    assert "discovered but not fetched" not in src, (
        "「少了 = 没发过」那句话还在源码里")
    assert src.count("had already been requested") == 1, (
        "两个数的句子只有一个作者")
    # 三处 break 都写同一颗计数（漏一处就是漏修一处）。
    assert len(re.findall(r"dropped_after_answering = len\(batch\) - _bi", src)) == 3, (
        "max_pages / 字符帽 / 时钟三处 break 必须都记账")


def test_the_manual_tells_the_two_numbers_apart():
    """回执分了两种"少"，手册就得教调用方两种少有不一样的解药。

    合在一个数里时，"抬预算"是唯一建议；分了两个数之后，已付的那一部分抬预算也救不回
    那些请求 —— 手册漏掉这层，两个数就白分了。
    """
    import inspect
    from pathlib import Path

    from dhole_mcp import crawl as crawl_mod
    doc = (Path(inspect.getsourcefile(crawl_mod)).resolve().parent
           / "skills" / "dhole-web" / "references" / "fields.md"
           ).read_text(encoding="utf-8")
    row = [ln for ln in doc.splitlines()
           if "truncated_by_total_chars` | Which budget stopped" in ln]
    assert len(row) == 1, "截断三兄弟仍是一行"
    assert "already been requested" in row[0] and "were never sent" in row[0], row[0]
    assert "cannot un-spend" in row[0], (
        f"没有说清已付的那一部分抬预算救不回来：{row[0][:400]}")
