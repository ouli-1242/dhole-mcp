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
3. host scope: a SECOND host asked about on the wire is refused until it is
   approved for itself (per-host ask, and a refusal never writes the roster),
   and a navigation that leaves the authorized hostname inside an attached
   session never reaches the other origin (the local site keeps a per-Host
   ledger, so "it did not arrive" is proved by the side it was aimed at);
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

            # ── 0. §3-17 的一半：私有主机的门只能存，不能逐次传 ──
            # 17.0 之前这三条线都靠 web_read(allow_private=true) 敲开 127.0.0.1；
            # 现在那个键在参数面上是陌生键。"拒绝而不是吞掉"必须由 wire 作证，
            # 否则整套默认身份只剩单元测试里的那句断言。
            gate_home = tmp_path / "home-refuse"
            client = _WireClient(gate_home, {})
            try:
                payload = _payload(client.call_tool("web_read", {
                    "url": f"{site.base_url}/home",
                    "allow_private": True,
                }))
            finally:
                client.close()
            assert "Unsupported argument" in payload.get("error", ""), \
                f"allow_private 该被拒成陌生键: {payload}"
            assert "allow_private" in payload.get("error", ""), \
                "拒绝必须点名被拒的那个键"
            assert not (gate_home / "identity.db").exists(), \
                "被拒的调用不该在盘上留下任何东西"

            # ── 1. gate: 未授权主机被拒绝，且什么都没执行、什么都没落盘 ──
            gate_home = tmp_path / "home-gate"
            client = _WireClient(gate_home, {})
            try:
                _payload(client.call_tool("cred_defaults", {"set": {"allow_private": True}}))
                payload = _payload(client.call_tool("web_read", {
                    "url": f"{site.base_url}/home",
                    "cdp_url": endpoint,
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
                stored = _payload(client.call_tool("cred_defaults",
                                                   {"set": {"allow_private": True}}))
                assert "allow_private" in json.dumps(stored), stored
                payload = _payload(client.call_tool("web_read", {
                    "url": f"{site.base_url}/home",
                    "cdp_url": endpoint,
                    "confirm": True,
                    "check_login": True,
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

            # ── 3. host scope: 会话内部跳到白名单外的**另一个主机名** ──
            # 顶层一个没授权过的主机走的是"先问一次"那一层（§1 钉的就是它），围栏层
            # 管的是 attach 会话里被重定向走的那一跳 —— 两个形状不同。这一段从前把
            # 前者当成后者，于是断言了一个这条路径永远走不到的错误码（D-30）。
            client = _WireClient(attach_home, {})   # 复用已授权过 127.0.0.1 的家
            try:
                # 存下来的身份默认**按进程**活（`DHOLE_PERSIST_CREDENTIALS=1` 才落盘），
                # 所以这个新进程要自己敲一次 127.0.0.1 的门 —— 上一段那次存储不属于
                # 这里（实测：不存就直接吃 `invalid_request: URL targets internal/private IP`）。
                _payload(client.call_tool("cred_defaults",
                                          {"set": {"allow_private": True}}))
                payload = _payload(client.call_tool("web_read", {
                    "url": "https://example.com/",
                    "cdp_url": endpoint,
                }))
                n0 = len(site._server.hits)
                # `force_fetcher="stealthy"` 是把这一段判据钉在**attach 的那台浏览器**上：
                # 不加它，读档先走 HTTP 档，而 HTTP 档会跟着这条 302 打到名册外的主机名
                # （它没有 cookie，名册不是它的合同）。混在一个窗口里量，量到的就不是这一刀
                # 要判的那件事。UA 不是判别器（实测 `.scratch/liveverify/fence_attrib.py`：
                # 两条栈都会发 Windows/Macintosh 的 Chrome UA，primp 的指纹还会一轮一变），
                # 所以判据按**代码路径**下：窗口里一笔 `Host: localhost` 都不许有。
                # 这一把也量出了第二件事：attach 路径上私网目标的探活（`_private_health_probe`）
                # 原先会跟着那一跳跑出去，账上多一笔与围栏无关的 `Host: localhost`。它现在不跟
                # 重定向（3xx 本身就是"有人应答"），所以这条断言覆盖的是**整个调用窗口**，
                # 不只是浏览器那一侧。
                fence = _payload(client.call_tool("web_read", {
                    "url": f"{site.base_url}/fence",
                    "cdp_url": endpoint,
                    "force_fetcher": "stealthy",
                }))
                n1 = len(site._server.hits)
                fence_img = _payload(client.call_tool("web_read", {
                    "url": f"{site.base_url}/fence-img",
                    "cdp_url": endpoint,
                    "force_fetcher": "stealthy",
                }))
                n2 = len(site._server.hits)
                img_self = _payload(client.call_tool("web_read", {
                    "url": f"{site.base_url}/img-self",
                    "cdp_url": endpoint,
                    "force_fetcher": "stealthy",
                }))
                n3 = len(site._server.hits)
            finally:
                client.close()
            hits = site._server.hits
            nav_out = [h for (_p, h) in hits[n0:n1] if "localhost" in h]
            sub_out = [h for (_p, h) in hits[n1:n2] if "localhost" in h]
            self_paths = [p for (p, _h) in hits[n2:n3]]
            print("\nNAV window:", json.dumps(hits[n0:n1]))
            print("SUB window:", json.dumps(hits[n1:n2]))
            print("SELF window (同主机图片对照):", json.dumps(hits[n2:n3]))
            print("FENCE refusal said:", str(fence.get("error"))[:260], flush=True)

            # 三件事，形状各不相同：
            # 1) 同主机的图片一笔没到 —— route handler **装了也在跑**（`disable_resources`
            #    那一支先把它掐了）。留着它是为了在围栏失败时区分"没装"与"没管到"。
            assert "/pixel.png" not in self_paths, (
                f"同主机的图片本该被 disable_resources 掐掉: {self_paths}")
            # 2) 子资源形状：那张 localhost 的图也没到（这一条不能算围栏的功 —— 它同样
            #    会被上面那一支先掐掉，所以只作记录）。
            # 3) 结果边界：落在授权名册外的那一页，不许当成"你点名的那一页"交回去 ——
            #    那是 attach 最容易被读错的一种答案。
            assert "cdp_host_not_allowed" in (fence.get("error") or ""), (
                f"围栏外的正文被当成结果交回来了: {fence}")
            assert not (fence.get("content") or []), (
                f"拒绝不许带正文: {fence}")
            # 4) 请求边界（D-31b，2026-10-09 起成立）：主文档那一跳**不许再出现在账上**。
            #    这颗钉子原本钉的是反面（"确实出去了"），为的是哪天机制变了就红一下，
            #    逼着把 configuration.md 那句 "What it cannot do" 一起改掉 —— 今天机制真
            #    变了，所以它翻到这一头，手册同批改。将来翻回来时也别顺手删：两面都是判据。
            assert not nav_out, (
                f"名册外那一跳还是发出去了（请求边界的 Fetch 监听器没生效？）: {nav_out}")
            assert "ended before it left the browser" in (fence.get("error") or ""), (
                f"拒绝的话要说清这一跳没出去，别退回旧那句: {fence}")

            assert "cdp_authorization_required" in payload.get("error", ""), (
                "第二个主机也要各问一次，不能被第一个主机的批准顺带放行: "
                f"{payload}")
            authorized = json.loads(
                (attach_home / "cdp_hosts.json").read_text(encoding="utf-8"))["hosts"]
            assert "example.com" not in authorized, (
                f"一次被拒的顶层请求不该把主机记进名册: {authorized}")

            # ── 3b. 装不上那一支（D-39）：退路必须既拒绝、又说实话、又不留半只钩子 ──
            # §3 里这条一直挂着"未验"：`BrowserSession.fetch` 那三百行只在**装得上**的那一支
            # 被真机走过，单元测试量的是 `_DocumentFence` 自己与那条接线的形状。这里把同一台
            # Chrome、同一个靶站再用一次，只把 `_DocumentFence.install` 换成一定抛的那一只，
            # 于是四件事第一次被真发一遍：
            #   ⓪ 门的**顺序**：这一发第一次量到"私有门先说话"。靶站在回环上，名册外那一跳
            #      指向的也是回环（localhost:同一端口），所以没敲开 `allow_private` 时交回来的是
            #      `ssrf_blocked: … (localhost:PORT)`，`cdp_host_not_allowed` 那句压根没轮到。
            #      这不是这次注入造成的形状，是 wire 那三段一直靠 `cred_defaults(set=allow_private)`
            #      才绕开的第一道门 —— 现在两面都有了真机账。
            #   ① 敲开那道门之后，名册外那一跳**确实出去了** —— 由靶站的 Host 账作证，不靠回执自述；
            #   ② 正文仍然被结果边界拒掉，而那句拒绝改成"已经离开浏览器"（不许还说"在发出
            #      之前终止"——那是装得上的那一支才有的功劳）；
            #   ③ 摘得干净的证据是**这台浏览器之后还能用**：D-31b 在真机上量过"每一笔 paused
            #      必须应答，否则导航吊到 20 秒"，留了半只钩子的话，随后那一发 /home 出不来。
            import contextlib

            from dhole_mcp import browser as _browser_mod
            from dhole_mcp.browser import CDPHostNotAllowedError
            from dhole_mcp.security import allow_private_scope

            async def _drive_install_failure(grant_private: bool):
                real = _browser_mod._DocumentFence.install

                async def _boom(self):
                    raise RuntimeError("Fetch.enable refused (injected by D-39)")

                _browser_mod._DocumentFence.install = _boom
                out, hits = {}, []
                try:
                    scope = allow_private_scope(True) if grant_private \
                        else contextlib.nullcontext()
                    with scope:
                        session = _browser_mod.BrowserSession(
                            cdp_url=endpoint, headless=True, humanize=False,
                            allowed_hosts=frozenset({"127.0.0.1"}))
                        await session.start()
                        n = len(site._server.hits)
                        try:
                            await session.fetch(f"{site.base_url}/fence")
                            out["refusal"] = "<no exception: the body came back>"
                        except CDPHostNotAllowedError as exc:
                            out["refusal"] = str(exc)
                        except Exception as exc:        # 形状变了也要看得见
                            out["refusal"] = f"{type(exc).__name__}: {exc}"
                        hits = list(site._server.hits[n:])
                        # 同一台浏览器随后那一发：半只钩子会把它吊死在这里。
                        resp = await session.fetch(f"{site.base_url}/home")
                        out["follow_status"] = getattr(resp, "status", 0)
                        out["follow_bytes"] = len(resp.body or b"")
                        await session.close()
                finally:
                    _browser_mod._DocumentFence.install = real
                return out, hits

            blocked, blocked_hits = asyncio.run(
                asyncio.wait_for(_drive_install_failure(False), timeout=90))
            fell, fell_hits = asyncio.run(
                asyncio.wait_for(_drive_install_failure(True), timeout=90))
            print("\nD-39 (no allow_private) said:", str(blocked.get("refusal"))[:200])
            print("D-39 (install refused) said:", str(fell.get("refusal"))[:260])
            print("D-39 windows:", json.dumps({"private_closed": blocked_hits,
                                               "fenced": fell_hits}), flush=True)

            # ⓪ 私有门在前：它说话时不该带围栏那句
            assert "ssrf_blocked" in str(blocked.get("refusal")), blocked
            assert "cdp_host_not_allowed" not in str(blocked.get("refusal")), blocked
            assert blocked.get("follow_status") == 200, (
                f"被 SSRF 拦下也不许把这台浏览器用废: {blocked}")
            # 两轮账上都有那一跳 —— 这正是"没有请求边界"的意思：两道门都在**它出去之后**
            # 才说话，区别只是各自的名字与给谁的出路。装得上那一支（上面第 3 段）账上是空的。
            assert any("localhost" in str(h) for (_p, h) in blocked_hits), (
                f"围栏缺席时那一跳该真的出去: {blocked_hits}")

            refusal = str(fell.get("refusal"))
            assert "cdp_host_not_allowed" in refusal, (
                f"装不上围栏时，结果边界必须仍然把名册外那一页拒掉: {refusal}")
            assert "already left the browser" in refusal, (
                f"装不上那一支要说实话（旧那句是'在发出之前终止'）: {refusal}")
            assert "ended before it left" not in refusal, (
                f"这一支没有请求边界可言，不许冒领: {refusal}")
            assert any("localhost" in str(h) for (_p, h) in fell_hits), (
                f"退路里那一跳本该真的出去（靶站账上一笔都没有）: {fell_hits}")
            assert fell.get("follow_status") == 200, (
                f"install 失败之后这台浏览器不能废掉（半只钩子的形状）: {fell}")
            assert fell.get("follow_bytes", 0) > 0, fell

            # ── 4. bulk: urls= 与 cdp_url= 不兼容，必须在闸口之前拒绝 ──
            bulk_home = tmp_path / "home-bulk"
            client = _WireClient(bulk_home, {})
            try:
                _payload(client.call_tool("cred_defaults", {"set": {"allow_private": True}}))
                payload = _payload(client.call_tool("web_read", {
                    "urls": [f"{site.base_url}/home"],
                    "cdp_url": endpoint,
                    "confirm": True,
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
