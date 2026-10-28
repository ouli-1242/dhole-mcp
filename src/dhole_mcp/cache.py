"""带 TTL 的 SQLite 内容缓存。

按 URL+参数哈希存抓取到的内容。超过 TTL 的条目自动过期。

每个操作都开自己的 aiosqlite 连接（connect → query → close）。实测
（2026-10-24，evals/.runtime/measure_cache_hit*.py）：一次命中 p50 约 3.0ms、
一次未命中探测约 2.8ms，而同一条查询复用连接只要 0.21ms —— 连接/关闭占价格的
约 93%，而它会在每次调用的未命中探测上重复支付。一个按事件循环常驻的连接能把
它降到约 0.2ms（如果哪天真在意的话）；独立的内存 LRU 层已被实测否决
（见 docs/improvement-plan.md §4：它在一条已经替换掉 17ms–36s 实时抓取的路径上
省 3ms，代价是把 TTL/ctx/clear 语义复制出第二份，而这份 DB 是它们的唯一权威）。
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

# 缓存住在 dhole home（paths.py 是 dhole 写出的每个文件的唯一真源：
# ~/.dhole/cache.db、~/.dhole/models/ …）。
_CACHE_DIR = paths.cache_dir()
_DB_NAME = "cache.db"

DEFAULT_TTL = 3600  # 1 小时
MAX_CACHE_ENTRIES = 10000  # 硬上限：长期存活的 agent 不能让缓存 DB 无限增长

# 键版本，存在 SQLite 的 per-file PRAGMA user_version 槽里（不额外占一个状态
# 文件）。当 _cache_key 的**输入**发生变化、已写入的行不再适合作答时提升它；
# 下面的升级只删受影响的那一部分，而不是作废整个缓存 —— 对这个工具来说，有些
# 网络环境下缓存被清空是不可恢复的（见 paths.migrate_legacy_cache_dir）。
# v2：ctx 指纹加入了 PDF 口令（server._cache_context）。v3：fetch 行的信封加入了
# substance 判决（server._finalize_result）。在此之前写入的行根本没有 `substance`
# 键，回放路径于是只从 status + content **重算** `content_ok` —— 交出一个期3.4
# 闸门从未背书过的绿灯。本机缓存实测：134 行 fetch 里 132 行缺这颗判决，其中
# 128 行存着 content_ok=true，所以"下一次写入时自愈"会让整个既有缓存继续在没有
# 证据的情况下说话。只删 HTML 行（PDF / JSON / 图片行本来就没有 substance 判决，
# 重取它们买不到任何东西）。
_KEY_VERSION = 3

# 共享的 DB 路径缓存 —— 避免在每个操作上重跑 PRAGMA
_db_initialized: dict[Path, bool] = {}
_db_init_lock: asyncio.Lock | None = None
_db_init_lock_loop: asyncio.AbstractEventLoop | None = None


def _get_db_lock() -> asyncio.Lock:
    """惰性初始化 asyncio Lock —— **每个事件循环一个**。

    惰性是为了在没有运行中循环时也能导入本模块（测试期间）。按循环分是因为
    `asyncio.Lock` 绑定创建它的事件循环，而 `acquire()` 只在必须**排队**时才问
    那是哪个循环：无竞争的快速路径从不调用 `_get_loop()`。于是一个模块级单锁看
    起来完全正确，直到同一进程里的第二个循环来争它。

    实测（2026-10-09，搭建 D8 单飞时踩到）：两轮 `asyncio.run()`，每轮六个并发
    首次读取 —— 第一轮 `ok`，第二轮
    `RuntimeError: <asyncio.Lock …> is bound to a different event loop`。生产
    进程只有一个循环，所以那里永不触发；它落在嵌入方，以及任何在前一个测试创建
    过锁之后又跑并发缓存突发的测试上，那是一个制造 flake 的机器，而不是任何人能
    读懂的崩溃。

    重建不花什么：这把锁守的是一次 `mkdir` 加几条 DDL，每个数据库文件一次。
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
    """由抓取参数生成确定性的缓存键。

    ``source`` 把实时与 archive.org 条目分开，这样 TTL 内解封的页面不会被喂一份
    过期的存档快照（反过来也一样）。``scope`` 把 fetch 条目与搜索结果条目分开
    （search.py 在同一张表里存查询串 + 序列化参数）。``ctx`` 是请求上下文指纹
    （cookies / headers / user agent / 代理 / PDF 口令 / 影响内容形状的标志，
    由 ``server._cache_context`` 生成）。它存在是因为这台机器上的 DB 被所有会话
    共享：带凭据抓到的正文绝不能回放给一次没带凭据的请求，用 include_media=false
    抽出的页面也不能冒充 include_media 的答案。默认 "" 让普通请求（以及既有条目）
    保持与从前完全相同的键。
    """
    raw = f"{scope}|{url}|{extraction_type}|{css_selector or ''}|{pages or ''}|{source or 'live'}|{ctx}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


async def _ensure_db(cache_dir: Path | None = None) -> Path:
    """确保 DB 与表存在。返回 DB 路径。

    缓存初始化状态以避免多余的 PRAGMA 调用。WAL 日志模式带来更好的并发读写；
    busy timeout 优雅处理锁竞争；整个过程加锁，防止并发首次访问时的竞态。
    """
    d = cache_dir or _CACHE_DIR
    paths.ensure_private_dir(d)
    db_path = d / _DB_NAME

    # 快速路径：已经初始化，无需加锁
    if _db_initialized.get(db_path):
        return db_path

    async with _get_db_lock():
        # 拿到锁后复查（另一个任务可能已经初始化）
        if _db_initialized.get(db_path):
            return db_path

        async with aiosqlite.connect(db_path) as db:
            # WAL 模式：读不挡写，写不挡读。
            await db.execute("PRAGMA journal_mode=WAL")
            # DB 被其他连接锁住时最多等 5 秒。
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

            # v3.5.3 模式升级：加 content_type 与 total_size_bytes 两列。
            # 幂等 —— 已加过时 ALTER TABLE 会抛 "duplicate column"。
            # SQLite 复用同一错误类，aiosqlite 包一层；我们经 sqlite3 捕获。
            for ddl in (
                "ALTER TABLE cache ADD COLUMN content_type TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE cache ADD COLUMN total_size_bytes INTEGER NOT NULL DEFAULT 0",
                # v10：信封往返 metadata/links/quality_score/toc/page_type/
                # source/archived_at，让缓存命中能恢复它们（从前命中即丢失）。
                "ALTER TABLE cache ADD COLUMN envelope TEXT NOT NULL DEFAULT '{}'",
                # scope 把 fetch 条目与复用同一张表的搜索结果条目分开
                # （search.py 把查询存在 url 列里）。
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
    """缓存仍新鲜时返回缓存响应，否则 None。

    取存储 TTL 与调用方请求 TTL 中**较小**者。调用方想要更新的窗口时，这能防止
    交出过期缓存。

    ``ctx`` 必须与写入侧用的值一致（见 ``_cache_key``）：否则同一 URL 的带凭据
    与匿名抓取会共用同一个槽。
    """
    key = _cache_key(url, extraction_type, css_selector, pages, source, scope, ctx)
    db_path = await _ensure_db(cache_dir)

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        # 用 MIN(stored_ttl, requested_ttl)，让调用方可以要求更新鲜的数据
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
    """把一份响应存进缓存。

    v3.5.3+：content_type 与 total_size_bytes 经缓存往返，命中时 agent 仍能看到
    MIME 信息，而不是永远拿到空/0。
    v10：``envelope`` 往返 metadata/links/quality_score/toc/page_type/source/
    archived_at，让缓存命中恢复完整的研究级响应（从前这些字段在命中的一刻被静默
    丢弃）。
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
        # 给缓存封顶：超过 MAX_CACHE_ENTRIES 时按 fetched_at 淘汰最旧的行，
        # 一直降到上限的 90%。比每次插入单条淘汰更便宜，且摊薄成本（只在超限时跑）。
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
    """清掉所有过期条目。返回清除的行数。"""
    db_path = await _ensure_db(cache_dir)
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "DELETE FROM cache WHERE fetched_at + ttl <= ?", (time.time(),)
        )
        await db.commit()
        return cursor.rowcount


async def clear_all_cache(cache_dir: Path | None = None) -> int:
    """清空整个缓存。返回清除的行数。"""
    db_path = await _ensure_db(cache_dir)
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("DELETE FROM cache")
        await db.commit()
        return cursor.rowcount


async def reclaim_cache(
    cache_dir: Path | None = None, *, vacuum_over_bytes: int = 4 * 1024 * 1024
) -> dict:
    """清掉过期条目，并在文件够大时 VACUUM 把空间真正还给文件系统。

    ``clear_cache`` 只删行，SQLite 的页不会跟着缩 —— 实测 404 条撑到 32 MB
    之后，条目清零文件仍停在 32 MB。一个只读工具不该让磁盘只涨不落，所以
    启动时跑一次。

    VACUUM 独占整库，所以只在真删掉了东西、且文件超过 ``vacuum_over_bytes``
    时做；此时若有并发写者，失败是正常的 —— 吞掉并把它写进返回值，不抛。

    返回 ``{"purged", "before_bytes", "after_bytes", "error"}``。
    """
    db_path = await _ensure_db(cache_dir)
    try:
        before = db_path.stat().st_size
    except OSError:
        before = 0
    purged = 0
    error = ""
    try:
        # isolation_level=None：VACUUM 不能在事务里跑，而 aiosqlite 默认会
        # 为 DELETE 开一个隐式事务。
        async with aiosqlite.connect(db_path, isolation_level=None) as db:
            cursor = await db.execute(
                "DELETE FROM cache WHERE fetched_at + ttl <= ?", (time.time(),)
            )
            purged = max(0, cursor.rowcount or 0)
            if purged and before > vacuum_over_bytes:
                await db.execute("VACUUM")
    except Exception as e:
        error = str(e)[:200]
    try:
        after = db_path.stat().st_size
    except OSError:
        after = before
    return {"purged": purged, "before_bytes": before, "after_bytes": after, "error": error}


async def cache_stats(cache_dir: Path | None = None) -> dict:
    """缓存现在装着什么，供 `sys_state(show=["cache"])` 用。

    构造上只读：跑一条 SELECT，不跑 DELETE。一个靠清空来报告的状态工具，会让人
    在还想保持登录的会话里不敢调用它。

    `expired` 是一次普通清理会丢掉的行，`entries` 是一次清空会丢掉的行，所以调用方
    能分清"3 行，其中 3 行过期"和"3 行，全部新鲜" —— 那是"清理免费"与"清理要重抓"
    的区别。
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
