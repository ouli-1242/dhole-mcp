"""剥掉环境变量的宿主，也必须找得到浏览器。2026-10-05 的浏览器层事故验收。

判据（全部 tied 到那次真实故障：Qoder spawn 的 dhole 进程 env 里没有 LOCALAPPDATA，
于是 `_browsers_root()` 给出**相对**路径 `ms-playwright`，按进程 CWD 解析 —— 安装子进程
按 driver 的规则写进 `C:\\Users\\<u>\\AppData\\Local\\ms-playwright` 并 exit 0，dhole 用
同一个坏函数复查，仍然说没有，于是调用方收到 "chromium install failed (exit 0) -
chromium-1228 is still missing"，而盘上那颗 exe 一直在那儿）：

1. win32 没有 LOCALAPPDATA 时，根目录落到 `home()/AppData/Local/ms-playwright`，
   与 driver 的 `defaultCacheDirectory = LOCALAPPDATA || homedir()/AppData/Local` 同式。
2. **任何** env/平台组合都不许给出相对路径 —— 这条是结构不变式，不是巧合。
3. 上游约定的 `PLAYWRIGHT_BROWSERS_PATH="0"`（落在包内 `.local-browsers`）要说得上话。
4. 相对 override 按 `INIT_CWD || cwd` 展开，和 driver 一样。
5. dhole 只读 driver 也读的那一颗变量：`PATCHRIGHT_BROWSERS_PATH` 那种"只有 dhole 读"
   的门必须消失 —— 它会让检查与启动各看一个目录，正是本次故障的形状。
6. 存在性判词与进程 CWD 无关（换目录得到不同答案是 bug，不是特性）。
7. 系统浏览器的检测扛得住被剥干净的 env：注册表 App Paths 不读 env、不起进程。
8. 安装根清单只有一份，两处检测共用（两份手抄已经漂过一次）。
9. 系统浏览器的版本号从文件系统的版本目录读；宿主不许起进程时 UA 仍然到位
   （读不到就静默不覆盖，等于让隐身档自称 HeadlessChrome 而不告诉任何人）。
"""

from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

import pytest

from dhole_mcp import browser

REV = 1228


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch):
    monkeypatch.setattr(browser, "_chrome_channel_cache", None)
    monkeypatch.setattr(browser, "_chrome_version_cache", {})
    browser.reset_install_state()
    yield
    browser.reset_install_state()


def _force_win(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")


def _bare_windows_env(monkeypatch, tmp_path):
    """一个"被宿主剥过"的 Windows 进程 env：没有 LOCALAPPDATA，也没有 PROGRAMFILES。"""
    for name in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
                 "PLAYWRIGHT_BROWSERS_PATH", "INIT_CWD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    _force_win(monkeypatch)


# ─── 1 · 根目录必须与 driver 同式 ─────────────────────────────────────────────

def test_the_windows_browsers_root_falls_back_to_the_home_appdata_local_dir(
        monkeypatch, tmp_path):
    _bare_windows_env(monkeypatch, tmp_path)
    assert browser._browsers_root() == (
        tmp_path / "AppData" / "Local" / "ms-playwright")


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
@pytest.mark.parametrize("browsers_path", [None, "", "0", "relative-dir"])
def test_no_environment_combination_yields_a_relative_browsers_root(
        monkeypatch, tmp_path, platform, browsers_path):
    """相对路径是本次事故的形状本身：任何组合都不得返回它。"""
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("PROGRAMFILES", raising=False)
    monkeypatch.delenv("INIT_CWD", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    if browsers_path is None:
        monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    else:
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", browsers_path)
    assert browser._browsers_root().is_absolute()


def test_the_documented_upstream_zero_value_means_the_package_local_browsers_dir(
        monkeypatch, tmp_path):
    pkg = tmp_path / "patchright_pkg"
    (pkg / "driver" / "package").mkdir(parents=True)
    monkeypatch.setattr(browser, "_package_dir", lambda: pkg)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")
    assert browser._browsers_root() == (
        pkg / "driver" / "package" / ".local-browsers")


def test_a_relative_override_is_expanded_against_init_cwd_like_the_driver_does(
        monkeypatch, tmp_path):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "browsers-here")
    monkeypatch.setenv("INIT_CWD", str(tmp_path))
    assert browser._browsers_root() == tmp_path / "browsers-here"


# ─── 2 · 只留 driver 也读的那一颗门 ───────────────────────────────────────────

def test_dhole_reads_exactly_the_browsers_path_variable_the_driver_reads():
    """`PATCHRIGHT_BROWSERS_PATH` 只有 dhole 读：留着它就是"检查与启动各看一个目录"。"""
    assert "PATCHRIGHT_BROWSERS_PATH" not in inspect.getsource(browser._browsers_root)
    src_root = Path(browser.__file__).parent
    strays = [p.name for p in src_root.glob("*.py")
              if "PATCHRIGHT_BROWSERS_PATH" in p.read_text(encoding="utf-8")]
    assert strays == [], f"仍然在读 driver 不认识的那颗门: {strays}"


# ─── 3 · 存在性判词与 CWD 无关（事故的机械复现）─────────────────────────────

def test_the_presence_verdict_does_not_depend_on_the_current_directory(
        monkeypatch, tmp_path, fake_home):
    """换个工作目录就说"二进制没装" —— 这条不设防，就没有第二次发现的机会。"""
    _bare_windows_env(monkeypatch, tmp_path)
    monkeypatch.setattr(browser, "_detect_chrome_channel", lambda: "chromium")
    monkeypatch.setattr(browser, "_expected_chromium_revision", lambda: REV)
    root = browser._browsers_root()
    artifact = root / browser._ARTIFACT_DIRS["chromium"].format(rev=REV)
    artifact.mkdir(parents=True)
    (artifact / "INSTALLATION_COMPLETE").write_bytes(b"")
    (artifact / "chrome-win64").mkdir()
    (artifact / "chrome-win64" / "chrome.exe").write_bytes(b"MZ")

    here = Path.cwd()
    other = tmp_path / "somewhere_else"
    first = tmp_path / "first"
    other.mkdir()
    first.mkdir()
    roots = []
    try:
        monkeypatch.chdir(first)
        roots.append(browser._browsers_root())
        present_first = browser.chromium_status(False)["present"]
        monkeypatch.chdir(other)
        roots.append(browser._browsers_root())
        present_second = browser.chromium_status(False)["present"]
    finally:
        monkeypatch.chdir(here)
    assert roots[0] == roots[1], f"同一台机器换了 CWD 就在找两个目录: {roots}"
    assert present_first and present_second, (
        "盘上同一颗二进制，换个 CWD 就说没装 —— 这正是 exit 0 那句谎的来路")


@pytest.fixture
def fake_home(tmp_path):
    return tmp_path


# ─── 4 · 检测扛得住被剥掉的 env ───────────────────────────────────────────────

def test_the_registry_probe_finds_a_system_browser_when_no_env_var_is_set(
        monkeypatch, tmp_path):
    """Qoder 那种 env 块里没有 PROGRAMFILES；注册表里那行是唯一的真话。"""
    _bare_windows_env(monkeypatch, tmp_path)
    exe = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(browser, "_winreg_app_paths", lambda name: [str(exe)])
    assert browser._detect_chrome_channel() == "chrome"


def test_the_win_install_roots_are_one_list_not_two_copies():
    """三根清单曾经被手抄两遍并且已经漂了；现在两处都只能走同一个入口。"""
    for fn in (browser._detect_chrome_channel, browser._system_browser_path):
        body = inspect.getsource(fn)
        assert "os.environ.get(\"PROGRAMFILES" not in body, fn.__name__
    assert "_win_system_browser_path" in inspect.getsource(
        browser._detect_chrome_channel)
    assert "_win_system_browser_path" in inspect.getsource(
        browser._system_browser_path)


# ─── 5 · 版本号不靠起进程（隐身档的 UA 不能静默降级）─────────────────────────

def test_the_system_browser_version_is_read_without_starting_a_process(
        monkeypatch, tmp_path):
    """宿主禁了进程创建时，UA 必须仍然带真实版本 —— 否则 Chrome 自称 HeadlessChrome。"""
    _force_win(monkeypatch)
    app = tmp_path / "Google" / "Chrome" / "Application"
    app.mkdir(parents=True)
    (app / "chrome.exe").write_bytes(b"MZ")
    for version in ("140.0.0.0", "154.0.8037.93"):
        d = app / version
        d.mkdir()
        (d / "chrome.exe").write_bytes(b"MZ")
    monkeypatch.setattr(browser, "_system_browser_path", lambda ch: str(app / "chrome.exe"))

    def _no_processes(*a, **k):
        raise OSError("process creation blocked by the host")

    monkeypatch.setattr(subprocess, "run", _no_processes)
    ua = browser._get_chrome_ua("chrome")
    assert ua and "Chrome/154.0.8037.93" in ua, ua
    assert "Headless" not in (ua or "")


def test_the_version_directory_lookup_prefers_the_highest(
        monkeypatch, tmp_path):
    """Edge 会留下多个版本目录；起的是最高的那一个，UA 也必须说那一个。"""
    app = tmp_path / "Microsoft" / "Edge" / "Application"
    app.mkdir(parents=True)
    (app / "msedge.exe").write_bytes(b"MZ")
    for version in ("154.0.4258.48", "154.0.4258.53", "99.0.0.0"):
        d = app / version
        d.mkdir()
        (d / "msedge.exe").write_bytes(b"MZ")
    assert browser._win_version_dir_version(str(app / "msedge.exe")) == "154.0.4258.53"
