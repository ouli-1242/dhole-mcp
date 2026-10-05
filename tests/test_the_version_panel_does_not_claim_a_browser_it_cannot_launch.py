"""17.0 批次 1 · Stage C：能力面板与 doctor 不再用 import 检查替浏览器作保。

2026-10-05T06:20Z，同一台机器、同一分钟打印着两句话：

    dhole -v            →  browser tier ✓ ready (anti-bot / JS rendering / screenshot)
    web_act(...)        →  browser_binary_missing: chromium install failed (exit 0)

因为那一行等于 `_has_module("patchright") and _has_module("playwright")` —— 两个 import
检查。**能导入一个 driver 不等于它有一台能起的浏览器**，而"silent degradation"这套设计
里，`-v` 是操作员唯一的自查入口：它说谎的时候，现场没有任何别的东西会说真话。

七条判据：

1. 面板那一行读文件系统（`chromium_status()`），不读 import 表。
2. 盘上有但从没起过 → 说的是"未验证"，不是"就绪"。
3. 记录里有一次被拒的启动 → 那一行不 ok。
4. `-v` 永不启动浏览器（启动一次 3–5 秒，那是每一个宿主的税）；`--doctor` 才付这笔税。
5. 探针**永不触发下载**：盘上什么都没有时它立刻返回并说明跳过了什么。
6. 套件里探针自陈被跳过（静默少一行 = 这一项不存在）。
7. 任何 dhole 读物都不许再把用户支去跑 `playwright install chromium`（那颗命令装的是
   上游 playwright 的浏览器，patchright 的 driver 只认自己清单里那一版）。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from dhole_mcp import browser, updater

REV = 1228


@pytest.fixture(autouse=True)
def fresh_install_state():
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
    base = tmp_path / "ms-playwright"
    base.mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(base))
    return base


@pytest.fixture
def no_system_chrome(monkeypatch):
    monkeypatch.setattr(browser, "_detect_chrome_channel", lambda: "chromium")


@pytest.fixture
def isolated_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("DHOLE_HOME", str(home))
    return home


def _artifact(base: Path, rev=REV, kind="chromium") -> Path:
    d = base / browser._ARTIFACT_DIRS[kind].format(rev=rev)
    d.mkdir(parents=True, exist_ok=True)
    (d / "INSTALLATION_COMPLETE").write_text("", encoding="utf-8")
    return d


def _row(label: str) -> tuple[str, str, bool]:
    return next(r for r in updater.capabilities() if r[0] == label)


# ─── 1 · 那一行问的是文件系统 ─────────────────────────────────────────────

class TestThePanelAsksTheDisk:

    def test_importable_driver_with_nothing_on_disk_is_not_reported_ready(
            self, fake_package, fake_browsers_dir, no_system_chrome):
        """这条就是事故的那一屏：两颗 import 都为真，浏览器层整体不可用。

        桩故意把 `_has_module` 钉成真 —— 面板从此不许靠它说话。
        """
        label, state, ok = _row("browser tier")
        assert "binaries not downloaded" in state, state
        assert ok is True, "还没装的那一份会在第一次抓取时装上，那不是缺依赖"
        assert "ready (anti-bot" not in state, state

    def test_a_driver_that_cannot_be_imported_is_still_the_missing_case(
            self, monkeypatch):
        monkeypatch.setattr(updater, "_has_module", lambda name: False)
        label, state, ok = _row("browser tier")
        assert ok is False, state
        assert "HTTP-only" in state, state

    def test_the_missing_row_names_the_command_patchright_actually_wants(
            self, monkeypatch):
        """`playwright install chromium` 装的是另一家浏览器；driver 只认自己清单那一版。"""
        monkeypatch.setattr(updater, "_has_module", lambda name: False)
        _label, state, _ok = _row("browser tier")
        assert "python -m patchright install chromium" in state, state
        assert "playwright install chromium" not in state.replace(
            "patchright install chromium", ""), state

    def test_an_unresolvable_root_is_said_as_a_knob_not_as_an_absence(
            self, fake_package, monkeypatch):
        monkeypatch.setattr(browser, "_detect_chrome_channel", lambda: "chromium")
        monkeypatch.setattr(browser, "_browsers_root",
                            lambda: (_ for _ in ()).throw(RuntimeError("bad override")))
        _label, state, ok = _row("browser tier")
        assert ok is False, state
        assert "PLAYWRIGHT_BROWSERS_PATH" in state, state


# ─── 2 · "在盘上"与"起得来"是两件事 ──────────────────────────────────────

class TestPresentIsNotVerified:

    def test_a_binary_that_has_never_been_launched_says_unverified(
            self, fake_package, fake_browsers_dir, no_system_chrome, isolated_home):
        _artifact(fake_browsers_dir)
        _label, state, ok = _row("browser tier")
        assert ok is True, state
        assert "launch not verified" in state, state
        assert "ready" not in state, state

    def test_a_recorded_launch_refusal_takes_the_row_off_ok(
            self, fake_package, fake_browsers_dir, no_system_chrome, isolated_home):
        """文件系统说是绿的、上一启动说是红的 —— 信那个付过代价的。"""
        _artifact(fake_browsers_dir)
        browser.record_launch_failure(RuntimeError(
            "browserType.launch: Cannot access executable chrome.exe (0x5)"))
        _label, state, ok = _row("browser tier")
        assert ok is False, state
        assert "refused" in state, state

    def test_the_refusal_is_attributed_to_a_launch_not_to_an_install(
            self, fake_package, fake_browsers_dir, no_system_chrome, isolated_home):
        """把"起不来"印成"没装"，用户会重跑一次安装 —— 而那正是本次事故里跑了 100 次
        还返回 exit 0 的那一步。"""
        _artifact(fake_browsers_dir)
        browser.record_launch_failure(RuntimeError(
            "browserType.launch: Access is denied. (0x5)"))
        _label, state, _ok = _row("browser tier")
        assert "not downloaded" not in state, state
        assert "install" not in state, state


# ─── 3 · `-v` 不起浏览器，`--doctor` 才付这笔税 ──────────────────────────

class TestTheVersionPanelNeverLaunches:

    def test_printing_the_capability_panel_starts_no_browser(
            self, fake_package, fake_browsers_dir, no_system_chrome, isolated_home,
            monkeypatch, capsys):
        """`print_capabilities()` 一行两个读者（`-v` 与 doctor），税只该由后者付。

        这里不数时间而数调用：0.2 秒与 5 秒的差别在一台慢机器上会被读成"恰好如此"。
        """
        _artifact(fake_browsers_dir)
        calls = []
        monkeypatch.setattr(browser, "launch_probe_async",
                            lambda *a, **k: calls.append("probe"))
        monkeypatch.setattr(browser.subprocess, "run",
                            lambda *a, **k: calls.append("subprocess"))
        updater.print_capabilities()
        assert calls == [], calls
        assert "browser tier" in capsys.readouterr().out

    def test_the_launch_probe_is_the_only_row_that_earns_the_word_launches(
            self, fake_package, fake_browsers_dir, no_system_chrome, isolated_home,
            monkeypatch):
        """探针必须真起一次，并把"起的是哪一台、起了多久"说出来。"""
        async def fake_probe(timeout_s: float = 25.0) -> dict:
            return {"channel": "chromium", "artifact": "chromium", "elapsed_s": 4.2}

        _artifact(fake_browsers_dir)
        monkeypatch.setattr(browser, "launch_probe_async", fake_probe)
        checks: list = []
        monkeypatch.setattr(updater, "LIVE_CHECKS", True)
        updater._append_browser_launch(checks)
        row = next(c for c in checks if c[0] == "chromium launch")
        assert row[1] == "ok", row
        assert "4.2s" in row[2] or "chromium" in row[2], row


# ─── 4 · 探针永不触发下载，也不静默 ──────────────────────────────────────

class TestTheProbeNeverInstallsAndNeverSaysNothing:

    def test_an_absent_binary_skips_the_probe_without_installing(
            self, fake_package, fake_browsers_dir, no_system_chrome, isolated_home,
            monkeypatch):
        """装了 193MB 的"体检"不是体检。这条靠跳闸证明，而不是靠读代码相信。"""
        def tripwire(*a, **k):
            raise AssertionError("_append_browser_launch 触发了安装器")

        monkeypatch.setattr(browser, "ensure_chromium", tripwire)
        monkeypatch.setattr(browser, "ensure_chromium_async", tripwire)
        monkeypatch.setattr(browser.subprocess, "run", tripwire)
        monkeypatch.setattr(updater, "LIVE_CHECKS", True)
        checks: list = []
        updater._append_browser_launch(checks)
        row = next(c for c in checks if c[0] == "chromium launch")
        assert row[1] == "info", row
        assert "nothing to launch" in row[2], row
        assert "chromium-1228" in row[2], row

    def test_the_suite_says_out_loud_that_it_skipped_the_probe(
            self, fake_package, fake_browsers_dir, no_system_chrome, isolated_home):
        """conftest 关掉 live 项之后，静默少一行会让人以为这一项本来就不存在。"""
        checks: list = []
        updater._append_browser_launch(checks)
        row = next(c for c in checks if c[0] == "chromium launch")
        assert "skipped by the test harness" in row[2], row
        assert "launches a browser" in row[2], row

    def test_a_probe_that_could_not_start_names_the_directory(
            self, fake_package, fake_browsers_dir, no_system_chrome, isolated_home,
            monkeypatch):
        _artifact(fake_browsers_dir)

        async def broken(timeout_s: float = 25.0) -> dict:
            raise RuntimeError("browserType.launch: Access is denied. (0x5)")

        monkeypatch.setattr(browser, "launch_probe_async", broken)
        monkeypatch.setattr(updater, "LIVE_CHECKS", True)
        checks: list = []
        updater._append_browser_launch(checks)
        row = next(c for c in checks if c[0] == "chromium launch")
        assert row[1] == "fail", row
        assert "present but would not start" in row[2], row
        assert str(fake_browsers_dir) in row[2], row

    def test_a_failed_probe_leaves_only_the_launch_segment(
            self, fake_package, fake_browsers_dir, no_system_chrome, isolated_home,
            monkeypatch):
        """doctor 的老承诺是"除了自己的探针什么都不写"。这条钉的是那条豁免的确切边界：
        探针失败时它可以写 `launch` 那一段，并且**只有那一段**。"""
        _artifact(fake_browsers_dir)

        async def broken(timeout_s: float = 25.0) -> dict:
            raise RuntimeError("browserType.launch: Access is denied. (0x5)")

        monkeypatch.setattr(browser, "launch_probe_async", broken)
        monkeypatch.setattr(updater, "LIVE_CHECKS", True)
        updater._append_browser_launch([])
        data = json.loads((isolated_home / "browser_install.json").read_text(
            encoding="utf-8"))
        assert set(data) == {"launch"}, data
        assert data["launch"]["status"] == "failed", data

    def test_a_successful_probe_writes_nothing_at_all(
            self, fake_package, fake_browsers_dir, no_system_chrome, isolated_home,
            monkeypatch):
        """机制自检（`test_doctor_items.py` 那条豁免的反面）：把探针 stub 成成功，
        目录里必须一个文件都不多。少了这条，上一条只是在读自己的假设。"""
        _artifact(fake_browsers_dir)

        async def fine(timeout_s: float = 25.0) -> dict:
            return {"channel": "chromium", "artifact": "chromium", "elapsed_s": 1.0}

        monkeypatch.setattr(browser, "launch_probe_async", fine)
        monkeypatch.setattr(updater, "LIVE_CHECKS", True)
        updater._append_browser_launch([])
        assert not isolated_home.exists() or list(isolated_home.iterdir()) == []


# ─── 6 · 套件那道"不许真起浏览器"的死闸 ─────────────────────────────────

class TestTheSuiteLaunchGuardIsArmed:

    def test_the_guard_refuses_a_real_launch_and_is_not_swallowable(self):
        """conftest 的 `_nobody_launches_a_browser` 必须真的拦得住。

        镜像 `test_ensure_chromium.py:872` 那条先例：闸失效不会让任何用例变红，只会让
        "跑一次套件"变成"开 N 个 chromium"。这一批评测时它就抓到过三条顺带起真浏览器的
        升级链用例（`test_phase1_gaps` / `test_bug_report_regressions` / `test_revalidation_g23`），
        而那三条在闸装上之前一直是绿的。
        """
        import asyncio

        with pytest.raises(BaseException, match="launched a real browser") as ei:
            asyncio.run(browser.StealthyBrowser(headless=True,
                                                real_chrome=False).start())
        assert not isinstance(ei.value, Exception), \
            "跳闸必须是 Exception 之外的东西：启动路径吞掉一切 Exception（生产里对），" \
            "能被吞掉的闸等于没有闸"

    def test_the_guard_is_not_a_blanket_ban_on_other_subprocesses(self):
        """反向自检：闸只管启动。别的子进程照旧起得来，否则它就是挂具自己的一条新故障。"""
        out = browser.subprocess.run([sys.executable, "-c", "print(1)"],
                                     capture_output=True, text=True)
        assert out.returncode == 0 and out.stdout.strip() == "1"


# ─── 7 · 读物里不许再出现那条装错浏览器的命令 ────────────────────────────

class TestNoDocumentSendsTheUserToTheWrongInstaller:

    @pytest.mark.parametrize("rel", [
        "README.md",
        "src/dhole_mcp/skills/dhole-web/SKILL.md",
        "src/dhole_mcp/skills/dhole-web/references/configuration.md",
        "src/dhole_mcp/skills/dhole-web/references/troubleshooting.md",
    ])
    def test_the_shipped_instructions_never_say_playwright_install(self, rel):
        """`playwright install chromium` 装的是上游那一份，patchright 的 driver 不认。

        守卫只扫"现在还在教用户怎么装"的那几份读物：CHANGELOG 里那句是历史，讲的是
        当年确实印过错的命令，删掉它反而抹掉了这段的来路。
        """
        root = Path(updater.__file__).resolve().parents[2]
        text = (root / rel).read_text(encoding="utf-8")
        offenders = [line for line in text.splitlines()
                     if "playwright install" in line and "patchright install" not in line]
        assert not offenders, f"{rel}: {offenders}"

    def test_the_readme_still_tells_the_truth_about_the_first_run(
            self, monkeypatch):
        """README 那句"pip 装不到这一步"要有名字：真实命令与真实体积。"""
        root = Path(updater.__file__).resolve().parents[2]
        text = (root / "README.md").read_text(encoding="utf-8")
        assert "python -m patchright install chromium" in text, "README 不再给可复制的命令了"
