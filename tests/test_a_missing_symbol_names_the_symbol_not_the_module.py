"""R01-002 · 缺的是一个符号，就别报成"缺模块"，更别给一条修不好它的命令。

现场（`round-01/env-log.md` 23:32:34 与 23:33:44 两行对照）：起不来的时候真实异常是

```
ImportError: cannot import name 'snap_to_boundary' from 'dhole_mcp.focus'
```

而 `cli.py` 打出的是 `dhole_mcp.focus` —— 因为 CPython 对"cannot import name X from Y"
把 `exc.name` 给成 **Y（模块）**，唯一的病因（符号 X）只活在 `str(exc)` 里。守卫照抄
`name` 就把那一个数丢了，留下的还是"缺模块"这三个字。

更糟的是出路：那句话印的是 `pip install -e ".[all]"`（补依赖）。依赖一件都没缺，重装
依赖对真实病因一个字都不做 —— 用户照它跑一遍几百 MB 的下载，回来还是同一个错。
17.0 把无人看守的自愈改成"说清缺什么 + 给出管用的命令"（见 `cli.py` 的模块 docstring），
本刀是那条主张的最后一格：**说清的必须是真病因，命令必须是真命令**。

门禁语义不动：非零退出码、不装任何东西、不动任何进程 —— 两种形状都照旧。
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

from dhole_mcp import cli


def _report(exc) -> str:
    return cli._startup_failure(exc)


# ─── 1 · 两种形状各有各的病因与出路 ─────────────────────────────

def test_a_missing_module_names_the_module_and_the_dependency_install():
    exc = ModuleNotFoundError("No module named 'bs4'")
    exc.name = "bs4"
    text = _report(exc)
    assert "bs4" in text, text
    assert 'pip install -e ".[all]"' in text, text
    assert "cannot import name" not in text, text


def test_a_missing_symbol_names_the_symbol():
    """这一条就是 R01-002 的全部：符号名必须在回执里，不许只剩模块名。"""
    exc = ImportError(
        "cannot import name 'snap_to_boundary' from 'dhole_mcp.focus'")
    exc.name = "dhole_mcp.focus"          # CPython 3.14 实测：给的是模块，不是符号
    text = _report(exc)
    assert "snap_to_boundary" in text, text
    assert "dhole_mcp.focus" in text, text     # 两个数都要在：符号 + 它本该住在哪


def test_the_symbol_case_does_not_blame_a_dependency():
    """依赖一件都没缺：`.[all]` 对这一格是假出路，跑完几百 MB 回来还是同一个错。"""
    exc = ImportError("cannot import name 'snap_to_boundary' from 'dhole_mcp.focus'")
    exc.name = "dhole_mcp.focus"
    text = _report(exc)
    assert ".[all]" not in text, text


def test_the_symbol_case_names_a_repair_that_applies():
    """代码不一致要靠**取回代码**修，两条路都要点名（checkout 与索引各有各的那条）。

    这里刻意断言 `--force-reinstall` **不在**：17.0 B2-3 那道守卫
    （`test_self_heal_prints_only.py`）不许包内任何被执行到的字符串带上那个 flag，
    而一条让人先卸载再装回本体的写法同样能取回代码 —— 守卫是来管文案的，不是来被绕的。
    """
    exc = ImportError("cannot import name 'snap_to_boundary' from 'dhole_mcp.focus'")
    text = _report(exc)
    assert "--force-reinstall" not in text, text
    assert "git pull" in text, text
    assert "uninstall -y dhole-mcp" in text and "--no-deps" in text, text


def test_both_shapes_stay_honest_about_touching_nothing():
    """17.0 那句"什么都没装、没碰任何进程"不许被这一刀挤掉。"""
    for exc in (ModuleNotFoundError("No module named 'bs4'"),
                ImportError("cannot import name 'x' from 'dhole_mcp.focus'")):
        assert "nothing was installed and no process was touched" in _report(exc)


# ─── 2 · 量具不许靠自己造的句子活着：拿真 CPython 异常验一次 ────

def test_the_real_exception_from_cpython_carries_the_symbol_in_its_message(tmp_path):
    """自己造的 `ImportError(...)` 会跟着我的实现一起错 —— 所以要拿真家伙量。

    现场立一个包，从存在的模块里 import 一个不存在的名字，把 CPython 真正抛出的那个
    对象喂给守卫：符号名必须在回执里，而 `.name` 实测是模块名（这正是本条缺陷的成因）。
    """
    pkg = tmp_path / "brokenpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "mod.py").write_text("X = 1\n", encoding="utf-8")
    (pkg / "driver.py").write_text(
        "from brokenpkg.mod import X, missing_symbol\n", encoding="utf-8")
    code = textwrap.dedent(f"""
        import sys, json
        sys.path.insert(0, {str(tmp_path)!r})
        try:
            import brokenpkg.driver
        except (ImportError, ModuleNotFoundError) as e:
            print(json.dumps({{"type": type(e).__name__, "name": e.name,
                               "text": str(e)}}))
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    facts = json.loads(out.stdout.strip())
    assert facts["name"] == "brokenpkg.mod", facts        # 病因不在 .name 里 —— 实测
    assert "missing_symbol" in facts["text"], facts
    exc = ImportError(facts["text"])
    exc.name = facts["name"]
    assert "missing_symbol" in _report(exc), _report(exc)


# ─── 3 · 接线：守卫真的走这一条，且退出码语义不动 ───────────────

def test_main_reports_the_symbol_and_exits_non_zero(monkeypatch, capsys):
    """端到端：`from dhole_mcp.ops import main` 缺那个名字时，回执必须有它。

    夹具不是假异常 —— 往 `sys.modules` 里放一个**存在但没有 `main`** 的模块，那条
    import 语句就会按 CPython 的真实形状抛错（`exc.name` 是模块名，符号只在消息里）。
    这正是 R01-002 的成因形状，所以这一条测的是接线而不是我造的句子。
    """
    import types

    stub = types.ModuleType("dhole_mcp.ops")
    monkeypatch.setitem(sys.modules, "dhole_mcp.ops", stub)
    assert cli.main() == 1
    err = capsys.readouterr().err
    assert "main" in err and "dhole_mcp.ops" in err, err
    assert ".[all]" not in err, err
