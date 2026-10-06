"""页内样板折叠死在主路 —— trafilatura 成功时承诺的 duplicate_lines_collapsed 恒缺席。

症状（2026-10-06 实测，loopback fixture，web_read 与 doc_parse 输出逐字节一致）：

    doc_parse len: 339  web_read len: 339  identical: True
    web_read 的 duplicate_lines_collapsed: 0   （直调折叠函数：collapsed = 2）

守卫钉住了函数没钉住路径：test_the_page_boilerplate_is_folded.py 的链路测试直接
调 extractor.extract_content（唯一带折叠的入口），而线上主路 server.py 调
extract_with_trafilatura（全文无 collapse 调用），只有空结果回退才经过折叠。
17.0 承诺的压缩能力在主路上从不执行。

判据：loopback 夹具（页脚行 ×3，均 ≥24 字符）走 web_read 全链路：正文里该行
只出现 1 次、信封 duplicate_lines_collapsed ≥ 1；无重复的页照常 0/缺席——
折叠不是无条件改写；test_the_page_boilerplate_is_folded.py 的既有用例保持绿
（套的是路径，折叠规则一个字符不动）。
"""

from __future__ import annotations

import http.server
import socketserver
import threading

import pytest

from dhole_mcp.context import _BOILERPLATE_LINES_COLLAPSED
from dhole_mcp.server import MasterFetchServer

FOOTER = "Copyright 2026 Example Retail Company All Rights Reserved Notice"

BOILERPLATE_PAGE = f"""<html><head><title>Shop page</title></head><body>
<article>
<h1>Shop page</h1>
<p>Real paragraph one about the product range and seasonal offers.</p>
<p>{FOOTER}</p>
<p>Real paragraph two about shipping times and warranty terms.</p>
<p>{FOOTER}</p>
<p>Real paragraph three about payment methods and gift cards.</p>
<p>{FOOTER}</p>
</article>
</body></html>"""

CLEAN_PAGE = """<html><head><title>Clean page</title></head><body>
<h1>Clean page</h1>
<p>Only paragraph one, and it never says anything twice.</p>
<p>Only paragraph two, which is also unlike every other line here.</p>
</body></html>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.endswith("robots.txt"):
            self.send_response(200)
            self.send_header("content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"User-agent: *\nAllow: /")
            return
        body = (BOILERPLATE_PAGE if "boiler" in self.path else CLEAN_PAGE).encode()
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def origin():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
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
    from dhole_mcp import identity
    identity.set_value("allow_private", True)


@pytest.fixture(autouse=True)
def _reset_fold_count():
    token = _BOILERPLATE_LINES_COLLAPSED.set(0)
    yield
    _BOILERPLATE_LINES_COLLAPSED.reset(token)


@pytest.mark.asyncio
async def test_a_live_fetch_folds_the_boilerplate_it_reports(origin):
    """主路（trafilatura 成功）也要过同一道折叠，并把条数报在信封上。"""
    r = await MasterFetchServer().web_read(origin + "/boiler")
    assert r.status == 200 and r.content_ok, r.error
    body = "\n".join(r.content)
    assert "Real paragraph one" in body, "trafilatura 主路应真跑起来（正文在）"
    assert body.count(FOOTER) <= 1, f"页脚样板行应被折叠，实际出现 {body.count(FOOTER)} 次"
    assert r.duplicate_lines_collapsed >= 1, "信封必须报出删了几行——静默删比留着更糟"


@pytest.mark.asyncio
async def test_a_page_without_repeats_reports_zero(origin):
    """无重复的页照常 0/缺席——折叠不是无条件改写。"""
    r = await MasterFetchServer().web_read(origin + "/clean")
    assert r.status == 200 and r.content_ok, r.error
    assert r.duplicate_lines_collapsed == 0
