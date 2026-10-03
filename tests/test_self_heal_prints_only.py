"""17.0 B2-3 守卫：自愈**只许打印命令**，不许再动手。

删掉的东西（§3-34）：由任意 ImportError 触发的无人值守 ``pip --force-reinstall``、
``taskkill`` 全部 dhole 进程、写进状态目录的 ``repair.py``、``dhole -u`` 自更新，
以及 updater 里为它们服务的一整套装/回滚机制。

留下的东西：坏安装时把**缺的是哪个模块**说清楚，再给出一条人能跑的修复命令，
然后返回非零退出码。判据是同一条 —— 重装几百 MB、杀掉用户正在跑的进程，这种事
需要的是明确决定，不是一个异常分支。

这里的守卫因此分成两半：一半钉"打印"（文案必须说清缺什么、怎么修），另一半钉
"不再动手"（源码里不许再有 taskkill/pkill/subprocess/PyPI 探测，doctor 不许往状态
目录写东西）。动手那一半全部配了机制自检：扫不到东西就红的守卫，等于没有守卫。
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dhole_mcp import cli, updater

SRC_DIR = Path(__file__).resolve().parent.parent / "src" / "dhole_mcp"


def _py_sources() -> list[Path]:
    return sorted(p for p in SRC_DIR.rglob("*.py") if "__pycache__" not in p.parts)


# ─── 动手的那一半：不许复活 ─────────────────────────────────────────────

KILL_TOKENS = ("taskkill", "pkill", "--force-reinstall")

#: updater 独有的禁令：诊断命令不许联网问"最新是哪版"。pypi.org 这个域名在
#: envelope/search 里是合法的（认包页用的），所以只在本模块禁；而 doctor 打印的
#: 修复命令里本来就有 "pip install"，所以禁的是取数动作，不是这个词。
UPDATER_ONLY_TOKENS = ("urlopen", "pypi.org")

_DOC_BEARING = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _code_strings(path: Path) -> list[str]:
    """源码里**会被执行到**的字符串常量，docstring 除外。

    为什么不直接扫全文：这个模块要留着记录"这里曾经会 taskkill 所有进程、从索引
    重装几百 MB"，否则删除的理由就随删除一起消失了。把写历史的那段注释当成罪行，
    守卫的第一次误报就会逼人把它 disable —— 那比没有守卫更糟。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, _DOC_BEARING) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docs.add(id(first.value))
    return [c.value for c in ast.walk(tree)
            if isinstance(c, ast.Constant)
            and isinstance(c.value, str)
            and id(c) not in docs]


def _kill_tokens(text: str) -> list[str]:
    return [t for t in KILL_TOKENS if t in text]


class TestNothingActsOnItsOwn:

    def test_no_executed_string_in_the_package_carries_a_killing_token(self):
        hits: list[str] = []
        for path in _py_sources():
            for blob in _code_strings(path):
                for token in _kill_tokens(blob):
                    hits.append(f"{path.name}:{token}")
        assert not hits, f"自愈动手机制又回来了：{hits}"

    def test_the_scan_reads_a_killing_call_and_ignores_a_history_note(self):
        """机制自检，两个方向都要验：漏检的守卫是假的，把 docstring 当罪证的守卫
        会在第一次误报后被 disable。"""
        sample = SRC_DIR / "_guard_probe.py"
        sample.write_text(
            '"""这里曾经会 taskkill 所有进程，是从索引重装几百 MB 的入口。"""\n'
            "import subprocess\n"
            "subprocess.run(['pkill', '-x', 'dhole'])\n",
            encoding="utf-8",
        )
        try:
            found = [t for blob in _code_strings(sample) for t in _kill_tokens(blob)]
        finally:
            sample.unlink()
        assert found == ["pkill"], found

    def test_the_diagnostics_module_never_asks_an_index_whats_latest(self):
        """`dhole -v` 曾经联网取 PyPI 的最新版来比"up to date"。这个 fork 不发
        PyPI，比对本身就无意义，所以取数动作整个删掉 —— 面板只说自己是谁。"""
        blobs = _code_strings(SRC_DIR / "updater.py")
        hits = [t for blob in blobs for t in UPDATER_ONLY_TOKENS if t in blob]
        assert not hits, hits

    def test_cli_never_shells_out(self):
        """入口点连 subprocess 都不许 import：它唯一的动作是打印。"""
        tree = ast.parse((SRC_DIR / "cli.py").read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert "subprocess" not in imported, sorted(imported)
        assert not [n for n in ast.walk(tree)
                    if isinstance(n, ast.Attribute)
                    and isinstance(n.value, ast.Name)
                    and n.value.id in ("os", "subprocess")
                    and n.attr in ("system", "Popen", "run", "spawnl", "execv")], \
            "cli.py 里出现了执行外部程序的调用"

    def test_the_cli_import_scan_reacts_to_a_shelling_entry_point(self):
        tree = ast.parse("import subprocess\nsubprocess.run(['pip'])\n")
        names = {a.name.split(".")[0] for n in ast.walk(tree)
                 if isinstance(n, ast.Import) for a in n.names}
        assert "subprocess" in names

    @pytest.mark.parametrize("name", [
        "do_update", "check_version", "update_package", "update_index_url",
        "pad_version", "_at_or_ahead", "_advanced", "_dist", "_dist_spec",
        "repair_script_path", "_state_path", "_dhole_home", "_REPAIR_SCRIPT",
        "_write_repair_script", "_write_last_version", "_pip_cmd", "_heal_cmd",
        "_run_pip", "_diagnose", "_build_helper_source", "_spawn_helper",
        "_stop_all_dhole", "_looks_like_file_lock_error",
    ])
    def test_the_updater_exposes_no_install_machinery(self, name):
        assert not hasattr(updater, name), f"updater.{name} 又回来了"

    @pytest.mark.parametrize("name", [
        "_run_repair", "_repair_script_text", "_pip_spec", "_installed_version",
        "_index_url", "_auto_repair_enabled", "_dist_name",
    ])
    def test_the_cli_exposes_no_repair_helpers(self, name):
        assert not hasattr(cli, name), f"cli.{name} 又回来了"

    def test_the_updater_public_names_are_the_diagnostics_surface(self):
        assert sorted(updater.__all__) == sorted(
            ["capabilities", "doctor", "installed_version",
             "print_capabilities", "print_version"])

    def test_doctor_leaves_no_repair_script_behind(self, tmp_path, monkeypatch, capsys):
        """doctor 是"坏的时候才去跑"的命令，它自己不许再往状态目录写一个会重装、
        会杀进程的脚本。"""
        home = tmp_path / "home"
        monkeypatch.setenv("DHOLE_HOME", str(home))
        updater.doctor()
        capsys.readouterr()
        assert not (home / "repair.py").exists()
        assert not list(home.glob("repair*")), list(home.iterdir())

    def test_doctor_does_not_report_a_repair_script_as_a_feature(self, tmp_path,
                                                                  monkeypatch, capsys):
        monkeypatch.setenv("DHOLE_HOME", str(tmp_path / "home"))
        updater.doctor()
        out = capsys.readouterr().out
        assert "repair script" not in out, out


# ─── 打印的那一半：文案必须能用 ─────────────────────────────────────────

class TestBrokenImportSpeaksUp:

    @pytest.fixture
    def blocked_server(self, monkeypatch):
        """把 `import dhole_mcp.server` 变成"缺一个第三方依赖"的真实形状。"""
        class _Blocker:
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "dhole_mcp.server":
                    raise ModuleNotFoundError(
                        "No module named 'trafilatura'", name="trafilatura")
                return None

        blocker = _Blocker()
        monkeypatch.delitem(sys.modules, "dhole_mcp.server", raising=False)
        # B6 之后入口链是 cli → ops → server：清掉 ops 的缓存，那一行 server 导入才会真的再跑一次。
        monkeypatch.delitem(sys.modules, "dhole_mcp.ops", raising=False)
        sys.meta_path.insert(0, blocker)
        try:
            yield
        finally:
            sys.meta_path.remove(blocker)

    def _run_cli(self, monkeypatch, tmp_path):
        home = tmp_path / "home"
        monkeypatch.setenv("DHOLE_HOME", str(home))
        rc = cli.main()
        return rc, home

    def test_it_returns_nonzero_without_installing(self, blocked_server,
                                                   monkeypatch, tmp_path, capsys):
        rc, home = self._run_cli(monkeypatch, tmp_path)
        assert rc == 1, "坏安装必须给出非零退出码，否则宿主以为起来了"
        assert not home.exists() or list(home.iterdir()) == [], \
            "入口点不许在状态目录里留下任何东西"

    def test_the_message_names_the_missing_module(self, blocked_server,
                                                  monkeypatch, tmp_path, capsys):
        capsys.readouterr()
        self._run_cli(monkeypatch, tmp_path)
        err = capsys.readouterr().err
        assert "trafilatura" in err, err

    def test_stdout_stays_empty_on_the_failure_path(self, blocked_server,
                                                    monkeypatch, tmp_path, capsys):
        """`dhole` 的 stdout 是 MCP 协议通道。这条路走到时服务并没有起来，
        但把诊断写进 stdout 依然会让宿主解析到一个垃圾帧 —— 所以写 stderr。"""
        capsys.readouterr()
        self._run_cli(monkeypatch, tmp_path)
        captured = capsys.readouterr()
        assert captured.out == "", captured.out

    def test_the_message_gives_a_command_a_human_can_run(self, blocked_server,
                                                          monkeypatch, tmp_path,
                                                          capsys):
        capsys.readouterr()
        self._run_cli(monkeypatch, tmp_path)
        err = capsys.readouterr().err
        assert "pip install" in err, err
        # 说清楚"这次什么都没动"：否则用户以为自己已经被重装了。
        assert "nothing was installed" in err, err

    def test_it_does_not_pretend_to_have_recovered(self, blocked_server,
                                                   monkeypatch, tmp_path, capsys):
        capsys.readouterr()
        self._run_cli(monkeypatch, tmp_path)
        err = capsys.readouterr().err.lower()
        for lied in ("recovered", "repairing", "recovering"):
            assert lied not in err, f"入口点又假装自己在自愈了：{lied}"


class TestUpdateCommandIsGone:

    def test_dhole_u_is_rejected_by_argparse(self, monkeypatch, tmp_path, capsys):
        """不是"接受但什么都不做"，是明确拒绝：一个不再存在的开关静默成功，
        用户就永远不知道自己该去哪重装。"""
        from dhole_mcp import ops as srv

        monkeypatch.setenv("DHOLE_HOME", str(tmp_path / "home"))
        monkeypatch.setattr(sys, "argv", ["dhole", "-u"])
        with pytest.raises(SystemExit) as excinfo:
            srv.main()
        assert excinfo.value.code == 2
        assert "unrecognized" in capsys.readouterr().err

    def test_dhole_update_long_form_is_rejected_too(self, monkeypatch, tmp_path,
                                                    capsys):
        from dhole_mcp import ops as srv

        monkeypatch.setenv("DHOLE_HOME", str(tmp_path / "home"))
        monkeypatch.setattr(sys, "argv", ["dhole", "--update"])
        with pytest.raises(SystemExit) as excinfo:
            srv.main()
        assert excinfo.value.code == 2

    def test_help_advertises_no_update_command(self, monkeypatch, tmp_path, capsys):
        from dhole_mcp import ops as srv

        monkeypatch.setenv("DHOLE_HOME", str(tmp_path / "home"))
        monkeypatch.setattr(sys, "argv", ["dhole", "--help"])
        with pytest.raises(SystemExit):
            srv.main()
        out = capsys.readouterr().out
        assert "dhole -u" not in out
        assert "dhole doctor" in out

    def test_the_version_panel_points_at_the_manual_update_path(self, monkeypatch,
                                                                 capsys):
        """面板不许再说"更新用 dhole -u"（那条命令已经不存在），也不许承诺
        "up to date"——它已经不联网了。"""
        monkeypatch.setattr(updater, "installed_version", lambda: "17.0.0")
        updater.print_version()
        out = capsys.readouterr().out
        assert "dhole -u" not in out
        assert "up to date" not in out, out
        assert "pip install" in out, out

    def test_installed_version_prefers_the_runtime_dunder(self, monkeypatch):
        """元数据是安装那一刻的快照，editable 升版后它会说谎（实测 16.2 的代码
        横幅印过 16.1），所以 __version__ 优先。"""
        import dhole_mcp

        monkeypatch.setattr(dhole_mcp, "__version__", "17.0.0-test")
        assert updater.installed_version() == "17.0.0-test"


# ─── 入口点仍然要在坏安装下能导入 ───────────────────────────────────────

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
assert not hasattr(dhole_mcp.cli, "subprocess"), "入口点又拿回了 subprocess"
print("CLI_IMPORT_OK")
"""


def test_the_entry_point_still_imports_on_a_broken_install():
    """删自愈不许删掉"入口点在坏安装上还能跑"这件事本身：它现在只打印，
    但那一句打印就是用户唯一能得到的东西。"""
    result = subprocess.run(
        [sys.executable, "-c", _BLOCK_HEAVY_DEPS],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONPATH": str(SRC_DIR.parent)},
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "CLI_IMPORT_OK" in result.stdout
