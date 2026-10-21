"""test_fetcher_4.py —— 由 4 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import asyncio
import socket
import pytest
import dhole_mcp.fetcher as fetch_mod
from dhole_mcp.context import _arg_notes_scope
from dhole_mcp.errors import classify_network_error, get_hint, is_retryable
from dhole_mcp.models import ResponseModel
from dhole_mcp.server import MasterFetchServer
import os
import threading
import http.server
import socketserver
from dhole_mcp import identity
from dhole_mcp.fetcher import HTTPSession, _LOOPBACK_NO_PROXY_ENTRIES, _ensure_loopback_no_proxy
from unittest.mock import MagicMock
from dhole_mcp.fetcher import Response, _extract_encoding
from dhole_mcp import robots, security


# ============================================================================
# 来源：test_a_dns_verdict_from_the_probe_is_not_final.py
# ============================================================================
# 一次 DNS 抖动不该被判成死刑（R5，2026-10-09 真人体验测试 C6 的回归锁）

PUBLIC = "http://public-flaky.test/article"


class _NoSocket:
    def close(self):
        pass


@pytest.fixture()
def resolver(monkeypatch):
    """把 `create_connection` 换成一台可编程的 resolver：按次序决定第几次拨失败。

    patch 在 `create_connection` 而不是 `getaddrinfo`：conftest 的 `_offline_dns` 已经
    把 `getaddrinfo` 钉在 example.com 的地址上，钉在它下面的东西测不到任何事。
    """
    plan: list[str] = []      # 每一项: "ok" / "dns" / "refused" / "reset" / "timeout"
    attempts: list[str] = []

    def _fake(addr, timeout=None):
        attempts.append(str(addr[0]))
        verdict = plan[len(attempts) - 1] if len(attempts) <= len(plan) else plan[-1]
        if verdict == "ok":
            return _NoSocket()
        if verdict == "dns":
            raise socket.gaierror("Name or service not known")
        if verdict == "refused":
            raise ConnectionRefusedError
        if verdict == "reset":
            raise OSError(10054, "远程主机强迫关闭了一个现有的连接")
        raise socket.timeout

    monkeypatch.setattr(socket, "create_connection", _fake)
    return plan, attempts


class TestOneBlipIsNotAVerdict:

    def test_a_name_that_answers_on_the_second_probe_is_not_a_failure(self, resolver):
        plan, attempts = resolver
        plan[:] = ["dns", "ok"]
        with _arg_notes_scope() as notes:
            ok, category = fetch_mod.tcp_preflight(PUBLIC, timeout=0.2)
        assert (ok, category) == (True, ""), (ok, category)
        assert len(attempts) == 2, "第二次都没拨，'再试一次'就只是句漂亮话"
        assert any("resolved on the second" in n for n in notes), notes

    def test_the_absorbed_blip_leaves_a_trace(self, resolver):
        """被吸收掉的故障必须 still 说一句：否者调用方永远不知道这台机器在抖。"""
        plan, _ = resolver
        plan[:] = ["dns", "ok"]
        with _arg_notes_scope() as notes:
            fetch_mod.tcp_preflight(PUBLIC, timeout=0.2)
        note = " ".join(notes)
        assert "public-flaky.test" in note, note
        assert "flaky" in note, note

    def test_two_probes_both_blind_is_called_unresolvable(self, resolver):
        plan, attempts = resolver
        plan[:] = ["dns", "dns"]
        with _arg_notes_scope() as notes:
            ok, category = fetch_mod.tcp_preflight(PUBLIC, timeout=0.2)
        assert (ok, category) == (False, "dns_unresolvable")
        assert len(attempts) == 2
        assert notes == [], "两次都没答就不是抖动，别在 summary 里留一句抖动的解释"

    def test_the_name_is_not_what_the_caller_is_asked_to_fix(self, resolver):
        """一颗从没问过 DNS 的失败不许顶着一句"去核域名拼写"。

        `0177.0.0.1` 是字面 IP 的回环变体，整条链一次解析都没做过。曾经它是
        `dns_failure`；hint 里那句 "Verify the URL is correct" 让人去核一个不存在
        的域名，而真事是一扇关着的门。
        """
        ok, category = fetch_mod.tcp_preflight("http://0177.0.0.1:8080/", timeout=0.2)
        assert (ok, category) == (False, "private_target_blocked")

    def test_a_second_probe_that_reaches_the_port_is_classified_by_the_port(
            self, resolver):
        """第二次拨通了名字、死在端口上：标签属于第二次，不属于第一次。"""
        plan, _ = resolver
        plan[:] = ["dns", "refused"]
        ok, category = fetch_mod.tcp_preflight(PUBLIC, timeout=0.2)
        assert (ok, category) == (False, "connection_refused")

    def test_the_gap_between_probes_is_bounded(self, resolver):
        """重试不是无限兜底：只多拨一次。"""
        plan, attempts = resolver
        plan[:] = ["dns", "dns", "ok"]
        ok, category = fetch_mod.tcp_preflight(PUBLIC, timeout=0.2)
        assert (ok, category) == (False, "dns_unresolvable")
        assert len(attempts) == 2, attempts


class TestTheLabelsCarryTheirOwnRetryAnswer:
    """分类、hint、retryable 三件事同处一地（D5 立的法：两边分家会互相否决）。"""

    def test_the_preflight_label_is_read_back_by_the_classifier(self):
        for label in ("dns_unresolvable", "private_target_blocked"):
            category, hint = classify_network_error(
                f"network_error: {label} (TCP preflight)")
            assert category == label, (category, hint)
            assert hint and "unknown" not in hint[:20], hint

    def test_a_name_that_failed_twice_is_not_offered_a_retry(self):
        assert is_retryable("dns_unresolvable") is False
        assert "two probes" in get_hint("dns_unresolvable")
        # 但必须先承认预探测不是最后一个说话的人
        assert "escalation_path" in get_hint("dns_unresolvable")

    def test_a_closed_door_names_the_door_not_the_network(self):
        hint = get_hint("private_target_blocked").lower()
        assert "allow_private" in hint, hint
        assert "no request was sent" in hint, hint
        assert is_retryable("private_target_blocked") is False

    def test_the_old_dns_words_are_untouched(self):
        """正向锁：公网真失败的那句出路一字未改，别让放宽把有用的话冲掉。"""
        assert classify_network_error("net::ERR_NAME_NOT_RESOLVED")[0] == "dns_failure"
        assert is_retryable("dns_failure") is False
        assert is_retryable("connection_refused") is False


class _Calls:

    def __init__(self) -> None:
        self.browser: list[str] = []
        self.archive_urls: list[str] = []

    async def stealthy_one(self, url, *args, **kwargs):
        self.browser.append(url)
        return ResponseModel(url=url, status=200, content=["<p>rendered</p>"],
                             content_type="text/html", fetcher_used="stealthy")

    async def archive(self, url, *args, **kwargs):
        self.archive_urls.append(url)
        return None


@pytest.fixture()
def calls(monkeypatch):
    c = _Calls()
    monkeypatch.setattr(MasterFetchServer, "stealthy_fetch", c.stealthy_one)
    monkeypatch.setattr(MasterFetchServer, "_fetch_from_archive", c.archive)

    async def auto_session(self, *args, **kwargs):
        return "stub-session"

    monkeypatch.setattr(MasterFetchServer, "_ensure_auto_session", auto_session)
    return c


def _stub_probe(monkeypatch, category: str) -> None:
    monkeypatch.setattr(fetch_mod, "tcp_preflight",
                        lambda url, timeout=2.0: (False, category))


async def _http_fails(self, url, *args, **kwargs):
    return ResponseModel(
        url=url, status=0, content=[], fetcher_used="http",
        error=("error sending request for url (" + url + ") > operation timed out"),
        duration_ms=10.0)


def _read(url, **kw):
    return asyncio.run(MasterFetchServer().web_read(url=url, cache_ttl=0, **kw))


class TestTheProbeLosesItsPowerToSkipTwoTiers:
    """预探测不再有权跳掉 HTTP + 浏览器两档 —— 除了私网那两个它确实说得准的结论。"""

    def test_a_dns_verdict_from_the_probe_still_lets_the_real_tiers_speak(
            self, monkeypatch, calls):
        _stub_probe(monkeypatch, "dns_unresolvable")
        monkeypatch.setattr(MasterFetchServer, "get", _http_fails)
        r = _read(PUBLIC)
        assert r.escalation_path and "skipped_http+stealthy" not in r.escalation_path, \
            r.escalation_path
        assert calls.browser == [PUBLIC], (calls.browser, r.escalation_path)

    def test_a_public_refused_connection_no_longer_ends_the_round(
            self, monkeypatch, calls):
        """公网上一次 RST 可能出自中间设备；先应答再失败的那类栈把 refused 当礼貌。"""
        _stub_probe(monkeypatch, "connection_refused")
        monkeypatch.setattr(MasterFetchServer, "get", _http_fails)
        r = _read(PUBLIC)
        assert calls.browser == [PUBLIC], (calls.browser, r.escalation_path)

    def test_a_private_dead_port_still_stops_here(self, monkeypatch, calls):
        """反向锁（放宽不许放过头）：回环上 refused 与 timeout 都还是定论。

        G31 的量在那儿没有变：浏览器拨的是同一个死端口，实测烧满 45s。
        """
        monkeypatch.setenv("DHOLE_ALLOW_PRIVATE_HOSTS", "127.0.0.1")
        _stub_probe(monkeypatch, "connection_refused")
        monkeypatch.setattr(MasterFetchServer, "get", _http_fails)
        r = _read("http://127.0.0.1:9/x")
        assert "preflight:connection_refused(skipped" in r.escalation_path, \
            r.escalation_path
        assert calls.browser == [], calls.browser

    def test_a_dns_blip_on_a_private_target_also_gets_its_tiers(
            self, monkeypatch, calls):
        """私网保留的是 refused/timeout，不是一次解析失败。

        这条钉的是 `definitive` 里**只有**那两颗：名字解析在回环上通常是 hosts 文件
        或 `localhost` 别名的事，而那是 resolver 的问题，不是那个端口的问题。
        """
        monkeypatch.setenv("DHOLE_ALLOW_PRIVATE_HOSTS", "127.0.0.1")
        _stub_probe(monkeypatch, "dns_unresolvable")
        monkeypatch.setattr(MasterFetchServer, "get", _http_fails)
        r = _read("http://127.0.0.1:9/x")
        assert "skipped_http+stealthy" not in (r.escalation_path or ""), r.escalation_path


class TestTheSecondOpinionLeavesATrail:
    """archive 档答不上来时，那一次"问过"必须留在回执里。"""

    @pytest.mark.parametrize("way, wanted", [
        ("no-snapshot", "holds no snapshot"),
        ("api-500", "availability API answered HTTP 500"),
        ("empty-snapshot", "answered HTTP 404 with no readable body"),
        ("raised", "raised before it could answer"),
    ])
    def test_each_way_it_answers_nothing_is_said_differently(
            self, monkeypatch, way, wanted):
        server = MasterFetchServer()

        async def fake_http_get(url, timeout=10):
            from types import SimpleNamespace
            if way == "api-500":
                return SimpleNamespace(status=500, body="")
            if way == "raised":
                raise RuntimeError("socket hang-up")
            # 只有"快照页那一跳"这一支需要 API 真交出一个快照地址，其余三支都在
            # 更早的地方就答不上了。
            return SimpleNamespace(
                status=200,
                body=_wayback_body("has-snapshot" if way == "empty-snapshot" else way))

        async def fake_snapshot_get(self, url, **kwargs):
            return ResponseModel(url=url, status=404, content=["   "],
                                 fetcher_used="http")

        monkeypatch.setattr("dhole_mcp.server._fallback_http_get", fake_http_get)
        monkeypatch.setattr(MasterFetchServer, "get", fake_snapshot_get)
        with _arg_notes_scope() as notes:
            out = asyncio.run(server._fetch_from_archive(PUBLIC, "markdown", None,
                                                         0, 1000))
        assert out is None, way
        blob = " ".join(notes)
        assert wanted in blob, (way, notes)

    def test_a_miss_is_reported_as_a_miss_not_as_an_answer(self, monkeypatch):
        """问过、没有 —— 与"没问过"在 summary 上是两句话，别合并成沉默。"""
        server = MasterFetchServer()

        async def fake_http_get(url, timeout=10):
            from types import SimpleNamespace
            return SimpleNamespace(status=200, body=_wayback_body("no-snapshot"))

        monkeypatch.setattr("dhole_mcp.server._fallback_http_get", fake_http_get)
        with _arg_notes_scope() as notes:
            asyncio.run(server._fetch_from_archive(PUBLIC, "markdown", None, 0, 1000))
        assert len(notes) == 1, notes


def _wayback_body(way: str) -> str:
    import json
    if way == "has-snapshot":
        snap = {"url": "https://web.archive.org/web/2026/snapshot",
                "available": True, "timestamp": "20260101000000"}
    else:
        snap = {"url": "", "available": False}
    return json.dumps({"archived_snapshots": {"closest": snap}})


# ============================================================================
# 来源：test_loopback_no_proxy_g31.py
# ============================================================================
# G31 — loopback targets must never ride a proxy.

# ─── NO_PROXY guard: unit behavior ──────────────────────────────────────────

def test_guard_adds_loopback_entries(monkeypatch):
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    _ensure_loopback_no_proxy()
    value = os.environ.get("NO_PROXY", "")
    for entry in _LOOPBACK_NO_PROXY_ENTRIES:
        assert entry in value.split(",")


def test_guard_preserves_user_entries_both_spellings(monkeypatch):
    # POSIX treats NO_PROXY / no_proxy as two variables and some tools only
    # read the lowercase one — the merge must union both, not clobber.
    # Windows merges the two spellings into ONE case-insensitive variable, so
    # the double-spelling scenario cannot even be constructed there; the
    # single-spelling preservation is covered below.
    if os.name == "nt":
        pytest.skip("Windows folds NO_PROXY/no_proxy into one variable")
    monkeypatch.setenv("NO_PROXY", "corp.example,internal.lan")
    monkeypatch.setenv("no_proxy", "legacy.host")
    _ensure_loopback_no_proxy()
    upper = set(os.environ.get("NO_PROXY", "").split(","))
    lower = set(os.environ.get("no_proxy", "").split(","))
    for entry in ("corp.example", "internal.lan", "legacy.host",
                  *_LOOPBACK_NO_PROXY_ENTRIES):
        assert entry in upper
        assert entry in lower


def test_guard_preserves_a_single_user_entry(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "corp.example")
    if os.name != "nt":  # Windows folds the two spellings into one variable
        monkeypatch.delenv("no_proxy", raising=False)
    _ensure_loopback_no_proxy()
    value = set(os.environ.get("NO_PROXY", "").split(","))
    assert {"corp.example", *_LOOPBACK_NO_PROXY_ENTRIES} <= value


def test_guard_is_idempotent(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.delenv("no_proxy", raising=False)
    _ensure_loopback_no_proxy()
    first = os.environ.get("NO_PROXY")
    _ensure_loopback_no_proxy()
    assert os.environ.get("NO_PROXY") == first
    assert first.split(",").count("localhost") == 1


# ─── _target_is_private: geography, not permission ──────────────────────────


_is_private = MasterFetchServer._target_is_private


def test_private_geography():
    assert _is_private("http://127.0.0.1:8080/") is True
    assert _is_private("http://localhost:3000/api") is True
    assert _is_private("http://[::1]:5173/") is True
    assert _is_private("http://192.168.1.50/") is True
    assert _is_private("http://10.0.0.2/") is True
    assert _is_private("http://sub.localhost/") is True


def test_public_and_hostnames_are_not_private():
    assert _is_private("https://example.com/") is False
    assert _is_private("http://8.8.8.8/") is False
    # A hostname's geography is unknown without DNS — not "private", the real
    # attempt decides. my-service.local is allowlist material, not literal.
    assert _is_private("http://my-service.local/") is False


# ─── Integration: a live local server survives a dead proxy in the env ──────

@pytest.mark.asyncio
async def test_forced_stealthy_dead_private_port_fails_fast():
    """The forced-stealthy half of G31: a dead private port must not burn the
    browser budget. The TCP preflight (or, on accept-everything network stacks,
    the HTTP health probe behind it) returns before any browser launch, so this
    runs without patchright installed."""
    import json
    import time

    from dhole_mcp.server import MasterFetchServer

    # 17.0 §3-17: allow_private left the call surface — the private-host gate is
    # stored once and read back per call, so the loopback target below is
    # reachable without naming the option on the wire. The store is what both the
    # entry check and the tiers read (and what the response echoes as
    # identity_applied, asserted below).
    identity.set_value("allow_private", True)

    srv = MasterFetchServer(cache_ttl=0)
    started = time.monotonic()
    r = await srv._dispatch("web_read", {
        "url": "http://127.0.0.1:1/",
        "force_fetcher": "stealthy",
        "timeout": 45000})
    elapsed = time.monotonic() - started
    payload = json.loads(r[0][0].text)
    # Fast: nowhere near the 45s budget the browser tier used to burn.
    assert elapsed < 20, f"forced-stealthy dead port took {elapsed:.1f}s"
    # Honest: no tier pretended to have fetched a live page, and the answer
    # points at the local service rather than a slow site. The TCP preflight
    # answers with fetcher_used="none"; when this environment's network stack
    # makes the TCP probe inconclusive, the HTTP health probe behind it answers
    # with "http" — either layer failing fast is the contract.
    assert payload["fetcher_used"] in ("none", "http")
    assert "skipped_stealthy" in payload.get("escalation_path", "")
    assert payload["status"] == 0
    # The call ran on the stored default, and says so by NAME only.
    assert payload.get("identity_applied") == "allow_private", payload
    assert "private" in payload.get("next_action", "").lower() or \
        "service" in payload.get("next_action", "").lower()


@pytest.mark.asyncio
async def test_loopback_fetch_survives_env_proxy(monkeypatch):
    """The G31 reproduction, as a regression net.

    A local HTTP server is live; the environment points every proxy variable at
    a dead port. Before the fix primp routed the request to the dead proxy and
    the fetch failed; after it NO_PROXY takes loopback off the proxy and the
    request goes direct.
    """
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", 0), Quiet)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        # Re-run the guard: it runs at import, but this test re-sets the env
        # afterwards — exactly the order a fresh process with proxies in its
        # environment would see.
        _ensure_loopback_no_proxy()
        async with HTTPSession(stealthy_headers=False) as session:
            resp = await session.get(f"http://127.0.0.1:{port}/", timeout=10)
        assert resp.status == 200
    finally:
        srv.shutdown()


# ─── 健康探针读的是传输，不是内容判定 ───────────────────────────────────────

class TestThePrivateHealthProbeReadsTransportNotContent:
    """`ResponseModel.error` 同时承载**内容判定**与传输失败，所以它不能当健康信号。

    实测（靶站 /login）：HTTP 200、正文是完整的登录表单、服务明明在跑，但内容
    分类器把 `error` 写成 `auth_wall_detected`。探针把它读成"目标不健康"，于是
    跳过浏览器层，返回 HTTP 层的结果 —— 一个 `{"snapshot": true}` 动作**从来没
    跑过**，而调用方收到的说法是"这个页面上没有密码框"。

    这正是这个项目最当回事的那类失败：抓取看起来成功了，而诊断是错的。
    """

    @staticmethod
    def _srv():
        from dhole_mcp.server import MasterFetchServer

        srv = MasterFetchServer(cache_ttl=0)

        def install(probe):
            async def fake_get(url, **kw):
                return probe(url)
            srv.get = fake_get
            return srv

        return srv, install

    @pytest.mark.asyncio
    async def test_a_content_verdict_on_a_200_does_not_count_as_unhealthy(self):
        from dhole_mcp.server import ResponseModel

        _srv, install = self._srv()
        srv = install(lambda url: ResponseModel(
            url=url, status=200, content=["请登录"], content_ok=False,
            error="auth_wall_detected: page is a login/sign-in wall, not content"))
        got = await srv._private_health_probe(
            "http://127.0.0.1:8399/login", None, None, None, 0.0)
        assert got is None, "200 + 内容判定 ≠ 目标不健康；浏览器层必须照常进"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status,error", [
        (0, "network_error: connection_refused"),
        (503, "http_error_503: server returned error status"),
        (500, ""),
    ])
    async def test_a_transport_failure_or_a_5xx_still_short_circuits(self, status, error):
        from dhole_mcp.server import ResponseModel

        _srv, install = self._srv()
        srv = install(lambda url: ResponseModel(
            url=url, status=status, content=[], error=error))
        got = await srv._private_health_probe(
            "http://127.0.0.1:8399/login", None, None, None, 0.0)
        assert got is not None and "skipped_stealthy" in got.escalation_path

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [200, 301, 404])
    async def test_2xx_3xx_4xx_are_alive(self, status):
        from dhole_mcp.server import ResponseModel

        _srv, install = self._srv()
        srv = install(lambda url: ResponseModel(
            url=url, status=status, content=["x"], error=""))
        assert await srv._private_health_probe(
            "http://127.0.0.1:8399/login", None, None, None, 0.0) is None


# ============================================================================
# 来源：test_fetcher.py
# ============================================================================
# Fetcher tests: Response class properties, CSS selector queries,
# cross-platform HTML parsing, follow_redirects coercion.

# ─── Response class ───────────────────────────────────────────────

class TestResponseClass:

    def test_properties_return_init_values(self):
        r = Response(
            url="https://example.com/page",
            body=b"<html><body>Hello</body></html>",
            status=200,
            headers={"content-type": "text/html"},
            encoding="utf-8",
            reason="OK",
            cookies={"session": "abc"},
        )
        assert r.status == 200
        assert r.url == "https://example.com/page"
        assert r.headers["content-type"] == "text/html"
        assert r.body == b"<html><body>Hello</body></html>"
        assert r.encoding == "utf-8"
        assert r.reason == "OK"
        assert r.cookies["session"] == "abc"

    def test_content_decodes_body(self):
        r = Response(url="https://x.com", body=b"Hello world", status=200)
        assert r.content == "Hello world"

    def test_content_caches_result(self):
        r = Response(url="https://x.com", body=b"Hello", status=200)
        first = r.content
        second = r.content
        assert first is second  # cached, not re-decoded

    def test_content_handles_non_utf8(self):
        r = Response(url="https://x.com", body="Hello".encode("latin-1"), status=200, encoding="latin-1")
        assert r.content == "Hello"

    def test_html_content_alias(self):
        r = Response(url="https://x.com", body=b"<p>Hi</p>", status=200)
        assert r.html_content == r.content

    def test_empty_body_returns_empty_content(self):
        r = Response(url="https://x.com", body=b"", status=200)
        assert r.content == ""

    def test_non_bytes_body_coerced_to_empty(self):
        r = Response(url="https://x.com", body=None, status=200)
        assert r.body == b""

    def test_default_headers_empty_dict(self):
        r = Response(url="https://x.com", body=b"x", status=200)
        assert r.headers == {}

    def test_default_cookies_empty_dict(self):
        r = Response(url="https://x.com", body=b"x", status=200)
        assert r.cookies == {}


# ─── CSS selector queries ──────────────────────────────────────────

class TestCSSSelectors:

    def test_css_finds_elements(self):
        html = b'<html><body><div class="main">Text</div></body></html>'
        r = Response(url="https://x.com", body=html, status=200)
        results = r.css(".main")
        assert len(results) == 1
        assert results[0].text_content() == "Text"

    def test_css_no_matches_returns_empty_list(self):
        html = b'<html><body><div>Text</div></body></html>'
        r = Response(url="https://x.com", body=html, status=200)
        results = r.css(".nonexistent")
        assert results == []

    def test_css_finds_multiple_elements(self):
        html = b'<html><body><p>A</p><p>B</p><p>C</p></body></html>'
        r = Response(url="https://x.com", body=html, status=200)
        results = r.css("p")
        assert len(results) == 3

    def test_css_selector_on_subtree(self):
        html = b'<html><body><div class="container"><p>Inner</p></div><p>Outer</p></body></html>'
        r = Response(url="https://x.com", body=html, status=200)
        container = r.css(".container")
        assert len(container) == 1
        inner = container[0].css("p")
        assert len(inner) == 1
        assert inner[0].text_content() == "Inner"

    def test_invalid_css_selector_raises_error(self):
        # PR #11.1.5: errors propagate, not silently swallowed
        html = b'<html><body><div>Text</div></body></html>'
        r = Response(url="https://x.com", body=html, status=200)
        from lxml.cssselect import SelectorSyntaxError
        with pytest.raises(SelectorSyntaxError):
            r.css("div > >")

    def test_element_wrapper_url_propagated(self):
        html = b'<html><body><div class="x">Y</div></body></html>'
        r = Response(url="https://example.com/page", body=html, status=200)
        el = r.css(".x")[0]
        assert el.url == "https://example.com/page"

    def test_text_content_includes_descendants(self):
        """回归：text_content() 曾只返回元素自身首段文本（.text），丢弃嵌套
        子元素的文本。现在与 lxml 原生 .text_content() 语义一致。"""
        html = (b'<html><body><div class="x">Hello <b>bold</b>'
                b' <a href="/l">link</a> world</div></body></html>')
        r = Response(url="https://x.com", body=html, status=200)
        assert r.css(".x")[0].text_content() == "Hello bold link world"

    def test_text_content_on_leaf_unchanged(self):
        html = b'<html><body><div class="main">Text</div></body></html>'
        r = Response(url="https://x.com", body=html, status=200)
        assert r.css(".main")[0].text_content() == "Text"


# ─── Cross-platform HTML parsing ──────────────────────────────────

class TestHTMLParsing:

    def test_full_html_document_parsed_correctly(self):
        html = b"""<!DOCTYPE html><html><head><title>Test</title></head>
        <body><div class="content">Hello</div></body></html>"""
        r = Response(url="https://x.com", body=html, status=200)
        results = r.css(".content")
        assert len(results) == 1
        assert results[0].text_content() == "Hello"

    def test_html_without_doctype_parsed(self):
        html = b'<html><body><p>No doctype</p></body></html>'
        r = Response(url="https://x.com", body=html, status=200)
        assert len(r.css("p")) == 1

    def test_fragment_html_parsed(self):
        html = b'<div><p>Fragment</p></div>'
        r = Response(url="https://x.com", body=html, status=200)
        assert len(r.css("p")) == 1

    def test_empty_body_does_not_crash(self):
        r = Response(url="https://x.com", body=b"", status=200)
        assert r.css("div") == []


# ─── Encoding extraction ──────────────────────────────────────────

class TestExtractEncoding:

    def test_extracts_utf8(self):
        assert _extract_encoding("text/html; charset=utf-8") == "utf-8"

    def test_extracts_latin1(self):
        assert _extract_encoding("text/html; charset=iso-8859-1") == "iso-8859-1"

    def test_returns_utf8_for_empty(self):
        assert _extract_encoding("") == "utf-8"

    def test_returns_utf8_for_no_charset(self):
        assert _extract_encoding("application/json") == "utf-8"

    def test_handles_quoted_charset(self):
        assert _extract_encoding('text/html; charset="utf-8"') == "utf-8"


# ─── HTTPSession follow_redirects coercion (v11.0.2 fix) ──────────

class TestFollowRedirectsCoercion:

    @pytest.mark.asyncio
    async def test_string_safe_coerced_to_follow(self):
        # scrapling-style "safe" -> 手动跟随（primp 收 follow_redirects=False，
        # 由 HTTPSession 逐跳校验目标 URL 后自行跟随）
        session = HTTPSession()
        session._client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.headers = {}
        mock_resp.status_code = 200
        mock_resp.content = b"ok"
        mock_resp.url = "https://example.com"
        mock_resp.reason = "OK"
        mock_resp.cookies = []
        session._client.request = MagicMock(return_value=mock_resp)

        await session.get("https://example.com", follow_redirects="safe")
        # primp 收到 False（手动跟随由本层实现，重定向目标逐跳 SSRF 校验）
        call_kwargs = session._client.request.call_args
        assert call_kwargs.kwargs.get("follow_redirects") is False

    @pytest.mark.asyncio
    async def test_string_never_coerced_to_false(self):
        session = HTTPSession()
        session._client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.headers = {}
        mock_resp.status_code = 200
        mock_resp.content = b"ok"
        mock_resp.url = "https://example.com"
        mock_resp.reason = "OK"
        mock_resp.cookies = []
        session._client.request = MagicMock(return_value=mock_resp)

        await session.get("https://example.com", follow_redirects="never")
        call_kwargs = session._client.request.call_args
        assert call_kwargs.kwargs.get("follow_redirects") is False

    @pytest.mark.asyncio
    async def test_bool_passed_through(self):
        session = HTTPSession()
        session._client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.headers = {}
        mock_resp.status_code = 200
        mock_resp.content = b"ok"
        mock_resp.url = "https://example.com"
        mock_resp.reason = "OK"
        mock_resp.cookies = []
        session._client.request = MagicMock(return_value=mock_resp)

        await session.get("https://example.com", follow_redirects=False)
        call_kwargs = session._client.request.call_args
        assert call_kwargs.kwargs.get("follow_redirects") is False


# ============================================================================
# 来源：test_the_robots_side_channel_does_not_lose_g31.py
# ============================================================================
# R01-020 — the robots side channel must not be the one path that forgets G31.

ROBOTS_BODY = b"User-agent: *\nDisallow: /private\n"


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = ROBOTS_BODY if self.path.startswith("/robots.txt") else b"secret page\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _serve():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


@pytest.fixture
def local_site(monkeypatch):
    """A live loopback server + a dead proxy in the environment, NO_PROXY cleared.

    This is the shape that reproduces the defect: the proxy is what the robots
    fetch picks up when the loopback guard has not run yet, and it can never reach
    this machine's own loopback.
    """
    httpd, base = _serve()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv(robots.ENV_IGNORE_ROBOTS, raising=False)
    # Both halves of the door: the store is what the tool call re-derives, the
    # contextvar is what a direct robots.check() sees.
    identity.set_value("allow_private", True)
    security.set_allow_private(True)
    robots.reset_robots_cache()
    try:
        yield base
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def plain_site(monkeypatch):
    """The same server with no proxy anywhere — the case where the file reads fine
    and only the disclosure is at stake."""
    httpd, base = _serve()
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                 "ALL_PROXY", "NO_PROXY", "no_proxy", robots.ENV_IGNORE_ROBOTS):
        monkeypatch.delenv(name, raising=False)
    identity.set_value("allow_private", True)
    security.set_allow_private(True)
    robots.reset_robots_cache()
    try:
        yield base
    finally:
        httpd.shutdown()
        httpd.server_close()


# ─── A · the route: loopback robots.txt is fetched direct ───────────────────

def test_the_robots_fetch_applies_the_loopback_guard_before_dialling(monkeypatch):
    """`_blocking_fetch` builds its own client, so it owns the duty to have the
    G31 rule in the environment at that moment — not at some later import.

    The env is cleared here rather than in the fixture: in a real fresh process
    (probe-measured: `python -c "import dhole_mcp.server"` leaves NO_PROXY unset)
    nothing has merged the exemption yet when the robots check runs, and a test
    that inherits an already-guarded process proves nothing.
    """
    seen = {}

    class _Client:
        def __init__(self, **kwargs):
            seen["no_proxy"] = str(os.environ.get("NO_PROXY") or "")
            seen["proxy_kw"] = kwargs.get("proxy")

        def get(self, url, headers=None):
            raise RuntimeError("stubbed before the network")

    import primp

    monkeypatch.setattr(primp, "Client", _Client)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    robots._blocking_fetch("http://127.0.0.1:9/robots.txt", None, 3.0)
    assert "no_proxy" in seen, "primp was never reached - the stub did not run"
    for entry in _LOOPBACK_NO_PROXY_ENTRIES:
        assert entry in seen["no_proxy"], (
            f"primp built its client with NO_PROXY={seen['no_proxy']!r}: the loopback "
            "exemption the fetcher relies on was not in place, so this robots.txt "
            "fetch is handed to a proxy that cannot reach the caller's own machine")


def test_rules_are_enforced_on_the_first_private_call(local_site):
    """End to end, against a live server: the Disallow in that file must actually
    stop the fetch. Pre-fix this returns allowed=True / reason=unavailable."""
    v = asyncio.run(robots.check(f"{local_site}/private/secret"))
    assert v.reason == robots.DISALLOWED, v
    assert v.allowed is False


def test_web_read_of_a_disallowed_private_path_is_blocked(local_site):
    """Same fact seen through the tool the user calls, with the receipt it owes."""
    payload = asyncio.run(MasterFetchServer(cache_ttl=0)._dispatch(
        "web_read", {"url": f"{local_site}/private/secret"}))
    pay = payload[1] if isinstance(payload, tuple) and len(payload) == 2 else {}
    assert "robots_disallowed" in str(pay.get("error") or ""), pay.get("error")
    assert pay.get("content") in ([], None, ""), "a Disallow path came back with a body"


# ─── B · the disclosure says which thing happened ───────────────────────────

def test_a_policy_refusal_is_not_reported_as_an_unreadable_file(monkeypatch):
    """Door closed → dhole itself refuses to dial the robots URL. Nothing about the
    file is unreadable; the refusal is ours, and it must be said as ours."""
    monkeypatch.delenv(robots.ENV_IGNORE_ROBOTS, raising=False)
    security._ALLOW_PRIVATE.set((frozenset(), False))
    robots.reset_robots_cache()
    v = asyncio.run(robots.check("http://127.0.0.1:1/anything"))
    assert v.allowed is True  # fail-open stays the documented policy
    assert v.reason == robots.UNAVAILABLE
    assert v.unavailable_reason == robots.REFUSED_BY_POLICY, v


def test_a_broken_fetch_is_still_reported_as_no_answer():
    """The other half of the split: when the file really is unreachable, the old
    honest sentence remains — the fix must not blur the two directions."""
    async def _boom(url, proxy):
        raise OSError("connection_reset")

    cache = robots.RobotsCache(fetch=_boom)
    v = asyncio.run(cache.allowed("https://example.com/x"))
    assert v.allowed is True and v.reason == robots.UNAVAILABLE
    assert v.unavailable_reason == robots.FETCH_FAILED, v


def test_the_labels_differ_and_only_one_blames_the_file(monkeypatch):
    from dhole_mcp.server import _robots_mode_label

    monkeypatch.delenv(robots.ENV_IGNORE_ROBOTS, raising=False)
    refused = robots.Verdict(True, robots.UNAVAILABLE, "",
                             unavailable_reason=robots.REFUSED_BY_POLICY)
    broken = robots.Verdict(True, robots.UNAVAILABLE, "",
                            unavailable_reason=robots.FETCH_FAILED)
    refused_label = _robots_mode_label(False, refused)
    broken_label = _robots_mode_label(False, broken)
    assert "unreadable" not in refused_label, refused_label
    assert "dhole" in refused_label.lower(), refused_label
    assert broken_label == "not checked (robots.txt unreadable)", broken_label
    assert _robots_mode_label(True, None).startswith("bypassed (ignore_robots")
    assert _robots_mode_label(False, robots.Verdict(True, robots.ALLOWED, "")) == "complied"


def test_a_read_of_the_rules_file_itself_is_not_claimed_unreadable(plain_site):
    """P04's S14/S16: the call that read /robots.txt and got 200 still said the
    file was unreadable. With the rules actually consulted the label is complied —
    nothing in that file disallows the path it describes."""
    payload = asyncio.run(MasterFetchServer(cache_ttl=0)._dispatch(
        "web_read", {"url": f"{plain_site}/robots.txt"}))
    pay = payload[1] if isinstance(payload, tuple) and len(payload) == 2 else {}
    assert pay.get("status") == 200, pay
    label = str((pay.get("metadata") or {}).get("robots"))
    assert "unreadable" not in label, label
    assert label == "complied", label
