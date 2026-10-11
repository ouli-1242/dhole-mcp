"""W3：一场进程一副身份，而且这副身份必须是**这一次冒充的那一家**的。

两条实测，都是本文件存在的理由（2026-10-09，本地 echo 站点，量具见提交信息）：

一、**跨调用漂移**。上一版把身份定在"会话级"，而 `bulk_get` 每次调用新建一个
    `HTTPSession` —— 于是"会话"在真实使用里等于"这一次调用"。同一进程连开 8 个会话
    摇出 **7 副**脸。一次 `web_read` 的重试、批量里的每一条、升级路上的每一档都是
    新会话，所以"这台机器的浏览器"在几秒内换了七副长相，而 TLS 握手一寸没跟着动。

二、**家族错配**。生成器默认四个家族两种设备，`impersonate` 却是写死 "chrome" 的：
    40 次里 6 次摇出 Safari（15%）、2 次摇出手机形状。而 dhole 那几颗头是**逐颗**盖在
    primp 自己那一套之上的（primp 只补字典里没有的名字，W1 量过），于是盖掉一半就
    造出三台机器：实测最后一副的线上头是

        user-agent:          Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 ...) Version/26.3 Mobile Safari/604.1
        sec-ch-ua:           "Google Chrome";v="149", "Chromium";v="149", ...
        sec-ch-ua-platform:  "iOS"

    另一条同族的抽签是 `impersonate_os="random"`：16 次里 1 次抽中**手机**预设
    （线上就是 `"Android"` + 一枚 `Mobile Safari` 的 UA）。它换掉的是一整台机器的
    形状，不是一位字段，所以"读不出系统就交给随机"并不等于"不许瞎猜"——它只是把
    错配交给概率。

修法三处，都在建会话的那一点上：`_process_identity` 按家族存一份进程级身份；
`_stealthy_baseline(family)` 把生成器钉成 `browser=[family], device=["desktop"]`；
UA 读不出系统时按家族落到桌面主力系统 —— 而**头里根本没有 UA**（browserforge 缺席）
时仍然交回 "random"，那种机器上谁也没声称自己是谁，那才是"不许猜"适用的形状。

`test_one_browser_identity_per_session_not_one_per_request.py` 是上一版立的案，它当时
把"跨会话仍各抽一份"写成了要保留的性质；那一条在本文件里被改掉，改的理由就是上面
的第一条实测。
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
    """钉住的是两颗参数，而且钉的是**这次要冒充的那一家** —— 不是恒定 chrome。"""
    asked = []

    class _Gen:
        def __init__(self, **kw):
            asked.append(kw)

        def generate(self):
            return {"User-Agent": CHROME_DESKTOP_UA}

    _fake_browserforge(monkeypatch, _Gen)
    assert FT._stealthy_baseline("firefox")["user-agent"] == CHROME_DESKTOP_UA
    assert asked[-1] == {"browser": ["firefox"], "device": ["desktop"]}, asked


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
