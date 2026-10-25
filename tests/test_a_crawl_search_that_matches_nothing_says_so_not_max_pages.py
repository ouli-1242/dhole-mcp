"""R01-002：search 过滤清空队列时，summary 要说「没匹配」，不是「爬满了」。

现场（2026-10-25 · P02/P05 · crawl search='详情'/'卡片' · review/round-01）：
`web_crawl(url, search=<正文才有的词>)` → summary「crawled 0 page(s) … stopped:
max_pages reached」。事实是**过滤后队列为空**（页面爬回来了、词不在 URL/标题
里），「max_pages reached」读起来像"爬满了所以停"——没货和爬满共用一句措辞，
调用方跑去抬 max_pages，白跑一遍同样的空结果。

根因：`search` 过滤跑在 summary 组装**之后**；清空队列时 summary 里躺着的还是
过滤前的停因位（页帽/时限都是真的，但接在「crawled 0 page(s)」后面就变成谎话
的形状）。next_action 那句「matched none of the crawled pages. It matches URL
path + title only」本来就是对的——summary 跟上它。

契约（本文件钉住）：
  * search 清空后 summary 不得再带「stopped: max_pages reached」；
  * 清空后的 summary 要说得出「爬了几页、0 页带词」（数字对得上账）；
  * search 有命中时 summary 照旧（filtered to N）。
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dhole_mcp import identity
from dhole_mcp.server import MasterFetchServer

ROOT = "http://127.0.0.1:0"

_PAGE = ("<!doctype html><html><head><title>{title}</title></head><body>"
         "<h1>{title}</h1><p>{body}</p>"
         "<a href='/work/1'>Item One</a> <a href='/work/2'>Item Two</a>"
         "</body></html>")
_ROUTES = {
    "/": ("Home", "Start page."),
    "/work/1": ("Item One", "Detail card content lives in the body only."),
    "/work/2": ("Item Two", "More card content in the body."),
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in _ROUTES:
            title, body = _ROUTES[path]
            data = _PAGE.format(title=title, body=body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass


@pytest.fixture(autouse=True)
def _local_target():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    global ROOT
    ROOT = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
def _the_test_origin_is_fetchable():
    identity.set_value("allow_private", True)


@pytest.mark.asyncio
async def test_an_emptied_search_does_not_borrow_the_max_pages_sentence():
    r = await MasterFetchServer().web_crawl(url=ROOT, search="详情", max_pages=5,
                                            cache_ttl=0)
    assert not r.pages, "fixture 的词只长在正文里，URL/标题过滤必须一个不剩"
    assert "max_pages reached" not in r.summary, r.summary


@pytest.mark.asyncio
async def test_the_emptied_search_summary_adds_up():
    r = await MasterFetchServer().web_crawl(url=ROOT, search="详情", max_pages=5,
                                            cache_ttl=0)
    summary = r.summary
    assert "search='详情'" in summary, summary
    # 账要能对：起始页 + 两个子页都爬回来了（3 页），0 页带词
    assert "matched 0 of 3 crawled page(s)" in summary, summary


@pytest.mark.asyncio
async def test_a_matching_search_keeps_its_filtered_to_sentence():
    r = await MasterFetchServer().web_crawl(url=ROOT, search="item", max_pages=5,
                                            cache_ttl=0)
    assert r.pages, "Item One/Two 的标题里就有 item，这一发必须命中"
    assert "filtered to" in r.summary, r.summary
