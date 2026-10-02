"""Reranker model selection state — persisted MCP-server state (not tool cache).

Holds ``~/.dhole/config/reranker.json``: ``{"model": "<name>"}``. The registry
of known models lives in ``reranker.MODELS``; unknown names are rejected so a
typo can never silently decide which model scores your results.

17.0: ``dhole model`` (the CLI verb) is gone —— 写这个文件的手只剩两处：用户自己
手写，以及 B8 收敛成单模型后彻底删除本模块。``get_selected`` 仍然如实读它：一台
16.x 上选过模型的机器不该在某个配置批次里被悄悄换掉打分器。
"""

from __future__ import annotations

import json
from pathlib import Path

from dhole_mcp import paths


def _path() -> Path:
    return paths.home() / "config" / "reranker.json"


def get_selected() -> str | None:
    """Return the configured model name, or None (use the registry default)."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        name = (data.get("model") or "").strip()
        return name or None
    except Exception:
        return None
