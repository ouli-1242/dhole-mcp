"""Wire-level validation of the CDP-attach path over the REAL MCP stdio server.

The other e2e files drive dhole's classes directly; this one spawns the actual
``python -m dhole_mcp`` server — from src/ via PYTHONPATH, never a stale
installed wheel — and speaks the newline-JSON MCP protocol to it, so it
validates exactly what an agent client sees:

1. gate: ``cdp_url`` without ``DHOLE_CDP_ATTACH`` is refused on the wire;
2. attach: with ``DHOLE_CDP_ATTACH=1`` + ``DHOLE_CDP_HOSTS=127.0.0.1``,
   smart_fetch rides the attached Chrome's logged-in cookie — the response
   carries ``cdp_attached: true`` and ``login_check.logged_in: true``;
3. host scope: a URL outside ``DHOLE_CDP_HOSTS`` is refused on the wire;
4. audit: the attach attempt lands in the ``DHOLE_USAGE_LOG`` channel.

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
            "DHOLE_NO_BROWSER_PREWARM": "1",
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

            # ── 1. gate: attach refused without the explicit opt-in ──
            client = _WireClient(tmp_path / "home-gate", {})
            try:
                payload = _payload(client.call_tool("smart_fetch", {
                    "url": f"{site.base_url}/home",
                    "cdp_url": endpoint,
                    "allow_private": True,
                }))
            finally:
                client.close()
            assert "cdp_attach_disabled" in payload.get("error", ""), \
                f"expected the gate to refuse: {payload}"
            assert "DHOLE_CDP_ATTACH" in payload.get("error", ""), \
                "refusal must say how to enable"

            # ── 2. attach: the logged-in cookie rides the wire call ──
            client = _WireClient(tmp_path / "home-attach", {
                "DHOLE_CDP_ATTACH": "1",
                "DHOLE_CDP_HOSTS": "127.0.0.1",
            })
            try:
                payload = _payload(client.call_tool("smart_fetch", {
                    "url": f"{site.base_url}/home",
                    "cdp_url": endpoint,
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
            audit = (tmp_path / "home-attach" / "usage.jsonl").read_text(encoding="utf-8")
            assert "cdp_attach" in audit, "attach attempt missing from the audit log"

            # ── 3. host scope: off-fence navigation refused on the wire ──
            client = _WireClient(tmp_path / "home-scope", {
                "DHOLE_CDP_ATTACH": "1",
                "DHOLE_CDP_HOSTS": "127.0.0.1",
            })
            try:
                payload = _payload(client.call_tool("smart_fetch", {
                    "url": "https://example.com/",
                    "cdp_url": endpoint,
                }))
            finally:
                client.close()
            assert "cdp_host_not_allowed" in payload.get("error", ""), \
                f"expected host scope to refuse example.com: {payload}"
        finally:
            _stop_chrome(chrome_proc)

    print("\nWire verdict: gate refusal, attach + login probe, host scope, "
          "audit — all confirmed over the real MCP stdio protocol.")
