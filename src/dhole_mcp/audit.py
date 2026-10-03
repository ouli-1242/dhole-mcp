"""Dhole 的本地调用日志：`DHOLE_USAGE_LOG` 那一面，以及只往这里发的安全事件。

从 server.py 原样搬出（17.0 B6 的第五刀，零行为变更）。三颗函数是一份日志的两个写入口：
`_log_tool_call` 记每次调用，`_audit_event` 记 CDP attach 这类"成不成都该看见"的事件，
`_result_error` 把工具自己报的失败从响应里挑出来喂给前两者。它们必须待在一起，
因为共用同一份纪律：默认关、只落本机、不记参数值、best-effort 永不抛。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from dhole_mcp import env_registry, paths
from dhole_mcp.security import redact_api_key


def _audit_event(event: str, **fields: Any) -> None:
    """Security-relevant events (CDP attach attempts) into the opt-in local
    audit channel. Same file as ``_log_tool_call`` (DHOLE_USAGE_LOG), same
    discipline: opt-in, local-only, best-effort, never raises. Attach attempts
    are recorded whether or not they succeed — a refused attach is exactly the
    event worth seeing in the log."""
    target = (env_registry.value_of("DHOLE_USAGE_LOG") or "").strip()
    if not target:
        return
    owned = target.lower() in ("1", "true", "yes", "on")
    if owned:
        target = str(paths.file("usage.jsonl"))
    try:
        parent = os.path.dirname(os.path.abspath(target))
        if parent:
            os.makedirs(parent, exist_ok=True)
        entry: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
        }
        entry.update(fields)
        with open(target, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if owned:
            paths.harden_dir(parent)
            paths.harden_file(target)
    except Exception:
        pass


def _result_error(result: Any) -> str:
    """The application-level error a tool reported, or "" when it reported none.

    ``ok`` in the call log means "the call ran to completion without raising" —
    a tool that answers with an envelope carrying ``error`` never raises, so
    without this the log said ``ok: true`` for calls that plainly failed.
    Measured: a ``feed_read`` refused by the SSRF guard logged
    ``{"tool": "feed_read", "ok": true, "ms": 22.0}`` with no trace of the
    refusal. The question this log exists to answer is "does my client actually
    route here, and does it hold up?" — and a refusal that reads like a success
    is the one shape that question cannot survive.

    Additive on purpose: ``ok`` keeps its meaning (a batch that answers with one
    failed URL is still a completed call), and the reported error rides in the
    existing ``error`` field, so ``select(.error)`` finds every call a tool
    itself called a failure.
    """
    payload: Any = result
    if isinstance(result, tuple) and len(result) == 2:
        payload = result[1]
    rows = payload if isinstance(payload, list) else [payload]
    for row in rows:
        if isinstance(row, dict):
            err = row.get("error")
            if err:
                return str(err)[:200]
    return ""


def _log_tool_call(name: str, ok: bool, duration_ms: float, error: str = "") -> None:
    """Append one line to the opt-in local call log (DHOLE_USAGE_LOG=1 or a path).

    Off by default, and it never leaves the machine - nothing is uploaded. It
    exists because the top failure mode of a tool like this is silent: the agent
    simply never calls it (or calls it and ignores the answer), and that question
    - "does my client actually route here, and does it hold up?" - cannot be
    answered without a local record. Argument VALUES are never written, only the
    tool name and the outcome. ``ok`` means "the call ran to completion without
    raising" — it is NOT "the tool reported success": a refused or failed call
    answers with an envelope rather than an exception, so what the tool itself
    called a failure is recorded in ``error`` (see ``_result_error``). A clean
    call is ``{"tool": ..., "ok": true, "ms": ...}`` with no ``error`` key; a call
    that completed but reported a failure carries both, so ``select(.error)``
    finds every one of them. Best-effort: never raises, never blocks startup.

    When the log lives under the dhole home (the ``=1`` form) that home and the
    log are tightened to 0700/0600 like the other state files. A path the user
    chose via the environment is left exactly as they set it up.
    """
    target = (env_registry.value_of("DHOLE_USAGE_LOG") or "").strip()
    if not target:
        return
    owned = target.lower() in ("1", "true", "yes", "on")
    if owned:
        target = str(paths.file("usage.jsonl"))
    try:
        parent = os.path.dirname(os.path.abspath(target))
        if parent:
            os.makedirs(parent, exist_ok=True)
        entry: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": name,
            "ok": bool(ok),
            "ms": round(float(duration_ms), 1),
        }
        if error:
            entry["error"] = redact_api_key(str(error)[:200])
        with open(target, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if owned:
            paths.harden_dir(parent)
            paths.harden_file(target)
    except Exception:
        pass
