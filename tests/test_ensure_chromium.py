"""17.0 B3 · chromium 首用同步安装。

零配置那句承诺（"浏览器第一次需要时自己装"）此前只有 doctor 的一行承诺文案，代码里
没有任何东西真的去装：`check_browser_available()` 只问 patchright 能不能 import，
二进制不在就是 launch 失败，然后由 `_BROWSER_ERROR_HINTS` 让用户自己去敲
`python -m playwright install chromium`。这一批把那句话说真。

判据同时定死了四条规矩，都在这里钉住：

1. **绝不在事件循环上装** —— 被删掉的 `paths.migrate_legacy_cache_dir` 上方那条
   BLOCKING 注释讲的是同一个坑（下载几分钟，客户端拿到 -32001 而不是答复）。这里
   用运行期跳闸把它变成代码而不是一句提醒。
2. **下载时长不计入调用 timeout** —— 装完之后页面该拿到的预算要还是原来的预算。
3. **装不上就诚实拒绝并给出命令**，不许把"没装"说成"这个站读不到"。
4. **装的那一个必须正是起得来的那一个**，判据只能有一份（`_launch_channel`）。第一版
   按 headless 挑了 119MB 的 shell，启动那行传的却是显式 channel='chromium'，于是
   下载完成、复核通过、当场起不来 —— 干净环境验收就是为抓这个而红的。

并发只装一次由 `threading.Lock` + 状态文件保证；两个调用者共享同一次下载，第二个
等到的那一份不许重发。
"""

import asyncio
import json
import sys
import threading
import time

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
    """期望版本与"装过了"都是进程内的，测试之间必须互相看不见。"""
    browser.reset_install_state()
    yield
    browser.reset_install_state()


@pytest.fixture
def fake_package(monkeypatch, tmp_path):
    """造一个假的 patchright 包目录：browsers.json 说期望 rev 是 1228。"""
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
    """PLAYWRIGHT_BROWSERS_PATH 指到临时目录，并给出跨平台的可执行文件相对路径。"""
    base = tmp_path / "ms-playwright"
    base.mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(base))
    return base


@pytest.fixture
def no_system_chrome(monkeypatch):
    monkeypatch.setattr(browser, "_detect_chrome_channel", lambda: "chromium")


@pytest.fixture
def isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("DHOLE_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def _make_artifact(base, rev=REV, kind="chromium", *, complete=True):
    """按真实的落盘形状造一个"装好了"的 artifact 目录。

    `kind` 必须说清：dhole 那条默认路起的是**完整**的 `chromium-<rev>`，而
    `chromium_headless_shell-<rev>` 那一份它根本不 launch（判据是显式 channel，不是
    headless —— 见 `_ARTIFACT_DIRS` 上方那段实测）。只造 shell 等于在测一个本次启动
    不会用的目录，这正是验收抓到的那次分叉。
    """
    d = base / browser._ARTIFACT_DIRS[kind].format(rev=rev)
    d.mkdir(parents=True, exist_ok=True)
    if complete:
        (d / "INSTALLATION_COMPLETE").write_text("", encoding="utf-8")
    return d


def _make_both(base, rev=REV):
    """`patchright install chromium` 不加旗标时一次放下两份，盘上就两样都有。"""
    _make_artifact(base, rev, "chromium")
    _make_artifact(base, rev, "headless_shell")


# ─── 1 · 状态：到底在不在，期望的是哪一版 ────────────────────────────────

class TestChromiumStatus:

    def test_the_expected_revision_comes_from_the_driver_not_from_a_guess(
            self, fake_package, fake_browsers_dir, no_system_chrome):
        """目录里躺着的最高版本号不是答案：pip 升过 patchright 之后，旧 revision 的
        目录还在，driver 却只认 browsers.json 里那一版。照目录名猜会把"启动必然失败"
        报成"已就绪"。"""
        _make_artifact(fake_browsers_dir, 1299)
        status = browser.chromium_status()
        assert status["expected_revision"] == REV
        assert status["present"] is False, status

    def test_a_matching_complete_installation_is_present(self, fake_package,
                                                        fake_browsers_dir,
                                                        no_system_chrome):
        _make_both(fake_browsers_dir)
        status = browser.chromium_status()
        assert status["present"] is True, status
        assert status["source"] == "bundled", status

    def test_a_half_installed_directory_is_not_present(self, fake_package,
                                                      fake_browsers_dir,
                                                      no_system_chrome):
        """下载被打断会留下一个目录。它存在不等于能用。"""
        _make_artifact(fake_browsers_dir, complete=False)
        assert browser.chromium_status()["present"] is False

    def test_system_chrome_counts_and_needs_no_download(self, fake_package,
                                                       fake_browsers_dir,
                                                       monkeypatch):
        monkeypatch.setattr(browser, "_detect_chrome_channel", lambda: "chrome")
        status = browser.chromium_status()
        assert status["present"] is True
        assert status["source"] == "system-chrome", status

    def test_forcing_real_chrome_installs_nothing_even_without_a_detected_chrome(
            self, fake_package, fake_browsers_dir, no_system_chrome, monkeypatch,
            isolated_home):
        """`real_chrome=True` 那次起的是用户本机的 Chrome，不是 bundled chromium。
        这时候下 193MB 是一份永远不会被 launch 的东西 —— 装它比不装更糟，因为它还会
        让错误文案说"二进制已就绪"。
        """
        seen = []
        monkeypatch.setattr(browser.subprocess, "run",
                            lambda *a, **k: (seen.append(a), _Done())[1])
        ok, detail = browser.ensure_chromium(real_chrome=True)
        assert ok is True, detail
        assert seen == [], seen
        assert "system" in detail, detail

    def test_the_default_headless_path_wants_the_full_build_not_the_shell(
            self, fake_package, fake_browsers_dir, no_system_chrome):
        """B3 验收抓到的那次分叉，实测形状（空浏览器目录里让驱动自己报缺失路径）：

            channel='chromium' + headless=True → chromium-1228\\chrome-win64\\chrome.exe
            不传 channel   + headless=True → chromium_headless_shell-1228\\…

        驱动源码 `getExecutableName()` 的顺序就是"显式 channel 优先"，而 dhole 两条路
        都传 channel。所以按 headless 判 artifact 会装出一份启动不会用的二进制：下载
        完成、复核通过、当场起不来。
        """
        _make_artifact(fake_browsers_dir, kind="headless_shell")
        status = browser.chromium_status()
        assert status["present"] is False, status
        assert status["detail"].startswith("chromium-1228"), status
        assert browser.launch_artifact() == "chromium"

    def test_an_install_that_only_produced_the_shell_is_a_failure(
            self, fake_package, fake_browsers_dir, no_system_chrome, monkeypatch,
            isolated_home):
        """退出码 0 + "盘上有一个 chromium 目录"不等于这次能用。桩只交出 shell（这正是
        上一版真实干的事），复核必须报失败，不能因为"装了点什么"就报成功。"""
        def fake_run(cmd, *a, **k):
            _make_artifact(fake_browsers_dir, kind="headless_shell")
            return _Done()

        monkeypatch.setattr(browser.subprocess, "run", fake_run)
        ok, detail = browser.ensure_chromium()
        assert ok is False, detail
        assert "chromium-1228 is still missing" in detail, detail

    def test_the_verify_asks_for_the_artifact_the_channel_launches(
            self, fake_package, fake_browsers_dir, no_system_chrome, monkeypatch,
            isolated_home):
        """桩按真实的形状落盘：`--no-shell` 那一条只交出 chromium-<rev> + 两个小依赖。
        复核问的就是它，既不许因为"盘上没 shell"而失败，也不许因为"有别的 chromium
        目录"而放行。"""
        made = []

        def fake_run(cmd, *a, **k):
            _make_artifact(fake_browsers_dir, kind="chromium")
            made.append(cmd)
            return _Done()

        monkeypatch.setattr(browser.subprocess, "run", fake_run)
        ok, detail = browser.ensure_chromium()
        assert ok is True, detail
        assert "chromium-1228" in detail, detail
        assert len(made) == 1, made

    def test_an_unreadable_driver_manifest_is_said_not_guessed(self, monkeypatch,
                                                                tmp_path,
                                                                fake_browsers_dir,
                                                                no_system_chrome):
        bad = tmp_path / "no_pkg"
        monkeypatch.setattr(browser, "_package_dir", lambda: bad)
        status = browser.chromium_status()
        assert status["present"] is False
        assert "browsers.json" in status["detail"], status


# ─── 2 · 事件循环跳闸（先例：-32001） ────────────────────────────────────

class TestNeverOnTheEventLoop:

    def test_the_sync_installer_refuses_to_run_on_a_running_loop(self, fake_package,
                                                                 fake_browsers_dir,
                                                                 no_system_chrome,
                                                                 monkeypatch):
        """这条是整个批次里最贵的一课，所以写成代码而不是注释：在事件循环线程上直接
        调同步安装必须被拒（旧迁移的 BLOCKING 注释讲的正是"注释守不住"）。"""
        calls = []
        monkeypatch.setattr(browser.subprocess, "run",
                            lambda *a, **k: calls.append(a) or _Done())

        async def on_loop():
            return browser.ensure_chromium()

        ok, detail = asyncio.run(on_loop())
        assert ok is False, detail
        assert "event loop thread" in detail, detail
        assert calls == [], "被拒之后还跑了子进程，等于跳闸没生效"

    def test_the_async_wrapper_does_the_work_off_the_loop(self, fake_package,
                                                          fake_browsers_dir,
                                                          no_system_chrome,
                                                          monkeypatch):
        """反向自检：`ensure_chromium_async` 必须走 to_thread。少了它，上面那条跳闸
        就会在真实路径上响，而这正是我们要避免的现场。"""
        seen = {}
        real_to_thread = asyncio.to_thread

        def spy(fn, *a, **k):
            seen["fn"] = getattr(fn, "__name__", str(fn))
            return real_to_thread(fn, *a, **k)

        def fake_run(cmd, *a, **k):
            _make_both(fake_browsers_dir)
            return _Done()

        monkeypatch.setattr(asyncio, "to_thread", spy)
        monkeypatch.setattr(browser.subprocess, "run", fake_run)
        outcome = asyncio.run(browser.ensure_chromium_async())
        assert outcome.ok is True, outcome
        assert "ensure_chromium" in seen.get("fn", ""), seen


# ─── 3 · 安装动作本身 ────────────────────────────────────────────────────

class TestInstall:

    def _stub_run(self, monkeypatch, *, code=0, err="", installs=True,
                  base=None):
        """桩必须**真的**把二进制放下去：`ensure_chromium` 装完会复核状态，不复核的
        安装等于相信子进程的退出码。"""
        seen = []

        def fake_run(cmd, *a, **k):
            seen.append(cmd)
            if code == 0 and installs:
                _make_both(base)
            return _Done(code, err)

        monkeypatch.setattr(browser.subprocess, "run", fake_run)
        return seen

    def test_a_missing_binary_triggers_one_install_of_the_named_browser(
            self, fake_package, fake_browsers_dir, no_system_chrome, monkeypatch,
            isolated_home):
        seen = self._stub_run(monkeypatch, base=fake_browsers_dir)
        ok, detail = browser.ensure_chromium()
        assert ok is True, detail
        assert len(seen) == 1, seen
        cmd = seen[0]
        assert cmd[:3] == [sys.executable, "-m", "patchright"], cmd
        assert "chromium" in cmd, cmd

    def test_the_install_fetches_the_artifact_the_channel_launches(
            self, fake_package, fake_browsers_dir, no_system_chrome, monkeypatch,
            isolated_home):
        """`install chromium` 不加旗标会把两份都拖下来（实测 193MB + 119MB = 312MB）。
        默认那条路只用得着完整 chromium 那一份，所以首用等待只该为那一份买单；旗标写反
        就是白下一份启动永远不碰的二进制。"""
        seen = []

        def fake_run(cmd, *a, **k):
            seen.append(cmd)
            _make_artifact(fake_browsers_dir,
                           kind="headless_shell" if "--only-shell" in cmd else "chromium")
            return _Done()

        monkeypatch.setattr(browser.subprocess, "run", fake_run)
        ok, detail = browser.ensure_chromium()
        assert ok is True, detail
        assert "--no-shell" in seen[0], seen[0]
        assert "--only-shell" not in seen[0], seen[0]
        assert len(seen) == 1, seen

    def test_the_shell_artifact_would_be_installed_with_its_own_flag(
            self, fake_package, fake_browsers_dir, no_system_chrome):
        """反向自检：旗标表不是只写对了当前那一条。哪天真要 shell，它得指对方向。"""
        assert "--only-shell" in browser._install_argv("headless_shell")
        assert "--no-shell" in browser._install_argv("chromium")

    def test_an_existing_binary_installs_nothing(self, fake_package,
                                                 fake_browsers_dir,
                                                 no_system_chrome, monkeypatch):
        seen = self._stub_run(monkeypatch, base=fake_browsers_dir)
        _make_both(fake_browsers_dir)
        ok, detail = browser.ensure_chromium()
        assert ok is True and seen == [], (ok, detail, seen)

    def test_a_exit_code_that_does_not_match_the_disk_is_a_failure(
            self, fake_package, fake_browsers_dir, no_system_chrome, monkeypatch):
        """子进程说"成功"但盘上没有 = 失败。相信退出码而不复核，就会把一次没装上的
        浏览器说成装好了，下一次 launch 再莫名其妙地失败。"""
        self._stub_run(monkeypatch, code=0, installs=False, base=fake_browsers_dir)
        ok, detail = browser.ensure_chromium()
        assert ok is False, detail
        assert "install failed" in detail, detail

    def test_a_failed_install_names_the_command_the_user_can_run(
            self, fake_package, fake_browsers_dir, no_system_chrome, monkeypatch,
            isolated_home):
        """装不上是网络/依赖事实，不能被说成"这个站读不到"。诚实拒绝的判据是：给出一条
        真的能敲的命令，并且不谎报成功。"""
        self._stub_run(monkeypatch, code=1, err="download failed: ECONNRESET",
                       base=fake_browsers_dir)
        ok, detail = browser.ensure_chromium()
        assert ok is False
        assert "python -m patchright install chromium" in detail, detail
        assert "ECONNRESET" in detail, detail

    def test_the_state_file_records_what_happened(self, fake_package,
                                                  fake_browsers_dir,
                                                  no_system_chrome, monkeypatch,
                                                  isolated_home):
        """状态文件是给"另一个进程 / 下一次 doctor"看的，不是给自己的内存看的。"""
        self._stub_run(monkeypatch, base=fake_browsers_dir)
        browser.ensure_chromium()
        path = browser._install_state_path()
        assert path.startswith(str(isolated_home)), path
        state = json.loads(open(path, encoding="utf-8").read())
        assert state["revision"] == REV, state
        assert state["status"] in ("done", "failed"), state

    def test_two_threads_share_one_download(self, fake_package, fake_browsers_dir,
                                            no_system_chrome, monkeypatch):
        """并发第二次必须等同一个安装，而不是再拉一遍 193MB。"""
        started = threading.Event()
        release = threading.Event()
        calls = []

        def fake_run(cmd, *a, **k):
            calls.append(cmd)
            started.set()
            release.wait(10)
            _make_both(fake_browsers_dir)
            return _Done()

        monkeypatch.setattr(browser.subprocess, "run", fake_run)
        results = {}

        def worker(tag):
            results[tag] = browser.ensure_chromium()

        threads = [threading.Thread(target=worker, args=(t,)) for t in "ab"]
        for t in threads:
            t.start()
        assert started.wait(10), "没有开始安装"
        time.sleep(0.2)
        release.set()
        for t in threads:
            t.join(10)
        assert len(calls) == 1, f"并发把一次下载变成了 {len(calls)} 次"
        assert all(ok for ok, _ in results.values()), results

    def test_an_unsupported_platform_refuses_with_a_command_not_a_crash(
            self, fake_package, fake_browsers_dir, no_system_chrome, monkeypatch,
            isolated_home):
        """Termux 上没有 playwright wheel —— 这一档要老实说"装不了"，并把命令给出来。"""
        seen = self._stub_run(monkeypatch, base=fake_browsers_dir)
        monkeypatch.setattr(browser, "_platform_can_install",
                            lambda: (False, "Termux ships no playwright wheel"))
        ok, detail = browser.ensure_chromium()
        assert ok is False
        assert "Termux" in detail, detail
        assert seen == [], "平台都说不清了还去跑安装"


# ─── 4 · 下载时长不吃掉调用预算 ──────────────────────────────────────────

class TestBudgetIsNotEatenByTheDownload:

    def test_the_outcome_reports_how_long_it_waited(self, fake_package,
                                                    fake_browsers_dir,
                                                    no_system_chrome, monkeypatch):
        def fake_run(cmd, *a, **k):
            time.sleep(0.3)
            _make_both(fake_browsers_dir)
            return _Done()

        monkeypatch.setattr(browser.subprocess, "run", fake_run)
        outcome = asyncio.run(browser.ensure_chromium_async())
        assert outcome.ok and outcome.waited_s >= 0.25, outcome

    def test_a_second_caller_waits_on_the_same_download_not_a_second_one(
            self, fake_package, fake_browsers_dir, no_system_chrome, monkeypatch):
        """asyncio.Lock 的意义：并发两路只需要一个 to_thread 安装，第二路等到的是
        结果，而它自己的 waited_s 说真话（它确实等了这么久，预算也要还它）。"""
        calls = []

        def fake_run(cmd, *a, **k):
            calls.append(cmd)
            time.sleep(0.4)
            _make_both(fake_browsers_dir)
            return _Done()

        monkeypatch.setattr(browser.subprocess, "run", fake_run)

        async def scenario():
            return await asyncio.gather(browser.ensure_chromium_async(),
                                        browser.ensure_chromium_async())

        outcomes = asyncio.run(scenario())
        assert len(calls) == 1, calls
        assert all(o.ok for o in outcomes), outcomes
        assert max(o.waited_s for o in outcomes) >= 0.3, outcomes

    def test_a_call_that_found_it_already_installed_waits_zero_seconds(
            self, fake_package, fake_browsers_dir, no_system_chrome, monkeypatch):
        _make_both(fake_browsers_dir)
        outcome = asyncio.run(browser.ensure_chromium_async())
        assert outcome.ok and outcome.waited_s == 0.0, outcome


# ─── 4b · 那道真的掐表的闸门也要把安装的时间还出来 ────────────────────────

class _Timeout312:
    """3.12+ 的 `asyncio.timeout()` 对象形状：`when()` 读，`reschedule()` 写。"""

    def __init__(self, when):
        self._when = when

    def when(self):
        return self._when

    def reschedule(self, when):
        self._when = when
        return True


class _Timeout311:
    """3.11 的形状：`deadline()` 一个方法两用。声明支持 >=3.11，所以两种都要测得到。"""

    def __init__(self, when):
        self._when = when

    def deadline(self, when=None):
        if when is None:
            return self._when
        self._when = when


class TestTheCallBackstopIsInstallAware:
    """上面那条量的是 `waited_s`；这一条量的是"有人拿着它做事"。

    实测过的失败形状：干净环境 + `force_fetcher='stealthy'` + 一次 40s 的下载 →
    信封里回来的是 `timeout: the 30000ms call budget ran out during the forced
    stealthy tier`。那句话每个字都成立，合起来是假的 —— 预算不是页面用掉的。
    `_within_call_budget` 是最后那道 `asyncio.timeout`，所以时间必须还到它身上。

    用例里的预算写成 200ms 但实际闸门是 1s：`asked_s` 有 1 秒下限。所以安装的模拟
    都睡 1.3s —— 短于它的等待根本压不过闸门，测了等于没测。
    """

    def test_the_backstop_waits_out_an_install_it_credited(self, monkeypatch):
        from dhole_mcp import server as srv_mod

        async def slow_install(*args, **kwargs):
            await asyncio.sleep(1.3)
            return browser.InstallOutcome(True, "installed chromium", 1.3)

        monkeypatch.setattr(browser, "ensure_chromium_async", slow_install)
        monkeypatch.setattr(srv_mod, "_BACKSTOP_GRACE_S", 0.0)

        async def fetch_like():
            outcome = await srv_mod.ensure_chromium_async()
            assert outcome.ok, outcome
            return "page content"

        async def scenario():
            return await srv_mod.MasterFetchServer()._within_call_budget(
                fetch_like(), "https://example.com", 200, "forced stealthy tier")

        got = asyncio.run(scenario())
        assert got == "page content", got

    def test_the_same_backstop_still_kills_a_page_that_only_ran_long(self,
                                                                    monkeypatch):
        """反向自检：这条不许顺手把闸门放宽。没在装机的慢页面照旧要被判超时，
        否则"预算"两个字就成了装饰。"""
        from dhole_mcp import server as srv_mod

        monkeypatch.setattr(srv_mod, "_BACKSTOP_GRACE_S", 0.0)

        async def slow_page():
            await asyncio.sleep(1.3)
            return "late"

        async def scenario():
            return await srv_mod.MasterFetchServer()._within_call_budget(
                slow_page(), "https://example.com", 200, "stealthy tier")

        got = asyncio.run(scenario())
        assert got.error.startswith("timeout: the "), got.error

    def test_a_waiter_queued_behind_someone_elses_install_gets_its_own_time(
            self, monkeypatch):
        """第二个等待者等的是同一段时间，账要记它自己实际等的那一段。只记第一个的话,
        第二个会被自己的闸门掐死 —— 而它明明是"等到的那一个"。"""
        from dhole_mcp import server as srv_mod

        started = asyncio.Event()
        release = asyncio.Event()
        seen = {}

        async def slow_install(*args, **kwargs):
            started.set()
            await release.wait()
            return browser.InstallOutcome(True, "installed chromium", 0.0)

        monkeypatch.setattr(browser, "ensure_chromium_async", slow_install)
        monkeypatch.setattr(srv_mod, "_BACKSTOP_GRACE_S", 0.0)

        async def waiter():
            seen["credit"] = srv_mod._budget_credit.get()
            await srv_mod.ensure_chromium_async()
            return "page content"

        async def scenario():
            # 不带账本的持有者：启动预热那条就是这个形状
            holder = asyncio.create_task(srv_mod.ensure_chromium_async())
            await started.wait()
            loop = asyncio.get_running_loop()
            loop.call_later(1.3, release.set)
            got = await srv_mod.MasterFetchServer()._within_call_budget(
                waiter(), "https://example.com", 200, "forced stealthy tier")
            await holder
            return got

        assert asyncio.run(scenario()) == "page content"
        assert seen["credit"] is not None
        assert seen["credit"].spent_s >= 1.0, seen["credit"].spent_s

    @pytest.mark.parametrize("shape", [_Timeout312, _Timeout311],
                             ids=["3.12+", "3.11"])
    def test_the_grant_is_bounded_and_settles_to_what_was_used(self, shape):
        """放宽必须有上限，用完要收回。安装自己有 `INSTALL_TIMEOUT_S` 兜底，账本就按
        那个上限放宽；结束时只留下真正花掉的秒数。

        两种形状都要过：pyproject 声明支持 >=3.11，而截止对象在 3.12 换过名字。
        """
        from dhole_mcp import server as srv_mod

        cm = shape(100.0)
        credit = srv_mod._BudgetCredit()
        credit.attach(cm)
        assert srv_mod._timeout_when(cm) == 100.0, "接上时不许改动原截止"
        credit.begin_install(900.0)
        assert srv_mod._timeout_when(cm) == pytest.approx(1000.0)
        credit.end_install(41.0)
        assert credit.spent_s == pytest.approx(41.0)
        assert srv_mod._timeout_when(cm) == pytest.approx(141.0)

    def test_an_expired_timeout_is_left_alone_inst_of_becoming_the_user_error(
            self):
        """实测撞过的形状：安装跑满它自己的上限时，`asyncio.timeout` 早已 expire，
        这时改期会抛 `RuntimeError: Cannot change state of expiring Timeout` —— 而那句
        话会一路变成用户看到的抓取错误。到期就是到期，记账认下来即可。
        """
        from dhole_mcp import server as srv_mod

        class _Expired:
            def when(self):
                return 100.0

            def reschedule(self, when):
                raise RuntimeError("Cannot change state of expiring Timeout")

        credit = srv_mod._BudgetCredit()
        credit.attach(_Expired())
        credit.begin_install(900.0)
        credit.end_install(41.0)
        assert credit.spent_s == pytest.approx(41.0)

    def test_an_install_outside_any_budget_scope_does_not_need_a_ledger(
            self, monkeypatch):
        """预热与 CLI 都在 `_within_call_budget` 外面调用它。那时账本是空的，记账
        这一段不许把自己弄崩。"""
        from dhole_mcp import server as srv_mod

        async def ok_install(*args, **kwargs):
            return browser.InstallOutcome(True, "installed", 1.0)

        monkeypatch.setattr(browser, "ensure_chromium_async", ok_install)
        assert srv_mod._budget_credit.get() is None
        outcome = asyncio.run(srv_mod.ensure_chromium_async())
        assert outcome.ok and outcome.waited_s == 1.0, outcome


# ─── 5 · 接线：需要浏览器的那几条路都要先过这里 ──────────────────────────

class TestWiring:

    def test_open_session_ensures_before_it_launches(self, monkeypatch, isolated_home):
        """`open_session` 是唯一构造 session 的地方（自动、显式、钉死档都走它），
        所以安装挂在这里就覆盖了全部路径。挂在 launch 之后等于白挂。"""
        from dhole_mcp import server as srv_mod

        order = []

        async def fake_start(self):
            order.append("start")

        async def ensure(*args, **kwargs):
            order.append("ensure")
            return browser.InstallOutcome(True, "bundled chromium", 0.0)

        monkeypatch.setattr(browser.StealthyBrowser, "start", fake_start)
        monkeypatch.setattr(srv_mod, "ensure_chromium_async", ensure)
        server = srv_mod.MasterFetchServer()

        async def scenario():
            try:
                await server.open_session()
            except Exception:  # noqa: BLE001 - 真实构造之后的事不归本用例管
                pass

        asyncio.run(scenario())
        assert order[:2] == ["ensure", "start"], order

    def test_attach_mode_installs_nothing(self, monkeypatch, isolated_home):
        """连的是用户自己开着的 Chrome：那个浏览器本来就在跑，再下 193MB 是纯粹的
        浪费，而且装的那个也跟这次要用的不是同一个。"""
        from dhole_mcp import server as srv_mod

        calls = []

        async def ensure(*args, **kwargs):
            calls.append(1)
            return browser.InstallOutcome(True, "x", 0.0)

        async def fake_start(self):
            pass

        monkeypatch.setattr(browser.StealthyBrowser, "start", fake_start)
        monkeypatch.setattr(srv_mod, "ensure_chromium_async", ensure)
        server = srv_mod.MasterFetchServer()

        async def scenario():
            try:
                await server.open_session(cdp_url="http://127.0.0.1:9222")
            except Exception:  # noqa: BLE001
                pass

        asyncio.run(scenario())
        assert calls == [], "attach 模式不该去装 chromium"

    def test_startup_never_downloads_the_browser_it_may_never_use(self, monkeypatch,
                                                                  fake_package,
                                                                  fake_browsers_dir,
                                                                  no_system_chrome):
        """计划里那句是"第一次**用到**时同步装"，用到它的是抓取，不是启动。

        这条为什么要是行为用例而不是一句注释：预热一旦去 ensure 二进制，每台干净机器
        连上宿主就先花 193MB，包括一次浏览器档都不需要的那些，而这条链路可能是计费的
        （本机实测 120KB/s ~ 2.4MB/s，最慢一端一份要一小时）。
        """
        from dhole_mcp import server as srv_mod

        seen = []
        monkeypatch.setattr(browser.subprocess, "run",
                            lambda *a, **k: (seen.append(a), _Done())[1])
        assert browser.chromium_status()["present"] is False
        assert srv_mod._browser_binary_ready() is False
        assert seen == [], "启动路径上跑了安装子进程"

    def test_a_ready_binary_still_gets_its_prewarm(self, monkeypatch, fake_package,
                                                   fake_browsers_dir,
                                                   no_system_chrome):
        """反向自检：这条闸不许顺手把预热整个关掉。二进制在的时候冷启动那 3-5s 还是
        要被预热掉，否则这批省下的带宽是拿首字节时间换的。"""
        from dhole_mcp import server as srv_mod

        _make_artifact(fake_browsers_dir, kind="chromium")
        assert srv_mod._browser_binary_ready() is True

    def test_the_prewarm_window_contains_no_installer(self):
        """源码顺序守卫：预热的 30s 窗口里不许再有"等下载"这一步。

        代价是实测过的：让下载落进窗口，超时会掐死正拿着安装锁的任务，worker 线程取消
        不掉还在下，锁一放下一个调用看盘上"还是没装"又起一次 —— 实测同时跑着三个
        `patchright install chromium`，套件停在 26%。
        """
        import inspect

        from dhole_mcp import server as srv_mod

        src = inspect.getsource(srv_mod.MasterFetchServer._prewarm_stealthy)
        assert "_browser_binary_ready" in src, "预热已经不查二进制了？"
        assert "ensure_chromium_async" not in src, \
            "窗口里又出现安装调用：下载会被这条 30s 超时掐死，见上一条用例"
        assert "asyncio.wait_for(_warm()" in src, "那条 30s 窗口挪走了"

    def test_the_deadline_shift_gives_back_exactly_the_download_time(self):
        """装完之后的页面必须还拿到原来那份预算。不挪 deadline，一次 90s 的下载会让
        下一档在检查剩余时间时读到"已用尽"，用户看到的就是一条关于站点的假故障。"""
        from dhole_mcp import server as srv_mod

        assert srv_mod._apply_install_shift(1000.0, 93.0) == pytest.approx(1093.0)
        # 没装东西时不许凭空多给时间 —— 那等于把 timeout 参数变成建议值
        assert srv_mod._apply_install_shift(1000.0, 0.0) == 1000.0
        assert srv_mod._apply_install_shift(1000.0, -5.0) == 1000.0

    def test_a_missing_binary_does_not_become_a_site_failure(self, monkeypatch,
                                                            isolated_home):
        """诚实拒绝的形状：错误里必须有"二进制没装上"与那条命令，不能只说
        status/connection —— 那是把安装问题伪装成站点问题。"""
        from dhole_mcp import server as srv_mod

        async def ensure(*args, **kwargs):
            return browser.InstallOutcome(
                False,
                "chromium install failed - run: python -m patchright install chromium",
                0.0)

        monkeypatch.setattr(srv_mod, "ensure_chromium_async", ensure)
        server = srv_mod.MasterFetchServer()

        async def scenario():
            with pytest.raises(RuntimeError) as ei:
                await server.open_session()
            return str(ei.value)

        msg = asyncio.run(scenario())
        assert "patchright install chromium" in msg, msg
        assert "browser_binary_missing" in msg, msg



# ─── 6 · doctor 的第 3 项读的是真状态 ────────────────────────────────────

class TestDoctorReadsTheRealState:

    def test_the_row_names_the_expected_revision_and_the_real_state(
            self, fake_package, fake_browsers_dir, no_system_chrome, monkeypatch,
            isolated_home, capsys):
        from dhole_mcp import updater

        monkeypatch.setenv("DHOLE_HOME", str(isolated_home))
        updater.doctor()
        out = capsys.readouterr().out
        line = next(x for x in out.splitlines() if "chromium binary" in x)
        assert str(REV) in line, line
        assert "not installed" in line, line

    def test_a_failed_install_is_visible_in_doctor(self, fake_package,
                                                   fake_browsers_dir,
                                                   no_system_chrome, monkeypatch,
                                                   isolated_home, capsys):
        from dhole_mcp import updater

        isolated_home.mkdir(parents=True, exist_ok=True)
        (isolated_home / "browser_install.json").write_text(json.dumps({
            "status": "failed", "revision": REV,
            "error": "download failed: ECONNRESET",
            "command": "python -m patchright install chromium",
        }), encoding="utf-8")
        monkeypatch.setenv("DHOLE_HOME", str(isolated_home))
        updater.doctor()
        out = capsys.readouterr().out
        assert "install failed" in out, out
        assert "ECONNRESET" in out, out


# ─── 7 · 不许有第二条"要不要用系统 Chrome"的判据 ─────────────────────────

def test_the_channel_and_the_installer_agree_about_system_chrome(fake_package,
                                                                 fake_browsers_dir,
                                                                 monkeypatch,
                                                                 isolated_home):
    """launch 用 channel=chrome 时不需要 bundled chromium；安装侧必须先看到同一件事，
    否则装了 Chrome 的机器会被拖去下载一份永远用不上的 193MB。两处判据必须同源。"""
    monkeypatch.setattr(browser, "_detect_chrome_channel", lambda: "chrome")
    seen = []
    monkeypatch.setattr(browser.subprocess, "run",
                        lambda *a, **k: (seen.append(a), _Done())[1])
    ok, detail = browser.ensure_chromium()
    assert ok is True and seen == [], (ok, detail, seen)
    assert "system" in detail, detail


def test_the_launch_and_the_installer_read_one_channel_decision():
    """"该装哪一个"与"实际会传哪个 channel"必须是同一个表达式，不是两份写得一样好的
    条件。守卫看的是源码里有没有第二份判据。"""
    import inspect

    start = inspect.getsource(browser.StealthyBrowser.start)
    assert "_launch_channel(self._real_chrome)" in start, \
        "启动那行不再走公共判据了 —— 安装侧就会开始装错那一份"
    assert "if self._real_chrome else" not in start, \
        "判据被抄回了启动侧，两处各写一遍就是验收抓到的那次分叉"
    assert '"chrome"' in inspect.getsource(browser._launch_channel)


# ─── 8 · 状态文件的权限与不污染 ──────────────────────────────────────────

def test_the_state_file_lands_owner_only(fake_package, fake_browsers_dir,
                                         no_system_chrome, monkeypatch,
                                         isolated_home):
    """它记的是这台机器的浏览器安装事实（含命令与错误原文），按同一条规矩收紧。"""
    chmods = []
    monkeypatch.setattr(browser.os, "chmod",
                        lambda p, m: chmods.append((str(p), oct(m))))
    monkeypatch.setattr(browser.subprocess, "run",
                        lambda *a, **k: _Done())
    browser.ensure_chromium()
    assert any(oct(0o600) == m for _p, m in chmods), chmods


def test_reset_state_is_available_for_tests():
    """并发用例必须能把"只装一次"的进程内状态清掉，否则用例之间互相污染。"""
    assert callable(browser.reset_install_state)


def test_a_process_that_never_installed_can_still_be_read(isolated_home, monkeypatch):
    """doctor 在一台从没装过 chromium 的机器上问的第一句就是"装过吗"。

    `_install_state` 在代码里只被 `_record_install` 用 `global` 赋过值，所以"模块级没有
    默认值"这个 bug 会让一次都没装过的进程抛 NameError —— 而 doctor 把它读成"这项查不
    了"。本文件的 autouse 挂具先调了 reset，正好把这行缺失盖住，所以这里重新导入一次，
    回到真正的冷进程形状。
    """
    import importlib

    monkeypatch.setenv("DHOLE_HOME", str(isolated_home))
    fresh = importlib.reload(browser)
    assert fresh.read_install_state() == {}


# ─── 9 · 套件那道"不许真下载"的死闸 ──────────────────────────────────────

def test_the_suite_guard_refuses_a_real_installer(fake_package, fake_browsers_dir,
                                                 no_system_chrome, isolated_home):
    """conftest 的 `_nobody_installs_a_browser` 必须真的拦得住。

    它存在的理由是一次实测：上一版跑套件时同时起了三个 `patchright install chromium`
    子进程，套件停在 26%，三条下载互相抢带宽。闸失效不会让任何用例变红，只会让
    "跑一次测试"变成一次 300MB 的下载。
    """
    with pytest.raises(BaseException, match="real browser installer") as ei:
        browser.ensure_chromium()
    assert not isinstance(ei.value, Exception), \
        "跳闸必须是 Exception 之外的东西：ensure_chromium 吞掉一切 Exception（生产里对），"\
        "能被吞掉的闸等于没有闸"


def test_the_guard_is_not_a_blanket_ban_on_subprocess():
    """反向自检：闸只拦安装。别的子进程照旧起得来，否则它就不是一道闸而是一堵墙，
    而下一个要用 subprocess 的用例会花半天去查为什么"凭空"失败。"""
    out = browser.subprocess.run([sys.executable, "-c", "print(1)"],
                                 capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == "1"
