"""SQLite-based content cache with TTL support.

Stores fetched content keyed by URL+params hash. Auto-expires entries past TTL.

Each operation opens its own aiosqlite connection (connect → query → close).
Measured (2026-10-24, evals/.runtime/measure_cache_hit*.py): a hit costs
~3.0ms p50 and a miss probe ~2.8ms, while the same query on a reused
connection costs 0.21ms — the connect/close is ~93% of the price, and it is
paid on every call's miss probe. A persistent per-loop connection would take
that to ~0.2ms if it ever matters; a separate in-memory LRU layer was measured
out (see docs/improvement-plan.md §4: it saves 3ms on a path that already
replaced a 17ms–36s live fetch, at the price of a second copy of the
TTL/ctx/clear semantics that this one DB is the sole authority for).
"""
import asyncio
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from dhole_mcp import paths

# Cache lives in the dhole home (paths.py is the single source of truth for
# every file dhole writes: ~/.dhole/cache.db, ~/.dhole/models/, ...).
_CACHE_DIR = paths.cache_dir()
_DB_NAME = "cache.db"

DEFAULT_TTL = 3600  # 1 hour
MAX_CACHE_ENTRIES = 10000  # hard cap so a long-lived agent's cache DB can't grow unbounded

# Key version, stored in SQLite's per-file PRAGMA user_version slot (no extra
# state file). Bump it when _cache_key's *inputs* change in a way that makes
# already-written rows unsafe to serve; the upgrade below then deletes only the
# affected subset rather than invalidating the whole cache, because for this tool
# a wiped cache can be unrecoverable on some networks (see
# paths.migrate_legacy_cache_dir). v2: the ctx fingerprint gained the PDF
# password (server._cache_context). v3: a fetch row's envelope gained the
# substance verdict (server._finalize_result). Rows written before it carry no
# `substance` key at all, and the replay path then re-derives `content_ok` from
# status + content alone - which hands out a green light the 期3.4 gate never
# backed. Measured on this machine's own cache: 132 of 134 fetch rows lacked the
# verdict and 128 of those stored content_ok=true, so "self-heals on the next
# write" would have left the whole existing cache speaking without evidence.
# Only HTML rows are dropped (PDF / JSON / image rows legitimately have no
# substance verdict, and refetching them buys nothing).
_KEY_VERSION = 3

# Shared DB path cache — avoids re-running PRAGMA on every operation
_db_initialized: dict[Path, bool] = {}
_db_init_lock: asyncio.Lock | None = None
_db_init_lock_loop: asyncio.AbstractEventLoop | None = None


def _get_db_lock() -> asyncio.Lock:
    """Lazy-init the asyncio Lock — **one per event loop**.

    Lazy so importing this module outside a running loop is fine (during testing).
    Per-loop because an `asyncio.Lock` is bound to the loop that created it, and
    `acquire()` only asks which loop that was when it has to **queue**: the
    uncontended fast path never calls `_get_loop()`. So a single module-level lock
    looks correct right up until the second loop in one process contends it.

    Measured (2026-10-09, while building the D8 single-flight and tripping over
    this): two rounds of `asyncio.run()`, six concurrent first-time reads each —
    round 1 `ok`, round 2 `RuntimeError: <asyncio.Lock …> is bound to a different
    event loop`. The process has one loop in production, so this never fires
    there; it lands on embedders and on any test that runs a concurrent cache
    burst after an earlier test created the lock, which is a flake generator
    rather than a crash anybody can read.

    Rebuilding costs nothing: this lock guards one `mkdir` plus a handful of DDL
    statements, once per database file.
    """
    global _db_init_lock, _db_init_lock_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:                      # 不在循环里：交给调用方自己决定
        loop = None
    if _db_init_lock is None or _db_init_lock_loop is not loop:
        _db_init_lock = asyncio.Lock()
        _db_init_lock_loop = loop
    return _db_init_lock


def _cache_key(url: str, extraction_type: str, css_selector: str | None = None,
               pages: str | None = None, source: str = "live", scope: str = "fetch",
               ctx: str = "") -> str:
    """Deterministic cache key from fetch params.

    ``source`` separates live vs archive.org entries so a page that gets unblocked
    within TTL isn't served a stale archive snapshot (and vice versa).
    ``scope`` separates fetch entries from search-result entries (search.py
    stores query strings + serialized params in the same table).
    ``ctx`` is a request-context fingerprint (cookies / headers / user agent /
    proxy / PDF password / content-shaping flags, built by
    ``server._cache_context``). It exists
    because the DB is shared by every session on the machine: a body fetched WITH
    credentials must never be replayed to a request that carried none, and a page
    extracted with include_media=false must not masquerade as the include_media
    answer. Default "" keeps plain requests (and pre-existing entries) on exactly
    the same key as before.
    """
    raw = f"{scope}|{url}|{extraction_type}|{css_selector or ''}|{pages or ''}|{source or 'live'}|{ctx}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


async def _ensure_db(cache_dir: Path | None = None) -> Path:
    """Ensure the DB and table exist. Returns DB path.

    Caches initialization status to avoid redundant PRAGMA calls.
    WAL journal mode for better concurrent read/write performance.
    Busy timeout to handle lock contention gracefully.
    Lock-protected to prevent races during concurrent first-access.
    """
    d = cache_dir or _CACHE_DIR
    paths.ensure_private_dir(d)
    db_path = d / _DB_NAME

    # Fast path: already initialized, no lock needed
    if _db_initialized.get(db_path):
        return db_path

    async with _get_db_lock():
        # Re-check after acquiring lock (another task may have initialized)
        if _db_initialized.get(db_path):
            return db_path

        async with aiosqlite.connect(db_path) as db:
            # WAL mode: readers don't block writers, writers don't block readers.
            await db.execute("PRAGMA journal_mode=WAL")
            # Wait up to 5s if DB is locked by another connection.
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    extraction_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status INTEGER NOT NULL,
                    fetched_at REAL NOT NULL,
                    ttl INTEGER NOT NULL DEFAULT 3600
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_fetched_at ON cache(fetched_at)")

            # v3.5.3 schema upgrade: add content_type and total_size_bytes columns.
            # Idempotent — ALTER TABLE raises "duplicate column" if already added.
            # SQLite reuses the same error class as aiosqlite wraps; we catch via sqlite3.
            for ddl in (
                "ALTER TABLE cache ADD COLUMN content_type TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE cache ADD COLUMN total_size_bytes INTEGER NOT NULL DEFAULT 0",
                # v10: envelope round-trips metadata/links/quality_score/toc/page_type/
                # source/archived_at so cache hits restore them (previously lost on hit).
                "ALTER TABLE cache ADD COLUMN envelope TEXT NOT NULL DEFAULT '{}'",
                # scope separates fetch entries from search-result entries that
                # reuse the same table (search.py stores queries in the url column).
                "ALTER TABLE cache ADD COLUMN scope TEXT NOT NULL DEFAULT 'fetch'",
            ):
                try:
                    await db.execute(ddl)
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc):
                        raise

            # 键版本升级：ctx 指纹此前不含 PDF 口令，所以用 password= 解出来的正文
            # 会和同一 URL 的匿名行撞同一个键 —— 之后的匿名请求能直接复读解密结果。
            # 只清唯一可能受影响的子集（PDF 行），不作废整个缓存。
            # 判据用 content_type/url 而不是 extraction_type：后者是调用方传进来的
            # 取值（默认 markdown），PDF 走的却是响应里的 MIME，两者不对应。
            # user_version 不能参数绑定，这里拼的是模块级整数常量，非外部可控。
            (ver,) = await (await db.execute("PRAGMA user_version")).fetchone()
            if (ver or 0) < _KEY_VERSION:
                if (ver or 0) < 2:
                    await db.execute(
                        "DELETE FROM cache WHERE scope = 'fetch' AND ("
                        "lower(url) LIKE '%.pdf%' OR lower(content_type) LIKE '%pdf%')"
                    )
                if (ver or 0) < 3:
                    # v3：没有实质判决的 HTML 行一律不作答。留着的后果不是"少一个字段"，
                    # 是回放路径从 status + content **重算** 出一个 `content_ok=true`，
                    # 而那一发从没被判过（本机实测：134 行 fetch 里 132 行缺这颗读数，
                    # 其中 128 行存的是 true）。非 HTML 不删 —— PDF / JSON / 图片本来
                    # 就没有 substance 这一说，重取一次买不到任何东西。判据用 LIKE 而不
                    # 是 json_extract：不赌这台机器的 SQLite 编译带 JSON1。
                    await db.execute(
                        "DELETE FROM cache WHERE scope = 'fetch' AND "
                        "COALESCE(envelope, '') NOT LIKE '%substance%' AND ("
                        "lower(content_type) LIKE '%html%' OR lower(url) LIKE '%.html%')"
                    )
                await db.execute(f"PRAGMA user_version={_KEY_VERSION}")

            await db.commit()

        _db_initialized[db_path] = True
        return db_path


# F-B-J1 第 (1) 步：命中读数。cache_ref（复读句柄）要不要上 wire，判决依据是
# 「频率证据读不到」—— 先给计数，有一段读数再走棘轮。进程级计数，与 census
# 同生命周期：重启归零不是缺陷，census 报的本来就是本进程的状态。
_hit_counters = {"hits": 0, "misses": 0}


async def get_cached(
    url: str,
    extraction_type: str,
    css_selector: str | None = None,
    ttl: int = DEFAULT_TTL,
    cache_dir: Path | None = None,
    pages: str | None = None,
    source: str = "live",
    scope: str = "fetch",
    ctx: str = "",
) -> dict | None:
    """Return cached response if fresh, else None.

    Uses the *lesser* of the stored TTL and the caller-requested TTL.
    This prevents serving stale cache when caller wants a fresher window.

    ``ctx`` must match the value used on the write side (see ``_cache_key``):
    without it, credentialed and anonymous fetches of the same URL share a slot.
    """
    key = _cache_key(url, extraction_type, css_selector, pages, source, scope, ctx)
    db_path = await _ensure_db(cache_dir)

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        # Use MIN(stored_ttl, requested_ttl) so caller can request fresher data
        cursor = await db.execute(
            "SELECT * FROM cache WHERE key = ? AND fetched_at + MIN(ttl, ?) > ?",
            (key, ttl, time.time()),
        )
        row = await cursor.fetchone()
        if row is None:
            _hit_counters["misses"] += 1
            return None
        _hit_counters["hits"] += 1
        env = row["envelope"] if "envelope" in row.keys() else "{}"
        return {
            "status": row["status"],
            "content": json.loads(row["content"]),
            "url": row["url"],
            "content_type": row["content_type"],
            "total_size_bytes": row["total_size_bytes"],
            # 存的时候就有这一列（`fetched_at REAL NOT NULL`，建表语句里就是它做 TTL
            # 判定的），读的时候却把它丢了 —— 于是"这份正文是什么时候抓的"只剩调用方
            # 自己那一刻的时间，一次 400 秒前的缓存回放与一次刚抓完的回执在
            # `fetched_at` / `content_age_days` / `is_stale` 三个字段上完全同形。
            "fetched_at": row["fetched_at"],
            "envelope": json.loads(env) if env else {},
        }


def fetched_at_iso(cached: dict | None) -> str:
    """把 `get_cached` 那一条的抓取时刻翻成 ISO-8601（信封上 `fetched_at` 的形状）。

    存的是 epoch float（SQLite 的 REAL，TTL 判定就用的那一列），信封要的是带时区的串
    —— 翻译只在这一处发生。两个缓存命中的重建点各自 `fromtimestamp` 一遍，就会一个
    带 `+00:00` 一个不带，而那正是"同一个事实两个作者"的下一种修法。
    """
    raw = (cached or {}).get("fetched_at")
    if not raw:
        return ""
    try:
        return datetime.fromtimestamp(float(raw), timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


async def set_cached(
    url: str,
    extraction_type: str,
    content: list[str],
    status: int,
    css_selector: str | None = None,
    ttl: int = DEFAULT_TTL,
    cache_dir: Path | None = None,
    content_type: str = "",
    total_size_bytes: int = 0,
    pages: str | None = None,
    source: str = "live",
    envelope: dict | None = None,
    scope: str = "fetch",
    ctx: str = "",
) -> None:
    """Store a response in cache.

    v3.5.3+: content_type and total_size_bytes round-trip through cache so
    agents preserve MIME info on hits instead of always seeing empty/0.
    v10: ``envelope`` round-trips metadata/links/quality_score/toc/page_type/
    source/archived_at so cache hits restore the full research-grade response
    (previously these fields were silently dropped on cache hits).
    """
    key = _cache_key(url, extraction_type, css_selector, pages, source, scope, ctx)
    db_path = await _ensure_db(cache_dir)
    env_json = json.dumps(envelope) if envelope else "{}"

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT OR REPLACE INTO cache
               (key, url, extraction_type, content, status, fetched_at, ttl,
                content_type, total_size_bytes, envelope, scope)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (key, url, extraction_type, json.dumps(content), status,
             time.time(), ttl, content_type, total_size_bytes, env_json, scope),
        )
        # Bound the cache: if over MAX_CACHE_ENTRIES, evict the oldest rows by
        # fetched_at down to 90% of the cap. Cheaper than per-insert single
        # evictions and amortizes the cost (only runs when the cap is exceeded).
        count_cursor = await db.execute("SELECT COUNT(*) FROM cache")
        (count,) = await count_cursor.fetchone()
        if count > MAX_CACHE_ENTRIES:
            excess = count - int(MAX_CACHE_ENTRIES * 0.9)
            await db.execute(
                "DELETE FROM cache WHERE key IN "
                "(SELECT key FROM cache ORDER BY fetched_at ASC LIMIT ?)",
                (excess,),
            )
        await db.commit()


async def clear_cache(cache_dir: Path | None = None) -> int:
    """Clear all expired entries. Returns count of purged rows."""
    db_path = await _ensure_db(cache_dir)
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "DELETE FROM cache WHERE fetched_at + ttl <= ?", (time.time(),)
        )
        await db.commit()
        return cursor.rowcount


async def clear_all_cache(cache_dir: Path | None = None) -> int:
    """Nuke the entire cache. Returns count of purged rows."""
    db_path = await _ensure_db(cache_dir)
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("DELETE FROM cache")
        await db.commit()
        return cursor.rowcount


async def cache_stats(cache_dir: Path | None = None) -> dict:
    """What the cache holds right now, for `sys_state(show=["cache"])`.

    Read-only by construction: it runs one SELECT and no DELETE. A state tool
    that reports by wiping would make the census un-callable from a session you
    still want logged in.

    `expired` is what a plain sweep would drop and `entries` is what a wipe
    drops, so the caller can tell "3 rows, 3 of them stale" from "3 rows, all
    of them fresh" — the difference between clearing being free and being a
    re-fetch.
    """
    db_path = await _ensure_db(cache_dir)
    async with aiosqlite.connect(db_path) as db:
        row = await (await db.execute(
            "SELECT COUNT(*) AS entries, "
            "SUM(CASE WHEN fetched_at + ttl <= ? THEN 1 ELSE 0 END) AS expired, "
            "MIN(fetched_at) AS oldest FROM cache",
            (time.time(),),
        )).fetchone()
    entries, expired, oldest = (row or (0, 0, None))
    return {
        "entries": int(entries or 0),
        "expired": int(expired or 0),
        "oldest_age_s": int(time.time() - oldest) if oldest else 0,
        "cap": MAX_CACHE_ENTRIES,
        # F-B-J1：本进程的命中读数（cache_ref 棘轮的判据来源）。说明句放在
        # sys_state 的 cache 字段描述里，这里只给数。
        "hits": _hit_counters["hits"],
        "misses": _hit_counters["misses"],
    }
