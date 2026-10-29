"""Dhole —— 给 AI agent 的网页研究工具。

永远 $0。绕过反爬抓任意页面，外加网页搜索。
"""

__version__ = "17.6"

# 惰性 import——server 会拖进重依赖（patchright、playwright 等）；其它模块
# （cache、security）很轻，可以在没有完整依赖链的情况下直接 import 来测试。


def __getattr__(name: str):
    """server 级导出的惰性属性访问。"""
    _lazy_exports = {
        "MasterFetchServer",
        "ResponseModel",
        "BulkResponseModel",
        "SessionInfo",
        "SessionCreatedModel",
        "SysStateModel",
    }
    if name in _lazy_exports:
        # F401：这些名字通过下方 locals()[name] 返回，是刻意的惰性再导出，
        # ruff 看不到用法，故显式豁免。
        from dhole_mcp.server import (  # noqa: F401
            MasterFetchServer,
            ResponseModel,
            BulkResponseModel,
            SessionInfo,
            SessionCreatedModel,
            SysStateModel,
        )

        return locals()[name]
    if name == "main":
        # 17.0 B6-1 把进程入口搬进 ops.py，再导出的来源必须跟着搬 —— 留在
        # server 那一行会变成 ImportError，而 `dhole_mcp.main` 是公开面。
        from dhole_mcp.ops import main

        return main
    raise AttributeError(f"module 'dhole_mcp' has no attribute '{name}'")


__all__ = [
    "MasterFetchServer",
    "ResponseModel",
    "BulkResponseModel",
    "SessionInfo",
    "SessionCreatedModel",
    "SysStateModel",
    "main",
]
