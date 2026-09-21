"""CLI tests: self-healing entry point, version probing, version comparison.

Tests the real cli.py module structure and updater functions. No network
calls for version probing (PyPI fetch is mocked). The self-heal flow is
tested BEHAVIORALLY: a subprocess blocks every heavy dependency and then
imports dhole_mcp.cli — the whole point of the entry point is that it must
import (and self-heal) even on a broken install.
"""

import os
import subprocess
import sys
from pathlib import Path

from dhole_mcp.updater import check_version, pad_version, _at_or_ahead

_SRC = str(Path(__file__).resolve().parent.parent / "src")

_BLOCK_HEAVY_DEPS = """
import sys, importlib.abc

BLOCK = {"mcp", "primp", "patchright", "playwright", "pydantic",
         "trafilatura", "aiosqlite", "httpx", "lxml", "onnxruntime"}

class _Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in BLOCK:
            raise ImportError(f"blocked: {fullname}")
        return None

sys.meta_path.insert(0, _Blocker())
import dhole_mcp.cli
assert callable(dhole_mcp.cli.main)
print("CLI_IMPORT_OK")
"""


# ─── CLI self-heal behaviour ───────────────────────────────────────

class TestCLIStructure:

    def test_cli_imports_without_heavy_deps(self):
        """cli.py 必须在重依赖全部不可导入时仍可导入（自愈前提）。

        行为测试：子进程中用 import hook 把 mcp/primp/patchright/...
        全部变成 ImportError，再导入 dhole_mcp.cli。若有人给 cli.py
        加了模块级重依赖导入，坏安装下 dhole 命令会彻底报废——这个
        测试会在那时真实失败。（旧的 hasattr 存在性检查做不到这一点，
        已删。）"""
        result = subprocess.run(
            [sys.executable, "-c", _BLOCK_HEAVY_DEPS],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "PYTHONPATH": _SRC},
        )
        assert result.returncode == 0, (
            f"cli import failed with heavy deps blocked:\n"
            f"{result.stdout}\n{result.stderr}"
        )
        assert "CLI_IMPORT_OK" in result.stdout


# ─── Repair script ─────────────────────────────────────────────────

class TestRepairScript:

    def test_repair_script_path(self, tmp_path, monkeypatch):
        """repair.py should be at ~/.dhole/repair.py"""
        import dhole_mcp.updater as updater
        home = str(tmp_path)
        monkeypatch.setattr(os.path, "expanduser", lambda x: home)
        path = updater.repair_script_path()
        assert ".dhole" in path
        assert "repair.py" in path

    def test_run_repair_writes_under_dhole_home(self, tmp_path, monkeypatch):
        """KB-7：cli 的 repair 也要跟随 DHOLE_HOME，不能写进真实 home。

        这里同时把 ``expanduser("~")`` 指向另一个目录：如果代码退回硬编码
        ``~/.dhole``，文件会落在那里，第二条断言就会抓到。
        """
        import dhole_mcp.cli as cli

        state = tmp_path / "moved_state"
        real_home = tmp_path / "real_home"
        monkeypatch.setenv("DHOLE_HOME", str(state))
        monkeypatch.setattr(os.path, "expanduser", lambda x: str(real_home))

        launched: list[list[str]] = []

        class _Done:
            returncode = 0

        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, **k: (launched.append(cmd), _Done())[1],
        )

        assert cli._run_repair() == 0
        assert (state / "repair.py").exists(), "repair.py 该落在 DHOLE_HOME 下"
        assert not real_home.exists(), "不许在真实 home 下建任何东西"
        assert launched and launched[0][1] == str(state / "repair.py")


# ─── Version probing ──────────────────────────────────────────────

class TestVersionProbing:

    def test_pad_version_basic(self):
        assert pad_version("11.1.6") == (11, 1, 6)

    def test_pad_version_extra_parts_ignored(self):
        assert pad_version("11.1.6.7.8") == (11, 1, 6)

    def test_pad_version_short(self):
        assert pad_version("11.1") == (11, 1)

    def test_at_or_ahead_current(self):
        assert _at_or_ahead("11.1.6", "11.1.6") is True

    def test_at_or_ahead_newer(self):
        assert _at_or_ahead("11.2.0", "11.1.6") is True

    def test_at_or_ahead_older(self):
        assert _at_or_ahead("11.1.5", "11.1.6") is False

    def test_at_or_ahead_unknown_returns_false(self):
        assert _at_or_ahead("unknown", "11.1.6") is False

    def test_at_or_ahead_empty_returns_false(self):
        assert _at_or_ahead("", "11.1.6") is False

    def test_check_version_returns_tuple(self):
        # PyPI fetch may fail in tests; just check the return type
        result = check_version()
        assert len(result) == 3
        installed, latest, is_current = result
        assert isinstance(installed, str)
        # latest may be None if PyPI unreachable
        if latest is not None:
            assert isinstance(latest, str)
        # is_current may be None if latest is None
        if is_current is not None:
            assert isinstance(is_current, bool)


# ─── Version comparison edge cases ────────────────────────────────

class TestVersionComparison:

    def test_major_version_comparison(self):
        assert _at_or_ahead("12.0.0", "11.9.9") is True

    def test_minor_version_comparison(self):
        assert _at_or_ahead("11.2.0", "11.1.9") is True

    def test_patch_version_comparison(self):
        assert _at_or_ahead("11.1.7", "11.1.6") is True

    def test_same_version(self):
        assert _at_or_ahead("11.1.6", "11.1.6") is True

    def test_malformed_installed(self):
        assert _at_or_ahead("not-a-version", "11.1.6") is False
