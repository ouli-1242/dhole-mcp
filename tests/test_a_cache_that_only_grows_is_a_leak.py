"""缓存删行不缩文件 = 磁盘只涨不落。

``clear_cache`` 早就有，但全仓没有调用点，过期行一直躺到 10000 条上限才被
按年龄挤掉，而 SQLite 的页在 VACUUM 之前不会还给文件系统。这里钉住
``reclaim_cache`` 的两件事：过期行真的没了，活行一条不动。
"""


from __future__ import annotations


import sqlite3
import time

import pytest

from dhole_mcp import cache

pytestmark = pytest.mark.smoke


@pytest.mark.asyncio
async def test_expired_rows_are_purged_and_live_rows_survive(tmp_path):
    await cache.set_cached("https://a.example", "markdown", ["alive"], 200,
                           ttl=3600, cache_dir=tmp_path)
    await cache.set_cached("https://b.example", "markdown", ["dead"], 200,
                           ttl=1, cache_dir=tmp_path)

    db_path = await cache._ensure_db(tmp_path)
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE cache SET fetched_at = ? WHERE url = ?",
                   (time.time() - 600, "https://b.example"))

    outcome = await cache.reclaim_cache(tmp_path, vacuum_over_bytes=0)

    assert outcome["purged"] == 1, outcome
    assert outcome["error"] == ""
    assert outcome["after_bytes"] <= outcome["before_bytes"]

    with sqlite3.connect(db_path) as db:
        urls = [row[0] for row in db.execute("SELECT url FROM cache")]
    assert urls == ["https://a.example"], "活条目被误删"


@pytest.mark.asyncio
async def test_an_empty_purge_does_not_rewrite_the_file(tmp_path):
    """没有过期行时不 VACUUM —— 每次启动重写一遍大库是纯浪费。"""
    await cache.set_cached("https://a.example", "markdown", ["alive"], 200,
                           ttl=3600, cache_dir=tmp_path)

    outcome = await cache.reclaim_cache(tmp_path, vacuum_over_bytes=0)

    assert outcome["purged"] == 0
    assert outcome["after_bytes"] == outcome["before_bytes"]


@pytest.mark.asyncio
async def test_a_locked_database_reports_instead_of_raising(tmp_path, monkeypatch):
    await cache.set_cached("https://a.example", "markdown", ["alive"], 200,
                           ttl=3600, cache_dir=tmp_path)

    class _Boom(Exception):
        pass

    def _explode(*_a, **_kw):
        raise _Boom("database is locked")

    monkeypatch.setattr(cache.aiosqlite, "connect", _explode)
    outcome = await cache.reclaim_cache(tmp_path)

    assert "database is locked" in outcome["error"], "锁库要写进返回值，而不是抛出"
