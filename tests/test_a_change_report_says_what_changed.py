"""「变了」的回执要说清「变了什么」（提升计划 3.1 · check 的 unified diff）。

`extraction_type='check'` 从前只回答"变了吗"：`changed=true` 那一回带着首个分叉的
坐标窗口（`first_divergence_at` / `diff_window`），而"每一处改了什么"要调用方自己再
抓一遍来对。现在 `changed=true` 的回执多一颗 `diff`：baseline → fresh 的 unified
diff，带 2KB 帽与 hunk 计数——超限**截断**而不是不给（前 2KB 常常已经够定位，总数
如实报）。坐标窗口照旧保留：那格答"第一处在哪里"，diff 答"每一处是什么"。

本文件用与 `test_check_extraction_g1` 同款的 loopback 真 origin（正文与 etag 可中途
改写、真答 304），不 mock 抓取内核。钉住：diff 的三件套（---/+++/@@）、增删行、
hunk 计数、2KB 截断、以及 unchanged/基线两支不带 diff。
"""

from __future__ import annotations

import http.server
import socketserver
import threading

import pytest

from dhole_mcp import identity
from dhole_mcp.server import MasterFetchServer

BODY_V1 = ("<html><body><h1>Steady</h1>\n"
           "<p>The reading is 42 and the page is calm.</p>\n"
           "<p>The footer never moves.</p></body></html>")
BODY_V2 = ("<html><body><h1>Steady</h1>\n"
           "<p>The reading is 43 — the page moved under us.</p>\n"
           "<p>The footer never moves.</p></body></html>")
BODY_BIG = ("<html><body><h1>Rewritten</h1>\n"
            + "".join(f"<p>Line {i} is brand new here.</p>\n" for i in range(400))
            + "</body></html>")


class _MutableOriginHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        srv = self.server
        if self.path.endswith("robots.txt"):
            self.send_response(200)
            self.send_header("content-type", "text/plain")
            self.send_header("content-length", len(b"User-agent: *\nAllow: /"))
            self.end_headers()
            self.wfile.write(b"User-agent: *\nAllow: /")
            return
        if self.headers.get("if-none-match") == srv.etag:
            self.send_response(304)
            self.send_header("etag", srv.etag)
            self.end_headers()
            return
        body = srv.body.encode()
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.send_header("etag", srv.etag)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def origin():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _MutableOriginHandler)
    srv.body = BODY_V1
    srv.etag = '"v1"'
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/page", srv
    srv.shutdown()


@pytest.fixture(autouse=True)
def _throwaway_cache(tmp_path, monkeypatch):
    from dhole_mcp import cache as cache_mod
    monkeypatch.setattr(cache_mod, "_CACHE_DIR", tmp_path)
    cache_mod._db_initialized.clear()
    yield
    cache_mod._db_initialized.clear()


@pytest.fixture(autouse=True)
def _the_test_origin_is_fetchable():
    identity.set_value("allow_private", True)


# ─── 1 · 帮手本身 ──────────────────────────────────────────────────────

def test_a_small_change_is_a_proper_unified_diff():
    out = MasterFetchServer._unified_diff_capped(BODY_V1, BODY_V2)
    assert out["truncated"] is False
    assert out["hunks"] == 1
    assert out["text"].startswith("--- baseline\n+++ fresh\n@@"), out["text"][:80]
    assert "-<p>The reading is 42 and the page is calm.</p>" in out["text"]
    assert "+<p>The reading is 43 — the page moved under us.</p>" in out["text"]


def test_a_big_change_is_capped_at_the_line_boundary_and_counts_hunks():
    out = MasterFetchServer._unified_diff_capped(BODY_V1, BODY_BIG)
    assert out["truncated"] is True
    assert len(out["text"]) <= MasterFetchServer._CHECK_DIFF_CAP
    assert out["hunks"] >= 1
    assert "\n+" in out["text"] or out["text"].endswith("+")


# ─── 2 · 整条 check 链 ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_changed_page_carries_the_diff(origin):
    url, srv = origin
    srv.body, srv.etag = BODY_V1, '"v1"'
    await MasterFetchServer().web_read(url, extraction_type="check")
    srv.body, srv.etag = BODY_V2, '"v2"'
    r = await MasterFetchServer().web_read(url, extraction_type="check")
    assert r["changed"] is True
    assert r["diff"].startswith("--- baseline\n+++ fresh\n@@"), r["diff"][:60]
    assert "-<p>The reading is 42" in r["diff"]
    assert "+<p>The reading is 43" in r["diff"]
    assert r["diff_hunks"] == 1
    assert r["diff_truncated"] is False
    # 坐标窗口照旧在：diff 与窗口答的是两问
    assert "43" in r["diff_window"]


@pytest.mark.asyncio
async def test_a_huge_change_truncates_and_says_how_many_hunks(origin):
    url, srv = origin
    srv.body, srv.etag = BODY_V1, '"v1"'
    await MasterFetchServer().web_read(url, extraction_type="check")
    srv.body, srv.etag = BODY_BIG, '"big"'
    r = await MasterFetchServer().web_read(url, extraction_type="check")
    assert r["changed"] is True
    assert r["diff_truncated"] is True
    assert len(r["diff"]) <= MasterFetchServer._CHECK_DIFF_CAP
    assert r["diff_hunks"] >= 1
    assert "hunk(s)" in r["note"]


@pytest.mark.asyncio
async def test_unchanged_and_baseline_rows_do_not_carry_a_diff(origin):
    url, srv = origin
    srv.body, srv.etag = BODY_V1, '"v1"'
    first = await MasterFetchServer().web_read(url, extraction_type="check")
    assert first["changed"] is None and "diff" not in first
    second = await MasterFetchServer().web_read(url, extraction_type="check")
    assert second["changed"] is False and "diff" not in second


# ─── 3 · 无基线那一句：成功才说「已记下」 ────────────────────────────────

@pytest.mark.asyncio
async def test_a_successful_first_check_says_the_baseline_was_recorded(origin):
    """正面锁：抓到了 2xx 才说 fetched and recorded one。"""
    url, srv = origin
    srv.body, srv.etag = BODY_V1, '"v1"'
    r = await MasterFetchServer().web_read(url, extraction_type="check")
    assert r["changed"] is None
    assert r["status"] == 200
    assert "fetched and recorded one" in r["note"], r["note"]


@pytest.mark.asyncio
async def test_a_failed_first_fetch_does_not_claim_a_baseline_was_recorded():
    """失败的那一回不许说「已记下」——失败的抓取不落缓存，什么都没记。

    现场（2026-10-24 活体剧本）：盯一个当时不可达的站（两档全败、status 0），
    回执却带着 "this call fetched and recorded one"。单 URL 形状与批量回执那层
    （它把失败行分得清清楚楚）各说各话，是"看起来成功"的那一类。
    9 端口上没有东西在听：loopback 的 connection refused，两档都快败。
    """
    dead = "http://127.0.0.1:9/page"
    r = await MasterFetchServer().web_read(dead, extraction_type="check",
                                           timeout=8000)
    assert r["changed"] is None
    assert r.get("error") or int(r.get("status") or 0) == 0
    assert "recorded one" not in r["note"], r["note"]
    assert "nothing was recorded" in r["note"], r["note"]
