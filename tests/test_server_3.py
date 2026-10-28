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


def test_the_ruler_agrees_with_the_wire_it_measures():
    """仓库里的量尺（`tests/tool_payload_measure.py`）与真机 wire 必须同值。

    这一条守的是**量尺本身**：它曾按工具数累加路由行，把默认连接多算了 6,000 字符
    （web_discover / web_crawl 共用那句被算了两遍）。读数、进程内预测、真机三者
    同值就够了 —— 具体是多少不进测试，那是账本，每改一句描述都要来改一次。
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
    assert ruler_compact == wire["tools_compact"], (
        f"仓库量尺与 wire 的 tools/list 对不上：{ruler_compact} vs {wire['tools_compact']}")
    assert ruler_instr == len(wire["instructions"]), (
        f"仓库量尺多算了路由行：{ruler_instr} vs wire {len(wire['instructions'])}")


def test_the_two_tool_group_pays_what_the_server_sends():
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
    12,020 → 12,373（+353，R3 `include_headers`）：这一组里只有 `web_read` 付它，
    `web_search` 不发请求，也没有响应头可交。
    12,373 → 12,866（+455，R4 `save_to`）：同一句话的第二次 —— 落盘也是 `web_read`
    的活，`web_search` 连字节都没有。
    12,866 → 12,959（+93，免密引擎扩员 + 分层 + shenma）：`web_search` 描述
    换成 12 家名单与池序=分层那句，`web_read` 不付。
    12,959 → 13,066（+107，round-13 F1）：收窄 selector 的措辞落在 `web_read`
    描述上；13,066 → 13,076（+10，E1）：`urls` 点名回执行数组 `results`。
    """
    _assert_price("web_read,web_search", ["web_read", "web_search"])


def test_the_ninth_tool_costs_its_own_row_and_no_routing_line():
    """加 web_discover 只多它自己那一行：路由行与 web_crawl 共用，去重后不再付第二次。

    这是 F-A-J1 那把共享路由行在 wire 上的样子：README 的 9 颗 28,709，
    instructions 一字不变。C3 之后 `web_discover` 自己那一行涨了
    （地图大小两颗旋钮上这门），涨的部分照旧只付一次；C5 没动它（那一扇门一次尝试都不
    走 HTTP 正文通道，所以它不欠那句上限）。D2 的 +28 全部落在 `web_read`，所以这一组
    与 8 颗那组同幅上涨；E1/E8 的净 −9 也同幅（共享的 `web_read`/`feed_read`）。
    """
    nine = SUB8 + ["web_discover"]
    _assert_price(",".join(nine), nine)
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
    assert 30 <= envelope <= 60, f"tools/list 那一行的信封开销异常：{envelope}"


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
