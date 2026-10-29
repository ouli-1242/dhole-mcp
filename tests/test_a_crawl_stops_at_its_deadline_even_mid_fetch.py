"""`deadline_ms` 是 hard stop：在飞的那一发也要在到点时被放下。

症状（2026-10-29 · 真实站点 career.fit.edu.cn）：`web_crawl(deadline_ms=25000,
timeout=30000)` 跑了 44,894ms —— 25s 的整趟预算，时钟只在**批次边界**读，而一批里
最慢的那一发把整个 `timeout=` 跑满才算完。宿主按 30s 砍连接，于是调用方什么都没拿到；
同一晚 `web_discover(timeout=20000)` 也跑出 26,695ms。wire 描述承诺的是 "Hard stop
for the whole crawl"，而实现给的是一颗只在两批之间生效的软闸。

修法不是砍掉整趟（那会把已经抓完的页一起扔掉），而是把**每一页自己的抓取**包进
剩余预算：到点取消的是这一页，批次照常回来，已完成的行照旧交回。取消之后 HTTP/浏览器
栈收尾还要一点时间，所以墙钟的上界是 `deadline_ms` ＋ 一小段收尾宽限，而不是
`deadline_ms` ＋ 一次完整的 `timeout=`。

本地靶把形状钉死：起点页睡 5s，帽子 500ms。旧实现会在 5s 后回来（超预算 4.5s）；
新实现必须在 500ms 附近回来，并且靶站日志证明那一发**确实发出去过**——它被放下的位置
是飞行途中，不是队列里。
"""

from __future__ import annotations

import http.server
import socketserver
import threading
import time

import pytest

from dhole_mcp.server import MasterFetchServer

NEWLINE = chr(10).encode()
FAST_PAGE = (b"<html><head><title>Fast</title></head><body><article>"
             b"<h1>Fast page</h1><p>Short but real prose for the extractor.</p>"
             b"</article></body></html>")
SLOW_S = 5.0
DEADLINE_MS = 500
HITS: list[str] = []


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
        if path != "/fast":
            time.sleep(SLOW_S)
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.send_header("content-length", str(len(FAST_PAGE)))
        self.end_headers()
        self.wfile.write(FAST_PAGE)

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


async def _warm(origin):
    """把 robots.txt 与抽取器冷启动挪出帽子之外（与 R02-038 那份钉子同法）。"""
    await MasterFetchServer().web_read(url=origin + "/fast", cache_ttl=0)
    HITS.clear()


@pytest.mark.asyncio
async def test_a_fetch_still_in_flight_is_cut_at_the_deadline(origin):
    """整趟墙钟被 deadline 钉住：5s 的页在 500ms 的帽子上不许被等完。"""
    await _warm(origin)
    started = time.monotonic()
    r = await MasterFetchServer().web_crawl(
        origin + "/slow", max_pages=3, concurrency=2,
        deadline_ms=DEADLINE_MS, timeout=30_000, cache_ttl=0)
    elapsed_ms = (time.monotonic() - started) * 1000

    # 旧实现要等满 5s；新实现的上界是 500ms + 收尾宽限。2.5s 是给满载 runner 的余量，
    # 同时仍然把"等满一发 timeout"的旧形状排除在外。
    assert elapsed_ms < 2_500, (
        f"deadline_ms={DEADLINE_MS} 却跑了 {elapsed_ms:.0f}ms：在飞请求没有被放下")
    assert r.duration_ms < 2_500, r.duration_ms
    assert r.truncated_by_time, (
        f"被时钟放下的这一发要把截断说出来：{r.summary}")
    assert r.budget_overrun_ms < 2_000, (
        f"越线只剩收尾宽限，不该是一整发 timeout：{r.budget_overrun_ms}ms")
    assert r.deadline_ms_applied == DEADLINE_MS, r.deadline_ms_applied


@pytest.mark.asyncio
async def test_the_slow_page_was_actually_requested_not_skipped(origin):
    """靶站日志证明：它被放下时已经在飞，而不是压根没轮到的队列尾巴。"""
    await _warm(origin)
    await MasterFetchServer().web_crawl(
        origin + "/slow", max_pages=3, concurrency=2,
        deadline_ms=DEADLINE_MS, timeout=30_000, cache_ttl=0)
    assert "/slow" in HITS, f"起点页根本没发出去：{HITS}"


@pytest.mark.asyncio
async def test_a_run_inside_its_deadline_still_returns_every_row(origin):
    """反面锁：帽子里跑得完的那一趟，一行都不许因为这条新闸少掉。"""
    await _warm(origin)
    r = await MasterFetchServer().web_crawl(
        origin + "/fast", max_pages=1, concurrency=1,
        deadline_ms=30_000, timeout=30_000, cache_ttl=0)
    assert not r.truncated_by_time, r.summary
    assert len(r.pages) == 1, r.summary
    assert r.budget_overrun_ms == 0, r.budget_overrun_ms
