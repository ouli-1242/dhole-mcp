"""批次 10 → 操作员要一台看得见的浏览器时，这件事必须说话。

判据都来自 2026-10-05 那条真实使用的路：「帮我搜索抓取应该是**不显示**的，帮我操作、
登录此类应该**显示**，方便我随时辅助」。这句话里有两个事实，代码今天只满足第一个：

1. 隐身层确实是自己起的（`_launch_channel` → 检测 chrome → msedge → 内置 chromium），
   但 `headless` 在 `BrowserSession` 的签名里、在 6 个调用点上都写死成 True，从没上过
   wire（实测 `'headless' not in json.dumps(_TOOL_DEFS)`）—— 起的是看不见的那台；
2. 看不见就没法手工登录。`profile` 这颗是有的（`~/.dhole/profiles/<name>/`，跨调用
   持久），所以**缺的只有"窗口可见"这一颗**，不是整条持久化的路。

三件必须钉住的东西，按房内规矩写：

一、**可见性是操作员的决定，不是调用方的权力。** 不给 `web_read` / `web_act` 加
`headed=` 参数：弹一个带着用户登录态的可见窗口，与 submit 要 confirm、CDP attach 要
confirm 是同一类事 —— 问人。所以这一颗走 env/config 文件，且这条不许多开一扇门
（反向守卫：`headless` 与 `headed` 都不许出现在工具定义里）。

二、**说了"起浏览器"就得说清起的是哪一台、装的是哪一份。** 安装侧与启动侧共用
`_launch_channel` 那一个判据是批次 1 立的规矩（"装了 119MB 却起不来"的根因就是两处
各写一遍），强制 msedge 之后这条必须仍然成立。

三、**开着窗口不等于窗口不会被回收。** `AUTO_SESSION_IDLE_TIMEOUT=300` 只收 auto 会话；
人在里面打字时把它关掉，扔掉的是一半填好的登录表单，而那看起来像站点的问题。
"""
import json

import pytest

from dhole_mcp import env_registry, identity
from dhole_mcp import browser as B
from dhole_mcp.context import _stamp_browser_mode
from dhole_mcp.server import MasterFetchServer, ResponseModel


# ─── 1 · 两颗旋钮的注册与读取时机 ────────────────────────────────────────────

def test_both_knobs_are_registered_and_read_per_call():
    """`read_at="call"` 是这条能力"有时需要"的前提 —— 改了不必重启宿主。"""
    by = {e.name: e for e in env_registry.ALL}
    for name in ("DHOLE_BROWSER_HEADED", "DHOLE_BROWSER_CHANNEL"):
        assert name in by, f"{name} 没注册：`dhole config set` 会拒它，doctor 也看不见它"
        assert by[name].read_at == "call", (
            f"{name} 是 import 期读取的：操作员要切一次可见性就得重启 MCP")


def test_the_two_knobs_are_documented_in_the_scenario_they_belong_to():
    """旋钮的家在手册那一卷：`sys_guide(topic='config')` 送出去的就是这一份。"""
    from dhole_mcp import guide

    cfg = (guide._ROOT / "references" / "configuration.md")
    text = cfg.read_text(encoding="utf-8")
    assert "DHOLE_BROWSER_HEADED" in text and "DHOLE_BROWSER_CHANNEL" in text


# ─── 2 · 起哪一台：判据只有一份 ──────────────────────────────────────────────

def test_the_forced_channel_wins_over_detection(monkeypatch):
    monkeypatch.setenv("DHOLE_BROWSER_CHANNEL", "msedge")
    assert B._forced_browser_channel() == "msedge"
    assert B._launch_channel(False) == "msedge"


def test_an_unknown_channel_is_not_applied(monkeypatch):
    """`firefox` 不是 patchright 认得的 channel —— 宁可不改，也不要起不来。"""
    monkeypatch.setenv("DHOLE_BROWSER_CHANNEL", "firefox")
    assert B._forced_browser_channel() == ""
    assert B._launch_channel(False) == B._detect_chrome_channel()


def test_unset_channel_keeps_the_detection_order(monkeypatch):
    monkeypatch.delenv("DHOLE_BROWSER_CHANNEL", raising=False)
    assert B._launch_channel(False) == B._detect_chrome_channel()


def test_real_chrome_stays_pinned_to_chrome(monkeypatch):
    """那一档说"起的就是用户自己那个 Chrome"，点名换浏览器属于检测那一支的语义。"""
    monkeypatch.setenv("DHOLE_BROWSER_CHANNEL", "msedge")
    assert B._launch_channel(True) == "chrome"


def test_the_installed_artifact_follows_the_forced_channel(monkeypatch):
    """批次 1 那条规矩的延续：装哪一个与起哪一个共用同一个判据。"""
    monkeypatch.setenv("DHOLE_BROWSER_CHANNEL", "msedge")
    assert B.launch_artifact(False) == B.SYSTEM_CHROME, (
        "点名 Edge 却去下载 bundled chromium —— 两处各写一遍就是当年那起事故")
    monkeypatch.setenv("DHOLE_BROWSER_CHANNEL", "chromium")
    assert B.launch_artifact(False) == "chromium"


# ─── 3 · 可见与不可见 ────────────────────────────────────────────────────────

def test_headed_flag_flips_the_launch_mode(monkeypatch):
    monkeypatch.setenv("DHOLE_BROWSER_HEADED", "1")
    assert B._browser_headed() is True
    assert B._resolve_headless(True) is False, "操作员要窗口，起的却还是 headless 的那台"
    monkeypatch.setenv("DHOLE_BROWSER_HEADED", "0")
    assert B._resolve_headless(True) is True


def test_the_session_itself_honours_the_knob(monkeypatch):
    """覆盖发生在 `BrowserSession` 里，不在六个调用点各判一遍。"""
    monkeypatch.setenv("DHOLE_BROWSER_HEADED", "1")
    assert B.BrowserSession()._headless is False
    monkeypatch.setenv("DHOLE_BROWSER_HEADED", "0")
    assert B.BrowserSession()._headless is True


def test_visibility_is_never_a_caller_power():
    """反向守卫：`headless` / `headed` 都不许出现在工具定义里。

    这条守卫看着像"少了个功能"，实际是权限边界：可见窗口里跑着用户的登录态，
    让被页面正文诱导的那一方决定要不要把它弹出来，是把 submit/attach 问人的那件事
    变成不问。
    """
    blob = json.dumps(MasterFetchServer._TOOL_DEFS, ensure_ascii=False).lower()
    assert "headless" not in blob and "headed" not in blob


def test_a_visible_browser_is_not_reaped_while_hidden_ones_are():
    """5 分钟空闲关掉一个正在被打字的窗口，扔掉的是半张登录表单。"""
    import inspect

    body = inspect.getsource(MasterFetchServer._start_idle_monitor)
    assert "_browser_headed()" in body, (
        "空闲回收又只看时间了 —— 可见的窗口必须留在人手里")
    assert "AUTO_SESSION_IDLE_TIMEOUT" in body, "headless 那台照旧回收，别一刀切关掉"


# ─── 4 · 说出口：metadata.browser ────────────────────────────────────────────

def _row(fetcher_used="stealthy", metadata=None, cached=False):
    return ResponseModel(url="https://example.test/", status=200, content=["body"],
                         fetcher_used=fetcher_used, cached=cached,
                         metadata=metadata or {})


@pytest.fixture()
def _headed(monkeypatch):
    monkeypatch.setenv("DHOLE_BROWSER_HEADED", "1")
    yield
    monkeypatch.delenv("DHOLE_BROWSER_HEADED", raising=False)


def test_the_named_profile_is_said(_headed):
    identity.set_value("profile", "main")
    row = _row()
    _stamp_browser_mode(row)
    note = row.metadata["browser"]
    assert "visible window" in note and "main" in note
    assert "survive" in note, "登录活不活得下来，是这一句最该回答的"


def test_a_throwaway_window_says_that_instead(_headed):
    row = _row()
    _stamp_browser_mode(row)
    note = row.metadata["browser"]
    assert "THROWAWAY" in note and "nothing you log in here survives" in note
    assert "cred_defaults" in note, "只说问题不给出路，等于让操作员猜旋钮叫什么"


def test_rows_that_never_ran_a_browser_stay_clean(_headed):
    for fetcher in ("http", "cache", "none"):
        row = _row(fetcher_used=fetcher)
        _stamp_browser_mode(row)
        assert "browser" not in row.metadata, f"{fetcher} 那一层没起过浏览器，别声称起过"


def test_turning_the_knob_off_removes_a_stale_claim():
    """缓存里存着的一句 "visible window"，不许在旋钮关掉后继续跟着正文走。

    这就是 `_stamp_robots_mode` 当年同样的道理：这句话讲的是**本进程**的形态，
    而正文可能是别的进程、别的时刻写的。
    """
    row = _row(metadata={"browser": "visible window (DHOLE_BROWSER_HEADED), profile 'x'"})
    _stamp_browser_mode(row)
    assert "browser" not in row.metadata, (
        "操作员已经关掉可见窗口，回执却还在说窗口开着 —— 一句关于过去的现在时")
