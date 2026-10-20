"""爬取把每一行都交回了，却跑过了自己的时间预算 —— 这一格今天全是静默。

现场（2026-10-14 真实使用模拟 · P02-04 白榆（专利代理师 L2）· S2B，H2 在本轮的复现）：

    web_crawl(url='…/blog', max_pages=15, timeout=6000, cache_ttl=0)
      → 13 行全部交回；63,893 字符对 60,000 那颗**字符**预算申报了（summary 明写
        「returned 13 page(s) / 63,893 chars against the 60,000 you set」）；
      → duration_ms=12,018 —— 点名的那颗**时间**预算的 2.0 倍，没有一句话。

同一封响应里两种预算两种口径，这正是用户不再相信参数的地方。她在 S4 直接放弃了原本要
做的内容筛，原话是「我不信我点的时间是有效的，那我不想在一次未知的等待上再赌一次」——
本轮第一个由自述不一致直接导致的放弃。而且 `timeout=` 在 dhole 的语义里管的是一次 HTTP
尝试，整趟跑的是 `deadline_ms`；这个单位陷阱在 web_read 那一侧早有 `timeout_applied`
收尾，爬取这一侧一直没有。

机制（两条事实叠在一起）：
  1. 已知并在 backlog 记着的：`deadline_ms` 不是硬上限 —— 循环只在每一圈**开头**看钟，
     已经在飞的那一发会跑完它自己。这是设计（掐掉一次已经完成的下载比让它跑完更糟）。
  2. 本缺陷：于是「全部页都交回来了」的那一发里 `truncated_by_time` 是 false，而今天
     每一句关于时间的话都挂在那颗旗子上 —— 没有截断就没有那句话，越线成为静默。
     第 01 轮 R01-042 立的是「被掐掉那层的事实要上回执」，本刀是它的**补集**：没掐到的
     那一半也要说话，因为用户点过名。

契约（本文件钉住）：
  * 跑得比这一趟实际受约束的那颗 deadline 久 → `budget_overrun_ms` 报出超了多少，
    `deadline_ms_applied` 报出这一趟受的是哪颗数字（点名 `deadline_ms=` 时是它，没点名
    时是本工具的默认帽子 —— 两种都算「这一趟的预算」）。
  * 那句话同时把**单位**说清：`timeout=` 管一次 HTTP 尝试，整趟管 `deadline_ms`。不许
    把前者说成后者的硬上限（那是假话），也不许把一次越线说成一次失败。
  * 反面锁：跑在预算内 → 两个数都是 0（压实线上等于缺席），summary 不多嘴。
  * 反面锁 II：真被截断的那一发今天就在说话（行数与游标都在它那句里）。本刀不改它的
    口径，也不在同一封响应里把同一件事说两遍。
  * 与 `test_a_crawl_that_ran_out_of_characters_blames_the_characters.py` 同族：那一刀
    管字符预算的那一半，这一刀管时钟的那一半。

时间形状是钉出来的，不是碰运气：本地靶把最后一颗页做成 600ms 的慢发、前两颗 20ms ——
第三圈开头看钟时仍在帽内（照发），回来时队列已空（不截断）。越线必然发生，且两侧都留
着一大段余量。
"""

from __future__ import annotations

import http.server
import socketserver
import threading
import time

import pytest

from dhole_mcp.server import MasterFetchServer

NEWLINE = chr(10).encode()
PAGE = (b"<html><head><title>Post</title></head><body><article>"
        b"<h1>A post about indexing patents</h1>"
        + b"<p>The claims describe a method for ranking families by priority date. "
        b"It is long enough to be real prose for the extractor.</p>" * 6
        + b"<a href=\"/post-2\">next</a><a href=\"/post-3\">and</a>"
        b"</article></body></html>")

FAST_S = 0.02
SLOW_S = 1.2


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.endswith("robots.txt"):
            self.send_response(200)
            self.send_header("content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"User-agent: *" + NEWLINE + b"Allow: /")
            return
        # 最后一颗页慢一发：越线落在「已经在飞的那一发跑完了」那一格里。
        time.sleep(SLOW_S if self.path.endswith("/post-3") else FAST_S)
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.send_header("content-length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *a):
        pass


@pytest.fixture
def origin():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _the_test_origin_is_fetchable():
    from dhole_mcp import identity
    identity.set_value("allow_private", True)


@pytest.mark.asyncio
async def test_the_caller_named_a_time_and_the_run_passed_it(origin):
    """R02-012 现场那一格：整趟没越 `deadline_ms`，却越过了用户**点名的那颗数字**。

    `timeout=200` 而这一趟跑了 ~1,300ms —— 三个行全部交回、没有任何截断，因此今天
    `truncated_by_time` 是 false，而每一句关于时间的话都挂在那颗旗子上：静默。
    现场是 `timeout=6000` 对 `duration_ms=12,018`（同一形状，只是大一号）。
    """
    r = await MasterFetchServer().web_crawl(origin + "/", max_pages=3,
                                            deadline_ms=20_000, timeout=200,
                                            cache_ttl=0)
    assert not r.truncated_by_time, f"这一发不该被截断：{r.summary}"
    assert len(r.pages) == 3, f"三行都要交回来：{len(r.pages)}"
    assert r.attempt_cap_ms == 200, (
        f"回执说不出一次尝试的帽子是哪颗数字：{r.attempt_cap_ms}")
    assert r.deadline_ms_applied == 20_000, r.deadline_ms_applied
    assert r.budget_overrun_ms == 0, (
        f"整趟并没有越过 deadline_ms，这一格不该报越线：{r.budget_overrun_ms}")
    text = f"{r.summary} {r.next_action}"
    assert "200" in text, text
    assert "ONE HTTP attempt" in text, text
    assert "deadline_ms" in text, text


@pytest.mark.asyncio
async def test_a_crawl_inside_its_time_budget_says_nothing_about_it(origin):
    """反面锁：没超就不要多嘴（0 = 缺席，压实线上不占字节）。"""
    r = await MasterFetchServer().web_crawl(origin + "/", max_pages=1,
                                            deadline_ms=60_000, timeout=5000,
                                            cache_ttl=0)
    assert r.budget_overrun_ms == 0, r.budget_overrun_ms
    assert "outlived" not in (r.summary or "").lower(), r.summary


@pytest.mark.asyncio
async def test_the_overrun_sentence_names_the_unit_not_a_false_cap(origin):
    """那句话不许把 `timeout=` 说成整趟的帽子，也不许把越线说成失败。"""
    r = await MasterFetchServer().web_crawl(origin + "/", max_pages=3,
                                            deadline_ms=1000, timeout=200,
                                            cache_ttl=0)
    text = f"{r.summary} {r.next_action}"
    assert r.budget_overrun_ms > 0, f"这一发也要越线才有话可查：{r.duration_ms}"
    assert "deadline_ms" in text, text
    assert "one http attempt" in text.lower(), text
    assert "hard limit" not in text.lower() and "timed out" not in text.lower(), (
        f"句子把一次越线说成了一次失败：{text[:220]}")


@pytest.mark.asyncio
async def test_a_truncated_crawl_names_its_number_and_says_it_once(origin):
    """被截断那一支：行数的句子今天就在，缺的是**越了多少 ms** 这个数 —— 两句各司事，
    而同一句"越线了"不许出现两遍。"""
    r = await MasterFetchServer().web_crawl(origin + "/", max_pages=8,
                                            deadline_ms=100, timeout=5000,
                                            cache_ttl=0)
    assert r.truncated_by_time, f"这一发应当被截断：{r.summary}"
    assert r.deadline_ms_applied == 100, r.deadline_ms_applied
    assert r.budget_overrun_ms > 0, (
        f"被截断的这一发同样越过了整趟预算，却没有那个数：{r.duration_ms}")
    text = r.summary or ""
    assert "outlived" in text.lower(), text
    assert text.lower().count("outlived") == 1, text
    assert "100" in text, text


