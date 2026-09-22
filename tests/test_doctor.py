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
