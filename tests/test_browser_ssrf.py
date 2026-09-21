"""浏览器层 SSRF 守卫：入口 URL 之外的每一跳都不走 validate_url，这里补一道。

威胁（此前 README 只写了"盲打式内网可达面"，实测下来不止）：入口 URL 过了
validate_url，但浏览器内部的重定向 / 子资源 / 页面 JS 发的 fetch 都不过 —— 一个被
Cloudflare 墙或 JS 壳逼着升级到浏览器的页面，只要把浏览器引到
`http://169.254.169.254/...`（云元数据）或 `http://127.0.0.1:9222/`（本机服务），
渲染出来的正文就会被抓回给 agent。那不是盲打，那是**把内网页面读出来**。

守在哪：`StealthyBrowser.fetch()` 给每个 page 装 route handler ——
- 请求前判定：http(s) 且目标解析到内网 → abort（重定向、子资源、页面 JS 的 fetch 全覆盖）
- 落地后判定：主文档被拦 / 落地 URL 是内网 → 抛 BrowserSSRFBlockedError，不交正文、不重试
- 豁免：**同一个入口站点**（入口已过 validate_url；同站点 http↔https 默认端口升级也算）。
  异端口必须拦：`localhost:8080` 页面把浏览器引向 `localhost:9222` 正是典型内网跳板。

本文件上半是纯函数 + 假 route 对象（默认离线运行）；下半用**真浏览器 + 真 HTTP 服务**
证明请求根本没发出去（判红的依据是靶场自己记的命中数，只断言"报错了"不算数），
标了 e2e，`-m e2e` 才跑。
"""

import asyncio
from typing import Any, Optional

import pytest

from dhole_mcp.browser import (
    _create_route_handler,
    _is_entry_target,
    _landed_url_refused,
)

ENTRY = "https://example.com/article"


# ─── 假 route / request（只喂 handler 真正用到的字段）────────────────────────

class _FakeRequest:
    def __init__(self, url: str, resource_type: str = "document",
                 navigation: bool = True, frame: Any = "main"):
        self.url = url
        self.resource_type = resource_type
        self._navigation = navigation
        self.frame = frame

    def is_navigation_request(self) -> bool:
        return self._navigation


class _FakeRoute:
    def __init__(self, url: str, resource_type: str = "document",
                 navigation: bool = True, frame: Any = "main"):
        self.request = _FakeRequest(url, resource_type, navigation, frame)
        self.action = ""
        self.aborted = False

    async def abort(self) -> None:
        self.action = "abort"
        self.aborted = True

    async def continue_(self) -> None:
        self.action = "continue"


def _run(handler, route: _FakeRoute) -> _FakeRoute:
    asyncio.run(handler(route))
    return route


class TestEntryTargetExemption:
    """入口站点自己豁免（含 http↔https 升级），异主机/异端口不豁免。"""

    def test_same_host_same_port_is_entry(self):
        assert _is_entry_target("https://example.com/other", ENTRY)

    def test_http_to_https_upgrade_is_entry(self):
        assert _is_entry_target("http://example.com/x", "http://example.com/x")
        assert _is_entry_target("https://example.com/x", "http://example.com/x")

    def test_same_host_other_port_is_not_entry(self):
        # localhost:8080 -> localhost:9222 是内网跳板的经典形态
        assert not _is_entry_target("http://localhost:9222/json", "http://localhost:8080/")
        assert not _is_entry_target("https://example.com:8443/x", ENTRY)

    def test_other_host_is_not_entry(self):
        assert not _is_entry_target("https://evil.test/x", ENTRY)


class TestRequestGuard:
    """请求前判定：内网目标 abort，正常目标 continue。"""

    def _handler(self, entry: str = ENTRY, watch: Optional[list] = None):
        return _create_route_handler(False, None, watch if watch is not None else [],
                                     entry_url=entry)

    def test_loopback_target_is_aborted_and_recorded(self):
        watch: list = []
        route = _run(self._handler(watch=watch), _FakeRoute("http://127.0.0.1:9222/json"))
        assert route.aborted, "回环目标必须被拦"
        assert watch and watch[0][0] == "127.0.0.1:9222" and watch[0][1] is True

    def test_private_and_metadata_and_variants_are_aborted(self):
        for u in ("http://169.254.169.254/latest/meta-data/",
                  "http://10.1.2.3/admin",
                  "http://192.168.1.1/",
                  "http://[::1]:8080/x",
                  "http://0177.0.0.1/",          # 八进制变体
                  "http://2130706433/",          # 十进制变体
                  "http://localhost/admin"):
            route = _run(self._handler(), _FakeRoute(u))
            assert route.aborted, f"{u} 必须被拦"

    def test_public_host_continues(self):
        route = _run(self._handler(),
                     _FakeRoute("https://example.org/asset.js",
                                resource_type="script", navigation=False))
        assert route.action == "continue", "公网目标被误杀"

    def test_entry_host_continues_even_if_internal(self):
        """入口站点豁免：本地/内网入口（hosts 钉位、开发环境）不该被自家守卫拒掉。"""
        route = _run(self._handler(entry="http://127.0.0.1:8080/page"),
                     _FakeRoute("http://127.0.0.1:8080/next"))
        assert not route.aborted, "同站点请求被误杀"

    def test_non_network_schemes_are_not_touched(self):
        """data:/blob: 不是网络请求 —— 拦它们只会弄坏页面（CF 挑战页自己就用）。"""
        for u in ("data:text/html,<b>x</b>", "blob:https://example.com/uuid", "about:blank"):
            route = _run(self._handler(),
                         _FakeRoute(u, resource_type="image", navigation=False))
            assert not route.aborted, f"{u} 不该被 SSRF 判定拦下"

    def test_disabled_resource_types_still_aborted(self):
        """资源拦截行为不变（守卫是叠加，不是替换）。"""
        handler = _create_route_handler(True, None, [], entry_url=ENTRY)
        route = _run(handler, _FakeRoute("https://cdn.example.org/a.png",
                                        resource_type="image", navigation=False))
        assert route.aborted

    def test_subresource_block_is_recorded_as_non_main(self):
        watch: list = []
        _run(self._handler(watch=watch),
             _FakeRoute("http://127.0.0.1:9/x", resource_type="xhr", navigation=False))
        assert watch == [("127.0.0.1:9", False)], "子资源不该被记成主文档（判罚口径不同）"


class TestLandedUrlRefused:
    """落地判定：内网落地要拒，入口站点落地要放。"""

    def test_internal_landing_is_refused(self):
        assert _landed_url_refused("http://127.0.0.1:9222/json", ENTRY)
        assert _landed_url_refused("http://169.254.169.254/", ENTRY)

    def test_entry_landing_is_allowed(self):
        assert not _landed_url_refused("http://127.0.0.1:8080/done", "http://127.0.0.1:8080/x")

    def test_public_landing_is_allowed(self):
        assert not _landed_url_refused("https://example.org/final", ENTRY)

    def test_non_http_landing_is_not_judged(self):
        assert not _landed_url_refused("about:blank", ENTRY)


# ─── 真浏览器 + 真 HTTP 服务的端到端证明（-m e2e）────────────────────────────

def _serve(handler_cls):
    import socketserver
    import threading

    class _S(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    srv = _S(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


@pytest.mark.e2e
class TestBrowserSSRFEndToEnd:
    """判红依据：内网服务器**一次都没被连上**（只断言"报错了"不算数）。"""

    @pytest.fixture
    def range_(self):
        import http.server

        hits: list = []

        class Inner(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                hits.append(self.path)
                body = b"<html><body>INTERNAL_SECRET_DATA</body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        inner_srv, inner_port = _serve(Inner)
        target = f"http://127.0.0.1:{inner_port}/internal-secret"

        class Outer(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == "/js":
                    html = (f"<html><body><script>location.href='{target}';</script>"
                            "<p>redirecting</p></body></html>")
                elif self.path == "/302":
                    self.send_response(302)
                    self.send_header("Location", target)
                    self.end_headers()
                    return
                elif self.path == "/fetch":
                    html = (f"<html><body><script>fetch('{target}').then(r=>r.text())"
                            ".catch(()=>{});</script><p>fetching</p></body></html>")
                elif self.path == "/iframe":
                    html = (f'<html><body><iframe src="{target}"></iframe>'
                            "<p>framing</p></body></html>")
                else:
                    html = f'<html><body><a href="{target}">x</a>PLAIN_OK</body></html>'
                body = html.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        outer_srv, outer_port = _serve(Outer)
        yield inner_port, outer_port, hits
        outer_srv.shutdown()
        inner_srv.shutdown()

    @staticmethod
    def _fetch(entry: str):
        from dhole_mcp.browser import StealthyBrowser

        async def _go():
            async with StealthyBrowser(headless=True, timeout=25000) as b:
                return await b.fetch(entry)

        return asyncio.run(_go())

    def test_server_side_redirect_is_refused_and_content_never_returned(self, range_):
        """HTTP 3xx：请求会发出去（Playwright 不拦截 route 已放行请求的重定向目标），
        但**内容一个字都不回流**。

        实测（2026-09-21，本机 patchright）：route handler 只看到入口那一次 document
        请求，302 的目标根本不经过它 —— 这是 Playwright 的拦截模型，不是我们漏判。
        要连"盲打请求"一起堵掉得在解析器/代理层做（`--host-resolver-rules` 或本地过滤
        代理），那两条都与 Cloudflare 求解这条唯一能力面冲突且无法在此验证，故不做；
        这里钉住底线：内容不回流 + 明确报错。
        """
        inner_port, outer_port, hits = range_
        from dhole_mcp.browser import BrowserSSRFBlockedError

        with pytest.raises(BrowserSSRFBlockedError) as ei:
            self._fetch(f"http://127.0.0.1:{outer_port}/302")
        assert "ssrf_blocked" in str(ei.value)
        assert str(inner_port) in str(ei.value), "错误里要指出被拒的内网目标"
        # 不断言 hits == []：这里它必然非空（见 docstring）。断言的是内容没回流 ——
        # 报错路径不会带回任何正文（调用方只拿到错误文本）。
        assert "INTERNAL_SECRET_DATA" not in str(ei.value)

    def test_js_redirect_to_internal_is_blocked_before_it_leaves(self, range_):
        inner_port, outer_port, hits = range_
        from dhole_mcp.browser import BrowserSSRFBlockedError

        with pytest.raises(BrowserSSRFBlockedError):
            self._fetch(f"http://127.0.0.1:{outer_port}/js")
        assert hits == [], f"内网服务器被连上了: {hits}"

    def test_page_script_fetch_to_internal_is_blocked(self, range_):
        """子资源路径：抓取照常完成，但内网请求一条都没出去，正文里也没有内网内容。"""
        inner_port, outer_port, hits = range_
        resp = self._fetch(f"http://127.0.0.1:{outer_port}/fetch")
        assert hits == [], f"页面 JS 的内网请求没被拦下: {hits}"
        assert b"INTERNAL_SECRET_DATA" not in resp.body

    def test_iframe_to_internal_is_blocked(self, range_):
        """iframe 的内嵌页面是最典型的内网读取姿势（同源策略帮不了跨源读取之外的忙）。"""
        inner_port, outer_port, hits = range_
        resp = self._fetch(f"http://127.0.0.1:{outer_port}/iframe")
        assert hits == [], f"iframe 的内网请求没被拦下: {hits}"
        assert b"INTERNAL_SECRET_DATA" not in resp.body

    def test_benign_local_entry_still_works(self, range_):
        """正对照：入口站点自己不被误杀（守卫不能把正常抓取一起挡掉）。"""
        inner_port, outer_port, _hits = range_
        resp = self._fetch(f"http://127.0.0.1:{outer_port}/plain")
        assert resp.status == 200 and b"PLAIN_OK" in resp.body
