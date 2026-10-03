"""Dhole — Web research for AI agents.

$0 forever. Fetch any page with anti-bot bypass plus web search.
"""

__version__ = "17.0"

# Lazy imports — server pulls in heavy deps (patchright, playwright, etc.)
# Other modules (cache, security) are lightweight and can be imported directly
# for testing without the full dependency chain.


def __getattr__(name: str):
    """Lazy attribute access for server-level exports."""
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
