"""Runtime path layout — every file dhole persists lives under ONE directory.

Everything dhole writes is regenerable state, and it used to be split across two
roots (``~/.dhole/`` for state and ``~/.dhole_mcp_cache/`` for the content cache
and the reranker model). That made "what does this tool leave on my machine?" a
two-directory answer and the uninstall instructions wrong. This module is the
single source of truth for those paths:

    ~/.dhole/
      cache.db                 content cache (SQLite; + -wal/-shm sidecars)
      models/                  downloaded model files (neural reranker)
      circuit_breaker.json     engine cooldown state
      search_feedback.json     opt-in implicit domain preference
      usage.jsonl              opt-in local call log
      last_version             last version offered by the updater
      repair.py                self-heal script (stdlib-only, survives a broken install)
      search_proxies.json      user config (read-only for dhole)

Not in here, on purpose:

* the browser profile — a per-session ``tempfile.mkdtemp()`` directory that is
  removed on close (transient scratch, not state; a crash leaves it to the OS
  temp cleaner rather than to this directory forever);
* ``rapidocr``'s OCR models — bundled with that package, not downloaded here.

Stdlib only, and imported by cache/reranker/search/updater, so it must never
import anything heavier than ``os``/``pathlib``.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger("dhole-mcp.paths")

_HOME_DIR_NAME = ".dhole"
# 14.3 之前缓存与模型在另一个根目录下；迁移逻辑就在本模块（见下）。
_LEGACY_CACHE_DIR_NAME = ".dhole_mcp_cache"


def home() -> Path:
    """``~/.dhole`` — the one directory dhole owns."""
    return Path.home() / _HOME_DIR_NAME


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


_legacy_migrated = False


def _merge_tree(src: Path, dst: Path) -> None:
    """Move the contents of ``src`` into ``dst`` without overwriting.

    Directories that do not exist at the destination are moved whole (one
    rename); the rest are merged recursively. Best-effort throughout.
    """
    for child in src.iterdir():
        target = dst / child.name
        if child.is_dir():
            if not target.exists():
                try:
                    os.replace(child, target)
                    continue
                except OSError:
                    target.mkdir(parents=True, exist_ok=True)
            _merge_tree(child, target)
        elif not target.exists():
            try:
                shutil.move(str(child), str(target))
            except Exception:
                pass
    # Remove the directories this call emptied, so the legacy root can be
    # reclaimed instead of surviving as a chain of empty folders.
    try:
        for child in src.iterdir():
            if child.is_dir() and not any(child.iterdir()):
                child.rmdir()
        if not any(src.iterdir()):
            src.rmdir()
    except OSError:
        pass


def migrate_legacy_cache_dir() -> None:
    """Move a pre-14.3 ``~/.dhole_mcp_cache`` under ``~/.dhole``.

    Old: ``~/.dhole_mcp_cache/cache.db``  + ``~/.dhole_mcp_cache/models/**``
    New: ``~/.dhole/cache.db``            + ``~/.dhole/models/**``

    Why bother instead of letting the cache rebuild: the reranker model is ~90MB
    and on some networks (CN hosts-file blocks, proxies) it cannot be re-fetched
    at all, so silently starting from an empty directory would quietly downgrade
    search ranking forever.

    Idempotent and best-effort: only moves entries that are MISSING at the
    destination, never overwrites, never deletes a non-empty legacy directory,
    never raises. A partial/failed move costs at most a re-download, never data
    that existed only in the new location. Called on first cache access and on
    reranker lookup.
    """
    global _legacy_migrated
    if _legacy_migrated:
        return
    _legacy_migrated = True
    try:
        legacy = Path.home() / _LEGACY_CACHE_DIR_NAME
        if not legacy.is_dir():
            return
        dest = home()
        dest.mkdir(parents=True, exist_ok=True)

        # SQLite DB + its WAL sidecars (moving the DB without them can drop the
        # most recent writes).
        for name in ("cache.db", "cache.db-wal", "cache.db-shm"):
            src, dst = legacy / name, dest / name
            if src.exists() and not dst.exists():
                try:
                    os.replace(src, dst)
                    logger.info("moved %s -> %s", src, dst)
                except OSError:
                    # Cross-device or locked (a running server holds the DB):
                    # copy is not worth it for a regenerable cache — skip.
                    pass

        # Model tree. One recursive merge handles both cases: an empty
        # destination (fast path: move the whole tree in one rename) and a
        # partially populated one (move only what is missing, level by level).
        legacy_models = legacy / "models"
        if legacy_models.is_dir():
            dest_models = models_dir()
            if dest_models.exists() and not any(dest_models.iterdir()):
                try:
                    dest_models.rmdir()
                    os.replace(legacy_models, dest_models)
                    logger.info("moved %s -> %s", legacy_models, dest_models)
                except OSError:
                    dest_models.mkdir(parents=True, exist_ok=True)
                    _merge_tree(legacy_models, dest_models)
            else:
                dest_models.mkdir(parents=True, exist_ok=True)
                _merge_tree(legacy_models, dest_models)

        # Drop the legacy directory only when it is empty — never remove
        # anything the user may still want.
        try:
            if not any(legacy.iterdir()):
                legacy.rmdir()
        except OSError:
            pass
    except Exception:
        pass
