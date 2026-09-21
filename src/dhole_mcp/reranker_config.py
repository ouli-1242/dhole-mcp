"""Reranker model selection state — persisted MCP-server state (not tool cache).

Holds ``~/.dhole/config/reranker.json``: ``{"model": "<name>"}``. The registry
of known models lives in ``reranker.MODELS``; unknown names are rejected so a
typo can never silently decide which model scores your results.
"""

from __future__ import annotations

import json
import os
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


def set_selected(name: str) -> Path:
    """Persist the model choice. Raises ValueError on unknown names."""
    from dhole_mcp.reranker import MODELS

    if name not in MODELS:
        raise ValueError(
            f"unknown reranker model {name!r} (known: {', '.join(sorted(MODELS))})"
        )
    p = _path()
    paths.ensure_private_dir(p.parent)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"model": name}, ensure_ascii=False, indent=2)
                   + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p
