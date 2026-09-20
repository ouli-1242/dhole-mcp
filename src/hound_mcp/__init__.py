"""Deprecated compatibility alias: this package was renamed to ``dhole_mcp``.

Kept through the 14.0 rename so existing MCP client configs that launch
``python -m hound_mcp`` or import ``hound_mcp`` keep working unchanged.
Importing emits a DeprecationWarning. Remove this shim package once every
client config points at dhole / dhole_mcp.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "hound_mcp was renamed to dhole_mcp; update your launch command / imports",
    DeprecationWarning,
    stacklevel=2,
)

from dhole_mcp.server import (  # noqa: E402,F401
    BulkResponseModel,
    CacheInfoModel,
    MasterFetchServer,
    ResponseModel,
    SessionClosedModel,
    SessionCreatedModel,
    SessionInfo,
    main,
)

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
