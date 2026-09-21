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
