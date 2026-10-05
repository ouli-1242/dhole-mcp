"""17.0 批次 1 · Stage B：浏览器层坏掉的时候，那句话要能自己解释自己。

钉住 2026-10-05T06:20Z 那次真实事故（dhole 17.0 作为 MCP 服务被宿主启动）的观察：
调用方收到

    browser_binary_missing: chromium install failed (exit 0) - chromium-1228 is still missing

而盘上 `%LOCALAPPDATA%\\ms-playwright\\chromium-1228` 一直在那儿，`INSTALLATION_COMPLETE`
也在。这句话之所以无法反驳，是因为它**没说自己是在哪儿看的**：根目录解析与 driver
不同式（宿主剥掉了 `LOCALAPPDATA` → `Path("") / "ms-playwright"` 成了相对路径 → 按
CWD 判存在），安装子进程写在正确的绝对目录、复查看的是 CWD 下面那个不存在的目录。

于是这一批评的是"下一次同样五分钟能诊断"，共七条判据：

1. 缺席的那句必须带上**它搜过的绝对目录** —— 一句没有地点的"没装"没法被反驳。
2. 安装 exit 0 而复查仍判缺席时，如果那份 artifact 真的躺在**别的**根里，两句路径都要
   出现；但 `present` 绝不许因此翻真（driver 起的是它自己那一份）。
3. 什么都没找到时保持原来的句子（`test_ensure_chromium.py:188/:338` 钉的两个子串）。
4. 一次失败之后 15 分钟内不再重付 193MB：`subprocess.run` 的**次数**是判据，不是文案。
5. 退避期间磁盘上真的出现了那一份 → 立刻赢（闸不许把好消息挡在门外）。
6. "在场但起不来"是第三种状态，记下来时不得伪装成"没装"（`browser_binary_missing`
   覆盖不了它），也不得把渲染超时那种等待算进浏览器判词。
7. 退避与启动判词是进程内/磁盘两份，各自都要过冷进程那道形状守卫。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from dhole_mcp import browser

REV = 1228


class _Done:
    """subprocess.run 的返回值替身。"""

    def __init__(self, code=0, err=""):
        self.returncode = code
        self.stderr = err
        self.stdout = err


@pytest.fixture(autouse=True)
def fresh_install_state():
    """退避、期望修订号、状态镜像都是进程内的 —— 用例之间必须互相看不见。"""
    browser.reset_install_state()
    yield
    browser.reset_install_state()


@pytest.fixture
def fake_package(monkeypatch, tmp_path):
    pkg = tmp_path / "patchright_pkg"
    (pkg / "driver" / "package").mkdir(parents=True)
    (pkg / "driver" / "package" / "browsers.json").write_text(
        json.dumps({"browsers": [{"name": "chromium", "revision": REV,
                                  "installByDefault": True}]}),
        encoding="utf-8")
    monkeypatch.setattr(browser, "_package_dir", lambda: pkg)
    return pkg


@pytest.fixture
def fake_browsers_dir(monkeypatch, tmp_path):
    """dhole 看的那个根：绝对路径、空、什么 artifact 都没有。"""
    base = tmp_path / "ms-playwright"
    base.mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(base))
    return base


@pytest.fixture
def no_system_chrome(monkeypatch):
    """让这一次真的走 bundled 路线（本机装着 Chrome，注册表探测会赢）。"""
    monkeypatch.setattr(browser, "_detect_chrome_channel", lambda: "chromium")


@pytest.fixture
def isolated_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("DHOLE_HOME", str(home))
    return home


def _artifact_dir(base: Path, rev=REV, kind="chromium", *, complete=True) -> Path:
    d = base / browser._ARTIFACT_DIRS[kind].format(rev=rev)
    d.mkdir(parents=True, exist_ok=True)
    if complete:
        (d / "INSTALLATION_COMPLETE").write_text("", encoding="utf-8")
    return d


# ─── 1 · 缺席的那句要说清它去哪儿看过 ────────────────────────────────────

class TestAnAbsenceNamesItsLocation:

    def test_status_reports_the_absolute_root_it_checked(self, fake_package,
                                                        fake_browsers_dir,
                                                        no_system_chrome):
        status = browser.chromium_status()
        assert status["present"] is False, status
        assert Path(status["searched_in"]).is_absolute(), status
        assert status["searched_in"] == str(fake_browsers_dir), status

    def test_status_names_which_of_the_three_states_this_is(self, fake_package,
                                                            fake_browsers_dir,
                                                            no_system_chrome):
        """`present` 是一个布尔，而故障有三种形状。三态用名字分，不靠调用方猜。"""
        assert browser.chromium_status()["reason"] == "bundled_absent"
        _artifact_dir(fake_browsers_dir)
        assert browser.chromium_status()["reason"] == "bundled_present"

    def test_a_system_browser_never_touches_the_bundled_root(
            self, fake_package, monkeypatch):
        """A-3 比 A-1 更值钱的那一半，钉成可执行的形式：检测赢的时候，bundled 根连读
        都不读一次。

        这里故意让 `_browsers_root()` 抛 —— 它真会抛（`PLAYWRIGHT_BROWSERS_PATH="0"`
        而 patchright 又没装的时候）。这一次明明有一个能用的浏览器，一个没参与决策的
        目录不许把它炸掉。
        """
        monkeypatch.setattr(browser, "_detect_chrome_channel", lambda: "chrome")
        monkeypatch.setattr(browser, "_system_browser_path",
                            lambda channel: "C:/somewhere/chrome.exe")

        def boom():
            raise AssertionError("_browsers_root() must not be consulted here")

        monkeypatch.setattr(browser, "_browsers_root", boom)
        status = browser.chromium_status()
        assert status["present"] is True, status
        assert status["reason"] == "system_browser", status
        assert status["browser_path"] == "C:/somewhere/chrome.exe", status
        assert status["searched_in"] == "", status

    def test_an_unresolvable_root_is_its_own_state_not_a_fake_absence(
            self, fake_package, monkeypatch):
        """第四态：不是"没装"，是"没地方可看" —— 并进 bundled_absent 会让人去跑一次
        必败的安装（driver 自己会写到它解析得出来的地方，那个地方还不是这一个）。"""
        monkeypatch.setattr(browser, "_detect_chrome_channel", lambda: "chromium")
        monkeypatch.setattr(browser, "_browsers_root",
                            lambda: (_ for _ in ()).throw(
                                RuntimeError("PLAYWRIGHT_BROWSERS_PATH=0 but "
                                             "patchright is not importable")))
        status = browser.chromium_status()
        assert status["present"] is False, status
        assert status["reason"] == "root_unresolvable", status
        assert "PLAYWRIGHT_BROWSERS_PATH" in status["detail"], status

    def test_the_failure_detail_carries_the_directory_it_looked_in(
            self, fake_package, fake_browsers_dir, no_system_chrome, monkeypatch,
            isolated_home):
        """安装桩什么都不产出（磁盘上确实没有）：那句失败里必须有地点。"""
        monkeypatch.setattr(browser.subprocess, "run", lambda *a, **k: _Done())
        ok, detail = browser.ensure_chromium()
        assert ok is False, detail
        assert str(fake_browsers_dir) in detail, detail
        assert Path(fake_browsers_dir).is_absolute()


# ─── 2 · "装上了却看不到"是另一种失败 ────────────────────────────────────

class TestInstalledElsewhere:

    def test_an_artifact_written_to_a_different_root_prints_both_paths(
            self, fake_package, fake_browsers_dir, no_system_chrome, monkeypatch,
            isolated_home, tmp_path):
        """本次事故的机械复现：安装成功写到 CWD 下面那个根，复查看的却是另一个根。

        两句路径都在，操作员才可能自己看出是两边解析不一致 —— 而不是再跑一遍
        `patchright install chromium`，然后第一百次看到 exit 0 + "still missing"。
        """
        elsewhere = tmp_path / "elsewhere" / "ms-playwright"

        def installer(cmd, *a, **k):
            _artifact_dir(elsewhere)
            return _Done()

        monkeypatch.setattr(browser.subprocess, "run", installer)
        monkeypatch.setattr(browser, "_artifact_elsewhere",
                            lambda wanted: str(elsewhere / wanted))
        ok, detail = browser.ensure_chromium()
        assert ok is False, detail
        assert str(elsewhere / f"chromium-{REV}") in detail, detail
        assert str(fake_browsers_dir) in detail, detail
        assert browser.chromium_status()["present"] is False

    def test_finding_it_elsewhere_never_makes_the_status_present(
            self, fake_package, fake_browsers_dir, no_system_chrome, monkeypatch,
            isolated_home, tmp_path):
        """替 driver 认一个它不会去的目录，等于放行一次必败的 launch。

        `test_ensure_chromium.py:111` 与 `test_doctor_items.py` 禁的就是这个方向：
        "磁盘上有另一份"永远不等于"这次起得来"。
        """
        elsewhere = tmp_path / "elsewhere" / "ms-playwright"
        monkeypatch.setattr(browser.subprocess, "run",
                            lambda cmd, *a, **k: (_artifact_dir(elsewhere), _Done())[1])
        ok, _detail = browser.ensure_chromium()
        assert ok is False
        assert browser.chromium_status()["present"] is False

    def test_nothing_found_anywhere_keeps_the_two_pinned_substrings(
            self, fake_package, fake_browsers_dir, no_system_chrome, monkeypatch,
            isolated_home):
        """守卫反向的那半：加了地点不许动走原句。这两个子串是别处的合同。"""
        monkeypatch.setattr(browser.subprocess, "run", lambda *a, **k: _Done(0))
        ok, detail = browser.ensure_chromium()
        assert ok is False, detail
        assert "install failed" in detail, detail
        assert f"chromium-{REV} is still missing" in detail, detail


# ─── 3 · 一次失败不许多次重付 193MB ───────────────────────────────────────

class TestTheRetryBackoff:

    def _counting_installer(self, monkeypatch, code=1):
        calls: list[list] = []

        def installer(cmd, *a, **k):
            calls.append(cmd)
            return _Done(code)

        monkeypatch.setattr(browser.subprocess, "run", installer)
        return calls

    def test_the_second_call_in_the_window_does_not_start_a_second_download(
            self, fake_package, fake_browsers_dir, no_system_chrome, monkeypatch,
            isolated_home):
        """判据是**次数**，不是文案：一次失败的安装可能是 193MB，而这句话会撒谎。"""
        calls = self._counting_installer(monkeypatch)
        first_ok, first_detail = browser.ensure_chromium()
        second_ok, second_detail = browser.ensure_chromium()
        assert first_ok is False and second_ok is False
        assert len(calls) == 1, f"第二次又起了一遍安装子进程：{calls}"
        assert "not being retried" in second_detail, second_detail

    def test_the_backoff_message_says_what_failed_and_where(
            self, fake_package, fake_browsers_dir, no_system_chrome, monkeypatch,
            isolated_home):
        self._counting_installer(monkeypatch)
        browser.ensure_chromium()
        _ok, detail = browser.ensure_chromium()
        assert str(fake_browsers_dir) in detail, detail
        assert browser._INSTALL_COMMAND_STR in detail, detail

    def test_the_timeout_and_could_not_run_paths_arm_the_backoff_too(
            self, fake_package, fake_browsers_dir, no_system_chrome, monkeypatch,
            isolated_home):
        """三条失败路径都要置位。只在那条"exit 非零"上置位，就等于慢链路和坏链路
        每次调用照样重下一遍 —— 而慢链路恰是唯一现实的失败原因。"""
        def slow(cmd, *a, **k):
            raise subprocess.TimeoutExpired(cmd, 1)

        monkeypatch.setattr(browser.subprocess, "run", slow)
        ok, detail = browser.ensure_chromium()
        assert ok is False, detail
        assert browser._install_retry_after > time.time(), "超时没有置退避"
        calls: list = []
        monkeypatch.setattr(browser.subprocess, "run",
                            lambda cmd, *a, **k: calls.append(cmd) or _Done())
        _ok2, detail2 = browser.ensure_chromium()
        assert "not being retried" in detail2, detail2
        assert calls == [], f"退避窗口里又起了一次子进程：{calls}"

    def test_a_could_not_run_failure_arms_the_backoff_too(
            self, fake_package, fake_browsers_dir, no_system_chrome, monkeypatch,
            isolated_home):
        """第三条：连子进程都没起来（权限、坏解释器）。它同样是"再试一遍也一样"。"""
        def broken(cmd, *a, **k):
            raise OSError("[WinError 5] Access is denied")

        monkeypatch.setattr(browser.subprocess, "run", broken)
        ok, detail = browser.ensure_chromium()
        assert ok is False, detail
        assert browser._install_retry_after > time.time()

    def test_the_backoff_is_in_process_only_and_the_record_still_names_the_dir(
            self, fake_package, fake_browsers_dir, no_system_chrome, monkeypatch,
            isolated_home):
        """退避活在进程内存里，磁盘上那条记录只是事实。

        跨进程抑制会把"刚在另一个终端手动装好了"的人晾在原地 —— 那正是本次事故里
        操作员实际做的事。这里断言的是：状态文件写了，而判定没用它。
        """
        self._counting_installer(monkeypatch)
        browser.ensure_chromium()
        state = json.loads((isolated_home / "browser_install.json").read_text(
            encoding="utf-8"))
        assert state["status"] == "failed", state
        assert state["looked_in"] == str(fake_browsers_dir), state

    def test_a_binary_that_appears_during_the_backoff_wins_immediately(
            self, fake_package, fake_browsers_dir, no_system_chrome, monkeypatch,
            isolated_home):
        """坏消息被抑制，好消息不许被抑制：闸只挡"再下一次"，不挡"其实已经有了"。"""
        calls = self._counting_installer(monkeypatch)
        assert browser.ensure_chromium()[0] is False
        _artifact_dir(fake_browsers_dir)
        ok, detail = browser.ensure_chromium()
        assert ok is True, detail
        assert len(calls) == 1, calls

    def test_a_successful_install_clears_the_window(
            self, fake_package, fake_browsers_dir, no_system_chrome, monkeypatch,
            isolated_home):
        """成功之后必须清零，否则下一次真坏掉的安装会被上一次的成功挡住 15 分钟。

        窗口"过期"是直接把 `_install_retry_after` 拨回过去做的：睡 900 秒不是这条断言
        要买的东西，而"过期之后还能装"由上面那条"退避期间磁盘变在场则立即赢"覆盖。
        """
        calls = self._counting_installer(monkeypatch)
        assert browser.ensure_chromium()[0] is False
        assert browser._install_retry_after > time.time()
        browser._install_retry_after = time.time() - 1
        monkeypatch.setattr(browser.subprocess, "run",
                            lambda cmd, *a, **k: (_artifact_dir(fake_browsers_dir),
                                                   _Done())[1])
        ok, detail = browser.ensure_chromium()
        assert ok is True, detail
        assert browser._install_retry_after == 0.0
        assert len(calls) == 1, calls

    def test_the_backoff_survives_a_cold_import(self, isolated_home):
        """镜像 `test_ensure_chromium.py:861` 那条教训：模块级必须有默认值。

        `_install_retry_after` 只被 `global` 赋过值，"一次都没装过的进程里读它就是
        NameError" 这个坑当年在 `_install_state` 上踩过一次，这一颗是同一形状。

        用子进程而不是 `importlib.reload`：reload 会换掉 `dhole_mcp.browser` 里所有类的
        对象身份，而别的测试文件是 `from dhole_mcp.browser import X` 早就绑死旧类的 ——
        一 reload，那些文件里的 `pytest.raises(X)` 从此接不住自己模块抛出的那个 X。实测
        过：套件随机序时 `test_cdp_attach_g33` 因此变红，而它自己单跑永远绿。
        """
        env = {**os.environ, "DHOLE_HOME": str(isolated_home)}
        out = subprocess.run(
            [sys.executable, "-c",
             "import dhole_mcp.browser as b; "
             "print(b._install_retry_after, b.INSTALL_BACKOFF_S, b.read_install_state())"],
            capture_output=True, text=True, env=env, timeout=120)
        assert out.returncode == 0, out.stderr
        assert "0.0" in out.stdout, out.stdout
        assert "{}" in out.stdout, out.stdout


# ─── 4 · 第三态：在场、起不来 ────────────────────────────────────────────

class TestPresentButUnlaunchable:

    def test_a_denied_launch_is_recorded_in_the_same_state_file(
            self, fake_package, fake_browsers_dir, no_system_chrome, isolated_home):
        """记在同一个文件的 `launch` 段：一文件一段一写者，不另开一颗状态文件。"""
        _artifact_dir(fake_browsers_dir)
        browser.record_launch_failure(
            RuntimeError("browserType.launch: Cannot access executable "
                         "C:\\x\\chrome.exe (os error 5). (0x5): Access is denied."))
        seg = browser.recorded_launch_failure()
        assert seg["status"] == "failed", seg
        assert "Access is denied" in seg["error"], seg
        assert seg["searched_in"] == str(fake_browsers_dir), seg

    def test_an_absent_binary_is_not_recorded_as_unlaunchable(
            self, fake_package, fake_browsers_dir, no_system_chrome, isolated_home):
        """条件 (i)：盘上什么都没有时那是**安装**问题，安装段已经记了它。"""
        browser.record_launch_failure(
            RuntimeError("browserType.launch: Access is denied. (0x5)"))
        assert browser.recorded_launch_failure() == {}

    def test_a_page_timeout_is_not_read_as_a_broken_browser(
            self, fake_package, fake_browsers_dir, no_system_chrome, isolated_home):
        """条件 (ii) 的反面：首行不在窄表里就一个字都不写。

        "Timeout 30000ms exceeded" 是站点慢。把它写成浏览器判词，下一次调用方就会
        去修一个本来好好的浏览器层 —— 这是本次事故里最容易犯的过度归因。
        """
        _artifact_dir(fake_browsers_dir)
        browser.record_launch_failure(
            TimeoutError("Timeout 30000ms exceeded. navigating to https://example.com/"))
        assert browser.recorded_launch_failure() == {}

    def test_the_record_is_throttled_on_the_launch_hot_path(
            self, fake_package, fake_browsers_dir, no_system_chrome, isolated_home,
            monkeypatch):
        """60 秒节流：它站在启动热路径上，一次坏浏览器 + 一百次调用 = 一百次文件写。"""
        _artifact_dir(fake_browsers_dir)
        first = RuntimeError("browserType.launch: Access is denied. (0x5)")
        browser.record_launch_failure(first)
        stamp = browser.recorded_launch_failure()["at"]
        browser.record_launch_failure(
            RuntimeError("browserType.launch: executable doesn't exist"))
        assert browser.recorded_launch_failure()["at"] == stamp

    def test_the_recorder_never_raises_even_when_the_disk_refuses(
            self, fake_package, fake_browsers_dir, no_system_chrome, isolated_home,
            monkeypatch):
        """诊断管路不能自己变成新故障面 —— 这条本来就是为了让故障更好诊断。"""
        _artifact_dir(fake_browsers_dir)
        monkeypatch.setattr(browser, "_install_state_path",
                            lambda: str(isolated_home / "no" / "deep.json"))
        browser.record_launch_failure(
            RuntimeError("browserType.launch: Access is denied. (0x5)"))

    def test_the_launch_record_lands_owner_only(
            self, fake_package, fake_browsers_dir, no_system_chrome, isolated_home,
            monkeypatch):
        """它记的是这台机器的浏览器路径与 OS 错误原文，按同一条规矩收紧。"""
        _artifact_dir(fake_browsers_dir)
        chmods = []
        monkeypatch.setattr(browser.os, "chmod",
                            lambda p, m: chmods.append((str(p), oct(m))))
        browser.record_launch_failure(
            RuntimeError("browserType.launch: Access is denied. (0x5)"))
        assert any(oct(0o600) == m for _p, m in chmods), chmods

    def test_an_install_record_survives_a_launch_record_and_vice_versa(
            self, fake_package, fake_browsers_dir, no_system_chrome, isolated_home,
            monkeypatch):
        """两段各写各的：一次安装不得抹掉启动判词，反之亦然。

        这正是"安装 done + launch 被拒"那个组合的形状 —— 抹掉任何一段，剩下的那段
        就把故事讲成一个错误的结论。
        """
        _artifact_dir(fake_browsers_dir)
        browser.record_launch_failure(
            RuntimeError("browserType.launch: Access is denied. (0x5)"))
        browser._record_install({"status": "done", "revision": REV,
                                 "command": "x", "path": "y",
                                 "finished_at": time.time()})
        data = json.loads((isolated_home / "browser_install.json").read_text(
            encoding="utf-8"))
        assert data["status"] == "done", data
        assert data["launch"]["status"] == "failed", data


# ─── 5 · 分类：起不来不是渲染超时 ────────────────────────────────────────

class TestTheClassificationOrder:

    def test_timed_out_while_launching_browser_is_not_a_site_timeout(self):
        """镜像 `test_v17_fix_plan.py:463` 钉住的那条顺序规矩。

        driver 在进程创建被拒时说的就是 "Timed out while launching browser"。它必须先
        被读成启动失败，否则调用方拿到 "raise timeout=" —— 一条对着慢站点的建议，
        而这台机器的浏览器根本起不来。
        """
        from dhole_mcp.errors import classify_network_error

        category, hint = classify_network_error(
            "browserType.launch: Timed out while launching browser")
        assert category == "browser_launch", category
        assert "not a site problem" in hint, hint

    def test_a_plain_navigation_timeout_still_classifies_as_timeout(self):
        """反向守卫：`browser_launch` 不得把普通的站点慢一起吃掉。"""
        from dhole_mcp.errors import classify_network_error

        assert classify_network_error(
            "Timeout 30000ms exceeded. navigating to https://example.com/")[0] == "timeout"

    def test_the_action_hint_for_a_refused_launch_is_not_the_rendering_hint(self):
        """`_BROWSER_ERROR_HINTS` 里那颗新条目必须在 timeout 那条之前命中。"""
        from dhole_mcp.actions import _screenshot_error_message

        out = _screenshot_error_message(RuntimeError(
            "browserType.launchPersistentContext: Cannot access executable "
            "C:\\x\\chrome.exe (os error 5). (0x5): Access is denied."))
        assert "refused to start" in out, out
        assert "raise timeout=" not in out, out

    def test_a_process_creation_denial_is_recognised_from_its_own_wording(self):
        assert browser._is_launch_denied(
            "Error: browserType.launch: Access is denied. (0x5)") is True
        assert browser._is_launch_denied("拒绝访问。 (0x5)") is True
        assert browser._is_launch_denied(
            "Timeout 30000ms exceeded while waiting for navigation") is False


# ─── 6 · doctor 不再只报一个布尔 ─────────────────────────────────────────

class TestDoctorNamesTheSearchedDirectory:

    def _rows(self, capsys):
        """`capsys.readouterr()` 读一次就清空，所以整份输出先存下来再挑行。"""
        return capsys.readouterr().out.splitlines()

    def test_the_binary_row_and_its_evidence_are_two_rows(
            self, fake_package, fake_browsers_dir, no_system_chrome, isolated_home,
            capsys):
        """一句里既放结论又放绝对目录，会被 `_short(..., 280)` 的保尾截法吃掉结论那一头
        （实测：加进目录之后 `chromium-1228 not installed` 先被截掉）。所以证据单独成行
        —— 这一条钉的是"两行都在"，不是某个措辞。"""
        from dhole_mcp import updater
        updater.doctor()
        lines = self._rows(capsys)
        binary = [x for x in lines if "chromium binary" in x]
        root = [x for x in lines if "browsers root" in x]
        assert binary and root, lines
        assert str(fake_browsers_dir) in root[0], root
        assert "chromium-1228" in binary[0], binary

    def test_a_recorded_launch_failure_makes_the_binary_row_not_ok(
            self, fake_package, fake_browsers_dir, no_system_chrome, isolated_home,
            capsys):
        """文件系统检查说"就绪"、launch 说"起不来"的时候，信那个付过代价的。"""
        from dhole_mcp import updater
        _artifact_dir(fake_browsers_dir)
        browser.record_launch_failure(
            RuntimeError("browserType.launch: Access is denied. (0x5)"))
        updater.doctor()
        out = "\n".join(self._rows(capsys))
        assert "chromium binary" in out, out
        assert "refused" in out, out

    def test_a_system_browser_row_does_not_print_a_root_it_never_read(
            self, fake_package, fake_browsers_dir, monkeypatch, isolated_home,
            capsys):
        """这次决策走的是用户自己的浏览器，bundled 根连读都没读。

        印一个没参与过的目录，等于把操作员带去改一颗与此无关的 env —— 而本次事故里
        真正值得看的那一侧是注册表/`PROGRAMFILES`。
        """
        from dhole_mcp import updater
        monkeypatch.setattr(browser, "_detect_chrome_channel", lambda: "msedge")
        monkeypatch.setattr(browser, "_system_browser_path",
                            lambda channel: "C:/Edge/msedge.exe")
        updater.doctor()
        lines = self._rows(capsys)
        assert any("msedge.exe" in x for x in lines), lines
        assert not [x for x in lines if "browsers root" in x], lines
