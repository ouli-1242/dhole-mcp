"""G29 回归：`<meta http-equiv=refresh>` 是写在正文里的重定向。

实测（16.0，本地 loopback 服务器 + 两层取回）看到的分裂行为是这一项的全部理由：

| 页面 | HTTP 层 | 浏览器层 |
|---|---|---|
| ``content="0;url=/to"`` | 留在源页，page_type=redirect | **静默**跳到 /to |
| ``content="5;url=/to"`` | 留在源页 | 也留在源页（截图早于刷新） |
| ``http-equiv=REFRESH``（属性值无引号） | 连「这是跳转页」都没判出来 | 跳到 /to |
| 目标是站外 | 留在源页 | 跟到站外 |

同一个 URL 在两层拿到不同正文、且没人说明，比「不跟随」更糟。现在 HTTP 层把刷新
当成**同一跳预算里的一跳**：目标照旧过 validate_url，凭据照旧不跨源，跳数与来源
记在 ``Response.meta_refresh`` 里。浏览器层的 0 秒刷新本来就是跟的，两条路径因此
对齐；带延迟的那类浏览器跟不跟取决于时序，这一点写进文档，不假装已解决。
"""

from __future__ import annotations

import http.server
import socketserver
import threading

import pytest

from dhole_mcp.fetcher import (
    HTTPSession,
    Response,
    _decode_html_bytes,
    _meta_refresh_target,
)
from dhole_mcp.security import SecurityError


class _FakeResp:
    """Just enough of a primp response for the parser."""

    def __init__(self, html: str, content_type: str = "text/html; charset=utf-8"):
        self.headers = {"content-type": content_type}
        self.content = html.encode("utf-8")


def _target(html: str, base: str = "https://a.example/x", **kw) -> tuple[str, float]:
    return _meta_refresh_target(_FakeResp(html, **kw), base)


class TestTheParser:
    """四种真实写法都要认出来：测量里那页用的就是没引号的属性值。"""

    def test_quoted_standard_form(self):
        url, delay = _target('<html><head><meta http-equiv="refresh" '
                             'content="0;url=https://b.example/to"></head></html>')
        assert (url, delay) == ("https://b.example/to", 0.0)

    def test_unquoted_attribute_value(self):
        url, _ = _target('<html><head><meta http-equiv=REFRESH '
                         'content="3; URL=./to"></head></html>')
        assert url == "https://a.example/to"

    def test_single_quotes_and_spaces(self):
        url, delay = _target("<html><head><meta http-equiv='refresh' "
                             "content=' 2 ; url = /to '></head></html>")
        assert url == "https://a.example/to"
        assert delay == 2.0

    def test_relative_target_resolves_against_the_current_page(self):
        url, _ = _target('<meta http-equiv="refresh" content="0;url=next/page">',
                         base="https://a.example/docs/index")
        assert url == "https://a.example/docs/next/page"

    def test_self_refresh_is_not_a_redirect(self):
        """0;url=<自己> 是「重新加载」，跟它会白烧一跳，而且容易自己转圈。"""
        url, _ = _target('<meta http-equiv="refresh" content="0;url=/x">',
                         base="https://a.example/x")
        assert url == ""
        url, _ = _target('<meta http-equiv="refresh" content="0;url=/x#frag">',
                         base="https://a.example/x")
        assert url == ""

    def test_javascript_and_blank_targets_are_refused(self):
        for href in ("javascript:alert(1)", "about:blank", "data:text/html,x"):
            url, _ = _target(f'<meta http-equiv="refresh" content="0;url={href}">')
            assert url == "", href

    def test_non_html_body_is_not_parsed(self):
        html = '<meta http-equiv="refresh" content="0;url=https://b.example/to">'
        assert _target(html, content_type="application/json")[0] == ""

    def test_a_page_without_the_tag_returns_nothing(self):
        assert _target("<html><body><p>plain</p></body></html>")[0] == ""

    def test_first_refresh_wins(self):
        url, _ = _target('<meta http-equiv="refresh" content="0;url=/one">'
                         '<meta http-equiv="refresh" content="0;url=/two">')
        assert url == "https://a.example/one"

    def test_garbage_does_not_raise(self):
        for html in ('<meta http-equiv="refresh" content="url">',
                     '<meta http-equiv="refresh" content="">',
                     '<meta http-equiv="refresh">',
                     '<meta content="0;url=/x">',
                     '<meta http-equiv="refresh" content="abc;url=/x">'):
            assert isinstance(_target(html), tuple), html


class TestPageTypeStillSeesThePointer:

    def test_an_unquoted_refresh_tag_is_classified_as_a_redirect(self):
        """不跟随时（HEAD / follow_redirects=false）分类还得说得出「这是指针」。"""
        from dhole_mcp.envelope import detect_page_type
        html = ('<html><head><meta http-equiv=REFRESH content="3; URL=./to">'
                '<title>wait</title></head><body><p>' + "x" * 400 + "</p></body></html>")
        assert detect_page_type(html, "https://a.example/from", "text/html", 400) == "redirect"


class _HopClient:
    """A primp-shaped client whose whole chain of responses is supplied here."""

    def __init__(self, pages: dict[str, tuple[int, str]]):
        self.pages = pages
        self.seen: list[tuple[str, dict]] = []

    def request(self, method, url, **kw):
        self.seen.append((url, dict(kw.get("headers") or {})))
        status, html = self.pages.get(url, (404, "<html>gone</html>"))
        return _FakeHopResponse(url, status, html)


class _FakeHopResponse:
    def __init__(self, url: str, status: int, html: str):
        self.url = url
        self.status_code = status
        self.content = html.encode()
        self.headers = {"content-type": "text/html; charset=utf-8"}
        self.cookies = {}
        self.reason = ""

    @property
    def text(self) -> str:
        return self.content.decode()


async def _get(pages: dict, url: str, **get_kwargs):
    """Run one HTTPSession.get against a scripted hop chain; return (response, client)."""
    client = _HopClient(pages)
    session = HTTPSession()
    await session._init_client()
    session._client = client
    resp = await session.get(url, **get_kwargs)
    return resp, client


@pytest.fixture
def hop_pages():
    return {
        "https://a.example/from": (200, '<html><head><meta http-equiv="refresh" '
                                         'content="0;url=https://a.example/to">'
                                         "</head><body>pointer</body></html>"),
        "https://a.example/to": (200, "<html><body>destination</body></html>"),
        "https://a.example/off": (
            200, '<html><head><meta http-equiv="refresh" '
                 'content="0;url=https://off.example/to"></head></html>'),
        "https://off.example/to": (200, "<html><body>offsite destination</body></html>"),
    }


class TestTheHop:
    """跳要跳，但按 3xx 的规矩跳。"""

    @pytest.mark.asyncio
    async def test_a_refresh_page_is_walked_to_its_target(self, hop_pages):
        resp, client = await _get(hop_pages, "https://a.example/from")

        assert resp.url == "https://a.example/to"
        assert b"destination" in resp.body
        assert resp.meta_refresh == {"from": "https://a.example/from",
                                     "delay_s": 0.0, "hops": 1}
        assert [u for u, _ in client.seen] == ["https://a.example/from",
                                               "https://a.example/to"]

    @pytest.mark.asyncio
    async def test_credentials_do_not_ride_a_cross_origin_meta_hop(self, hop_pages):
        """刷新页指向别的主机：Cookie / Authorization 不能跟着过去。"""
        resp, client = await _get(hop_pages, "https://a.example/off",
                                  headers={"authorization": "Bearer secret",
                                           "cookie": "session=abc"})

        assert resp.url == "https://off.example/to"
        assert len(client.seen) == 2
        first, second = client.seen
        assert first[1].get("authorization") == "Bearer secret"
        assert "authorization" not in {k.lower() for k in second[1]}, second[1]
        assert "cookie" not in {k.lower() for k in second[1]}, second[1]

    @pytest.mark.asyncio
    async def test_same_origin_meta_hop_keeps_the_credentials(self, hop_pages):
        resp, client = await _get(hop_pages, "https://a.example/from",
                                  headers={"authorization": "Bearer secret"})
        assert resp.url == "https://a.example/to"
        assert client.seen[1][1].get("authorization") == "Bearer secret"

    @pytest.mark.asyncio
    async def test_head_does_not_follow(self, hop_pages):
        """HEAD 没有正文可读，跟下去就变成一次「说好不下载」的下载。"""
        resp, _ = await _get(hop_pages, "https://a.example/from", method="HEAD")
        assert resp.url == "https://a.example/from"
        assert resp.meta_refresh is None

    @pytest.mark.asyncio
    async def test_follow_redirects_false_does_not_follow(self, hop_pages):
        resp, _ = await _get(hop_pages, "https://a.example/from",
                             follow_redirects=False)
        assert resp.url == "https://a.example/from"
        assert resp.meta_refresh is None

    @pytest.mark.asyncio
    async def test_a_post_is_not_replayed_at_the_target(self, hop_pages):
        """表单 POST 之后跟着一个刷新页：把 body 寄到第二个地址上是错的。"""
        resp, _ = await _get(hop_pages, "https://a.example/from",
                             method="POST", body=b"field=1",
                             content_type="application/x-www-form-urlencoded")
        assert resp.url == "https://a.example/from"
        assert resp.meta_refresh is None

    @pytest.mark.asyncio
    async def test_a_meta_loop_shares_the_redirect_budget(self):
        pages = {
            "https://a.example/a": (200, '<html><head><meta http-equiv="refresh" '
                                          'content="0;url=https://a.example/b"></head></html>'),
            "https://a.example/b": (200, '<html><head><meta http-equiv="refresh" '
                                          'content="0;url=https://a.example/a"></head></html>'),
        }
        resp, client = await _get(pages, "https://a.example/a", max_redirects=3)
        assert len(client.seen) <= 4  # budget + 1，不是无限
        assert resp.meta_refresh["hops"] <= 4
        # 预算用尽时报的是「最后一次真正答话的地址」，不是正准备去的那一跳。
        assert resp.url == client.seen[-1][0]

    @pytest.mark.asyncio
    async def test_a_private_target_is_rejected_before_dialing(self):
        pages = {
            "https://a.example/loop": (
                200, '<html><head><meta http-equiv="refresh" '
                     'content="0;url=http://127.0.0.1:9/secret"></head></html>'),
        }
        client = _HopClient(pages)
        session = HTTPSession()
        await session._init_client()
        session._client = client
        with pytest.raises(SecurityError):
            await session.get("https://a.example/loop")
        # 一次都不许多：安全校验的「拒绝」不会因为再问一遍而改变，重试等于又被
        # 拒绝了才落回 origin —— 白敲一次服务器。
        assert [u for u, _ in client.seen] == ["https://a.example/loop"]


class TestTheRealServer:
    """端到端：真 socket、真 primp 客户端，只把内网放行当测试脚手架。"""

    @staticmethod
    def _serve(pages: dict[str, str]):
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                html = pages.get(self.path)
                if html is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = html.encode()
                self.send_response(200)
                self.send_header("content-type", "text/html; charset=utf-8")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    @pytest.mark.asyncio
    async def test_a_refresh_on_a_real_server_lands_on_the_destination(self):
        srv = self._serve({
            "/from": '<html><head><meta http-equiv="refresh" content="0;url=/to">'
                     "</head><body>pointer page</body></html>",
            "/to": "<html><body><h1>the real content</h1></body></html>",
        })
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            resp = await HTTPSession().get(f"{base}/from", allow_internal=True)
        finally:
            srv.shutdown()

        assert resp.url == f"{base}/to"
        text = _decode_html_bytes(resp.body, resp.encoding)
        assert "the real content" in text
        assert resp.meta_refresh["from"] == f"{base}/from"

    @pytest.mark.asyncio
    async def test_response_without_a_refresh_reports_nothing_extra(self):
        srv = self._serve({"/plain": "<html><body>plain</body></html>"})
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            resp = await HTTPSession().get(f"{base}/plain", allow_internal=True)
        finally:
            srv.shutdown()

        assert resp.meta_refresh is None
        assert resp.url == f"{base}/plain"


class TestTheResponseCarriesTheReason:

    def test_response_meta_refresh_defaults_to_none(self):
        assert Response(url="https://a.example/x", body=b"", status=200).meta_refresh is None

    def test_translate_response_surfaces_it_in_metadata(self, monkeypatch):
        from dhole_mcp import server as server_mod

        page = Response(url="https://a.example/to",
                        body=b"<html><body><p>destination</p></body></html>",
                        status=200, headers={"content-type": "text/html"},
                        meta_refresh={"from": "https://a.example/from",
                                      "delay_s": 0.0, "hops": 1})
        result = server_mod._translate_response(page, "markdown", None, False, True,
                                                "http", 1.0)
        assert result.metadata["meta_refresh"]["from"] == "https://a.example/from"
