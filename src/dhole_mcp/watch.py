"""A persistent watchlist for ``web_read(extraction_type='check')``.

The MCP tool answers "did this page change" one URL at a time. An agent can call
it repeatedly, but a person who wants a nightly/every-30-minutes signal needs a
list that survives restarts and a command that exits 0/1/2 for cron. This module
is that store and runner; the fetching itself is still the single check path in
``server._check_read`` (called through ``web_read``), so a watch inherits the
same baseline, ETag handling, diff window and robots/SSRF gates.

State is one owner-only JSON file under ``~/.dhole`` (``DHOLE_HOME`` moves it).
It holds URLs and verdicts, never page bodies or credentials. The first run of a
URL records its baseline (``verdict=baseline``) and is not a change; the second
and later runs can answer changed/unchanged. Degrading honestly matters more
than producing an alert: a 404 or a blocked fetch is an error row, never
"unchanged".
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Optional
from urllib.parse import urlsplit

from dhole_mcp import paths


class WatchStateError(Exception):
    """The watchlist file exists but cannot be read as a watchlist."""


class WatchBusy(Exception):
    """Another ``watch run`` holds the run lock."""


def state_path(path: Optional[Path | str] = None) -> Path:
    return Path(path) if path is not None else paths.file("watchlist.json")


def _empty() -> dict:
    return {"version": 1, "watches": []}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state(path: Optional[Path | str] = None) -> dict:
    p = state_path(path)
    if not p.exists():
        return _empty()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WatchStateError(
            f"{p.name} is not readable JSON ({exc.__class__.__name__}); fix or "
            "remove it - dhole will not guess what was being watched") from exc
    if not isinstance(data, dict) or not isinstance(data.get("watches"), list):
        raise WatchStateError(
            f"{p.name} does not have the shape dhole writes: a JSON object with "
            "a 'watches' list")
    for row in data["watches"]:
        if not isinstance(row, dict) or not isinstance(row.get("url"), str) \
                or not row["url"]:
            raise WatchStateError(
                f"{p.name} has a watch row without a URL; fix or remove it by hand")
    data.setdefault("version", 1)
    return data


def _save(state: dict, path: Optional[Path | str] = None) -> Path:
    p = state_path(path)
    paths.ensure_private_dir(p.parent)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
    paths.harden_file(p)
    return p


def _clean_url(url: str) -> str:
    from dhole_mcp.security import SecurityError, validate_url

    try:
        # Syntax/scheme checks only: whether this machine may FETCH a private
        # target is decided per call by the same SSRF gate every read uses, and
        # a blocked target must show up as an error row in `run`, not as a
        # different add-time rule that disagrees with it.
        url = validate_url(url, allow_internal=True)
    except SecurityError as exc:
        raise ValueError(f"not a URL dhole can watch: {exc}") from exc
    parts = urlsplit(url)
    if parts.username or parts.password:
        raise ValueError(
            "not a URL dhole can watch: it carries user:password@ credentials, "
            "and the watchlist is a plaintext local file. Store credentials with "
            "cred_defaults and watch the bare URL")
    # A fragment never reaches the origin and would only fork the cache baseline.
    return url.split("#", 1)[0]


def add(url: str, label: str = "", path: Optional[Path | str] = None) -> dict:
    url = _clean_url(url)
    state = load_state(path)
    row = next((w for w in state["watches"] if w["url"] == url), None)
    if row is None:
        row = {
            "url": url,
            "label": label.strip(),
            "added_at": _now(),
            "checks": 0,
            "last_checked_at": "",
            "last_verdict": "",
            "last_status": 0,
            "last_changed_at": "",
            "last_error": "",
        }
        state["watches"].append(row)
        created = True
    else:
        row["label"] = label.strip() or row.get("label", "")
        created = False
    _save(state, path)
    return {"created": created, "watch": row}


def remove(url: str, path: Optional[Path | str] = None) -> bool:
    state = load_state(path)
    before = len(state["watches"])
    state["watches"] = [w for w in state["watches"] if w["url"] != url]
    if len(state["watches"]) == before:
        return False
    _save(state, path)
    return True


def _verdict(row: dict) -> str:
    if row.get("changed") is True:
        return "changed"
    if row.get("changed") is False:
        return "unchanged"
    status = int(row.get("status") or 0)
    if row.get("error") or status == 0 or status >= 400:
        return "error"
    if 200 <= status < 400:
        return "baseline"
    return "error"


def _row_error(row: dict) -> str:
    if row.get("error"):
        return str(row["error"])
    status = int(row.get("status") or 0)
    return f"HTTP {status} without a changed/unchanged verdict"


async def run(checker: Callable[[str], Awaitable[dict]], *,
              path: Optional[Path | str] = None,
              only: Optional[Iterable[str]] = None,
              on_row: Optional[Callable[[dict], None]] = None) -> dict:
    """Check every watched URL once, persist each verdict, return a receipt.

    ``checker(url)`` must return the single-URL check dict (``changed``,
    ``status``, optional ``error``/``note``). Rows are saved after each URL, so
    an interrupted run keeps the verdicts it already earned. A URL removed while
    the run is in flight is not written back.
    """
    state = load_state(path)
    watched = {w["url"]: w for w in state["watches"]}
    wanted = [u for u in (only if only is not None else watched) if u in watched]
    unknown = [u for u in (only or []) if u not in watched]
    rows: list[dict] = []
    for url in wanted:
        try:
            raw = await checker(url)
        except Exception as exc:  # one dead URL does not end the sweep
            raw = {"changed": None, "status": 0,
                   "error": f"{exc.__class__.__name__}: {exc}"}
        verdict = _verdict(raw)
        checked_at = _now()
        row = {
            "url": url,
            "label": watched[url].get("label", ""),
            "verdict": verdict,
            "status": int(raw.get("status") or 0),
            "changed": raw.get("changed"),
            "note": raw.get("note", ""),
            "error": _row_error(raw) if verdict == "error" else "",
            "checked_at": checked_at,
            "diff_window": raw.get("diff_window", ""),
        }
        rows.append(row)
        # Reload before writing: `watch add`/`remove` in another process must
        # survive a long sweep. A removed URL is not resurrected.
        fresh = load_state(path)
        target = next((w for w in fresh["watches"] if w["url"] == url), None)
        if target is not None:
            target["checks"] = int(target.get("checks") or 0) + 1
            target["last_checked_at"] = checked_at
            target["last_verdict"] = verdict
            target["last_status"] = row["status"]
            target["last_error"] = row["error"]
            if verdict == "changed":
                target["last_changed_at"] = checked_at
            _save(fresh, path)
        if on_row is not None:
            on_row(row)
    changed = [r["url"] for r in rows if r["verdict"] == "changed"]
    unchanged = [r["url"] for r in rows if r["verdict"] == "unchanged"]
    baseline = [r["url"] for r in rows if r["verdict"] == "baseline"]
    errors = [{"url": r["url"], "error": r["error"]} for r in rows
              if r["verdict"] == "error"]
    return {
        "checked": len(rows),
        "changed": changed,
        "unchanged": unchanged,
        "baseline": baseline,
        "errors": errors,
        "unknown": unknown,
        "rows": rows,
        "exit_code": 2 if errors else 1 if changed else 0,
    }


class _Lock:
    def __init__(self, path: Optional[Path | str] = None):
        p = state_path(path)
        self.path = p.with_name(p.name + ".lock")
        self.fd: Optional[int] = None

    def __enter__(self):
        paths.ensure_private_dir(self.path.parent)
        try:
            self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                              0o600)
        except FileExistsError:
            try:
                age = time.time() - self.path.stat().st_mtime
            except OSError:
                age = 0
            if age < 6 * 3600:
                raise WatchBusy(
                    f"another `dhole watch run` is active ({self.path.name}); "
                    "if it crashed, remove that file and retry")
            try:
                self.path.unlink()
            except OSError:
                pass
            self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                              0o600)
        os.write(self.fd, f"{os.getpid()} {_now()}\n".encode())
        os.close(self.fd)
        self.fd = None
        return self

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except OSError:
            pass
        return False


async def run_locked(checker: Callable[[str], Awaitable[dict]], *,
                     path: Optional[Path | str] = None,
                     only: Optional[Iterable[str]] = None,
                     on_row: Optional[Callable[[dict], None]] = None) -> dict:
    with _Lock(path):
        return await run(checker, path=path, only=only, on_row=on_row)
