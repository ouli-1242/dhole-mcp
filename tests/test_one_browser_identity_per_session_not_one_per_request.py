"""一场会话一副身份：头不许每次请求重摇，TLS 档要跟着这副身份选。

实测（2026-10-08，`HTTPSession(stealthy_headers=True)._build_headers()` 连摇 12 次）：
UA 里的 Chrome 大版本在 **147 与 148 之间来回跳**，另有两次连 UA 都没有。原因是
`_build_headers` 里每次都新建一个 `HeaderGenerator()` 并 `generate()` —— 一份全新的
随机身份。而 TLS 指纹是 primp 在 `__aenter__` 那一刻按 `impersonate` **定死**的，
`impersonate_os` 当时还写着 `"random"`：两边各抽各的 OS。

于是每一次请求都是一次独立的抽签，两个方向都出错：

一、**会话内漂移**。真实浏览器不会在一场会话里换 Chrome 版本；同一连接上 UA 变了，
    而 TLS/HTTP2 指纹一寸没动 —— 这是检测方最容易交叉核对的形状。
二、**层间错配**。头说是 Windows，TLS 握手可能是 Linux 的那一套（实测约一半的调用
    落在这种组合上，因为 OS 是两个独立随机变量）。

修法把身份的决定收在**一处一次**：`_stealthy_baseline()` 在 `_init_client` 里跑一次，
存进会话；primp 的 `impersonate_os` 从这份 UA 里读出来。browserforge 不在（这是可选
依赖）时交回空身份并回落 `"random"` —— 那种机器上的行为与改动前一字相同。

三处反向守卫不许松：人工指定的 UA 仍然最大；非 Chromium 的 UA 不许带着 `sec-ch-ua*`
出去（那是 Chromium 一家才发的）；`Sec-Fetch-*` 各家都发，不许跟着一起摘。
"""
import asyncio
import re
import sys

import pytest

from dhole_mcp import fetcher as FT


def _session_with(baseline):
    s = FT.HTTPSession(stealthy_headers=True)
    s._baseline = dict(baseline)
    return s


CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36")
FIREFOX_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:139.0) "
              "Gecko/20100101 Firefox/139.0")
BASELINE = {
    "user-agent": CHROME_UA,
    "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    "sec-ch-ua-platform": '"Windows"',
    "accept-language": "en-US,en;q=0.9",
    "sec-fetch-mode": "navigate",
}


# ─── 1 · 一场会话一副身份 ────────────────────────────────────────────────────

def test_twelve_requests_in_one_session_build_one_identity():
    s = _session_with(BASELINE)
    builds = {tuple(sorted(s._build_headers().items())) for _ in range(12)}
    assert len(builds) == 1, (
        f"同一会话里摇出了 {len(builds)} 副身份 —— 就是那个每次重摇的形状")


def test_the_identity_is_decided_once_at_init_not_per_request(monkeypatch):
    """`_stealthy_baseline` 一场会话只该被问一次（每请求一次就是原来那个缺陷）。"""
    calls = []
    real = FT._stealthy_baseline

    def _spy():
        calls.append(1)
        return real()

    monkeypatch.setattr(FT, "_stealthy_baseline", _spy)
    monkeypatch.setattr(FT.primp, "Client", lambda **kw: object())

    async def _run():
        async with FT.HTTPSession(stealthy_headers=True) as s:
            for _ in range(5):
                s._build_headers()
    asyncio.run(_run())
    assert len(calls) == 1, f"身份被摇了 {len(calls)} 次（应为 1）"


def test_two_sessions_may_still_differ():
    """反向：会话内固定 ≠ 全局固定。跨会话仍各抽一份，轮换还在。"""
    a = FT.HTTPSession(stealthy_headers=True)
    b = FT.HTTPSession(stealthy_headers=True)
    a._baseline = {"user-agent": CHROME_UA}
    b._baseline = {"user-agent": FIREFOX_UA}
    assert a._build_headers()["user-agent"] != b._build_headers()["user-agent"]


# ─── 2 · TLS 档跟着这份身份选 OS ─────────────────────────────────────────────

def test_primp_gets_the_os_the_headers_claim(monkeypatch):
    """头说 Windows，握手就不许是随机的另一台机器。"""
    seen = {}

    class _Client:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(FT.primp, "Client", _Client)
    monkeypatch.setattr(FT, "_stealthy_baseline", lambda: dict(BASELINE))
    s = FT.HTTPSession(stealthy_headers=True)
    asyncio.run(s._init_client())
    assert seen["impersonate_os"] == "windows", seen


def test_an_unknown_os_falls_back_to_random(monkeypatch):
    """认不出 OS（browserforge 缺席 / Android 那类写法）时不许瞎猜一个。"""
    seen = {}

    class _Client:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(FT.primp, "Client", _Client)
    monkeypatch.setattr(FT, "_stealthy_baseline", lambda: {})
    asyncio.run(FT.HTTPSession(stealthy_headers=True)._init_client())
    assert seen["impersonate_os"] == "random", seen


@pytest.mark.parametrize("ua,expect", [
    (CHROME_UA, "windows"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36", "macos"),
    ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/147.0.0.0 Safari/537.36", "linux"),
    (FIREFOX_UA, "windows"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
     "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1", ""),
    ("", ""),
])
def test_the_os_is_read_from_the_ua_only_for_the_three_primp_takes(ua, expect):
    """读的是**操作系统**，不是浏览器：Firefox 跑在 Windows 上，OS 那一位就该是
    windows。primp 只认 windows/macos/linux（实测 "IOS" 被 `Invalid impersonate_os`
    拒掉），所以认不出来的形状交回空串，由调用方回落 random —— 不许猜一个。"""
    assert FT._os_from_ua(ua) == expect


# ─── 3 · 人工 UA 仍然最大，但不许带着别家的指纹出门 ──────────────────────────

def test_a_supplied_useragent_outranks_the_baseline():
    s = _session_with(BASELINE)
    out = s._build_headers(useragent="my-agent/1.0")
    assert out["user-agent"] == "my-agent/1.0"


def test_a_non_chromium_useragent_does_not_keep_the_chromium_hints():
    """`sec-ch-ua` 是 Chromium 一家才发的。挂在一个人工 Firefox UA 上，
    等于自己写下"这些头不是同一台机器"。"""
    s = _session_with(BASELINE)
    out = s._build_headers(useragent=FIREFOX_UA)
    assert not any(k.startswith("sec-ch-ua") for k in out), sorted(out)
    # 而 Sec-Fetch-* 各家都发，不许被一起摘掉
    assert "sec-fetch-mode" in out, sorted(out)


def test_a_chromium_useragent_keeps_its_hints():
    s = _session_with(BASELINE)
    out = s._build_headers(useragent=CHROME_UA)
    assert "sec-ch-ua" in out, sorted(out)


# ─── 4 · browserforge 不在的那台机器 ─────────────────────────────────────────

def test_a_machine_without_browserforge_builds_what_it_always_built(monkeypatch):
    """可选依赖缺席时的行为必须与改动前一字相同：只带 referer，不抛。"""
    import builtins
    real_import = builtins.__import__

    def _blocked(name, *a, **k):
        if name.startswith("browserforge"):
            raise ImportError("no browserforge")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    assert FT._stealthy_baseline() == {}
    s = FT.HTTPSession(stealthy_headers=True)
    out = s._build_headers()          # baseline 还没被填过，也不该炸
    assert out == {"referer": "https://www.google.com/"}, out


def test_the_ua_and_the_brand_list_agree_in_the_baseline():
    """生成器给的那一份身份，UA 与 sec-ch-ua 的版本自己得对得上。

    这一条测的是**取来的那副身份**，不是我们造的字面量：browserforge 缺席时跳过，
    在场时它必须交出内部一致的一套（这是它存在的意义）。
    """
    baseline = FT._stealthy_baseline()
    ua = baseline.get("user-agent", "")
    if not baseline or "Chrome/" not in ua:
        pytest.skip("这台机器上没有 browserforge，或非 Chromium 的一次抽签")
    ua_major = re.search(r"Chrome/(\d+)", ua).group(1)
    brands = re.findall(r'"(?:Google Chrome|Chromium)";v="(\d+)"',
                        baseline.get("sec-ch-ua", ""))
    assert brands and all(b == ua_major for b in brands), (ua, baseline["sec-ch-ua"])
