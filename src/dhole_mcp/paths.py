"""Runtime path layout — every file dhole persists lives under ONE directory.

Everything dhole writes is regenerable state, and it lives under exactly one
root: "what does this tool leave on my machine?" has to be answerable in one
directory, and an uninstall instruction that names two is a lie about one.
(``~/.dhole_mcp_cache/`` was the second root until 17.0 B2 - the migration that
folded it in is gone, and nothing reads that path any more. A pre-14.3 install
that still has it keeps it until the user deletes it.) This module is the single
source of truth for those paths:

    ~/.dhole/
      cache.db                 content cache (SQLite; + -wal/-shm sidecars)
      sessions.db              cookie jar for session_id (SQLite; values, not names)
      models/                  downloaded model files (neural reranker)
      circuit_breaker.json     engine cooldown state
      engine_stats.json        per-engine parse yield (who returned 0 usable results)
      usage.jsonl              opt-in local call log

Not in here, on purpose:

* the browser profile — a per-session ``tempfile.mkdtemp()`` directory that is
  removed on close (transient scratch, not state; a crash leaves it to the OS
  temp cleaner rather than to this directory forever);
* ``rapidocr``'s OCR models — bundled with that package, not downloaded here.

Stdlib only, and imported by cache/reranker/search/updater, so it must never
import a third-party dependency.
"""

from __future__ import annotations

import os
from pathlib import Path

_HOME_DIR_NAME = ".dhole"


def home() -> Path:
    """The one directory dhole owns: ``~/.dhole``, or ``DHOLE_HOME`` when set.

    Why the override exists: this directory holds every fetched page body in
    plaintext (cache.db), the search queries, and the reranker models. On a shared
    machine that is content the user may not want next to their other files, and
    the location used to be unmovable. ``DHOLE_HOME`` also lets a test or a
    sandbox point everything at a throwaway directory.

    (17.0 B2 deleted the pre-14.3 cache migration, so a custom home is now
    exactly what it says: nothing is folded in from ``~/.dhole_mcp_cache``.)
    """
    raw = (os.environ.get("DHOLE_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / _HOME_DIR_NAME


def ensure_private_dir(path: Path | str) -> Path:
    """mkdir -p + best-effort owner-only mode. Never raises.

    POSIX gets a real 0700. Windows largely ignores chmod (NTFS ACLs govern), so
    there this is a no-op and ``DHOLE_HOME`` is the actual lever — said out loud
    rather than pretending the bit was set.

    str callers exist (`sessions` / `identity` pass `os.path.dirname`), so the path
    is normalized here rather than at each caller. `except` is OSError-only on
    purpose: when this swallowed every exception, a `str.mkdir` AttributeError hid
    inside it and the store died later as sqlite's "unable to open database file".
    """
    path = Path(path)
    try:
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def harden_file(path: Path | str) -> None:
    """Best-effort 0600 on a state file we just wrote. Never raises.

    Applied after the atomic-replace so a temp file can't linger world-readable
    with page content in it.
    """
    try:
        os.chmod(str(path), 0o600)
    except Exception:
        pass


def harden_dir(path: Path | str) -> None:
    """Best-effort 0700 on a directory dhole owns. Never raises.

    Deliberately does NOT create the directory, unlike :func:`ensure_private_dir`:
    callers keep their own ``mkdir`` so a failure to create it still raises the
    way it always did, and this only tightens what already exists.
    """
    try:
        os.chmod(str(path), 0o700)
    except Exception:
        pass


def cache_dir() -> Path:
    """Directory holding the content cache DB (the dhole home itself)."""
    return home()


def db_path() -> Path:
    """The content cache SQLite file."""
    return home() / "cache.db"


def models_dir() -> Path:
    """Directory for downloaded model files (neural reranker)."""
    return home() / "models"


def file(name: str) -> Path:
    """A file directly inside the dhole home (``circuit_breaker.json``, ...)."""
    return home() / name


def profiles_dir() -> Path:
    """Parent of named persistent browser profiles (``~/.dhole/profiles/``).

    Each profile is a full Chrome user-data-dir: cookies, localStorage, login
    state. 17.0 没有任何调用方给它取名（按环境的 ``DHOLE_BROWSER_PROFILE`` 已
    注销，按次参数要等 B5 那颗「设一次的默认身份」工具回来才有人传），所以这个
    目录通常不存在。
    它一旦存在就是"keep me signed in"的产物，里面每一项都是凭据级：created 0700 (owner-only on POSIX; on Windows the
    profile ACL under the user's home directory is the real boundary).
    """
    return ensure_private_dir(home() / "profiles")


