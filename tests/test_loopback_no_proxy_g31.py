"""G31 — loopback targets must never ride a proxy.

primp (reqwest underneath), httpx (trust_env on by default) and the browser all
read proxy config from the environment. With a system proxy / fake-IP TUN set
up, a fetch to 127.0.0.1 / localhost was handed to the proxy — which cannot
reach the caller's own loopback and answers 502, so every local-dev fetch died
with a misleading "server returned error status". Measured: with a dead proxy
in the env, a LIVE local server was unreachable; without proxy env vars it
answered 200.

The fix merges loopback hosts into NO_PROXY at fetcher import (a proxy can
never reach this machine's loopback, so the exclusion is safe unconditionally),
plus a TCP preflight for forced-stealthy calls so an unreachable private target
fails fast instead of burning the whole timeout in the browser tier (measured:
127.0.0.1:1 cost 45s pre-fix, 2.9s post-fix).
"""

import os
import threading
import http.server
import socketserver

import pytest

from dhole_mcp import identity
from dhole_mcp.fetcher import (
    HTTPSession,
    _LOOPBACK_NO_PROXY_ENTRIES,
    _ensure_loopback_no_proxy,
)


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

from dhole_mcp.server import MasterFetchServer

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
