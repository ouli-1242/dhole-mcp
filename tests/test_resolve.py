"""A2：web_resolve 下线 —— 门换了，能力各有去处。

评审复核后的裁决：两个主用例都能被同次数的既有调用覆盖——
* 短链落点：web_read 的 HTTP 层走同一条会跟随重定向的 GET，落点回报在
  metadata（meta_refresh 跳数同源）；HEAD 对 301 不跟随，替不了这一半。
* 验活：web_request(method='HEAD') 同为一次调用。
它自己 docstring 教的「逐条 pre-screen 搜索结果」恰是 nested 注释点名要消灭的
N 次反模式；连 allow_private scope 都不读，连"web_read 的私有子集"都不成立。

这个文件因此只守两件事：旧名字的派发拒答要教到新的家；教出来的那条路
（web_read 跟随重定向）在靶站上是真成立的。
"""

from __future__ import annotations

import asyncio
import http.server
import socketserver
import threading

import pytest

from dhole_mcp.server import MasterFetchServer


class TestTheRemovedToolAnswersWithDirections:

    def test_the_old_name_points_at_the_new_homes(self):
        with pytest.raises(ValueError) as e:
            asyncio.run(MasterFetchServer()._dispatch(
                "web_resolve", {"url": "https://example.com/"}))
        msg = str(e.value)
        assert "Unknown tool" in msg
        assert "web_read" in msg, "拒答要教到跟随重定向的那扇门"
        assert "web_request" in msg, "验活的那一半要教到 HEAD"

    def test_the_old_wire_shows_no_such_property(self):
        defs = {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}
        assert "web_resolve" not in defs
        assert "web_screenshot" not in defs


class _RedirectHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/to":
            body = b"<html><body>landed</body></html>"
            self.send_response(200)
            self.send_header("content-type", "text/html")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            # 302 → /to：短链服务器的最小形态。
            self.send_response(302)
            self.send_header("location", "/to")
            self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def redirect_origin():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _RedirectHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/short"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _the_test_origin_is_fetchable():
    from dhole_mcp import identity
    identity.set_value("allow_private", True)


class TestTheReplacementPathReallyWorks:

    def test_web_read_follows_the_redirect_and_lands(self, redirect_origin,
                                                     tmp_path, monkeypatch):
        """评审实测的核心事实钉住：web_read 单发一次即落 /to —— 替代路径
        不是"理论可行"，是现在也成立的行为。"""
        from dhole_mcp import cache as cache_mod
        monkeypatch.setattr(cache_mod, "_CACHE_DIR", tmp_path)
        cache_mod._db_initialized.clear()

        r = asyncio.run(MasterFetchServer().web_read(redirect_origin, cache_ttl=0))
        assert r.status == 200
        assert "landed" in "".join(r.content), "重定向要真的跟过去"
        cache_mod._db_initialized.clear()
