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
