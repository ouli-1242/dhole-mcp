"""KB-8: 跑测试时到底在验哪份代码。

本机的 `site-packages` 里装着一份更早构建的 `dhole_mcp`，仓库的源码在 `src/`
下。pytest 靠 `pyproject.toml` 的 `pythonpath = ["src"]` 才加载到 src —— 也就是说
"验的是这份源码"这件事完全依赖那条配置生效。绕开它的情形都真实存在：

- 直接 `python -c "import dhole_mcp"` 或任何不走 pytest 的验证脚本；
- 装了旧轮的 venv / 系统 Python 里跑同一份测试；
- 有人动了 pythonpath 配置或换了收集方式。

那时断言会**通过**，但通过的是旧代码 —— 这是对验证结论本身的威胁，不是某一
功能的 bug。所以这条守卫故意大声失败：只要导入的不是这个 checkout 的 src，就
没有任何"全绿"可言。
"""

from pathlib import Path

import dhole_mcp

SRC = (Path(__file__).resolve().parent.parent / "src").resolve()


def test_tests_validate_this_checkout_not_an_installed_copy():
    got = Path(dhole_mcp.__file__).resolve()
    assert "site-packages" not in str(got), (
        f"import dhole_mcp 命中了已安装的副本：{got}"
    )
    assert SRC in got.parents, (
        f"import dhole_mcp 落在 {got}，不在本 checkout 的 {SRC} 下 —— "
        f"这轮测试验的不是这份源码。用 `PYTHONPATH=src python -m pytest ...`，"
        f"或确认 pyproject 的 pythonpath 生效"
    )
    # 同一次运行里更靠近断言的那些模块也必须同源（server 是被验得最多的一个）。
    import dhole_mcp.server as server

    assert Path(server.__file__).resolve().parent == got.parent


# ─── 同一个名字被定义两次：后一个静默遮蔽前一个 ────────────────────────────
#
# 实测到的场景：一个测试类被 `>>` 追加了两次，于是**第二次定义遮蔽了第一次**，
# pytest 照常收集、照常全绿 —— 但只跑了 24 条里的一半。红/绿报告本身没撒谎，
# 它只是回答了另一个问题（"后一份 12 条过了吗"）。
#
# 这和本文件的主旨是同一件事：**先确认"全绿"指的是哪批测试**。同一模块里
# 顶层的类/函数重名、同一个类里方法重名，都属于"验证结论不可信"的一类，所以
# 故意大声失败。
#
# 只查模块体与类体的**直接**子节点，不看 `if`/`try` 分支内部 —— 条件定义
# （`try: def f() ... except ImportError: def f() ...`）是合法写法，而遮蔽是
# 无条件的。

import ast

_SCANNED_DIRS = ("src/dhole_mcp", "tests")


def _duplicates(names: list[str]) -> list[str]:
    return sorted({n for n in names if names.count(n) > 1})


def _module_definitions(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = [n.name for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    issues = [f"模块级重名: {_duplicates(names)}"] if _duplicates(names) else []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = [m.name for m in node.body
                       if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
            if _duplicates(methods):
                issues.append(f"{node.name} 里方法重名: {_duplicates(methods)}")
    return issues


def test_no_definition_is_shadowed_by_a_duplicate():
    root = SRC.parent
    files: list[Path] = []
    for d in _SCANNED_DIRS:
        files.extend(sorted((root / d).glob("*.py")))
    assert files, "没扫到任何文件，这条守卫本身失效了"

    offenders = {}
    for f in files:
        issues = _module_definitions(f)
        if issues:
            offenders[str(f.relative_to(root))] = issues

    assert not offenders, (
        "同一个名字被定义两次，后者会静默遮蔽前者 —— 测试会照常全绿但少跑一半。\n"
        f"{offenders}"
    )
