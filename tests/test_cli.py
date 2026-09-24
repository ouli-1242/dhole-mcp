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


class TestSearchPoolRow:
    """`dhole -v` 的 search pool 行曾经是写死的字面量，15.0 换引擎池时漏改，面板里
    报的还是旧池（bing,duckduckgo,…,sogou_weixin）。现在从 DEFAULT_ENGINES 推导，
    这条钉住它不许再漂 —— 与 instructions 那条 `searches N engines` 同一类守卫。
    """

    @staticmethod
    def _pool_row(monkeypatch, env_value: str | None) -> str:
        from dhole_mcp import updater

        if env_value is None:
            monkeypatch.delenv("DHOLE_DEFAULT_ENGINES", raising=False)
        else:
            monkeypatch.setenv("DHOLE_DEFAULT_ENGINES", env_value)
        rows = {label: state for label, state, _ok in updater.capabilities()}
        return rows["search pool"]

    def test_row_lists_the_real_pool_and_nothing_else(self, monkeypatch):
        from dhole_mcp.search_engines import DEFAULT_ENGINES

        row = self._pool_row(monkeypatch, None)
        for eng in DEFAULT_ENGINES:
            assert eng in row, f"{eng} 不在 search pool 行里: {row}"
        # 不在池里的名字一个都不许出现（旧池的 sogou_weixin 就是这么漏进来的）
        for gone in ("sogou_weixin", "wikipedia", "grokipedia", "baidu_baike"):
            assert gone not in row, f"{gone} 已不在默认池，却还印在: {row}"

    def test_env_override_still_wins(self, monkeypatch):
        row = self._pool_row(monkeypatch, "bing,yandex")
        assert row == "bing,yandex"


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


class TestEnginesProbe:
    """`dhole engines probe` —— 逐引擎实测的那个子命令。

    它存在的理由是"这家今天到底能不能用"在真实搜索里问不出来（早退配额会把慢的
    引擎取消掉），所以它必须**一家一次**地打、并且把 metasearch 的原始状态如实
    印出来。下面几条钉的都是它的输出契约，不碰网络。
    """

    @staticmethod
    def _run(monkeypatch, capsys, args, reports=None, cooldowns=None):
        from dhole_mcp import search_engines as se
        from dhole_mcp import server as srv
        from dhole_mcp.search_engines import EngineReport

        given = reports or {}
        asked: list[str] = []

        async def fake_multi(query, max_results=10, *, engines=None, **kw):
            name = list(engines or [])[0]
            asked.append(name)
            rep = given.get(name) or EngineReport(name=name, ok=True, status="ok",
                                                  item_nodes=5, usable=5)
            return ([object()] * 3 if rep.ok else []), [rep]

        monkeypatch.setattr(se, "multi_search", fake_multi)
        monkeypatch.setattr(se, "_cooldowns", lambda: dict(cooldowns or {}))
        code = srv._engines_probe(args)
        return code, capsys.readouterr().out, asked

    def test_probes_every_default_engine_one_at_a_time(self, monkeypatch, capsys):
        from dhole_mcp.search_engines import DEFAULT_ENGINES

        code, out, asked = self._run(monkeypatch, capsys, [])
        assert asked == list(DEFAULT_ENGINES), "一家一次，顺序就是池子顺序"
        assert f"{len(DEFAULT_ENGINES)} of {len(DEFAULT_ENGINES)}" in out
        assert code == 0

    def test_names_the_status_code_instead_of_saying_no_results(self, monkeypatch, capsys):
        """302 没跟进 = 没看到结果页，输出里必须说得出是哪个状态码。"""
        from dhole_mcp.search_engines import EngineReport

        _, out, _ = self._run(
            monkeypatch, capsys, ["bing_global"],
            reports={"bing_global": EngineReport(
                name="bing_global", status="empty",
                error="no parsable results (HTTP 302, redirect not followed)")})
        assert "HTTP 302" in out and "redirect not followed" in out

    def test_does_not_knock_on_a_cooling_engine(self, monkeypatch, capsys):
        """点名单引擎时它在冷却里的话，metasearch 会抛"No engines could start"，
        输出就变成一个看不懂的 error —— 所以冷却要自己先判、干脆不发请求。"""
        code, out, asked = self._run(monkeypatch, capsys, ["so360"],
                                     cooldowns={"so360": 55.0})
        assert asked == [], "冷却中的引擎不许被探针敲"
        assert "cooling" in out and "55s left" in out
        assert code == 1, "没有任何引擎答话时退出码非零（脚本里能用）"

    def test_alias_resolves_to_the_backend_the_cooldown_is_keyed_by(self, monkeypatch, capsys):
        _, out, asked = self._run(monkeypatch, capsys, ["360"],
                                  cooldowns={"so360": 40.0})
        assert asked == [] and "cooling" in out, "别名 360 要认得 so360 的冷却表"

    def test_unknown_engine_name_is_rejected_with_a_way_to_list_them(self, monkeypatch, capsys):
        code, out, asked = self._run(monkeypatch, capsys, ["goole"])
        assert code == 2 and asked == []
        assert "goole" in out and "--all" in out

    def test_all_flag_covers_every_registered_keyless_engine(self, monkeypatch, capsys):
        from dhole_mcp.search_metasearch import _TEXT_ENGINES

        _, _, asked = self._run(monkeypatch, capsys, ["--all"])
        assert len(asked) == len(_TEXT_ENGINES), \
            f"--all 应当问遍 {len(_TEXT_ENGINES)} 个免密引擎，实际 {len(asked)}"
