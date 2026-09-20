"""Dhole — Web research for AI agents.

$0 forever. Fetch any page with anti-bot bypass plus web search.
"""

__version__ = "14.0"

# Rename compatibility (14.0: hound-mcp -> dhole-mcp): MCP client configs that
# predate the rename still export HOUND_* env vars. Copy each into its DHOLE_*
# equivalent unless the new name is already set. This runs at package import —
# before any submodule reads os.environ at import time — so every env read
# (module-level or lazy) sees the migrated values. DHOLE_* always wins.
import os as _os

for _k in [k for k in _os.environ if k.startswith("HOUND_")]:
    _os.environ.setdefault("DHOLE_" + _k[len("HOUND_"):], _os.environ[_k])

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
        "SessionClosedModel",
        "CacheInfoModel",
        "main",
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
            SessionClosedModel,
            CacheInfoModel,
            main,
        )
        return locals()[name]
    raise AttributeError(f"module 'dhole_mcp' has no attribute '{name}'")


__all__ = [
    "MasterFetchServer",
    "ResponseModel",
    "BulkResponseModel",
    "SessionInfo",
    "SessionCreatedModel",
    "SessionClosedModel",
    "CacheInfoModel",
    "main",
]