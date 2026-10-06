"""系统浏览器检测认得 Edge —— chrome → msedge → bundled chromium 的次序是承诺。

Edge 随 Windows 出厂，"没装过 Chrome 的机器"并不罕见；而系统 Chromium 系浏览器
的真实 TLS 指纹（JA4，BoringSSL）正是反爬档位最大的那根杠杆（见
`_detect_chrome_channel` 的 docstring）。把 Edge 纳入检测不是加一个候选那么简单，
有三件事必须一起成立，每一件都是一条测试：

1. **次序**：装了 Chrome 的机器行为一位不变（chrome 优先）；只有 Edge 的机器
   落到 'msedge'；都没有才走打包版 chromium。
2. **装机判据**：`launch_artifact()` / `chromium_status()` 把 msedge 与 chrome
   同等对待 —— 用户自己的浏览器在场就没有东西要下载。这条判据装侧启动侧共用
   （见 `_launch_channel`），改漏一边就是"装了 119MB 却起不来"的再来一次。
3. **UA 成套**：headless Edge 原生 UA 带 'HeadlessEdg'（死穴），必须按真实
   版本构造，且要带 `Edg/<ver>` 令牌 —— UA 说 Chrome、TLS/Sec-CH-UA 说 Edge
   的自相矛盾指纹比 HeadlessEdg 更容易被认出来。

`_chrome_channel_cache` / `_chrome_version_cache` 是进程级缓存，每个用例先清。
"""

from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

from dhole_mcp import browser


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch):
    monkeypatch.setattr(browser, "_chrome_channel_cache", None)
    monkeypatch.setattr(browser, "_chrome_version_cache", {})


@pytest.fixture
def win_roots(monkeypatch, tmp_path):
    """把 win32 的系统浏览器探测**全部**收进临时目录：env 根、注册表、PATH。

    只顶 env 根已经不够了（2026-10-05）：检测链现在先看注册表 App Paths 再看 PATH，
    而本机真装着 Chrome/Edge —— 挂具不封这两条，"这台机器没装系统浏览器"的用例就会
    在装了浏览器的机器上读到真答案。今天那条绿本来就只是巧合。
    """
    missing = tmp_path / "no-such-root"
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "pf"))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "pf86"))
    monkeypatch.setenv("LOCALAPPDATA", str(missing))
    monkeypatch.setattr(browser, "_winreg_app_paths", lambda exe_name: [])
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
    (tmp_path / "pf").mkdir()
    (tmp_path / "pf86").mkdir()
    return tmp_path


def _install(root, rel, exe):
    p = root / rel
    p.mkdir(parents=True, exist_ok=True)
    (p / exe).write_bytes(b"MZ")
    return p / exe


def _force_win(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")


def _force_posix(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")


class TestDetectionOrder:
    def test_chrome_beats_edge(self, monkeypatch, win_roots):
        """两样都装时结果一位不变：chrome 仍是次序第一。"""
        _force_win(monkeypatch)
        _install(win_roots / "pf86", "Microsoft/Edge/Application", "msedge.exe")
        _install(win_roots / "pf", "Google/Chrome/Application", "chrome.exe")
        assert browser._detect_chrome_channel() == "chrome"

    def test_edge_only_machine_gets_msedge(self, monkeypatch, win_roots):
        """没装 Chrome 只装 Edge（Windows 出厂即如此）→ channel='msedge'。"""
        _force_win(monkeypatch)
        _install(win_roots / "pf86", "Microsoft/Edge/Application", "msedge.exe")
        assert browser._detect_chrome_channel() == "msedge"

    def test_neither_falls_to_bundled_chromium(self, monkeypatch, win_roots):
        _force_win(monkeypatch)
        assert browser._detect_chrome_channel() == "chromium"

    def test_posix_edge_by_path_name(self, monkeypatch):
        """POSIX 上 Edge 以 microsoft-edge 进 PATH。"""
        _force_posix(monkeypatch)
        monkeypatch.setattr(shutil, "which",
                            lambda name: "/usr/bin/microsoft-edge"
                            if name == "microsoft-edge" else None)
        assert browser._detect_chrome_channel() == "msedge"

    def test_posix_chromium_still_lands_in_chrome_row(self, monkeypatch):
        """distro chromium 一直是系统浏览器（真实指纹），归位不变。"""
        _force_posix(monkeypatch)
        monkeypatch.setattr(shutil, "which",
                            lambda name: "/usr/bin/chromium"
                            if name == "chromium" else None)
        assert browser._detect_chrome_channel() == "chrome"


class TestInstallCriterion:
    def test_edge_only_machine_needs_no_download(self, monkeypatch, win_roots):
        """装侧判据认 Edge：`launch_artifact()` 回 SYSTEM_CHROME，不碰下载。

        这条判据装侧启动侧共用（`_launch_channel`），漏掉 msedge 的后果是
        Edge-only 机器被误判"需要装 chromium"，193MB 白下。
        """
        _force_win(monkeypatch)
        _install(win_roots / "pf86", "Microsoft/Edge/Application", "msedge.exe")
        assert browser.launch_artifact() == browser.SYSTEM_CHROME

    def test_chromium_status_reports_the_edge_channel(self, monkeypatch, win_roots):
        _force_win(monkeypatch)
        _install(win_roots / "pf86", "Microsoft/Edge/Application", "msedge.exe")
        status = browser.chromium_status()
        assert status["present"] is True
        assert status["source"] == "system-chrome"
        assert "msedge" in status["detail"]

    def test_artifacts_census_names_the_channel(self, monkeypatch, win_roots):
        """`chromium_artifacts()` 是 doctor/updater 的普查面：
        system_chrome 语义已扩为"系统浏览器在场"，launch_channel 指名是谁。"""
        _force_win(monkeypatch)
        _install(win_roots / "pf86", "Microsoft/Edge/Application", "msedge.exe")
        arts = browser.chromium_artifacts()
        assert arts["system_chrome"] is True
        assert arts["launch_channel"] == "msedge"
        assert arts["launch_needs"] == browser.SYSTEM_CHROME


class TestEdgeUserAgent:
    def test_headless_ua_carries_the_edg_token(self, monkeypatch, win_roots):
        """按真实 msedge.exe 版本构造 UA，带 Edg/ 令牌，不带 Headless。"""
        _force_win(monkeypatch)
        _install(win_roots / "pf86", "Microsoft/Edge/Application", "msedge.exe")

        class _Done:
            stdout = "139.0.3405.119\n"

        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Done())
        ua = browser._get_chrome_ua("msedge")
        assert ua is not None
        assert "Headless" not in ua
        assert "Chrome/139.0.3405.119" in ua
        assert "Edg/139.0.3405.119" in ua

    def test_chrome_ua_gains_no_edg_token(self, monkeypatch, win_roots):
        """Chrome 的 UA 一位不变：没有 Edg/ 令牌（回归护栏）。"""
        _force_win(monkeypatch)
        _install(win_roots / "pf", "Google/Chrome/Application", "chrome.exe")

        class _Done:
            stdout = "154.0.8037.93\n"

        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Done())
        ua = browser._get_chrome_ua("chrome")
        assert ua is not None
        assert "Edg/" not in ua
        assert "Chrome/154.0.8037.93" in ua

    def test_cache_is_per_channel(self, monkeypatch, win_roots):
        """两个通道各存各的 UA —— 缓存若还是单值，第二通道会拿到第一通道的串。"""
        _force_win(monkeypatch)
        _install(win_roots / "pf86", "Microsoft/Edge/Application", "msedge.exe")
        _install(win_roots / "pf", "Google/Chrome/Application", "chrome.exe")

        versions = {r"chrome.exe": "154.0.8037.93",
                    r"msedge.exe": "139.0.3405.119"}

        class _Done:
            def __init__(self, v):
                self.stdout = v + "\n"

        def fake_run(argv, **kw):
            cmd = argv[-1] if isinstance(argv, list) else ""
            return _Done(next(v for exe, v in versions.items() if exe in cmd))

        monkeypatch.setattr(subprocess, "run", fake_run)
        ua_chrome = browser._get_chrome_ua("chrome")
        ua_edge = browser._get_chrome_ua("msedge")
        assert "Chrome/154." in ua_chrome and "Edg/" not in ua_chrome
        assert "Edg/139." in ua_edge

    def test_missing_executable_returns_none(self, monkeypatch, win_roots):
        """探测说有、文件没了（装完又卸）：返回 None 而不是抛异常。"""
        _force_win(monkeypatch)
        assert browser._get_chrome_ua("msedge") is None
