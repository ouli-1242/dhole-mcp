"""G1 批量变更检查：一次调用给每个 URL 一条独立判决。

单条 check 的回答是 dict，普通批读却按 ResponseModel 去读 `row.status` —— 在
`check + urls=` 这条路上，那个形状错配会让整次调用在**所有页面都已抓完**之后崩在
`successful = sum(...)`，工作做了、回执没有。这里用真的会答 304 的 loopback origin
钉住修好之后的行为：每行独立、顺序保持、一行失败不吞掉邻行的判决。
"""

from __future__ import annotations

import http.server
import json
import socketserver
import threading

import pytest

from dhole_mcp import identity
from dhole_mcp.server import MasterFetchServer


class _MultiPageOrigin(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        srv = self.server
        if self.path == "/robots.txt":
            body = b"User-agent: *\nAllow: /"
            self.send_response(200)
            self.send_header("content-type", "text/plain")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        page = srv.pages.get(self.path)  # type: ignore[attr-defined]
        if page is None:
            self.send_response(404)
            self.send_header("content-length", "0")
            self.end_headers()
            return
        srv.hits[self.path] = srv.hits.get(self.path, 0) + 1  # type: ignore[attr-defined]
        if self.headers.get("if-none-match") == page["etag"]:
            self.send_response(304)
            self.send_header("etag", page["etag"])
            self.end_headers()
            return
        body = page["body"].encode()
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.send_header("etag", page["etag"])
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture()
def origin():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _MultiPageOrigin)
    srv.pages = {  # type: ignore[attr-defined]
        "/a": {"etag": '"a1"', "body": "<html><body><p>Alpha stays put.</p></body></html>"},
        "/b": {"etag": '"b1"', "body": "<html><body><p>Beta is also calm.</p></body></html>"},
    }
    srv.hits = {}  # type: ignore[attr-defined]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield base, srv
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


def _hit(srv, path):
    return srv.hits.get(path, 0)  # type: ignore[attr-defined]


class TestCheckBatch:

    @pytest.mark.asyncio
    async def test_a_batch_records_one_baseline_per_url_and_keeps_order(self, origin):
        base, srv = origin
        urls = [f"{base}/b", f"{base}/a"]
        r = await MasterFetchServer().web_read("", urls=urls, extraction_type="check")
        assert isinstance(r, dict)
        assert r["checked"] == 2
        assert [row["url"] for row in r["results"]] == urls
        assert r["baseline_urls"] == urls, "第一次见面的两页都是落基线，不是失败"
        assert r["changed_urls"] == [] and r["unchanged_urls"] == []
        assert r["error_urls"] == []
        assert _hit(srv, "/a") == 1 and _hit(srv, "/b") == 1
        for row in r["results"]:
            assert row["changed"] is None
            assert row["status"] == 200
            assert "baseline" in row["note"]

    @pytest.mark.asyncio
    async def test_a_second_batch_gets_two_304s_and_two_false_verdicts(self, origin):
        base, srv = origin
        urls = [f"{base}/a", f"{base}/b"]
        await MasterFetchServer().web_read("", urls=urls, extraction_type="check")
        r = await MasterFetchServer().web_read("", urls=urls, extraction_type="check")
        assert r["unchanged_urls"] == urls
        assert r["changed_urls"] == []
        assert [row["status"] for row in r["results"]] == [304, 304]
        assert _hit(srv, "/a") == 2 and _hit(srv, "/b") == 2, \
            "两行都必须真的回源问一次，不能拿缓存冒充站方答复"

    @pytest.mark.asyncio
    async def test_one_moved_page_does_not_relabel_its_neighbour(self, origin):
        base, srv = origin
        urls = [f"{base}/a", f"{base}/b"]
        await MasterFetchServer().web_read("", urls=urls, extraction_type="check")
        srv.pages["/b"] = {  # type: ignore[attr-defined]
            "etag": '"b2"',
            "body": "<html><body><p>Beta moved: 43.</p></body></html>",
        }
        r = await MasterFetchServer().web_read("", urls=urls, extraction_type="check")
        assert r["changed_urls"] == [f"{base}/b"]
        assert r["unchanged_urls"] == [f"{base}/a"]
        moved = r["results"][1]
        assert moved["changed"] is True and moved["status"] == 200
        assert "43" in moved["diff_window"]

    @pytest.mark.asyncio
    async def test_a_dead_row_is_named_without_erasing_the_live_ones(self, origin):
        base, srv = origin
        urls = [f"{base}/a", f"{base}/missing", f"{base}/b"]
        await MasterFetchServer().web_read("", urls=[urls[0], urls[2]],
                                            extraction_type="check")
        r = await MasterFetchServer().web_read("", urls=urls, extraction_type="check")
        assert r["checked"] == 3
        assert r["error_urls"] == [f"{base}/missing"]
        assert r["unchanged_urls"] == [f"{base}/a", f"{base}/b"]
        dead = r["results"][1]
        assert dead["changed"] is None
        assert dead["status"] == 404
        assert "404" in (dead.get("note") or "") + (dead.get("error") or "")

    @pytest.mark.asyncio
    async def test_refused_combinations_fetch_nothing(self, origin):
        base, srv = origin
        url = f"{base}/a"
        for kwargs in (
            {"schema": {"properties": {"x": {"selector": "p"}}}},
            {"nested_selector": "a", "nested_fields": [{"name": "x", "selector": "p"}]},
            {"cdp_url": "http://127.0.0.1:9"},
        ):
            r = await MasterFetchServer().web_read(
                "", urls=[url], extraction_type="check", **kwargs)
            assert r["checked"] == 0 and r["error"], kwargs
            assert r["next_action"]
        assert not srv.hits, "拒绝发生在任何请求之前"  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_the_batch_cap_is_announced_before_the_first_request(self, origin):
        base, srv = origin
        urls = [f"{base}/a?i={i}" for i in range(11)]
        r = await MasterFetchServer().web_read("", urls=urls, extraction_type="check")
        assert r["checked"] == 0
        assert "at most 10" in r["error"]
        assert "Nothing was fetched" in r["next_action"]
        assert not srv.hits  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_the_mcp_dispatch_returns_the_same_batch_envelope(self, origin):
        base, _ = origin
        srv = MasterFetchServer()
        content, _payload = await srv._dispatch("web_read", {
            "urls": [f"{base}/a", f"{base}/b"],
            "extraction_type": "check",
        })
        data = json.loads(content[0].text)
        assert data["checked"] == 2
        assert data["baseline_urls"] == [f"{base}/a", f"{base}/b"]
        assert len(data["results"]) == 2

    @pytest.mark.asyncio
    async def test_actions_are_refused_with_the_same_dict_shape(self, origin):
        base, _ = origin
        r = await MasterFetchServer().web_read(
            "", urls=[f"{base}/a"], extraction_type="check",
            actions=[{"scroll": 1}])
        assert r["checked"] == 0
        assert "actions=" in r["error"] and r["next_action"]
