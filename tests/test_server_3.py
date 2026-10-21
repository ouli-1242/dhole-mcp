"""test_server_3.py —— 由 3 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import asyncio
import http.server
import socket
import threading
import types
import pytest
from dhole_mcp import fetcher as FT
from dhole_mcp import identity
from dhole_mcp.server import MasterFetchServer
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import time
import anyio


# ============================================================================
# 来源：test_one_browser_face_per_process_not_one_per_call.py
# ============================================================================
# W3：一场进程一副身份，而且这副身份必须是**这一次冒充的那一家**的。

PAGE = (b"<html><head><title>T</title></head><body><p>"
        + b"Real body text so the extractor has something to chew on. " * 4
        + b"</p></body></html>")

# 真 socket 组：`_offline_dns`（autouse）把 getaddrinfo 钉成公网地址，回环站点要在
# **导入时**抓住真解析器，否则夹具里拿到的是被钉住的那一个。
_REAL_GETADDRINFO = socket.getaddrinfo

CHROME_DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36")
IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.3 Mobile/15E148 "
             "Safari/604.1")


class _Recorder:
    def __init__(self):
        self.received: list[tuple[str, dict]] = []

    def handler(self):
        rec = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                rec.received.append((self.path,
                                     {k.lower(): v for k, v in self.headers.items()}))
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(PAGE)))
                self.end_headers()
                self.wfile.write(PAGE)
        return Handler

    def page_headers(self) -> dict:
        hits = [h for p, h in self.received if p.split("?")[0] == "/"]
        assert hits, f"页面从未被请求：{[p for p, _ in self.received]}"
        return hits[-1]


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    rec = _Recorder()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), rec.handler())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    # 只走**存下来的**那一份放行（先例：W1 的 echo 文件里记的那次 ContextVar 泄漏）。
    identity.set_value("allow_private", True)
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/", rec
    finally:
        srv.shutdown()
        srv.server_close()


def _stub_client(monkeypatch, sink: list[dict]) -> None:
    class _Client:
        def __init__(self, **kw):
            sink.append(kw)

    monkeypatch.setattr(FT.primp, "Client", _Client)


def _fake_browserforge(monkeypatch, generator) -> None:
    """把 `browserforge.headers` 换成一个能记参数、能拒参数的模块。

    `_stealthy_baseline` 是函数体里 import 的，所以 `sys.modules` 打桩正好接得住
    （与 conftest 里 `_nobody_launches_a_browser` 同一个理由：不打方法打 import 点）。
    """
    mod = types.ModuleType("browserforge.headers")
    mod.HeaderGenerator = generator
    pkg = types.ModuleType("browserforge")
    pkg.headers = mod
    monkeypatch.setitem(__import__("sys").modules, "browserforge", pkg)
    monkeypatch.setitem(__import__("sys").modules, "browserforge.headers", mod)


# ─── 1 · 一场进程一副脸 ──────────────────────────────────────────────────────

def test_eight_sessions_in_one_process_roll_one_face(monkeypatch):
    """缺陷的原文形状：每会话一摇 = 每次调用一摇（实测 8 个会话 7 副脸）。"""
    rolls = []

    def _rolling(family: str = ""):
        rolls.append(family)
        return {"user-agent": f"agent/{len(rolls)}", "referer-keeper": "x"}

    monkeypatch.setattr(FT, "_stealthy_baseline", _rolling)
    _stub_client(monkeypatch, [])

    async def _run():
        faces = []
        for _ in range(8):
            s = FT.HTTPSession(stealthy_headers=True)
            await s._init_client()
            faces.append(tuple(sorted(s._build_headers().items())))
        return faces

    faces = asyncio.run(_run())
    assert len(rolls) == 1, f"身份被摇了 {len(rolls)} 次（一场进程该是 1 次）"
    assert len(set(faces)) == 1, "同一进程里交出了多副身份 —— 就是每次调用重摇的形状"


def test_the_face_a_call_rolled_does_not_decide_the_next_call(site, monkeypatch):
    """整条路的对照：两次独立的 `web_read` 必须发同一副脸。

    前一条测的是 `HTTPSession`，这一条测的是"用户能感觉到的那件事"——他连点两次同一个
    站，站那边看到的浏览器不该换。身份在这里被钉成一份可数的替身：这条要量的是**管道**
    （备忘录有没有跨调用活着），抽签本身由 `test_a_real_roll_matches_the_family...` 那组
    量。挂真抽签测管道会偶发红：实测一次跑里两次调用的 `sec-ch-ua` 一个是 browserforge
    的、一个是 primp 自己的，而钉住之后 40 次重跑全绿。

    运输是回环 echo，**判词必须是公网**：这一条量的是"合成出来的那副脸跨调用活着"，
    而合成只在公网那一侧发生 —— 刀9-1 之后私网目标收不到 dhole 的模板（一枚编造的
    google referer 落进用户自己的服务日志不是反爬，是污染），于是备忘录根本不参与，
    两次调用各自落到 primp 自己的 Chrome 预设上（实测 144 → 147）。那不是漂移，是
    那条路本来就不该由 dhole 声称身份。私网那一侧的语义由
    `test_a_private_target_gets_no_invented_referrer.py` 钉，这里只把门钉回公网的答案。
    """
    face = {"user-agent": CHROME_DESKTOP_UA,
            "sec-ch-ua": '"Google Chrome";v="147", "Chromium";v="147"',
            "sec-ch-ua-platform": '"Windows"'}
    rolls: list[str] = []
    monkeypatch.setattr(FT, "_stealthy_baseline",
                        lambda family="": rolls.append(family) or dict(face))
    monkeypatch.setattr(MasterFetchServer, "_target_is_private",
                        lambda self, u: False)
    url, rec = site
    s = MasterFetchServer()
    asyncio.run(s._dispatch("web_read", {"url": url, "cache_ttl": -1}))
    first = rec.page_headers()
    n_first = len(rec.received)
    asyncio.run(s._dispatch("web_read", {"url": url, "cache_ttl": -1}))
    second = rec.page_headers()
    for name in ("user-agent", "sec-ch-ua", "sec-ch-ua-platform"):
        assert first.get(name) == second.get(name), (
            f"{name} 在两次调用之间换了：{first.get(name)!r} -> {second.get(name)!r}\n"
            f"第一发（含 {n_first} 请求）: {first}\n"
            f"第二发（累计 {len(rec.received)} 请求）: {second}\n"
            f"备忘录: {sorted(FT._PROCESS_IDENTITY)}")
    assert rolls == ["chrome"], f"身份被摇了 {rolls} 次（一场进程一家一次）"


def test_the_reset_forgets_the_face_so_the_suite_does_not_share_one(monkeypatch):
    """挂具必须能把进程级状态忘掉，否则第一条用例摇出的脸决定后面每条用例看到什么。"""
    calls = []
    monkeypatch.setattr(FT, "_stealthy_baseline",
                        lambda family="": calls.append(family) or {"user-agent": f"u{len(calls)}"})
    first = FT._process_identity("chrome")
    assert FT._process_identity("chrome") is first, "同一家族第二次该读备忘录"
    FT.reset_process_identity()
    assert FT._process_identity("chrome") is not first, "复位后必须重新摇"
    assert len(calls) == 2, calls


def test_each_family_gets_its_own_face(monkeypatch):
    """进程级 ≠ 全局一副：池子里换家族时，头要跟着这一次冒充的那一家走。"""
    seen = []
    monkeypatch.setattr(FT, "_stealthy_baseline",
                        lambda family="": {"user-agent": f"{family or '<unpinned>'}/1.0"})
    _stub_client(monkeypatch, [])

    async def _run():
        for value in ("chrome", "firefox", "chrome", "safari"):
            s = FT.HTTPSession(impersonate=value, stealthy_headers=True)
            await s._init_client()
            seen.append(s._build_headers()["user-agent"])
    asyncio.run(_run())
    assert seen == ["chrome/1.0", "firefox/1.0", "chrome/1.0", "safari/1.0"], seen


# ─── 2 · 摇出来的必须是这一家的、桌面的那一副 ────────────────────────────────

@pytest.mark.parametrize("value,expect", [
    ("chrome", "chrome"),
    ("chrome_124", "chrome"),          # primp 的版本化写法，家族还是那一家
    ("Firefox", "firefox"),
    ("safari", "safari"),
    ("opera", ""),                      # 生成器不认识的名字：不钉，别塞
    ("random", ""),
    (["chrome", "firefox"], ""),       # 池子在 `_init_client` 里已经被收成单值
    (None, ""),
])
def test_the_family_is_read_from_the_impersonate_value(value, expect):
    assert FT._header_family(value) == expect


def test_the_generator_is_asked_for_the_family_and_a_desktop(monkeypatch):
    """钉住的是两颗参数，而且钉的是**这次要冒充的那一家** —— 不是恒定 chrome。

    挂具交出的那副身份现在必须**能说出自己的 brand 表**（`Chrome/147` 配 `";v="147"`）：
    交一副声称 Chrome 而没有 `sec-ch-ua` 的字典会被 `_brands_follow_the_ua` 拦下重摇，
    那时这一格读到的最后一次调用就已经是兜底那一次裸调用，量的就不再是"钉没钉住"了。
    顺手多钉一句"自洽的一副只摇一次"。
    """
    asked = []

    class _Gen:
        def __init__(self, **kw):
            asked.append(kw)

        def generate(self):
            return {"User-Agent": CHROME_DESKTOP_UA,
                    "Sec-CH-UA": '"Google Chrome";v="147", "Chromium";v="147"',
                    "Sec-CH-UA-Platform": '"Windows"'}

    _fake_browserforge(monkeypatch, _Gen)
    got = FT._stealthy_baseline("firefox")
    assert got["user-agent"] == CHROME_DESKTOP_UA
    assert asked == [{"browser": ["firefox"], "device": ["desktop"]}], asked


def test_a_generator_that_refuses_the_pinned_call_still_delivers_headers(monkeypatch):
    """旧版 browserforge 不认这两颗参数时，丢的只能是"钉住"，不能是整副身份。

    反过来写就是本仓库那条法禁止的第三种形状：能力从路上静默消失，响应里一句也没有。
    """
    class _Gen:
        def __init__(self, **kw):
            if kw:
                raise TypeError("HeaderGenerator() got an unexpected keyword argument")
            self.bare = True

        def generate(self):
            return {"User-Agent": CHROME_DESKTOP_UA}

    _fake_browserforge(monkeypatch, _Gen)
    assert FT._stealthy_baseline("chrome") == {"user-agent": CHROME_DESKTOP_UA}


@pytest.mark.parametrize("family", ["chrome", "firefox", "safari", "edge"])
def test_a_real_roll_matches_the_family_it_was_pinned_to(family):
    """测**摇出来的那副**，不是我们造的字面量（browserforge 缺席时跳过）。

    这条是家族错配的直接对照：钉之前 40 次里 6 次是 Safari 的形状挂在 Chrome 的
    TLS 预设上，另有 2 次是手机形状。UA 与 `sec-ch-ua` 的版本自己也得对得上（那条
    守卫在 `test_one_browser_identity_per_session_not_one_per_request.py` 里，仍然有效）。
    """
    got = FT._stealthy_baseline(family)
    ua = got.get("user-agent", "")
    if not ua:
        pytest.skip("this machine has no browserforge")
    if family == "chrome":
        assert "Chrome/" in ua and "Edg/" not in ua, ua
    elif family == "edge":
        assert "Edg/" in ua, ua
    elif family == "firefox":
        assert "Firefox/" in ua, ua
    else:
        assert "Version/" in ua and "Chrome/" not in ua and "Firefox/" not in ua, ua
    assert "Mobile" not in ua, f"手机形状摇进了桌面的 TLS 预设：{ua}"
    plat = got.get("sec-ch-ua-platform", "")
    if plat:
        assert plat.strip('"') in ("Windows", "macOS", "Linux"), got


def test_the_wire_carries_one_browsers_claims(site):
    """到线上看：UA 说的家族与 `sec-ch-ua` 说的家族必须是同一家（桌面形状）。

    缺陷那一发实测是三台机器：iPhone 的 UA、Chrome 的 `sec-ch-ua`、`"iOS"` 的平台。
    这一条不测替身，测的是**站点收到的那一发** —— 逐颗盖掉一套自洽的头，只有到线上
    才看得见剩下那几颗是谁的。

    这台机器上的生成器若不认钉住的那两颗参数，抽签就还在（那是上面那条回退的形状），
    此时这条跳过而不是红 —— 它测的是钉住之后的线上形状。
    """
    probe = FT._stealthy_baseline("chrome")
    if probe and "Chrome/" not in probe.get("user-agent", ""):
        pytest.skip("this browserforge refused the pinned call: the roll is a lottery "
                    "again, which is the fallback path, not the shape under test")
    url, rec = site
    for _ in range(6):
        asyncio.run(MasterFetchServer()._dispatch(
            "web_read", {"url": url, "cache_ttl": -1}))
        h = rec.page_headers()
        ua = h.get("user-agent", "")
        assert "Chrome/" in ua and "Edg/" not in ua, ua
        assert "Mobile" not in ua and "iPhone" not in ua, ua
        assert h.get("sec-ch-ua-platform", "").strip('"') in (
            "Windows", "macOS", "Linux", ""), h
        if "sec-ch-ua-mobile" in h:
            assert h["sec-ch-ua-mobile"] == "?0", h


# ─── 3 · OS 那半：读得出来读不出来，都别再抽一次签 ────────────────────────────

def test_an_unreadable_os_lands_on_the_familys_desktop_not_the_lottery(monkeypatch):
    """`impersonate_os="random"` 实测会抽中手机预设，而头说的是桌面 Chrome。

    唯一走到这一支的路是上面那条回退（生成器不认钉参数），所以它是钉不住时的兜底，
    不是日常形状 —— 但它必须是"这一家的桌面系统"，不是"再摇一次"。
    """
    seen: list[dict] = []
    _stub_client(monkeypatch, seen)
    monkeypatch.setattr(FT, "_stealthy_baseline",
                        lambda family="": {"user-agent": IPHONE_UA})
    asyncio.run(FT.HTTPSession(impersonate="chrome", stealthy_headers=True)._init_client())
    assert seen[0]["impersonate_os"] == "windows", seen
    assert seen[0]["impersonate_os"] != "random"


@pytest.mark.parametrize("family,expect", [
    ("chrome", "windows"), ("edge", "windows"), ("firefox", "windows"),
    ("safari", "macos"), ("", "random"),
])
def test_the_fallback_os_is_the_familys_desktop(family, expect, monkeypatch):
    seen: list[dict] = []
    _stub_client(monkeypatch, seen)
    monkeypatch.setattr(FT, "_stealthy_baseline", lambda _family="": {"user-agent": IPHONE_UA})
    asyncio.run(FT.HTTPSession(impersonate=family or "opera",
                               stealthy_headers=True)._init_client())
    assert seen[0]["impersonate_os"] == expect, seen


def test_no_headers_means_no_claim_means_the_lottery_stays(monkeypatch):
    """browserforge 缺席的那台机器：头里根本没有 UA，谁也没声称自己是哪台机器。

    这时钉一个 windows 反而是**新造的谎**，所以 "random" 必须留着 —— 这条是上面两条
    的反向守卫，摘掉它下面那两条就变成"永远猜 windows"。
    """
    seen: list[dict] = []
    _stub_client(monkeypatch, seen)
    monkeypatch.setattr(FT, "_stealthy_baseline", lambda family="": {})
    asyncio.run(FT.HTTPSession(impersonate="chrome", stealthy_headers=True)._init_client())
    assert seen[0]["impersonate_os"] == "random", seen


def test_a_readable_os_is_still_read_from_the_ua(monkeypatch):
    """钉家族不许把"跟着头走"这一半换掉：头说 macOS，握手就得是 macOS 的那一套。"""
    seen: list[dict] = []
    _stub_client(monkeypatch, seen)
    mac = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36")
    monkeypatch.setattr(FT, "_stealthy_baseline", lambda family="": {"user-agent": mac})
    asyncio.run(FT.HTTPSession(impersonate="chrome", stealthy_headers=True)._init_client())
    assert seen[0]["impersonate_os"] == "macos", seen


# ─── 4 · 手册里那句话得跟着改 ─────────────────────────────────────────────────

def test_the_manual_says_the_face_is_one_per_process():
    """W1 把那三发手机 UA 记成"明说的限制"；W3 把它的成因关掉了一半，文字就得跟着事实走。"""
    from pathlib import Path

    root = Path(FT.__file__).resolve().parent / "skills" / "dhole-web" / "references"
    troub = (root / "troubleshooting.md").read_text(encoding="utf-8")
    tools = (root / "tools.md").read_text(encoding="utf-8")
    assert "one face per process" in troub, "限制那条还写着'每调用重摇'"
    assert "one face per process" in tools, "档位说明没讲身份在哪一层定"


# ============================================================================
# 来源：test_the_dhole_tools_price_is_the_wire_itself.py
# ============================================================================
# DHOLE_TOOLS 的价目表必须就是 wire 本身：每个子集真起一个 stdio 进程去量。

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

SUB8 = ["web_read", "web_search", "web_request", "web_crawl",
        "doc_parse", "feed_read", "sys_state", "sys_guide"]

_wired: dict = {}


class _StdioClient:
    """A throwaway dhole over stdio, kept to the two connect-time requests."""

    def __init__(self, env):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "dhole_mcp"], cwd=str(REPO_ROOT), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        self.stderr_text = ""
        self._by_id: dict = {}
        self._cond = threading.Condition()

        def _out():
            for raw in iter(self.proc.stdout.readline, b""):
                text = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not text:
                    continue
                try:
                    parsed = json.loads(text)
                except Exception:
                    parsed = None
                with self._cond:
                    if isinstance(parsed, dict) and parsed.get("id") is not None:
                        self._by_id[parsed["id"]] = (len(raw), parsed)
                    self._cond.notify_all()

        def _err():
            chunks = []
            for raw in iter(self.proc.stderr.readline, b""):
                chunks.append(raw.decode("utf-8", "replace"))
            self.stderr_text = "".join(chunks)

        threading.Thread(target=_out, daemon=True).start()
        threading.Thread(target=_err, daemon=True).start()

    def _send(self, obj):
        line = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        self.proc.stdin.write((line + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def ask(self, rid, method, params=None, timeout=90):
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)
        deadline = time.time() + timeout
        with self._cond:
            while rid not in self._by_id:
                left = deadline - time.time()
                if left <= 0 or self.proc.poll() is not None:
                    raise AssertionError(
                        f"{method} never answered (stderr tail: "
                        f"{self.stderr_text[-400:]})")
                self._cond.wait(left)
        return self._by_id[rid]

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=20)
        except Exception:
            self.proc.kill()
            self.proc.wait(timeout=5)


def _connect(dhole_tools):
    """Ask a real server what it actually puts on the connect-time wire.

    Cached per DHOLE_TOOLS value: the knob is frozen at import, so re-asking the
    same subset costs a whole process and buys nothing.
    """
    if dhole_tools in _wired:
        return _wired[dhole_tools]
    env = {k: v for k, v in os.environ.items()
           if k not in ("DHOLE_TOOLS", "DHOLE_USAGE_LOG")}
    if dhole_tools is not None:
        env["DHOLE_TOOLS"] = dhole_tools
    client = _StdioClient(env)
    try:
        _raw, init = client.ask(1, "initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "price-audit", "version": "0"}})
        client._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        raw_len, listed = client.ask(2, "tools/list", {})
    finally:
        client.close()
    assert not client.stderr_text, (
        "起一台只问 initialize/tools/list 的服务器不该留下 stderr："
        f"{client.stderr_text[:300]}")
    tools = listed["result"]["tools"]
    wire = {
        "names": [t["name"] for t in tools],
        "tools_compact": len(json.dumps({"tools": tools},
                                        separators=(",", ":"), ensure_ascii=False)),
        "raw_tools_line": raw_len,
        "instructions": init["result"].get("instructions") or "",
    }
    _wired[dhole_tools] = wire
    return wire


def _predict(names):
    """The same two numbers, computed in THIS process from the imported source."""
    from dhole_mcp.server import MasterFetchServer, _compose_instructions

    defs = {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}
    compact = len(json.dumps({"tools": [defs[n] for n in names]},
                             separators=(",", ":"), ensure_ascii=False))
    return compact, len(_compose_instructions(frozenset(names)))


def _assert_price(dhole_tools, names, published=None):
    wire = _connect(dhole_tools)
    assert sorted(wire["names"]) == sorted(names), (
        f"广告集合不是请求的那一份：wire {wire['names']} vs {sorted(names)}")
    compact, instr_chars = _predict(names)
    assert wire["tools_compact"] == compact, (
        f"tools/list 字节与进程内预测不符：wire {wire['tools_compact']} vs {compact}")
    assert len(wire["instructions"]) == instr_chars, (
        f"instructions 字节与进程内预测不符：wire {len(wire['instructions'])} vs {instr_chars}")
    total = compact + instr_chars
    if published:
        path, phrase = published
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
        assert phrase.replace("{total}", f"{total:,}") in text, (
            f"{path} 里没印着真机量到的 {total:,}")
    return total


def _ruler_module():
    spec = importlib.util.spec_from_file_location(
        "tool_payload_measure", REPO_ROOT / "tests" / "tool_payload_measure.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_default_connect_is_the_published_33_210():
    """不设 DHOLE_TOOLS 时，wire 就是 CONNECT_CEILING 那笔账：33,210。

    这一条同时是量尺的回归锁：`tool_payload_measure._wire_for` 曾按工具数累加路由行，
    把默认连接报成 30,857（web_discover / web_crawl 共用那句被算了两遍）。修成按文本
    去重后，脚本的读数、进程内预测与真机三者必须同值。

    30,707 → 30,701（2026-10-09，审计 D1+D4）：`web_act.confirm` 的描述说清了
    `confirm` 的两处门（净 +34），`cred_defaults.set` 的描述改掉了「host 在 auth
    里面」那句错话并缩到真话（净 −40）。这一对是**往下**走的，所以 CONNECT_CEILING
    也跟着降，而不是抬上限。同批的账在 test_wire_moves_land_in_the_manual.py。

    30,701 → 30,719（2026-10-10，体验修复刀3-C2）：一次**上抬**。两颗 `search` 描述
    都写着 "(URL path + page title)"，而 `sitemap=true` 的图只有 `loc`，没有标题可比
    —— 常驻层里的这句话在一条真实的调用形状上是假的。改动只做"不再承诺标题那一半"
    （crawl +4 / discover +14），完整解释留在 references/tools.md 按需付。

    30,719 → 31,208（2026-10-10，体验修复刀3-C3）：一次**上抬 +489**，买的是地图
    的大小从此有主人。`max_total_chars` 从前只在 web_crawl 那颗门上，`web_discover`
    转发时不传 → 一张 1,000 行的地图（实测 116,173 字符 / ~32k token）把默认预算
    透支 1.9 倍而 `truncated_by_total_chars` 一路是 False；预算现在数的是序列化后的
    行。被切掉的行要有路可拿，否则修的就是一次静默截断，所以 `resume_token` 也上
    web_discover 这扇门（读已存下的地图，不走网络）。两颗参数行 +332、描述尾句 +157。

    31,208 → 31,423（2026-10-10，体验修复刀3-C5）：一次**上抬 +215**，买的是
    `timeout=` 从此不说两句单位。它声明的是整次调用的毫秒（max 120000），而 HTTP 一档
    钳的是每次尝试的秒（30s），一份文档又只有一次尝试 —— 那句话在"大文件请抬 timeout"
    这条真实用法上是一句够不着的承诺。三颗描述各补上限（web_read +98 / web_crawl +66 /
    web_request +52），`feed_read` 顺手把印错的默认值说回 20s（−1：那颗参数从前根本
    没换算，毫秒被当秒用）。收据本身（`timeout_applied`）是响应字段，不占这一页。

    31,423 → 31,451（2026-10-10，体验修复刀3-A2）：一次**上抬 +28**，买的是三颗**必填**
    载荷键上了 `web_act.actions`。那句清单从前数完 11 个动作名就只说目标怎么寻址，一个
    载荷键都没点名 —— `text` 与 `key` 在任何 wire 文本里都不存在，只读 `tools/list` 的
    调用方只能猜（实测猜错两次），而答案只住在报错里：那等于要求先失败一次。整表（含可选
    旋钮）的第一版是 519 字符，撞了 `test_wire_shape.py` 那颗 296 的闸；按那道闸自己的
    口径（搬家，不是缩写）可选项留在 references/tools.md，wire 只留必填的那三颗。
    31,451 → 31,824（2026-10-10，体验修复刀3-A3）：一次**上抬 +373**，买的是把一句做不到
    的承诺换成一对用得上的形状。常驻层写着 "Captchas get at most 3 read-and-retry rounds"，
    而每次调用都重新导航 —— 第二轮读到的挑战已经不是第一轮那一枚（实测靶站每次 GET 出新
    token）。同一句假话的第二个作者是 VISUAL ASSIST 那条：截图"就是你马上要照着打字的那一枚"，
    可那张图要等整条链跑完才交得到调用方眼前。新的两半说的是结构与那一种真能做的：图像挑战
    转交；页面把挑战写成文本时，`{"read": {..., "as": "code"}}` 再 `{"fill": 
    {"text": "{=code}"}}` 在同一条链里读完。动作清单 +6，工具描述 +367。
    31,824 → 32,466（2026-10-10，体验修复刀3-W1）：一次**上抬 +642**，买的是这一批里
    只剩最后一格的那句"调用方对自己那一发请求没有任何说法"。一次零点名的 `web_read`
    实测发 15 颗头 —— 一枚编造的 `referer: https://www.google.com/`、browserforge 的 UA
    与 `sec-ch-ua*` 一家（16 发抽样里 3 发把手机 UA 配在 `sec-ch-ua-platform: "Linux"`，
    13 发带着 `sec-fetch-site: "?1"` 那个错值），而旧参数面上没有任何一颗能改它。
    `headers=` 上 web_read（+386）与 web_request（+256）；同一刀把 `stealthy_headers`
    那两颗字面量交给 `anti_detect` 档位表读，公开面因此**没有**多第二颗伪装旋钮。
    三条设计边界（凭据 / UA / 校验子）各带自己那扇门的地址，而计划里那句
    "primp 编译层覆盖不掉" 被实测推翻了 —— 覆盖得掉，所以拒绝名单上没有"做不到"这一类。

    32,466 → 32,496（2026-10-10，体验修复刀3-D2）：一次**上抬 +30**，买的是常驻层不再对
    一台三个月前批过的主机说"这是第一次"。名册从前是无时间戳的裸 host 列表，三月的按键到
    十月还在放行；现在磁盘形状是 `{host: 批准的 ISO 时间戳}`，过窗（30 天）重新征询而不是
    静默失效。三处描述改词：web_read.confirm +24、web_read.cdp_url +4、web_act.cdp_url +2
    （instructions 一字不变，2,476 那一半属于路由行而不是这次）。

    32,496 → 32,707（2026-10-10，体验修复刀4-#42）：一次**上抬 +211**，买的是这一批里最后
    一句"说了却不管用"。`web_discover` 的 `timeout=` 在 `sitemap=true` 那条分支上从来没有被
    读过 —— 图走 `crawl_site(discover_only=True)` 而那次调用不带 deadline，抓取本身另有两颗
    硬编的 15s。实测：一台每次应答 20s 的本地站，`timeout=30000` 跑 30.0s 才回来、
    `sitemap_used=False`、0 行；把同一颗 30s 换成 8s，它照样跑满 20s 才应答 —— 数字改不改都
    与它无关。现在 `timeout=` 就是这张图的时钟：它既接进整次调用的 deadline，也接进读索引那
    一发请求，超出 1-120000 具名拒绝且什么都不读。改完之后同样的站点 20.0s 交出 50 行；
    `timeout=700` 在 1.70s 被切断，且说的是"这一次的时间预算用完了，这不是站点说的不"，
    下一句直接点名该抬哪颗参数（discover 抬 `timeout=`，crawl 抬 `deadline_ms`）。
    web_discover 那颗 2,057 → 2,268（+211 全在它的 `timeout` 描述上），9 颗那组随之
    26,541 → 26,752。

    32,700 → 32,828（2026-10-11，验收刀9-7 · F9）：一次**上抬 +128**，买的是这一批里唯一
    一条"骂人级且机械性必错"的那一格从此有治法。体验报告 S10 的原文是"我明明看清了它说
    我错！"：靶站 `/captcha` 每次 GET 换一枚 token，而每一次 `web_act` 调用都重新导航，
    所以第二轮填进去的必然是第一轮那道题的答案 —— 不是运气差，是结构，3 次上限救不了它。
    新参数 `web_act.pin=true`（驻留：下一次带 pin 的调用站在同一页上做事，不导航）的 JSON
    成本 +215（描述 174 字 + 那颗键的壳）；同一段里从前那句
    "a retry is a NEW page load, so the challenge you read last call belongs to a page that
    is gone unless the site binds it to the session"（138 字）整段退场，换成 50 字的指针
    （−88）。它不在 tools/list 上就说不出来：dispatch 按表外名把调用方的 `pin=` 拒在门外，
    而一次没人听见的驻留请求读起来就是一次成功的驻留。web_act 那颗 4,287 → 4,415；
    8 颗与 9 颗那两组不含 web_act，照旧不改 —— 这也是这一族子集账的判据本身。

    32,828 → 33,088（2026-10-15，R02-F34 · R02-054）：一次**上抬 +260**，买的是「这台机器的
    语义重排器装了没有」第一次在 wire 上点名问得出。`show` 的枚举多一颗 `models`（+8），
    `clear` 那句「config is show-only」改成两类各自点名（+12），sys_state 描述多一行路由
    （+240：这一类存在、它是只读的 —— dhole 不抹模型文件、以及往 sys_guide 的那句指针）。
    必须每次连接都到的理由与 G25 那次 confirm 门同类：**宿主在调用之前**就得决定要不要问这一格，
    而只住在 sys_guide 语料里的名字，调用方不知道可以问，也就永远不会问（画像那一发
    `sys_state(show=['config'])` 就是问错了门之后没再问的样子）。四格 `state` 怎么读、截断下载
    为什么算「不在」——解释全部留在 references/tools.md，按需付。sys_state 那颗 1,654 → 1,914；
    它住在 8 颗与 9 颗那两组里，所以两组随实测一起走：24,477 → 24,737、26,745 → 27,005（2026-10-20 起再 −11：24,726 / 26,994）。

    33,077 → 33,210（2026-10-20，P0 纵向搜索）：一次**上抬 +133**，买的是
    `web_search` 的 `sources`（web|news|academic|qa|code|community）与 `sort`
    （relevance|newest）两颗可选参数在连接期到场：表外名会被 dispatch 拒掉，
    而五个纵向后端（GDELT/arXiv/Stack Exchange/GitHub/Hacker News）的细节、
    限速与日期窗语义都住在 sys_guide 按需付。首版两张带 enum 的完整描述实测
    +860，按批次 6c 的裁决摘掉 enum、并收短 web_search 里与参数描述重复的
    "两把尺子"一句后，净 +133。web_search 那颗 4,032 → 4,165；它住在 8 颗与
    9 颗两组里，所以两组随实测走到 24,859 / 27,127；read+search 那一对
    11,887 → 12,020。
    """
    ruler = _ruler_module()
    import ast
    tree = ast.parse(ruler.SERVER.read_text(encoding="utf-8"))
    defs = ruler.literal_node(tree, "_TOOL_DEFS")
    parts = (ruler.literal_node(tree, "_INSTRUCTIONS_INTRO"),
             ruler.literal_node(tree, "_INSTRUCTIONS_ROUTING"),
             ruler.literal_node(tree, "_INSTRUCTIONS_RULES"))
    ruler_compact, ruler_instr = ruler._wire_for(
        defs, {td["name"] for td in defs}, parts)

    wire = _connect(None)
    assert len(wire["names"]) == 11
    assert ruler_compact == wire["tools_compact"], (
        f"仓库量尺与 wire 的 tools/list 对不上：{ruler_compact} vs {wire['tools_compact']}")
    assert ruler_instr == len(wire["instructions"]), (
        f"仓库量尺多算了路由行：{ruler_instr} vs wire {len(wire['instructions'])}")
    assert ruler_compact + ruler_instr == 33_210


def test_the_two_tool_daily_driver_pays_what_its_docstring_claims():
    """`DHOLE_TOOLS=web_read,web_search` 的真机价：12,020（这一族的起点是注释里的 11,480）。

    四次移动的账：11,423 → 11,382 是把签名影子摘掉时顺带瘦的字（那一刀量过真机），
    11,382 → 11,480 是 C5 给 `web_read` 的 `timeout` 补上"一次尝试封顶 30s"那一句
    （+98：web_read 在这一组里，`web_search` 那颗不谈下载所以没付），11,480 → 11,866
    是 W1 的 `headers=`（+386：这一组里只有 `web_read` 付它 —— `web_search` 不发请求，
    而一颗搜索的参数长不出一条 HTTP 头的账）。11,866 → 11,894 是 D2 的
    `confirm`/`cdp_url` 两句（+28：两颗都在 `web_read` 身上，`web_act` 那 +2 不在这一组）。
    11,894 → 11,887（−7，期4 第六刀）：`web_search` 那颗 `before=` 的拒辞换成逐行判决的说
    法，新句比旧句短 7 字 —— 这一组里只有 `web_search` 付它。
    同一条拒绝名单在 `web_request` 那一扇门上只花 256，因为它的描述指着
    `web_read.headers` 而不是重说一遍。
    11,887 → 12,020（+133，P0 纵向搜索）：`sources` / `sort` 两颗都长在
    `web_search` 身上，这一组正是买下"免费搜索一次问五类源"的那一对。
    """
    assert _assert_price("web_read,web_search",
                         ["web_read", "web_search"]) == 12_020


def test_the_readme_eight_tool_price_is_what_the_server_sends():
    """README 的 8 颗价不再只是算术 —— 真机同一值，且文档印的就是它（24,859）。"""
    _assert_price(",".join(SUB8), SUB8,
                  published=("README.md", "这 8 颗是 {total} 字符"))


def test_the_ninth_tool_costs_its_own_row_and_no_routing_line():
    """加 web_discover 只多它自己那一行：路由行与 web_crawl 共用，去重后不再付第二次。

    这是 F-A-J1 那把共享路由行在 wire 上的样子：README 的 9 颗 27,127 =
    24,859 + 2,268，instructions 一字不变。C3 之后 `web_discover` 自己那一行涨了
    （地图大小两颗旋钮上这门），涨的部分照旧只付一次；C5 没动它（那一扇门一次尝试都不
    走 HTTP 正文通道，所以它不欠那句上限）。D2 的 +28 全部落在 `web_read`，所以这一组
    与 8 颗那组同幅上涨。
    """
    nine = SUB8 + ["web_discover"]
    assert _assert_price(",".join(nine), nine,
                         published=("README.md", "共 9 颗 {total}")) == 27_127
    eight = _connect(",".join(SUB8))
    assert len(_connect(",".join(nine))["instructions"]) == len(eight["instructions"]), (
        "两颗共用一句路由的工具，第二颗不该再收一次路由行的钱")


def test_the_raw_line_is_the_compact_table_plus_a_bounded_envelope():
    """"字节"要说清是哪一层：JSON-RPC 信封只多几十字，价目表量的是表本身。

    发布口径是紧凑 tools/list 的字符数；真机那一行还带着
    `{"jsonrpc":…,"id":…,"result":…}`。信封差钉在 40–60 字符内 —— 越过这个带说明
    wire 形状变了（比如开始转义非 ASCII），那才是需要人来看的一次改动。
    """
    wire = _connect("web_read,web_search")
    envelope = wire["raw_tools_line"] - wire["tools_compact"]
    assert 40 <= envelope <= 60, f"tools/list 那一行的信封开销异常：{envelope}"


# ============================================================================
# 来源：test_a_line_the_parser_refuses_is_answered.py
# ============================================================================
# 一行 SDK 拒解析的 JSON，必须拿到 -32700，而不是永远的沉默（D7）。
# 重名消歧：REPO_ROOT → REPO_ROOT__a_line_the_parser_refuse_59d8

REPO_ROOT__a_line_the_parser_refuse_59d8 = pathlib.Path(__file__).resolve().parents[1]


# ─── 真 stdio：三种坏输入各拿一条 -32700 ────────────────────────────

class _Wire:
    """一台只用来问"这一行你回什么"的 dhole，按到达顺序记下每一行。"""

    def __init__(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("DHOLE_TOOLS", "DHOLE_USAGE_LOG")}
        env["PYTHONPATH"] = str(REPO_ROOT__a_line_the_parser_refuse_59d8 / "src")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "dhole_mcp"], cwd=str(REPO_ROOT__a_line_the_parser_refuse_59d8), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.lines: list = []
        self.stderr_text = ""
        self._cond = threading.Condition()

        def _out():
            for raw in iter(self.proc.stdout.readline, b""):
                text = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not text:
                    continue
                with self._cond:
                    self.lines.append(text)
                    self._cond.notify_all()

        def _err():
            chunks = []
            for raw in iter(self.proc.stderr.readline, b""):
                chunks.append(raw.decode("utf-8", "replace"))
            self.stderr_text = "".join(chunks)

        threading.Thread(target=_out, daemon=True).start()
        threading.Thread(target=_err, daemon=True).start()

    def send(self, obj):
        line = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        self.proc.stdin.write((line + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def send_raw(self, text):
        self.proc.stdin.write(text.encode("utf-8"))
        self.proc.stdin.flush()

    def wait_for(self, count, timeout=90):
        """等到达行数到 count（-32700 的 id 是 null，不能按 id 等）。"""
        deadline = time.time() + timeout
        with self._cond:
            while len(self.lines) < count:
                left = deadline - time.time()
                if left <= 0 or self.proc.poll() is not None:
                    raise AssertionError(
                        f"只收到 {len(self.lines)} 行（要 {count}）；stderr 尾巴："
                        f"{self.stderr_text[-400:]}")
                self._cond.wait(left)

    def parsed(self):
        return [json.loads(t) for t in list(self.lines)]

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=20)
        except Exception:
            self.proc.kill()
            self.proc.wait(timeout=5)


def _handshake(w):
    w.send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "d7", "version": "0"}}})
    w.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    w.wait_for(1)


def _deep_arguments(depth=300):
    """一个真能上 wire 的超深参数（300 层，远超解析器能读的 ~92）。"""
    inner = None
    for _ in range(depth):
        inner = {"n": inner}
    return {"x": inner}


@pytest.fixture
def wire():
    w = _Wire()
    try:
        _handshake(w)
        yield w
    finally:
        w.close()


class TestABadLineGetsAnAnswerNotSilence:

    def test_an_over_deep_argument_is_answered(self, wire):
        """报告记的是"150 层正常返回"；实测 150 层已静默。修后该拿 -32700。"""
        wire.send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                   "params": {"name": "sys_state",
                              "arguments": _deep_arguments(300)}})
        wire.wait_for(2)
        err = wire.parsed()[1]
        assert err["id"] is None, f"解析错误该带 null id（JSON-RPC 2.0），拿到 {err}"
        assert err["error"]["code"] == -32700, err
        assert err["error"]["message"] == "Parse error", err

    def test_a_truncated_line_is_answered(self, wire):
        wire.send_raw('{"jsonrpc":"2.0","id":4,"method":"tools/li\n')
        wire.wait_for(2)
        err = wire.parsed()[1]
        assert err["id"] is None and err["error"]["code"] == -32700, err

    def test_a_line_that_is_not_json_at_all_is_answered(self, wire):
        wire.send_raw("not json at all\n")
        wire.wait_for(2)
        err = wire.parsed()[1]
        assert err["id"] is None and err["error"]["code"] == -32700, err

    def test_the_answer_names_the_cause(self, wire):
        """'Parse error' 三个字不告诉调用方是哪一颗输入错了；data 要说话。"""
        wire.send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                   "params": {"name": "sys_state",
                              "arguments": _deep_arguments(300)}})
        wire.wait_for(2)
        data = wire.parsed()[1]["error"]["data"]
        assert isinstance(data, str) and data, "data 必须说清是哪一类解析失败"
        assert "deeper" in data or "deep" in data, (
            f"嵌套过深这一类该被点名，拿到 {data!r}")


class TestTheSessionSurvivesAndOrderHolds:
    """坏行不许把连接搞坏，也不许把后面的正常应答挤掉或换序。"""

    def test_a_normal_call_after_three_bad_lines_still_answers(self, wire):
        wire.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        wire.send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                   "params": {"name": "sys_state",
                              "arguments": _deep_arguments(300)}})
        wire.send_raw('{"jsonrpc":"2.0","id":4,"method":"tools/li\n')
        wire.send_raw("not json at all\n")
        wire.send({"jsonrpc": "2.0", "id": 5, "method": "tools/list"})
        wire.wait_for(6)
        got = wire.parsed()
        assert [m.get("id") for m in got] == [1, 2, None, None, None, 5], (
            f"到达顺序不是握手 / 正常 / 三条解析错误 / 正常：{got}")
        assert [m["error"]["code"] for m in got if "error" in m] == [-32700] * 3
        # 坏行之后的正常应答必须真的是正常应答，不是被挤空的信封
        assert got[-1]["result"]["tools"], "坏行之后的 tools/list 该照常回工具表"

    def test_a_clean_session_gets_no_spurious_parse_error(self, wire):
        """反向守卫：全是好行时一条 -32700 都不许出现。"""
        wire.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        wire.send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                   "params": {"name": "sys_state", "arguments": {}}})
        wire.wait_for(3)
        got = wire.parsed()
        assert [m.get("id") for m in got] == [1, 2, 3]
        codes = [m["error"]["code"] for m in got if m.get("error")]
        assert -32700 not in codes, f"干净会话里不该出现解析错误：{got}"

    def test_the_wire_stays_quiet_on_stderr(self, wire):
        """修法不许靠打印来"说明"，也不许留下噪声。"""
        wire.send_raw("not json at all\n")
        wire.wait_for(2)
        time.sleep(0.4)
        assert wire.stderr_text == "", f"stderr 该是空的：{wire.stderr_text[:300]!r}"


# ─── 协议层：包装对象必须真的满足 ReadStream ────────────────────────

class _FakeRead:
    """一个最小读流：把条目原样交出去，条目就是异常时**返回**它（SDK 的写法）。"""

    def __init__(self, items):
        self._items = list(items)
        self.closed = False
        self.last_context = "sender-ctx"

    async def receive(self):
        if not self._items:
            raise anyio.EndOfStream
        return self._items.pop(0)

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)

    def __aiter__(self):
        return self

    async def aclose(self):
        self.closed = True


class _FakeWrite:
    def __init__(self):
        self.sent = []

    async def send(self, item, /):
        self.sent.append(item)


def _wrapper(inner):
    from dhole_mcp.server import _ParseErrorAnsweringReadStream
    write = _FakeWrite()
    return _ParseErrorAnsweringReadStream(inner, write), write


class TestTheWrapperIsFaithfulToTheReadStreamProtocol:

    def test_a_good_item_passes_through_untouched(self):
        sentinel = object()
        stream, write = _wrapper(_FakeRead([sentinel]))
        assert anyio.run(stream.receive) is sentinel
        assert write.sent == [], "好条目不许被改写，也不许顺手答一条错误"

    def test_every_bad_item_gets_its_own_answer(self):
        """连续坏行要各答一条 —— 循环，不是只处理第一条。"""
        stream, write = _wrapper(_FakeRead([ValueError("a"), ValueError("b"),
                                            ValueError("c")]))
        with pytest.raises(anyio.EndOfStream):
            anyio.run(stream.receive)
        assert len(write.sent) == 3, f"三条坏行该有三条回答，拿到 {len(write.sent)}"

    def test_the_anext_path_answers_too(self):
        stream, write = _wrapper(_FakeRead([ValueError("a"), "good"]))
        assert anyio.run(stream.__anext__) == "good"
        assert len(write.sent) == 1

    def test_the_stream_end_is_not_swallowed(self):
        """流结束必须原样抛出 —— 吞掉它就是让服务器永远不退出。"""
        stream, _ = _wrapper(_FakeRead([]))
        with pytest.raises(anyio.EndOfStream):
            anyio.run(stream.receive)
        stream2, _ = _wrapper(_FakeRead([]))
        with pytest.raises(StopAsyncIteration):
            anyio.run(stream2.__anext__)

    def test_aclose_reaches_the_inner_stream(self):
        inner = _FakeRead([])
        stream, _ = _wrapper(inner)
        anyio.run(stream.aclose)
        assert inner.closed, "aclose 必须转到底层，否则连接不会真的关"

    def test_the_sender_context_is_forwarded(self):
        """runner 靠 last_context 还原每条消息的 sender context；包一层不能丢。"""
        stream, _ = _wrapper(_FakeRead([]))
        assert stream.last_context == "sender-ctx"

    def test_it_can_be_used_as_an_async_context_manager(self):
        stream, _ = _wrapper(_FakeRead([]))

        async def _use():
            async with stream as s:
                return s is stream

        assert anyio.run(_use) is True

    def test_the_answer_is_a_proper_jsonrpc_error_frame(self):
        stream, write = _wrapper(_FakeRead([ValueError("Invalid JSON: boom")]))
        with pytest.raises(anyio.EndOfStream):
            anyio.run(stream.receive)
        frame = json.loads(write.sent[0].message.model_dump_json(
            by_alias=True, exclude_unset=True))
        assert frame == {"jsonrpc": "2.0", "id": None,
                         "error": {"code": -32700, "message": "Parse error",
                                   "data": "the line is not valid JSON"}}, frame
