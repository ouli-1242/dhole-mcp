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
      engine_stats.json        per-engine parse yield (who returned 0 usable results)
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
    """The one directory dhole owns: ``~/.dhole``, or ``DHOLE_HOME`` when set.

    Why the override exists: this directory holds every fetched page body in
    plaintext (cache.db), the search queries, and the reranker models. On a shared
    machine that is content the user may not want next to their other files, and
    the location used to be unmovable. ``DHOLE_HOME`` also lets a test or a
    sandbox point everything at a throwaway directory.

    The pre-14.3 migration below targets this same function, so a custom home
    receives the migrated cache instead of a fresh ``~/.dhole``.
    """
    raw = (os.environ.get("DHOLE_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / _HOME_DIR_NAME


def ensure_private_dir(path: Path) -> Path:
    """mkdir -p + best-effort owner-only mode. Never raises.

    POSIX gets a real 0700. Windows largely ignores chmod (NTFS ACLs govern), so
    there this is a no-op and ``DHOLE_HOME`` is the actual lever — said out loud
    rather than pretending the bit was set.
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    except Exception:
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
    rename_legacy_model_dirs()
    _migrate_legacy_cache_dir_impl()
    # Also after the move: a legacy root can itself contain a pre-registry model
    # dir name (msmarco-minilm-l6-v2), which only exists post-move.
    rename_legacy_model_dirs()


def rename_legacy_model_dirs() -> None:
    """Rename pre-registry model dirs to their registry names.

    ``models/msmarco-minilm-l6-v2`` -> ``models/ms-marco`` (14.4 registry). Same
    bytes, new key — renaming avoids re-downloading ~90MB just because the
    directory was named after the repo instead of the model key.

    A plain rename when the destination is missing; if the destination already
    exists, only the MISSING children are merged in (never overwritten) and the
    emptied legacy dir is removed, so no stray duplicate directory survives.
    """
    legacy_map = {"msmarco-minilm-l6-v2": "ms-marco"}
    try:
        base = models_dir()
        if not base.is_dir():
            return
        for old, new in legacy_map.items():
            src, dst = base / old, base / new
            if not src.is_dir():
                continue
            if not dst.exists():
                try:
                    os.replace(src, dst)
                    logger.info("renamed model dir %s -> %s", src, dst)
                    continue
                except OSError:
                    pass
            dst.mkdir(parents=True, exist_ok=True)
            _merge_tree(src, dst)
    except Exception:
        pass


def _migrate_legacy_cache_dir_impl() -> None:
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
