"""Wire-level validation of the CDP-attach path over the REAL MCP stdio server.

The other e2e files drive dhole's classes directly; this one spawns the actual
``python -m dhole_mcp`` server — from src/ via PYTHONPATH, never a stale
installed wheel — and speaks the newline-JSON MCP protocol to it, so it
validates exactly what an agent client sees:

1. gate: ``cdp_url`` for a host the user has never authorized is refused on the
   wire with ``cdp_authorization_required`` -- and NOTHING executes, so the
   authorization file stays absent;
2. ask-then-remember: the same call with ``confirm: true`` attaches, rides the
   logged-in Chrome's cookie (``cdp_attached: true``, ``login_check.logged_in:
   true``), and writes the host into ``<DHOLE_HOME>/cdp_hosts.json``;
3. host scope: a URL outside the authorized hosts is refused on the wire;
4. bulk: ``urls=`` + ``cdp_url=`` is refused (the attach is per call, so a batch
   would otherwise authorize a host and then fetch every page un-attached);
5. audit: the refusal and the authorization both land in the ``DHOLE_USAGE_LOG``
   channel.

Run manually:
    pytest -m e2e tests/e2e_cdp_attach_wire_test.py -v

Skips cleanly when no Chrome / no patchright. Never runs in CI.
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

# Helpers from the falsification test: fake login site + Chrome launcher.
from tests.e2e_cdp_attach_test import (  # noqa: E402
    _COOKIE_NAME,
    _COOKIE_VALUE,
    _LoginSite,
    _launch_chrome,
    _require_browser_deps,
    _stop_chrome,
)

_SRC_DIR = Path(__file__).resolve().parents[1] / "src"


class _WireClient:
    """Minimal newline-JSON MCP client for one server process."""

    def __init__(self, home: Path, extra_env: dict):
        env = {
            **os.environ,
            "PYTHONPATH": str(_SRC_DIR),
            "DHOLE_HOME": str(home),
            "DHOLE_IGNORE_ROBOTS": "1",
            # 17.0：启动预热没有开关（DHOLE_NO_BROWSER_PREWARM 已注销），所以这里
            # 会有一次后台 stealthy 预热与本用例自己拉的 Chrome 并存。它整段
            # best-effort 且吞掉所有异常，不能当成"已经被关掉"来断言。
            "DHOLE_USAGE_LOG": "1",
        }
        env.update(extra_env)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "dhole_mcp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env, cwd=str(_SRC_DIR.parent),
            text=True,
        )
        self._id = 0
        # MCP handshake first: tools/call before initialize is refused by the
        # protocol layer (-32602) no matter how valid the arguments are.
        self._rpc("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "cdp-wire-e2e", "version": "1.0"},
        })

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        want = self._id
        msg: dict = {"jsonrpc": "2.0", "id": want, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        deadline = time.time() + 180
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"dhole server exited with code {self.proc.returncode}")
            line = self.proc.stdout.readline()
            if not line:
                time.sleep(0.05)
                continue
            text = line.strip()
            if not text:
                continue
            data = json.loads(text)
            # Skip notifications and stale ids; answer only to OUR request.
            if data.get("id") != want:
                continue
            return data
        raise RuntimeError("dhole server did not answer within 180s")

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self._rpc("tools/call", {"name": name, "arguments": arguments})

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def _payload(response: dict) -> dict:
    """The tool's own JSON payload (the MCP result wraps it as content text)."""
    return json.loads(response["result"]["content"][0]["text"])


def _raw_text(response: dict) -> str:
    """Everything the server said for one tool call, flattened to searchable text."""
    return json.dumps(response, ensure_ascii=False)


async def _seed_cookie(endpoint: str, base_url: str) -> None:
    """Put the persistent session cookie into the running Chrome's default
    context (expires set → survives; no form drive needed here — the
    falsification test already proved the profile round-trip)."""
    from patchright.async_api import async_playwright

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(endpoint)
        ctx = browser.contexts[0]
        await ctx.add_cookies([{
            "name": _COOKIE_NAME, "value": _COOKIE_VALUE,
            "url": base_url + "/", "expires": time.time() + 86400,
        }])
        await browser.close()  # disconnect only
    finally:
        await pw.stop()


def test_cdp_attach_over_the_mcp_wire(tmp_path):
    chrome = _require_browser_deps()
    from patchright.async_api import async_playwright  # noqa: F401  (dep check)

    with _LoginSite() as site, __import__("tempfile").TemporaryDirectory() as workdir:
        endpoint, chrome_proc = _launch_chrome(chrome, os.path.join(workdir, "wire-profile"))
        try:
            asyncio.run(_seed_cookie(endpoint, site.base_url))

            # ── 1. gate: 未授权主机被拒绝，且什么都没执行、什么都没落盘 ──
            gate_home = tmp_path / "home-gate"
            client = _WireClient(gate_home, {})
            try:
                payload = _payload(client.call_tool("smart_fetch", {
                    "url": f"{site.base_url}/home",
                    "cdp_url": endpoint,
                    "allow_private": True,
                }))
            finally:
                client.close()
            assert "cdp_authorization_required" in payload.get("error", ""), \
                f"expected the gate to refuse: {payload}"
            assert "confirm=true" in payload.get("error", ""), \
                "refusal must say how to authorize"
            # 拒绝的语义是"什么都没执行"：授权名册不该因此出现
            assert not (gate_home / "cdp_hosts.json").exists(), \
                "a refused attach must not authorize the host"

            # ── 2. 先问再记: confirm=true 才 attach，并把主机落盘 ──
            attach_home = tmp_path / "home-attach"
            client = _WireClient(attach_home, {})
            try:
                payload = _payload(client.call_tool("smart_fetch", {
                    "url": f"{site.base_url}/home",
                    "cdp_url": endpoint,
                    "confirm": True,
                    "check_login": True,
                    "allow_private": True,
                }))
            finally:
                client.close()
            assert payload.get("cdp_attached") is True, \
                f"attach not marked visible in the response: {payload}"
            probe = payload.get("login_check") or {}
            assert probe.get("logged_in") is True, \
                f"login probe did not confirm the login: {probe}"
            assert _COOKIE_NAME in probe.get("cookie_names", []), \
                f"cookie NAMES missing from login_check: {probe}"
            assert _COOKIE_VALUE not in json.dumps(payload), \
                "cookie VALUE must never reach the wire"
            # "记"这一半只有在这条线上测里能验：文件真的写进了这次连接的 DHOLE_HOME
            remembered = json.loads(
                (attach_home / "cdp_hosts.json").read_text(encoding="utf-8"))
            assert "127.0.0.1" in remembered["hosts"], remembered
            audit = (attach_home / "usage.jsonl").read_text(encoding="utf-8")
            assert "cdp_authorize" in audit, "authorization missing from the audit log"
            assert "cdp_attach" in audit, "attach attempt missing from the audit log"

            # ── 3. host scope: 围栏外的导航在 route 层被拒 ──
            client = _WireClient(attach_home, {})   # 复用已授权过 127.0.0.1 的家
            try:
                payload = _payload(client.call_tool("smart_fetch", {
                    "url": "https://example.com/",
                    "cdp_url": endpoint,
                }))
            finally:
                client.close()
            assert "cdp_host_not_allowed" in payload.get("error", ""), \
                f"expected host scope to refuse example.com: {payload}"

            # ── 4. bulk: urls= 与 cdp_url= 不兼容，必须在闸口之前拒绝 ──
            bulk_home = tmp_path / "home-bulk"
            client = _WireClient(bulk_home, {})
            try:
                payload = _payload(client.call_tool("smart_fetch", {
                    "urls": [f"{site.base_url}/home"],
                    "cdp_url": endpoint,
                    "confirm": True,
                    "allow_private": True,
                }))
            finally:
                client.close()
            assert "cannot be combined with urls=" in json.dumps(payload), \
                f"bulk + attach should be refused, not downgraded: {payload}"
            assert not (bulk_home / "cdp_hosts.json").exists(), \
                "a refused bulk call must not leave a host authorized"
        finally:
            _stop_chrome(chrome_proc)

    print("\nWire verdict: gate refusal (nothing executed), ask-then-remember "
          "(attach + login probe + host on disk), host scope, bulk refusal, "
          "audit — all confirmed over the real MCP stdio protocol.")
