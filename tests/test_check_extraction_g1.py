"""G1 + M3：extraction_type="check" 与 schema 重抽的缓存复用。

G1 问的是「这页变了吗」：基线是 dhole 自己落的 html 缓存行，envelope 里带着源站
的校验子 —— 所以「问站方」必须是真的条件请求（If-None-Match），304 是站方亲口说的
「没变」；200 才下载正文并与基线逐字比对。本文件的 origin 是一个真的会答 304 的
loopback 服务器，正文与 etag 可在用例中途改写 —— 不 mock 抓取内核，mock 出来的
「变了/没变」没有证人价值。

M3 是同一个问题的另一面：同一页换个 schema 重抽，第二遍不该再付整页网络。第一遍
schema 抓取以 html 落缓存，第二遍同键命中。断言用的是 origin 的命中计数，而不是
响应里的标志 —— 计数不会说谎。
"""

from __future__ import annotations

import http.server
import socketserver
import threading

import pytest

from dhole_mcp import identity, params
from dhole_mcp.server import MasterFetchServer

ETAG_V1 = '"v1"'
ETAG_V2 = '"v2"'
BODY_V1 = ("<html><body><h1>Steady</h1>"
           "<p>The reading is 42 and the page is calm.</p></body></html>")
BODY_V2 = ("<html><body><h1>Steady</h1>"
           "<p>The reading is 43 — the page moved under us.</p></body></html>")


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
        srv.hits += 1  # type: ignore[attr-defined]
        if self.headers.get("if-none-match") == srv.etag:  # type: ignore[attr-defined]
            self.send_response(304)
            self.send_header("etag", srv.etag)  # type: ignore[attr-defined]
            self.end_headers()
            return
        body = srv.body.encode()  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.send_header("etag", srv.etag)  # type: ignore[attr-defined]
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def origin():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _MutableOriginHandler)
    srv.hits = 0  # type: ignore[attr-defined]
    srv.body = BODY_V1  # type: ignore[attr-defined]
    srv.etag = ETAG_V1  # type: ignore[attr-defined]
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
    """与 G23 同款：本文件的对象是 127.0.0.1 上的 fixture 服务器，SSRF 围栏为它开着。"""
    identity.set_value("allow_private", True)


class TestCheckExtractionG1:

    @pytest.mark.asyncio
    async def test_first_check_records_a_baseline_and_says_so(self, origin):
        """无基线时 changed=null —— 「还没有可比的记忆」，不许冒充「没变」。"""
        url, srv = origin
        base = srv.hits
        r = await MasterFetchServer().web_read(url, extraction_type="check")
        assert isinstance(r, dict)
        assert r["changed"] is None
        assert r["status"] == 200
        assert "baseline" in r["note"]
        assert srv.hits - base == 1, "落基线的那一次抓取真的发生了"

    @pytest.mark.asyncio
    async def test_second_check_gets_a_304_and_a_false(self, origin):
        """基线在、源站答 304：changed=false，站方亲口说的，不是 dhole 猜的。"""
        url, srv = origin
        base = srv.hits
        first = await MasterFetchServer().web_read(url, extraction_type="check")
        assert first["changed"] is None
        second = await MasterFetchServer().web_read(url, extraction_type="check")
        assert isinstance(second, dict)
        assert second["changed"] is False
        assert second["status"] == 304, "第二次问必须是条件请求换回的 304"
        assert srv.hits - base == 2, "304 那次也是一次真的回源（问站方，不是问缓存）"

    @pytest.mark.asyncio
    async def test_a_changed_page_gets_true_and_a_diff_window(self, origin):
        """正文与校验子一起换：changed=true，首个分叉给一个窗口。"""
        url, srv = origin
        base = srv.hits
        srv.body, srv.etag = BODY_V1, ETAG_V1  # type: ignore[attr-defined]
        await MasterFetchServer().web_read(url, extraction_type="check")
        srv.body = BODY_V2  # type: ignore[attr-defined]
        srv.etag = ETAG_V2  # type: ignore[attr-defined]
        r = await MasterFetchServer().web_read(url, extraction_type="check")
        assert srv.hits - base == 2
        assert r["changed"] is True
        assert r["status"] == 200
        assert r["diff_window"] in BODY_V2, "窗口里是新鲜正文，不是基线正文"
        assert r["baseline_chars"] < r["fresh_chars"]
        assert "43" in r["diff_window"]

    @pytest.mark.asyncio
    async def test_a_200_with_an_identical_body_is_still_unchanged(self, origin):
        """源站无视校验子、回 200 同一具正文：逐字比对兜底出 changed=false。"""
        url, srv = origin
        srv.body, srv.etag = BODY_V1, ETAG_V1  # type: ignore[attr-defined]
        await MasterFetchServer().web_read(url, extraction_type="check")
        srv.etag = ETAG_V2  # type: ignore[attr-defined]  # 只换 etag，正文不动
        r = await MasterFetchServer().web_read(url, extraction_type="check")
        assert r["changed"] is False
        assert r["status"] == 200
        assert "byte for byte" in r["note"]

    @pytest.mark.asyncio
    async def test_check_refuses_to_carry_actions_or_schema(self, origin):
        url, _ = origin
        r = await MasterFetchServer().web_read(
            url, extraction_type="check",
            schema={"properties": {"heading": {"selector": "h1"}}})
        assert "cannot be combined" in (r.error or "")
        assert r.next_action, "拒绝要带上怎么改"

    def test_check_is_web_read_only_on_the_wire(self):
        """'check' 只属于 web_read —— 17.0 B6-7 之后这条界线由校验器守，不由 enum 守。

        enum 曾经在 schema 上，所以"别的工具一根汗毛都不能动"是免费的；现在四颗工具
        都只声明 `"type": "string"`，界线必须有一处**读得懂它的地方**，否则 web_request
        收到 'check' 会当成一种格式去抽取。
        """
        tools = {t["name"]: t for t in MasterFetchServer._TOOL_DEFS}
        for name in ("web_read", "web_request", "web_act"):
            assert "enum" not in tools[name]["inputSchema"]["properties"]["extraction_type"], \
                f"{name} 的 extraction_type 还带着 enum"
        assert "check" in params._READ_EXTRACTION_TYPES
        assert "check" not in params._EXTRACTION_TYPES
        assert params._ENUM_ARGS["web_read"]["extraction_type"] == params._READ_EXTRACTION_TYPES
        assert params._ENUM_ARGS["web_request"]["extraction_type"] == params._EXTRACTION_TYPES
        # 行为：同一颗值在两颗工具上一收一拒，而且拒绝里印有它接受的那五颗。
        assert params._validate_tool_args(
            "web_read", {"url": "http://x", "extraction_type": "check"}
        )["extraction_type"] == "check"
        with pytest.raises(ValueError) as e:
            params._validate_tool_args(
                "web_request", {"url": "http://x", "extraction_type": "check"})
        msg = str(e.value)
        assert "web_read" in msg, "拒绝要指出 'check' 住在哪颗工具里"
        for value in params._EXTRACTION_TYPES:
            assert value in msg, f"拒本没列全：{value} 不在 {msg}"


SCHEMA_A = {"properties": {"heading": {"selector": "h1"}}}
SCHEMA_B = {"properties": {"heading": {"selector": "h1"},
                           "para": {"selector": "p"}}}


class TestSchemaCacheM3:

    @pytest.mark.asyncio
    async def test_a_second_schema_on_the_same_page_pays_no_second_fetch(self, origin):
        """同一页换 schema 重抽：第二遍吃缓存，origin 的命中计数不动。"""
        url, srv = origin
        base = srv.hits
        first = await MasterFetchServer().web_read(url, schema=SCHEMA_A, cache_ttl=600)
        assert srv.hits - base == 1
        second = await MasterFetchServer().web_read(url, schema=SCHEMA_B, cache_ttl=600)
        assert srv.hits - base == 1, "第二遍 schema 回源了 —— html 行没落或没命中"
        assert second.content and second.extracted_type == "structured"
        assert "heading" in second.content[0]
        assert "para" in second.content[0], "换的 schema 得真的生效，不是回的第一份"

    @pytest.mark.asyncio
    async def test_cache_ttl_zero_still_goes_to_the_origin(self, origin):
        url, srv = origin
        base = srv.hits
        await MasterFetchServer().web_read(url, schema=SCHEMA_A, cache_ttl=0)
        await MasterFetchServer().web_read(url, schema=SCHEMA_B, cache_ttl=0)
        assert srv.hits - base == 2, "cache_ttl=0 的语义不许被 M3 偷走"
