"""一个没在听的本机端口，不该被同一个端口再拨一次（2026-10-08 验收 · D-08 的回归锁）

现场（本机回环，真实调用）：`web_read` 打一个没人听的私网端口，HTTP 档失败的形状是
`status=0` + 空正文 —— 恰好是 `_is_js_shell()` 认的那个"壳"形状。于是自动升级起了
浏览器，**拨的还是同一个死端口**（实测 30011ms 把整轮预算烧完），之后还去花一次
archive.org 的预算。强制档早就有 `_private_health_probe` 那条判断，自动档没有 ——
同一件事在两条路上有两个答案。

判据本身不新：`errors.classify_network_error` 分得出 `connection_refused` / `dns_failure`
/ `timeout`。新的那一半是**回环与私网的 connect 超时也算定论**：那里不存在"2 秒才
应答的合法服务"，而防火墙 DROP 型栈会把 refused 洗成 timeout（同一台机器上两种形状
都量到过），结果一样是死端口。公网保持 timeout=继续 —— filtered port 在公网上是
常态，把它当死端口会掐掉本来抓得到的页。

（R5 之后这份名单窄了一半：`dns_failure` 从定论里出去了，公网的 `connection_refused`
也出去了 —— 见 tests/test_a_dns_verdict_from_the_probe_is_not_final.py。留在这里的
全是**私网**那一支，它一个字没动。）

本轮实测过、当时没落成断言的三条都在这里：
1. 预检定论（私网的 refused / timeout）→ 直接回信，浏览器档一次都不被问、archive
   一分预算不花；
2. 预检过了而 HTTP 档仍没拿到应答（真慢服务 + 短 timeout）→ 私网目标不升级，回执写
   `http(private_target_no_answer)` 并把"浏览器拨的是同一个端口"说出口；
3. 私网的 connect 超时算定论、公网的不算（同一个 stub 出来的 timeout，两种地理）。

反向锁：判据认的是**地理位置不是许可**（hostname 一律 False，地理位置要等 DNS 才有
答案），以及公网那条路真的会继续升级到浏览器 —— 收紧不能收过头。
"""

from __future__ import annotations

import asyncio
import http.server
import socket
import socketserver
import threading
import time

import pytest

import dhole_mcp.fetcher as fetch_mod
from dhole_mcp.models import ResponseModel
from dhole_mcp.server import MasterFetchServer

TIMEOUT_ERROR = ("error sending request for url (http://public-filtered.test/) "
                 "> operation timed out")


class _Calls:
    """谁被问过：浏览器档与 archive 档各记一笔，判据全在这两张表上。"""

    def __init__(self) -> None:
        self.browser: list[str] = []
        self.archive_urls: list[str] = []

    async def stealthy_one(self, url, *args, **kwargs):
        self.browser.append(url)
        return ResponseModel(url=url, status=200, content=["<p>rendered</p>"],
                             content_type="text/html", fetcher_used="stealthy")

    async def stealthy_bulk(self, *args, **kwargs):
        self.browser.append(str(kwargs.get("urls") or args))
        from dhole_mcp.server import BulkResponseModel
        return BulkResponseModel(results=[], total=0, successful=0)

    async def archive(self, url, *args, **kwargs):
        self.archive_urls.append(url)
        return None


@pytest.fixture()
def calls(monkeypatch):
    c = _Calls()
    monkeypatch.setattr(MasterFetchServer, "stealthy_fetch", c.stealthy_one)
    monkeypatch.setattr(MasterFetchServer, "bulk_stealthy_fetch", c.stealthy_bulk)
    monkeypatch.setattr(MasterFetchServer, "_fetch_from_archive", c.archive)

    # 升级之前先去要一台共享浏览器（那才是 conftest 的守卫拦住的东西）。档本体已经
    # 是 stub，这一步只需要"别真启动"。
    async def auto_session(self, *args, **kwargs):
        return "stub-session"

    monkeypatch.setattr(MasterFetchServer, "_ensure_auto_session", auto_session)
    return c


@pytest.fixture()
def closed_port():
    """一个刚刚还被人听着、现在没人听的回环端口（真 connect 会立刻被拒）。"""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


@pytest.fixture(scope="module")
def slow_site():
    """一个活着、但 6s 才答的回环服务：预检说"有人听"，HTTP 档说"没等到"。"""
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(6)
            body = b"<html><body><p>eventually</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    class S(socketserver.ThreadingTCPServer):
        daemon_threads = True
        allow_reuse_address = True

    srv = S(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}/slow"
    srv.shutdown()


@pytest.fixture(autouse=True)
def allow_loopback(monkeypatch):
    monkeypatch.setenv("DHOLE_ALLOW_PRIVATE_HOSTS", "127.0.0.1")


def _read(url, **kw):
    return asyncio.run(MasterFetchServer().web_read(url=url, cache_ttl=0, **kw))


class TestADefinitivePreflightEndsTheRound:

    def test_a_dead_local_port_is_answered_without_a_second_dial(self, calls, closed_port):
        """哪一层先失败是平台的事，契约是"失败得早 + 不再拨第二次"（与 g31 同一约定）。"""
        r = _read(f"http://127.0.0.1:{closed_port}/")
        assert r.status == 0
        assert r.fetcher_used in ("none", "http"), r.fetcher_used
        assert calls.browser == [], f"浏览器档又去拨了一次死端口: {calls.browser}"

    def test_the_dead_port_is_classified_as_a_network_failure_not_an_invented_word(
            self, calls, closed_port):
        """本机实测：Windows 这一栈上，死掉的回环端口对预检一律答 `timeout`（2s 到点），
        一个都不回 connection_refused —— 正是"DROP 型栈把 refused 洗成 timeout"，也
        就是私网的 timeout 必须算定论的理由。另一形（HTTP 档自己被拒）走的是同一族。
        两形都不许落进 D-18 那个由首词兜底发明的 code。"""
        r = _read(f"http://127.0.0.1:{closed_port}/")
        code = (r.diagnosis or {}).get("code", "")
        assert code in ("network", "timeout", "connection_refused", "connection_reset",
                        "dns_failure"), (code, r.error)
        assert code != "error", r.error

    def test_the_advice_asks_about_the_service_not_about_the_site(self, closed_port):
        """通用那句"站点可能挂了、换个源"对回环目标是误导。"""
        r = _read(f"http://127.0.0.1:{closed_port}/")
        assert "local service" in r.next_action, r.next_action
        assert "port" in r.next_action, r.next_action

    def test_no_archive_budget_is_spent_on_a_dead_port(self, calls, closed_port):
        r = _read(f"http://127.0.0.1:{closed_port}/")
        assert calls.archive_urls == [], r.escalation_path
        assert "archive" not in (r.escalation_path or ""), r.escalation_path


class TestAnInconclusivePreflightStillDoesNotBuyADial:

    def test_a_service_that_never_answers_in_time_is_not_rendered(self, calls, slow_site):
        """预检过了（有人听），HTTP 档却没拿到应答 —— 这正是当年烧满 30s 的那一发。"""
        r = _read(slow_site, timeout=1500)
        assert r.status == 0 and "timed out" in (r.error or "").lower(), r.error
        assert r.escalation_path == "http(private_target_no_answer)", r.escalation_path
        assert calls.browser == [], f"同一个端口又被拨了一次: {calls.browser}"

    def test_the_receipt_says_who_was_not_called(self, slow_site):
        r = _read(slow_site, timeout=1500)
        assert "the browser tier was skipped" in r.error, r.error
        assert "it dials the same port" in r.error, r.error


class TestTimeoutMeansDifferentThingsByGeography:
    """同一次 connect 超时，在两种地方是两个结论 —— 所以它必须是两条路各自测。"""

    @staticmethod
    def _stub_network(monkeypatch):
        monkeypatch.setattr(fetch_mod, "tcp_preflight",
                            lambda url, timeout: (False, "timeout"))

        async def timing_out(self, url, *args, **kwargs):
            # 交回库的原文形状（primp 把超时包成一句 error 串）。不能 raise：那会被读成
            # "这一次调用到点了"，整轮预算被吃掉，测的就不是升级门了。
            return ResponseModel(url=url, status=0, content=[], fetcher_used="http",
                                 error=TIMEOUT_ERROR, duration_ms=10.0)

        monkeypatch.setattr(MasterFetchServer, "get", timing_out)

    def test_a_private_connect_timeout_is_taken_as_dead(self, monkeypatch, calls):
        self._stub_network(monkeypatch)
        r = _read("http://127.0.0.1:9/x")
        assert "preflight:timeout(skipped" in r.escalation_path, r.escalation_path
        assert calls.browser == [], calls.browser

    def test_a_public_connect_timeout_still_gets_its_render(self, monkeypatch, calls):
        """反向锁：公网的 filtered port 是常态，收成定论会掐掉抓得到的页。"""
        self._stub_network(monkeypatch)
        r = _read("http://public-filtered.test/")
        assert "preflight:timeout(skipped_stealthy)" not in (r.escalation_path or ""), \
            (r.escalation_path, r.error)
        assert calls.browser, f"公网超时不该也被拒升级: {r.error}"


class TestTheGeographyQuestionIsNotAPermissionQuestion:

    @pytest.mark.parametrize("url,wanted", [
        ("http://127.0.0.1:8080/", True),
        ("http://localhost:3000/", True),
        ("http://10.0.0.5/", True),
        ("http://192.168.1.1/", True),
        ("http://[::1]:80/", True),
        ("http://example.com/", False),      # 主机名：地理位置要等 DNS 才有答案
        ("http:///", False),
        ("not a url", False),
    ], ids=["loopback-ip", "localhost", "ten", "cpe", "v6-loopback", "hostname",
            "empty-host", "garbage"])
    def test_private_is_a_place_not_a_verdict(self, url, wanted):
        assert MasterFetchServer._target_is_private(url) is wanted, url
