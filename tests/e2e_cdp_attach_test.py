"""MVP-0 falsification experiment for the CDP-attach main path — RUN MANUALLY.

The plan's first load-bearing question: does ``connect_over_cdp`` +
``contexts[0]`` actually see the login state of the attached Chrome profile,
while ``new_context()`` (the code dhole shipped until G33) gets an isolated,
cookie-less profile?  Everything downstream (L1 auth strategy, MVP-1 scope)
was staked on the answer being yes — so it is verified against a REAL Chrome,
not mocked.

The test is fully self-contained (no user login, no real sites):

1. Start a throwaway HTTP login service on 127.0.0.1 (POST /login sets a
   session cookie; GET /whoami reports whether the cookie came back).
2. Launch the locally installed Chrome on a COPY profile
   (``--user-data-dir=<tmp>`` — Chrome 136+ forbids remote debugging on the
   default profile) with ``--remote-debugging-port``, drive its default
   context through the login form, verify the cookie exists, then DISCONNECT.
3. Relaunch Chrome on the SAME profile (fresh process — this is also what
   proves persistence) and compare, through CDP:
   - ``contexts[0]``  → must still be logged in          (the plan's bet)
   - ``new_context()`` → must NOT be logged in           (the old bug)
4. Drive dhole's own fixed path (``BrowserSession(cdp_url=...)``) against the
   same endpoint: attach must see the login, the login probe (G35) must answer
   ``logged_in=True``, and ``close()`` must DETACH without killing Chrome.

Run manually:
    pytest -m e2e tests/e2e_cdp_attach_test.py -v

Skips cleanly when no Chrome / no patchright — never fails CI.
"""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytestmark = pytest.mark.e2e

_USERNAME = "e2e"
_PASSWORD = "secret123"
_COOKIE_NAME = "dh_sid"
_COOKIE_VALUE = "e2e-session-1"

_LOGIN_HTML = """<!doctype html><html><body>
<form method="POST" action="/login">
  <input name="username" placeholder="username">
  <input name="password" type="password" placeholder="password">
  <button type="submit">Sign in</button>
</form>
</body></html>"""


class _LoginSiteHandler(BaseHTTPRequestHandler):
    """A fake site with a login wall. POST /login (correct credentials) sets a
    session cookie; GET /whoami answers with the truth about the cookie."""

    def log_message(self, *args):  # keep test output clean
        pass

    def _send(self, code: int, body: str, headers=()):
        data = body.encode("utf-8")
        self.send_response(code)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/whoami"):
            cookie = self.headers.get("Cookie") or ""
            logged = f"{_COOKIE_NAME}={_COOKIE_VALUE}" in cookie
            self._send(200, json.dumps({"logged_in": logged}))
        elif self.path.startswith("/home"):
            # A DOM-rich page for the G35 login probe: a logged-in view shows
            # a Sign-out control; the logged-out view IS the login form.
            cookie = self.headers.get("Cookie") or ""
            if f"{_COOKIE_NAME}={_COOKIE_VALUE}" in cookie:
                self._send(200, '<html><body><nav><a href="/logout">Sign out</a>'
                                '</nav><main id="app">welcome back</main></body></html>')
            else:
                self._send(200, _LOGIN_HTML)
        else:
            self._send(200, _LOGIN_HTML)

    def do_POST(self):
        if not self.path.startswith("/login"):
            self._send(404, "not found")
            return
        length = int(self.headers.get("Content-Length") or 0)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
        ok = (form.get("username", [""])[0] == _USERNAME
              and form.get("password", [""])[0] == _PASSWORD)
        if ok:
            # Max-Age matters: a cookie without it is a *session* cookie, which
            # Chrome keeps in memory only — it would vanish in the relaunch and
            # the experiment would measure cookie persistence, not context
            # isolation.
            self._send(303, "", [
                ("Location", "/whoami"),
                ("Set-Cookie", f"{_COOKIE_NAME}={_COOKIE_VALUE}; Path=/; Max-Age=86400; HttpOnly"),
            ])
        else:
            self._send(401, _LOGIN_HTML)


class _LoginSite:
    """Threaded local server on an ephemeral port."""

    def __init__(self):
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _LoginSiteHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()


def _json_from_html(text: str) -> dict:
    """dhole's Response.body is the full page HTML; pull the JSON payload out."""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise AssertionError(f"no JSON payload in page body: {text[:200]!r}")
    return json.loads(text[start:end + 1])


def _find_chrome() -> str | None:
    candidates = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    found = shutil.which("chrome") or shutil.which("chromium") or shutil.which("google-chrome")
    return found


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _launch_chrome(chrome_exe: str, profile_dir: str):
    """Start Chrome with remote debugging on a COPY profile; return
    (http_endpoint, process). The endpoint is parsed from Chrome's own
    'DevTools listening on ws://...' stderr line — never assume the port."""
    port = _free_port()
    proc = subprocess.Popen(
        [chrome_exe, f"--user-data-dir={profile_dir}",
         f"--remote-debugging-port={port}",
         "--headless=new", "--no-first-run", "--no-default-browser-check",
         "--disable-gpu", "--disable-extensions", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    deadline = time.time() + 30
    endpoint = None
    while time.time() < deadline:
        line = proc.stderr.readline() if proc.stderr else ""
        if "DevTools listening on" in line:
            ws = line.split("listening on", 1)[1].strip()
            # ws://127.0.0.1:PORT/devtools/browser/UUID -> http://127.0.0.1:PORT
            endpoint = ws.replace("ws://", "http://").split("/devtools")[0]
            break
        if proc.poll() is not None:
            break
    if not endpoint:
        proc.kill()
        pytest.skip("Chrome did not expose a DevTools endpoint (headless flag unsupported?)")
    return endpoint, proc


def _stop_chrome(proc) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _require_browser_deps():
    try:
        import patchright  # noqa: F401
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"patchright not importable: {exc}")
    chrome = _find_chrome()
    if not chrome:
        pytest.skip("no locally installed Chrome/Chromium found")
    return chrome


def test_cdp_attach_sees_login_state_that_new_context_misses():
    """The MVP-0 falsification: contexts[0] holds the profile's login state,
    new_context() does not. If the first assert fails, the whole CDP-attach
    main path is falsified and MVP-1 must be rescoped (per the plan)."""
    chrome = _require_browser_deps()
    from patchright.async_api import async_playwright

    async def drive():
        with _LoginSite() as site, tempfile.TemporaryDirectory() as workdir:
            profile = os.path.join(workdir, "cdp-profile")
            results = {}

            # ── Phase A: log in on the copy profile, then detach ──
            endpoint_a, proc_a = _launch_chrome(chrome, profile)
            pw = await async_playwright().start()
            browser = await pw.chromium.connect_over_cdp(endpoint_a)
            assert browser.contexts, "attached Chrome exposes no default context"
            ctx = browser.contexts[0]
            page = await ctx.new_page()
            await page.goto(site.base_url + "/")
            await page.fill('input[name="username"]', _USERNAME)
            await page.fill('input[name="password"]', _PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_load_state("domcontentloaded")
            body = json.loads(await page.inner_text("body"))
            assert body["logged_in"] is True, "local login site did not accept the login"
            cookie_names = {c.get("name") for c in (await ctx.cookies())}
            assert _COOKIE_NAME in cookie_names, "session cookie was not set"

            # Clean shutdown is load-bearing: Chrome batches cookie writes to
            # disk (~30s timer, or on shutdown). TerminateProcess would skip
            # the flush and the experiment would measure cookie loss, not
            # context isolation.
            try:
                cdp = await browser.new_browser_cdp_session()
                await cdp.send("Browser.close")
            except Exception:
                pass
            try:
                await browser.close()  # disconnect (may already be dead)
            except Exception:
                pass
            await pw.stop()
            try:
                proc_a.wait(timeout=15)
            except subprocess.TimeoutExpired:
                _stop_chrome(proc_a)

            # ── Phase B: fresh Chrome on the SAME profile, compare both APIs ──
            endpoint_b, proc_b = _launch_chrome(chrome, profile)
            try:
                pw2 = await async_playwright().start()
                browser2 = await pw2.chromium.connect_over_cdp(endpoint_b)

                # dhole's own G33 helper must pick the default context (批次 8:
                # 报不出 context 时它现在拒答，不再退回 new_context)。
                from dhole_mcp.browser import _resolve_cdp_context
                ctx0 = await _resolve_cdp_context(browser2)
                assert ctx0 is browser2.contexts[0], (
                    "_resolve_cdp_context did not reuse contexts[0]")

                p0 = await ctx0.new_page()
                await p0.goto(site.base_url + "/whoami")
                results["default_context"] = json.loads(await p0.inner_text("body"))
                results["default_context_cookies"] = len(await ctx0.cookies())

                # The pre-G33 behavior: an isolated, cookie-less profile.
                fresh_ctx = await browser2.new_context()
                pfresh = await fresh_ctx.new_page()
                await pfresh.goto(site.base_url + "/whoami")
                results["new_context"] = json.loads(await pfresh.inner_text("body"))

                # ── Phase C: dhole's fixed BrowserSession end to end ──
                from dhole_mcp.browser import BrowserSession
                session = BrowserSession(cdp_url=endpoint_b, headless=True, humanize=False)
                await session.start()
                assert session.cdp_attached is True, "BrowserSession did not mark attach"
                resp = await session.fetch(site.base_url + "/whoami")
                # dhole's fetch returns its own Response (body is bytes).
                results["dhole_session"] = _json_from_html(
                    (resp.body or b"").decode("utf-8", "replace"))
                probe = {}
                await session.fetch(site.base_url + "/home", probe_out=probe)
                results["login_probe"] = dict(probe)
                await session.close()
                # close() must DETACH, not kill the user's browser.
                results["chrome_alive_after_close"] = proc_b.poll() is None

                await browser2.close()
                await pw2.stop()
            finally:
                _stop_chrome(proc_b)

            return results

    results = asyncio.run(asyncio.wait_for(drive(), timeout=240))
    print(f"\nMVP-0 raw results: {json.dumps(results, default=str)}")

    # ── The verdicts ──
    assert results["default_context"]["logged_in"] is True, (
        "FALSIFIED: contexts[0] does not carry the profile's login state — "
        "the CDP-attach main path needs a rescope (plan, chapter 7).")
    assert results["new_context"]["logged_in"] is False, (
        "Expected new_context() to be ISOLATED; it saw the login cookie, so "
        "the isolation assumption behind the old code was wrong in our favor.")
    assert results["dhole_session"]["logged_in"] is True, (
        "dhole's attached BrowserSession cannot read the login state it "
        "attached for — the G33 fix did not survive integration.")
    assert results["login_probe"].get("logged_in") is True, (
        f"login probe did not confirm the login: {results['login_probe']}")
    assert results["chrome_alive_after_close"] is True, (
        "BrowserSession.close() killed the attached Chrome instead of "
        "detaching from it.")
    print(f"\nMVP-0 verdict: CONFIRMED — contexts[0] cookies="
          f"{results['default_context_cookies']}, new_context isolated, "
          f"dhole attach+probe OK, Chrome survived close().")
