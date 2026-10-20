"""R01-020 — the robots side channel must not be the one path that forgets G31.

Round 01 measured this on the local fixture: `web_read("http://127.0.0.1:8399/private
/secret")` came back 200 with the private body, and its receipt said
`metadata.robots = "not checked (robots.txt unreadable)"` — while the same tool read
that very file in 7ms and it says `Disallow: /private`. Four reproductions (P04
S14/S15/S16/S17, two of them cache-busted), plus the fixture's own request log:
**/robots.txt was never dialled on those calls.**

The mechanism is not the SSRF re-validation (with the private-host door open,
`validate_url` accepts the robots URL — measured). It is ordering: `fetcher.py:68`
merges loopback into `NO_PROXY` at import, and the robots check runs **before** any
tier has imported the fetcher. So the first private-target call of a process hands
`http://127.0.0.1:PORT/robots.txt` to the system proxy, which cannot reach the
caller's own loopback and answers 502 — fail-open on a 5xx is the documented policy,
so the rules go unenforced and the receipt blames the file for dhole's own route.

The disclosure is the second half: "unreadable" is only true of a broken *file*. When
dhole's own policy or its own route is why there is no answer, that is what the field
has to say.
"""

import asyncio
import http.server
import os
import threading

import pytest

from dhole_mcp import identity, robots, security
from dhole_mcp.fetcher import _LOOPBACK_NO_PROXY_ENTRIES
from dhole_mcp.server import MasterFetchServer

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
