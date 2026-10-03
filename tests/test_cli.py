"""CLI tests: the entry point's structure, the doctor/engines subcommand surface.

Tests the real cli.py module structure and ops.main() argument handling. No
network calls. The entry point is tested BEHAVIORALLY: a subprocess blocks every
heavy dependency and then imports dhole_mcp.cli — the whole point is that it must
still import on a broken install. What it *says* when the import fails, and the
promise that it says it without installing anything, is pinned in
tests/test_self_heal_prints_only.py.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest


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
        """池子换成什么样都行，但印出来的必须**恰好**是它 —— 不靠手写退役名单反查。

        旧写法是 `for gone in (sogou_weixin, wikipedia, ...)`：那种名单每次删引擎都要
        跟着改，改漏一次守卫就静默失效。现在正向比对：主体段 = 池子的逗号串，
        附注里点名的 VPN 引擎必须是池内名字。
        """
        from dhole_mcp.search_engines import DEFAULT_ENGINES

        row = self._pool_row(monkeypatch, None)
        assert row.split(" (")[0] == ",".join(DEFAULT_ENGINES), row
        if " (default; " in row:
            named = row.split(" (default; ")[1].split(" need VPN")[0]
            extra = set(named.split(",")) - set(DEFAULT_ENGINES)
            assert not extra, f"VPN 注记点名了池外引擎: {sorted(extra)}"

    def test_env_override_still_wins(self, monkeypatch):
        row = self._pool_row(monkeypatch, "bing,yandex")
        assert row == "bing,yandex"


# ─── W5 · CLI syntax unification ────────────────────────────────────
class TestDoctorSubcommandSyntax:
    """`dhole doctor` == `dhole --doctor`.

    Before W5 the four management subcommands (model / proxy / engines /
    skill) dispatched manually *before* argparse, but `doctor` was an
    argparse flag. Result: the intuitive spelling `dhole doctor` fell
    through to argparse and errored with `unrecognized arguments: doctor`
    without ever mentioning `--doctor`. These tests pin the fix.
    """

    @pytest.fixture
    def env_isolated(self, tmp_path, monkeypatch):
        from dhole_mcp import env_registry as _reg
        for e in _reg.ALL:
            monkeypatch.delenv(e.name, raising=False)
        monkeypatch.setenv("DHOLE_HOME", str(tmp_path / "home"))

    @pytest.mark.parametrize("argv_tail", [("doctor",), ("--doctor",)])
    def test_both_spellings_produce_identical_stdout_and_exit(self,
                                                              env_isolated,
                                                              monkeypatch,
                                                              capsys,
                                                              argv_tail):
        """Same env, same tmp home, both invocations must yield byte-identical
        stdout AND the same exit code (0/1 depending on install state)."""
        from dhole_mcp import ops as srv

        monkeypatch.setattr(sys, "argv", ["dhole", *argv_tail])
        code_a = None
        out_a = ""
        try:
            srv.main()
        except SystemExit as exc:
            code_a = exc.code if exc.code is not None else 0
        out_a = capsys.readouterr().out

        monkeypatch.setattr(sys, "argv", ["dhole", *(("--doctor",) if argv_tail == ("doctor",) else ("doctor",))])
        try:
            srv.main()
        except SystemExit as exc:
            code_b = exc.code if exc.code is not None else 0
        out_b = capsys.readouterr().out

        assert code_a == code_b
        assert out_a == out_b
        assert "all healthy" in out_a or "issue(s) found" in out_a

    def test_unknown_subcommand_exits_2_and_suggests_the_closest(
            self, env_isolated, monkeypatch, capsys):
        """`dhole doctoooor` was a "usage" dump with no hint. Now it says
        exactly what the plan wanted: `did you mean: dhole doctor?`."""
        from dhole_mcp import ops as srv

        monkeypatch.setattr(sys, "argv", ["dhole", "doctoooor"])
        try:
            srv.main()
        except SystemExit as exc:
            code = exc.code
        else:
            raise AssertionError("main() should exit for an unknown subcommand")
        err = capsys.readouterr().err
        assert code == 2
        assert "unknown subcommand: doctoooor" in err
        assert "did you mean: dhole doctor" in err

    def test_flag_first_args_still_reach_argparse(self, env_isolated,
                                                  monkeypatch, capsys):
        """The unknown-subcommand guard fires only for a bare positional that
        is not a flag. `-v`, `--http`, `-u` etc. must still parse normally."""
        from dhole_mcp import ops as srv

        monkeypatch.setattr(sys, "argv", ["dhole", "--help"])
        with pytest.raises(SystemExit) as excinfo:
            srv.main()
        # argparse uses exit 0 for --help.
        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        assert "dhole doctor" in out
        assert "dhole --doctor" in out


class TestEnginesProbe:
    """`dhole engines probe` —— 逐引擎实测的那个子命令。

    它存在的理由是"这家今天到底能不能用"在真实搜索里问不出来（早退配额会把慢的
    引擎取消掉），所以它必须**一家一次**地打、并且把 metasearch 的原始状态如实
    印出来。下面几条钉的都是它的输出契约，不碰网络。
    """

    @staticmethod
    def _run(monkeypatch, capsys, args, reports=None, cooldowns=None):
        from dhole_mcp import search_engines as se
        from dhole_mcp import ops as srv
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


class TestPublicReExports:
    """`dhole_mcp.<name>` 这份公开面在拆文件的每一刀都必须还成立。

    B6 把 server.py 一块块搬出去，而 `__init__` 的惰性再导出是按名字硬编码的：
    名字搬走了、导出的那行没跟着改，用户那边就是一个 ImportError —— 全量测试里
    没有任何一条会读到 `dhole_mcp.main`，所以这类断裂只能靠守卫钉住。
    """

    def test_every_declared_export_resolves(self):
        import dhole_mcp

        missing = []
        for name in dhole_mcp.__all__:
            try:
                getattr(dhole_mcp, name)
            except Exception as exc:  # noqa: BLE001 - 守卫要报出任何失败
                missing.append(f"{name}: {type(exc).__name__} {exc}")
        assert not missing, f"__all__ 里有名字导不出来：{missing}"

    def test_main_points_at_the_entry_that_was_moved(self):
        import dhole_mcp
        from dhole_mcp import ops

        assert dhole_mcp.main is ops.main, "再导出的必须是同一个函数对象"

    def test_an_unknown_name_is_still_a_plain_attribute_error(self):
        import dhole_mcp

        try:
            dhole_mcp.nope_not_a_dhole
        except AttributeError as exc:
            assert "nope_not_a_dhole" in str(exc)
        else:
            raise AssertionError("未知名字必须是 AttributeError，不是静默 None")
