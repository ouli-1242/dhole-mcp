"""Tests for `dhole --doctor`.

doctor() runs precisely when something is already broken, so it must never
raise, and its exit code has to distinguish healthy from broken so a script can
gate on it. The failure paths below INJECT the failure (a missing core module,
an unusable state dir, absent metadata) rather than hoping the test machine
happens to be broken in the right way.
"""

import pytest

from dhole_mcp import updater


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point every state path at a throwaway dir - doctor writes repair.py."""
    home = tmp_path / "home"
    monkeypatch.setenv("DHOLE_HOME", str(home))
    return home


class TestDoctorReporting:
    def test_never_raises_and_returns_an_exit_code(self, isolated_home, capsys):
        assert updater.doctor() in (0, 1)
        assert capsys.readouterr().out.strip()

    def test_reports_the_loaded_module_path(self, isolated_home, capsys):
        """The line that makes a wheel-vs-src mismatch visible at a glance."""
        updater.doctor()
        out = capsys.readouterr().out
        assert "module loaded from" in out
        assert "dhole_mcp" in out

    def test_checks_the_state_dir_and_the_proxy_pool(self, isolated_home, capsys):
        updater.doctor()
        out = capsys.readouterr().out
        assert "state dir writable" in out
        assert "proxy pool" in out

    def test_always_appends_the_capability_panel(self, isolated_home, capsys):
        updater.doctor()
        assert "capabilities" in capsys.readouterr().out

    def test_leaves_no_probe_file_behind(self, isolated_home):
        updater.doctor()
        leftovers = [p.name for p in isolated_home.iterdir()
                     if p.name.startswith(".doctor")]
        assert leftovers == []


class TestDoctorFailuresAreLoud:
    def test_missing_core_dependency_fails_and_names_the_fix(
            self, isolated_home, monkeypatch, capsys):
        monkeypatch.setattr(updater, "_has_module", lambda name: name != "httpx")
        assert updater.doctor() == 1
        out = capsys.readouterr().out
        assert "httpx" in out
        assert "fix:" in out

    def test_unusable_state_dir_fails_and_names_the_fix(
            self, isolated_home, monkeypatch, capsys):
        def boom(*args, **kwargs):
            raise PermissionError("denied")

        monkeypatch.setattr(updater.paths, "home", boom)
        assert updater.doctor() == 1
        out = capsys.readouterr().out
        assert "state dir writable" in out
        assert "DHOLE_HOME" in out

    def test_unusable_state_dir_does_not_crash_the_repair_check(
            self, isolated_home, monkeypatch, capsys):
        """repair_script_path() also resolves through the state dir."""
        def boom(*args, **kwargs):
            raise PermissionError("denied")

        monkeypatch.setattr(updater.paths, "home", boom)
        assert updater.doctor() == 1
        assert "repair script ready" in capsys.readouterr().out

    def test_missing_metadata_fails(self, isolated_home, monkeypatch, capsys):
        import importlib.metadata as md

        def boom(name):
            raise md.PackageNotFoundError(name)

        monkeypatch.setattr(md, "version", boom)
        assert updater.doctor() == 1
        assert "metadata consistent" in capsys.readouterr().out

    def test_healthy_run_exits_zero(self, isolated_home, monkeypatch, capsys):
        """Force every environment-dependent check to succeed, assert clean exit."""
        import importlib.metadata as md

        from dhole_mcp import __version__ as mod_ver

        monkeypatch.setattr(updater, "_has_module", lambda name: True)
        monkeypatch.setattr(updater, "_other_dhole_pids", lambda: [])
        monkeypatch.setattr(updater, "_dhole_launcher_path", lambda: __file__)
        monkeypatch.setattr(md, "version", lambda name: mod_ver)

        assert updater.doctor() == 0
        assert "all healthy" in capsys.readouterr().out


class TestProcessScanIsEncodingSafe:
    """`_other_dhole_pids` 通过外部命令列进程，输出编码由系统决定。

    中文 Windows 上 tasklist 按 GBK 输出，而 Python 的 UTF-8 模式会把
    text=True 的解码器设成 utf-8 —— 于是「没有匹配进程」时那条中文提示会
    解码失败，异常还发生在读取线程里（check_output 只会抛出无关的
    TypeError），被 except 吞掉后 doctor 永远报「none running」。

    这是静默假阴性：有残留进程也看不出来。修法是 errors="replace" —— 我们
    要的 dhole.exe 和 PID 都是 ASCII，坏字节替换掉不影响解析。
    """

    def test_never_raises_on_this_platform(self):
        assert isinstance(updater._other_dhole_pids(), list)

    def test_never_raises_when_output_is_undecodable(self, monkeypatch):
        """注入一段无法解码的输出，确认函数不把异常漏出去。"""
        import subprocess

        def boom(*a, **kw):
            raise UnicodeDecodeError("utf-8", b"\xd0", 0, 1, "invalid start byte")

        monkeypatch.setattr(subprocess, "check_output", boom)
        assert updater._other_dhole_pids() == []

    def test_subprocess_call_requests_lenient_decoding(self):
        """源码级守卫：这两处调用必须带 errors="replace"，否则回归即静默。"""
        import inspect
        import re

        src = inspect.getsource(updater._other_dhole_pids)
        calls = re.findall(r"check_output\((.*?)\n\s*\)", src, re.S)
        assert calls, "未找到 check_output 调用"
        for call in calls:
            assert 'errors="replace"' in call, f"缺少 errors=\"replace\": {call[:120]}"


class TestDoctorConfigAudit:
    """W2 · 配置审计段。

    doctor() 的 10 项检查全是"安装完整性"，看得见 broken launcher、看得见
    stale .exe，就是看不见 env。这一段的存在意义就是把"我设了但没生效"这条
    唯一的可见点建起来；下面四条断言分别对应 plan §3 W2 验收清单的四条。
    """

    @pytest.fixture(autouse=True)
    def _clear_dhole_env(self, monkeypatch, isolated_home):
        """Scrub every DHOLE_* export, INCLUDING the DHOLE_HOME the
        isolated_home fixture just set. Config audit reads ``os.environ``
        directly, so ambient exports on the dev machine (a real one here
        runs with DHOLE_SSRF_DNS_RECHECK + a couple more) would leak into
        every assertion, and ``isolated_home`` itself is what makes
        "1 of 39 set" the ambient baseline.
        """
        from dhole_mcp import env_registry as _reg
        for e in _reg.ALL:
            monkeypatch.delenv(e.name, raising=False)
        # Keep the isolated_home path available under a private name so
        # tests that need to verify no probe file landed in tmp_path can
        # find it. The writable check falls through to ~/.dhole; on this
        # dev box (and CI) that is writable, so doctor still exits 0.
        yield

    def test_invalid_int_env_returns_one_and_names_the_fix(self, isolated_home,
                                                            monkeypatch, capsys):
        """W2 验收 1：非法整数 env → doctor 退出码 1，输出含变量名 + using default。"""
        monkeypatch.setenv("DHOLE_DEFAULT_CONTENT_CHARS", "abc")
        assert updater.doctor() == 1
        out = capsys.readouterr().out
        assert "DHOLE_DEFAULT_CONTENT_CHARS" in out
        assert "using default" in out
        assert "is not an integer" in out

    def test_valid_env_never_echos_the_value(self, isolated_home, monkeypatch, capsys):
        """W2 验收 2：合法 env 显示行存在，哨兵值**不出现**在 stdout。

        选 kind=str 的 DHOLE_UPDATE_PACKAGE 做载体：任何字符串都算合法，
        哨兵一眼看得出来；这一条同时钉住 _config_effective_value 对 str
        只写 "set" 的分支。
        """
        sentinel = "SENTINEL-package-name-xyzzy-42"
        monkeypatch.setenv("DHOLE_UPDATE_PACKAGE", sentinel)
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        assert "DHOLE_UPDATE_PACKAGE" in out
        assert sentinel not in out

    def test_secret_env_shows_only_length(self, isolated_home, monkeypatch, capsys):
        """W2 验收 3：4 个 API key 类 env 只显示 ``set (len=N)``，绝不回显值。"""
        for key_env in ("DHOLE_BOCHA_API_KEY", "DHOLE_BRIGHTDATA_API_KEY",
                        "DHOLE_EXA_API_KEY", "DHOLE_TAVILY_API_KEY"):
            sentinel = f"super-secret-{key_env}-SENTINEL"
            monkeypatch.setenv(key_env, sentinel)
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        for key_env in ("DHOLE_BOCHA_API_KEY", "DHOLE_BRIGHTDATA_API_KEY",
                        "DHOLE_EXA_API_KEY", "DHOLE_TAVILY_API_KEY"):
            assert key_env in out
            assert f"SENTINEL" not in out
        assert "set (len=" in out

    def test_import_time_env_is_marked_as_restart(self, isolated_home, monkeypatch, capsys):
        """W2：read_at="import" 的行必须显式说 "restart the host to change"，
        不然操作者会按 call 类 env 那样改完直接跑一次工具，然后困惑为什么
        改了没生效——这是"配置改了没生效"最常见的一个来源。"""
        monkeypatch.setenv("DHOLE_STRUCTURED_CONTENT", "1")
        updater.doctor()
        out = capsys.readouterr().out
        assert "DHOLE_STRUCTURED_CONTENT" in out
        assert "restart the host" in out

    def test_call_time_env_is_not_marked_as_restart(self, isolated_home,
                                                    monkeypatch, capsys):
        monkeypatch.setenv("DHOLE_USAGE_LOG", "1")
        updater.doctor()
        out = capsys.readouterr().out
        assert "DHOLE_USAGE_LOG" in out
        # the same "restart the host" wording should not ride a call-time row
        for line in out.splitlines():
            if "DHOLE_USAGE_LOG" in line:
                assert "restart the host" not in line

    def test_none_set_is_a_green_summary_not_a_warning(self, isolated_home,
                                                        monkeypatch, capsys):
        """W2：一个都没设 → "config: none set - dhole runs on defaults"，且
        不能看起来像警告。doctor 输出里 "!" 是 magenta 警告标记，✓ 是 ok；
        断言这一行的 mark 是 ok。"""
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        assert "config" in out
        assert "none set" in out
        assert "runs on defaults" in out
        # the "config" summary line must use the ok (✓) mark, never the "!"
        # warning mark - otherwise a fully-clean machine reads as though
        # something was wrong.
        for line in out.splitlines():
            if "none set" in line:
                assert "\u2713" in line or "+" in line  # ui.ok glyph (win32 fallback +)
                assert "!" not in line

    def test_summary_counts_set_variables(self, isolated_home, monkeypatch, capsys):
        monkeypatch.setenv("DHOLE_USAGE_LOG", "1")
        monkeypatch.setenv("DHOLE_MULTIMODAL", "1")
        updater.doctor()
        out = capsys.readouterr().out
        assert "2 of 39 variables set" in out

    def test_config_audit_does_not_raise_when_the_section_itself_breaks(
            self, isolated_home, monkeypatch, capsys):
        """W2 的 "doctor() 绝不许抛" 契约延伸到新段：配置审计整个 try/except
        包住，就算它自己炸了也要退化成一行 info，绝不把整个 doctor 打崩。"""
        def boom(_checks):
            raise RuntimeError("registry exploded mid-audit")

        monkeypatch.setattr(updater, "_append_config_audit", boom)
        code = updater.doctor()
        assert code in (0, 1)
        out = capsys.readouterr().out
        assert out.strip()
        assert "config audit" in out

    def test_d1_gate_typo_is_reported_as_invalid(self, isolated_home, monkeypatch, capsys):
        """W2 与 D1 的接口：非法 flag 值走 fail-safe（视为 OFF），doctor 需要
        把这一条列成 fail 行而不是静默。"""
        monkeypatch.setenv("DHOLE_IGNORE_ROBOTS", "flase")
        assert updater.doctor() == 1
        out = capsys.readouterr().out
        assert "DHOLE_IGNORE_ROBOTS" in out
        assert "not a recognised" in out

    def test_d1_gate_off_value_still_valid_no_fail(self, isolated_home,
                                                   monkeypatch, capsys):
        """回归：合法 off 值（"0"/"false"/... ）不进 fail，避免噪声。"""
        monkeypatch.setenv("DHOLE_IGNORE_ROBOTS", "0")
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        assert "DHOLE_IGNORE_ROBOTS" in out
        # 0 → OFF，display 是 "off"
        for line in out.splitlines():
            if "DHOLE_IGNORE_ROBOTS" in line:
                assert "off" in line


class TestDoctorResolverReality:
    """W3 · fake-ip 代理检测。

    修的是 §1.5 那条被实测证伪的 README 断言："零配置即可用"。真实的拦截链路
    不是 "fake-ip 被拦" 这种笼统说法：`_PRIVATE_NETWORKS` 里根本没有
    198.18.0.0/15，是 AAAA 落 fc00::/7 才是拒绝点。测试必须按这条具体链路断言
    输出包含正确的地址、正确的网段、正确的修法，且不能因"环境就是这样"把
    doctor 判红。
    """

    @pytest.fixture(autouse=True)
    def _clear_dhole_env(self, monkeypatch, isolated_home):
        from dhole_mcp import env_registry as _reg
        from dhole_mcp import security as _sec
        for e in _reg.ALL:
            monkeypatch.delenv(e.name, raising=False)
        # W12 的探测结果按进程缓存。体检现在会问它"这台机器是不是已经自动跳过"，
        # 所以缓存不丢就会让上一个用例的环境泄漏进下一个 —— 顺序一改就闪红的
        # 那种脆弱。
        _sec.reset_fake_ip_probe()
        yield
        _sec.reset_fake_ip_probe()

    def _fake_addr(self, monkeypatch, answers):
        """Patch socket.getaddrinfo to return the given (family, ip) list."""
        import socket

        def fake(host, port, *args, **kwargs):
            return [(f, 1, 6, "", (ip, 0)) for (f, ip) in answers]

        monkeypatch.setattr(socket, "getaddrinfo", fake)

    def test_fake_ip_a_record_names_the_network_and_the_remedy(
            self, isolated_home, monkeypatch, capsys):
        import socket

        self._fake_addr(monkeypatch, [
            (socket.AF_INET, "198.18.0.32"),
            (socket.AF_INET6, "fd00::20"),
        ])
        # This is an environment fact, not an install fault - the plan is
        # explicit that doctor must NOT return 1 here.
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        assert "fake-ip" in out
        assert "198.18.0.32" in out
        assert "198.18.0.0/15" in out
        assert "fc00::/7" in out
        # W12: 没人设过这个变量时，dhole 已经自己跳过那一层了。体检再教用户去设
        # =0 就是在让他修一个已经好了的东西 —— 现在这一行说的是"要强制恢复怎么做"。
        assert "automatically" in out and "dhole config set DHOLE_SSRF_DNS_RECHECK on" in out
        # and the note about what disabling actually turns off:
        assert "IP-literal" in out or "IP literal" in out

    def test_forced_on_keeps_the_manual_remedy(self, isolated_home, monkeypatch, capsys):
        """显式说 on 是决定，不是环境：这时"每个 URL 都会失败"重新成为事实，
        体检必须回到教用户怎么关掉的那句话。"""
        import socket

        monkeypatch.setenv("DHOLE_SSRF_DNS_RECHECK", "on")
        self._fake_addr(monkeypatch, [(socket.AF_INET, "198.18.0.32")])
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        assert "fake-ip proxy detected" in out
        assert "would fail" in out
        assert "DHOLE_SSRF_DNS_RECHECK=0" in out

    def test_recheck_already_disabled_is_ok_green(
            self, isolated_home, monkeypatch, capsys):
        import socket

        monkeypatch.setenv("DHOLE_SSRF_DNS_RECHECK", "0")
        self._fake_addr(monkeypatch, [
            (socket.AF_INET, "198.18.0.127"),
            (socket.AF_INET6, "fd00::80"),
        ])
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        assert "fake-ip proxy detected" in out
        assert "DNS recheck already disabled" in out

    def test_offline_dns_never_counts_as_failure(
            self, isolated_home, monkeypatch, capsys):
        import socket

        def boom(*args, **kwargs):
            raise socket.gaierror("name or service not known")

        monkeypatch.setattr(socket, "getaddrinfo", boom)
        # doctor must stay 0 on an offline box (assuming nothing else fails)
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        assert "resolver reality" in out
        # the "info" glyph is "!" not "✗"; the line is present but not red.
        for line in out.splitlines():
            if "resolver reality" in line:
                assert "✗" not in line and "x" not in line[:15]

    def test_public_resolution_is_ok(self, isolated_home, monkeypatch, capsys):
        import socket

        self._fake_addr(monkeypatch, [
            (socket.AF_INET, "93.184.216.34"),
        ])
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        assert "resolver reality" in out
        assert "public answer" in out

    def test_only_a_record_in_block_still_triggers(self, isolated_home,
                                                   monkeypatch, capsys):
        """The plan's chain is: v4 hits 198.18.0.0/15 (which is NOT in
        _PRIVATE_NETWORKS) but v6 AAAA hits fc00::/7 which is the real
        reject path. If the AAAA is absent, the v4 signal alone still
        identifies fake-ip - the operator needs to hear about it."""
        import socket

        self._fake_addr(monkeypatch, [(socket.AF_INET, "198.18.0.1")])
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        assert "fake-ip" in out
        # 只有 A 记录命中也认得出来，而这一台机器上 dhole 已经自动跳过了那一层
        assert "dhole config set DHOLE_SSRF_DNS_RECHECK on" in out

    def test_resolver_reality_does_not_raise_even_if_patched_bad(
            self, isolated_home, monkeypatch, capsys):
        """_append_resolver_reality 内部炸了也不能把 doctor 打挂。"""
        def boom(*args, **kwargs):
            raise ValueError("getaddrinfo is on fire")

        import socket
        monkeypatch.setattr(socket, "getaddrinfo", boom)
        # doctor() must still return 0/1 - never raise.
        assert updater.doctor() in (0, 1)


class TestTheCdpFenceRow:
    """W16：attach 开着但没有主机围栏 —— 这是一个"能跑"和"安全"不是一件事的
    设置。体检沉默就等于把这条边界交给了 README 里的一段小字。

    它**不算 failure**：围栏是操作者的选择，把合法选择标成故障会让 exit code
    失去意义（`dhole init --cdp` 存在的理由正是让围栏成为默认写法，而不是让
    没有围栏看起来像坏了）。
    """

    def test_attach_without_hosts_names_the_gap_and_the_command(self, isolated_home,
                                                                monkeypatch, capsys):
        monkeypatch.setenv("DHOLE_CDP_ATTACH", "1")
        monkeypatch.delenv("DHOLE_CDP_HOSTS", raising=False)
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        assert "cdp fence" in out
        assert "every attached request carries your logged-in cookies" in out
        assert "dhole init --cdp" in out

    def test_a_fenced_attach_reports_the_count_in_the_right_form(self, isolated_home,
                                                                monkeypatch, capsys):
        monkeypatch.setenv("DHOLE_CDP_ATTACH", "1")
        for hosts, expected in (("portal.example.com", "1 exact hostname "),
                                ("a.com,b.com", "2 exact hostnames ")):
            monkeypatch.setenv("DHOLE_CDP_HOSTS", hosts)
            updater.doctor()
            out = capsys.readouterr().out
            assert expected in out, (hosts, out)
            assert "hostnams" not in out, "复数是拼出来的，不是 'hostnam' + 's'"

    def test_attach_off_prints_nothing_about_a_fence(self, isolated_home,
                                                    monkeypatch, capsys):
        monkeypatch.delenv("DHOLE_CDP_ATTACH", raising=False)
        updater.doctor()
        assert "cdp fence" not in capsys.readouterr().out

    def test_a_typo_on_the_gate_does_not_claim_a_fence_is_on(self, isolated_home,
                                                            monkeypatch, capsys):
        """fail_safe 读法在这条行上也成立：'flase' 不是 ON，所以围栏行不该出现。"""
        monkeypatch.setenv("DHOLE_CDP_ATTACH", "flase")
        updater.doctor()
        out = capsys.readouterr().out
        assert "cdp fence" not in out
        assert "not fenced" not in out
