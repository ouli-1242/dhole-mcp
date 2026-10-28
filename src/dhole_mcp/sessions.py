"""按会话的 cookie 罐（v16.0，gap G7）。

在此以前，cookie 只活一次调用：``options.cookies`` 构造一个 ``Cookie`` 头，调用
结束，站点应答的 ``Set-Cookie`` 被从响应里读出来然后丢掉。任何需要登录的事情
（表单 POST 再取后面的页面、一对 CSRF、同意横幅）都做不到：第二次调用从零开始。
本模块就是缺的那个存储 —— host 给我们的 cookie 按调用方选定的 ``session_id``
保存，下一次调用原样送回那个 host。

范围，以及刻意**不**在这里的东西：

* **按 host 而非按 domain。** ``Response.cookies`` 只带名字和值；一次抓取结束时
  ``Domain=`` / ``Path=`` / ``Secure`` 属性已经没了（fetcher.py 从客户端 cookie
  列表构造那个 dict）。重新推导意味着逐跳解析原始 ``Set-Cookie`` 头，而一个比
  设置 cookie 的确切 host 更宽的罐，就是把 cookie 发给一个从未索要它的子域。所以
  ``example.com`` 设的 cookie 只送回 ``example.com``。
* **值永不离开罐、流向模型。** 响应只报该 host 的 cookie **名字**
  （``session_cookie_names``），从不报值：会话 cookie 是凭据，而工具输出会落进
  agent 的 transcript —— 还会落进 SQLite 内容缓存，也可能落进日志。需要知道
  "我的登录还活着吗？"的 agent，由名字列表回答。
* **在固定 :data:`COOKIE_TTL_S` 后过期**（24 小时，17.0 写死）。每次
  ``Set-Cookie`` 写入一次，发送时不刷新，所以被遗忘的会话不会永久累积凭据。
  ``sys_state``（工具）可以丢掉整个罐、一个具名 id 或每一个 —— 包括磁盘上那一份：
  内存模式的进程列得出、也抹得掉另一个进程留下的持久会话（删行不需要解密），
  只是取不到那些 cookie（见 :func:`sessions_everywhere`）。

存储**默认在内存里**（SQLite ``:memory:`` —— 最安全的文件是不存在的那个文件）：
本进程的罐与进程同寿，重启从零开始。``DHOLE_PERSIST_CREDENTIALS=1`` 把罐移到
``~/.dhole/sessions.db``（目录/文件 0600，见 paths.py），每个 cookie **值**在
落盘前都用 Windows DPAPI 加密（:mod:`dhole_mcp.secretbox`）—— 密钥绑定 Windows
用户账户，那正是被复制或被同步的文件会失去的边界。pre-17.1 的明文文件会被读进
活存储一次，然后删除（内存模式）或原地重新加密（持久模式）；带 ``dpapi:`` 值的
文件**不是** legacy —— 它属于另一个持久模式的进程，内存模式原样留下，不吸收也不
删除。
"""

from __future__ import annotations

import logging
import os
import pathlib
import sqlite3
import threading
import time
from urllib.parse import urlparse

from dhole_mcp import env_registry, secretbox

logger = logging.getLogger("dhole-mcp.sessions")

# 一颗存下的 cookie 从站点设置它起活这么久。
# 17.0 写死（``DHOLE_COOKIE_TTL_S`` 已注销）：一个 cookie 该活多久是能算清的
# （"隔天还认不认"），需要改它的频率低于发版频率。过期仍存在 —— 废弃的会话
# 不能无限期持有凭据。
COOKIE_TTL_S = 24 * 3600.0


def _cookie_ttl() -> float:
    """生效的 TTL 秒数。17.0 写死为 :data:`COOKIE_TTL_S`（24 小时）。"""
    return COOKIE_TTL_S
# 罐的上限，让一次穿过 500 个 host 的爬取无法把它撑到无限。最接近过期的行先走。
MAX_COOKIES = 2000
MAX_SESSIONS = 50


def _db_path() -> str:
    from dhole_mcp import paths
    return str(paths.file("sessions.db"))


# 持久化决定每个进程只绑定一次：下面的罐是模块级单例连接，中途翻转也搬不动一个
# 已经打开的存储。导入时冻结 —— 与 DHOLE_TOOLS 同一档"重启宿主"级别（注册表里
# read_at 是 "import"）。测试为了隔离会钉住这个属性。
_PERSIST = env_registry.read_flag("DHOLE_PERSIST_CREDENTIALS", fail_safe=True)

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_file_backed = False

_CREATE_SQL = (
    "CREATE TABLE IF NOT EXISTS cookies ("
    "  session_id TEXT NOT NULL,"
    "  host TEXT NOT NULL,"
    "  name TEXT NOT NULL,"
    "  value TEXT NOT NULL,"
    "  expires_at REAL NOT NULL,"
    "  PRIMARY KEY (session_id, host, name))"
)


def _connect() -> sqlite3.Connection:
    """打开（并创建）罐。调用方持有 :data:`_lock`。

    默认是 ``:memory:``：cookie 是凭据，不存在的文件无法泄漏。持久化额外需要
    加密器 —— 在没有加密器的平台（没有 Windows DPAPI）上，开关降级为内存并告警，
    绝不降级为明文文件。
    """
    global _conn, _file_backed
    if _conn is not None:
        return _conn
    persist = _PERSIST
    if persist and not secretbox.available():
        logger.warning(
            "DHOLE_PERSIST_CREDENTIALS=1 but encrypted persistence only exists "
            "on Windows (DPAPI) - the cookie jar stays in memory, nothing is "
            "written to disk")
        persist = False
    if persist:
        from dhole_mcp import paths
        db = _db_path()
        paths.ensure_private_dir(os.path.dirname(db) or ".")
        _conn = sqlite3.connect(db, check_same_thread=False)
        _conn.execute(_CREATE_SQL)
        _conn.commit()
        paths.harden_file(db)
        _file_backed = True
        _reencrypt_plaintext_values(_conn)
    else:
        # check_same_thread=False：抓取路径在工作线程上收 Set-Cookie；
        # 这个罐本来就跨线程共享。
        _conn = sqlite3.connect(":memory:", check_same_thread=False)
        _conn.execute(_CREATE_SQL)
        _conn.commit()
        _file_backed = False
        _absorb_legacy_file(_conn)
    return _conn


def _absorb_legacy_file(conn: sqlite3.Connection) -> None:
    """pre-17.1 的罐明文躺在 ``sessions.db`` 里。内存模式把它读进活罐一次然后删除
    文件：把明文凭据留在磁盘上会毁掉"不持久化"的全部意义。只收活（未过期）行 ——
    罐的 TTL 纪律同样适用于一个被复活的罐。

    加密值不是 legacy：那是**另一个进程**开着 DHOLE_PERSIST_CREDENTIALS=1 时写的
    罐，归它所有。把 ``dpapi:…`` 当明文吸收进内存会做两件坏事——把密文当成 cookie
    值发出去，还把那个进程的持久罐删掉。所以只要看见一颗加密值，整个文件原样留下；
    内存模式照样能在普查里列出它、在 `sys_state(clear=…)` 里抹掉它（见
    :func:`sessions_on_disk`），只是不能取用。
    """
    try:
        db = _db_path()
        if not os.path.exists(db):
            return
        legacy = sqlite3.connect(db)
        try:
            rows = legacy.execute(
                "SELECT session_id, host, name, value, expires_at FROM cookies"
            ).fetchall()
        finally:
            legacy.close()
    except Exception as e:
        logger.debug("legacy sessions.db unreadable, left in place: %s", e)
        return
    if any(secretbox.is_protected(value) for _, _, _, value, _ in rows):
        logger.info(
            "sessions.db holds encrypted cookie values - it belongs to a "
            "persist-mode store, so this process leaves it in place (it can "
            "still list and erase those sessions through sys_state)")
        return
    now = time.time()
    kept = 0
    for sid, host, name, value, expires_at in rows:
        if expires_at > now:
            conn.execute(
                "INSERT INTO cookies (session_id, host, name, value, expires_at) "
                "VALUES (?,?,?,?,?)", (sid, host, name, value, expires_at))
            kept += 1
    conn.commit()
    try:
        os.remove(db)
        logger.info(
            "absorbed %d live cookie(s) from the legacy plaintext %s into "
            "memory; the file was deleted", kept, db)
    except OSError as e:
        logger.debug("could not delete legacy sessions.db: %s", e)


def _reencrypt_plaintext_values(conn: sqlite3.Connection) -> None:
    """持久模式的一次性迁移：pre-17.1 文件把 cookie 值存成明文；把每个不带
    ``dpapi:`` 标记的值重写一遍。靠 LIKE 守卫保证幂等。"""
    rows = conn.execute(
        "SELECT rowid, value FROM cookies WHERE value NOT LIKE 'dpapi:%'"
    ).fetchall()
    if not rows:
        return
    for rowid, value in rows:
        conn.execute("UPDATE cookies SET value=? WHERE rowid=?",
                     (secretbox.protect(value), rowid))
    conn.commit()
    logger.info("re-encrypted %d cookie value(s) that the legacy sessions.db "
                "kept in plaintext", len(rows))


def _hosts_of(url: str) -> str:
    """URL 的 host，小写并去端口（解析失败时为 ''）。"""
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return ""
    if ":" in host:
        host = host.split(":", 1)[0]
    return host


def _prune(conn: sqlite3.Connection, now: float) -> None:
    conn.execute("DELETE FROM cookies WHERE expires_at <= ?", (now,))
    n = conn.execute("SELECT COUNT(*) FROM cookies").fetchone()[0]
    if n > MAX_COOKIES:
        conn.execute(
            "DELETE FROM cookies WHERE rowid IN ("
            "  SELECT rowid FROM cookies ORDER BY expires_at LIMIT ?)",
            (n - MAX_COOKIES,),
        )
    # 会话按它持有的最新 cookie 排名，所以还在用的会话能活下来；一个被废弃的登录
    # 整体死掉，而不是丢掉一半 cookie（被清了一半的罐就是一个坏掉的会话）。
    live = conn.execute(
        "SELECT session_id FROM cookies GROUP BY session_id "
        "ORDER BY MAX(expires_at) DESC"
    ).fetchall()
    stale = [row[0] for row in live[MAX_SESSIONS:]]
    if stale:
        conn.execute(
            "DELETE FROM cookies WHERE session_id IN (%s)" % ",".join("?" * len(stale)),
            tuple(stale),
        )


def store(session_id: str, url: str, cookies: dict, now: float | None = None) -> int:
    """记住 host 刚刚设置的 cookie。返回存了多少颗。

    ``cookies`` 就是 ``Response.cookies`` 已经给出的 name->value dict（那里拿不到
    属性 —— 见模块 docstring）。
    """
    sid = (session_id or "").strip()
    host = _hosts_of(url)
    if not sid or not host or not cookies:
        return 0
    ts = time.time() if now is None else now
    expires = ts + _cookie_ttl()
    stored = 0
    with _lock:
        try:
            conn = _connect()
            for name, value in (cookies or {}).items():
                if not name or value is None:
                    continue
                stored_value = str(value)
                if _file_backed:
                    # 值是凭据；在磁盘上只能是加密形式。内存模式存明文 --
                    # 因为整个进程里的其他东西本来就都在 RAM 里。
                    stored_value = secretbox.protect(stored_value)
                conn.execute(
                    "INSERT INTO cookies (session_id, host, name, value, expires_at) "
                    "VALUES (?,?,?,?,?) "
                    "ON CONFLICT(session_id, host, name) DO UPDATE SET "
                    "  value=excluded.value, expires_at=excluded.expires_at",
                    (sid, host, str(name), stored_value, expires),
                )
                stored += 1
            _prune(conn, ts)
            conn.commit()
        except Exception as e:
            logger.debug("cookie jar store failed for %s/%s: %s", sid, host, e)
    return stored


def header_for(session_id: str, url: str, now: float | None = None) -> dict:
    """罐里为这个 URL 的确切 host 持有的 ``{name: value}``（没有则 ''）。"""
    sid = (session_id or "").strip()
    host = _hosts_of(url)
    if not sid or not host:
        return {}
    ts = time.time() if now is None else now
    with _lock:
        try:
            conn = _connect()
            conn.execute("DELETE FROM cookies WHERE expires_at <= ?", (ts,))
            rows = conn.execute(
                "SELECT name, value FROM cookies "
                "WHERE session_id=? AND host=? ORDER BY name", (sid, host),
            ).fetchall()
            conn.commit()
        except Exception as e:
            logger.debug("cookie jar read failed for %s/%s: %s", sid, host, e)
            return {}
    if _file_backed:
        rows = [(name, secretbox.unprotect(value)) for name, value in rows]
    return {name: value for name, value in rows}


def names_for(session_id: str, url: str) -> list:
    """为这个 host 持有的 cookie **名字**。值刻意不返回。"""
    sid = (session_id or "").strip()
    host = _hosts_of(url)
    if not sid or not host:
        return []
    with _lock:
        try:
            conn = _connect()
            rows = conn.execute(
                "SELECT name FROM cookies WHERE session_id=? AND host=? "
                "AND expires_at > ? ORDER BY name",
                (sid, host, time.time()),
            ).fetchall()
        except Exception as e:
            logger.debug("cookie jar names lookup failed for %s/%s: %s", sid, host, e)
            return []
    return [row[0] for row in rows]


def _census(rows: list, now: float) -> dict:
    """活行 -> 只报名字的普查。两个存储共用这一份算法，别让它们漂开。"""
    out: dict = {}
    for sid, host, name, expires_at in rows:
        entry = out.setdefault(sid, {"hosts": {}, "cookies": 0, "expires_in_s": 0})
        entry["hosts"].setdefault(host, []).append(name)
        entry["cookies"] += 1
        left = int(expires_at - now)
        if not entry["expires_in_s"] or left < entry["expires_in_s"]:
            entry["expires_in_s"] = max(0, left)
    return out


def sessions() -> dict:
    """只报名字的普查：``{session_id: {"hosts": {host: [names]},
    "cookies": n, "expires_in_s": s}}``。

    诊断与 ``sys_state`` 普查是两个消费者，而它们对同一个问题需要同一个答案：
    哪些 id 持有凭据、为哪些 host、还有多久自行失效。所以最早到期时间报成倒计时
    而不是 epoch 浮点 —— "21 小时后过期"是可行动的（"随它去"），裸的
    ``1790566780.9`` 不是。

    值永远不在这里。cookie 是凭据，而工具响应的每个字段都会落进 agent 的
    transcript。

    这里只是**本进程**的罐。磁盘上那份属于另一个进程时，用
    :func:`sessions_everywhere`。
    """
    now = time.time()
    with _lock:
        try:
            conn = _connect()
            rows = conn.execute(
                "SELECT session_id, host, name, expires_at FROM cookies "
                "WHERE expires_at > ? ORDER BY session_id, host, name",
                (now,),
            ).fetchall()
        except Exception as e:
            logger.debug("cookie jar census failed: %s", e)
            return {}
    return _census(rows, now)


def uses_disk() -> bool:
    """这个进程的罐是否**就是**磁盘上那一份。

    `_connect` 还没被调用时 `_file_backed` 仍是 False，所以答案要跟它将来做的
    决定一致：开关打开且有加密器，才落盘。`sys_state` 用这一格决定要不要另外去
    读磁盘 —— 同一份读两次会把一颗 cookie 数成两颗。
    """
    if _conn is not None:
        return _file_backed
    return bool(_PERSIST and secretbox.available())


def _disk_rows(db: str, now: float) -> list:
    """只读打开磁盘上的罐并取活行。

    ``mode=ro`` 是刻意的：普查只该读 —— 这一份可能属于另一个进程，顺手创建文件、
    写 journal 或把它锁在写入者外面都不是"看一眼"该有的副作用。（抹除是另一扇门：
    :func:`clear_on_disk`。）
    """
    if not os.path.exists(db):
        return []
    uri = pathlib.Path(os.path.abspath(db)).as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2.0)
    try:
        return conn.execute(
            "SELECT session_id, host, name, expires_at FROM cookies "
            "WHERE expires_at > ? ORDER BY session_id, host, name", (now,)
        ).fetchall()
    finally:
        conn.close()


def sessions_on_disk(now: float | None = None) -> dict:
    """磁盘那一份罐的普查（只有名字，没有值）。

    文件不存在、不是 dhole 的表、或读不了，都交回空 —— 读不到就是读不到，不替
    它编一行。
    """
    now = time.time() if now is None else now
    try:
        return _census(_disk_rows(_db_path(), now), now)
    except Exception as e:
        logger.debug("disk cookie jar census failed: %s", e)
        return {}


def sessions_everywhere() -> dict:
    """这台机器上所有罐的普查：本进程的，加上磁盘上那一份（两者不是同一份时）。

    两种存储按同一个 id 键。一个开了 ``DHOLE_PERSIST_CREDENTIALS=1`` 的进程会把
    cookie 留在 ``sessions.db`` 里，而一个内存模式的进程既取不到、从前也看不见
    它们：`sys_state` 对一份确实躺在磁盘上的凭据回 "no such session"，调用方
    于是没有任何工具可以确认或抹掉它 —— 状态面把自己的普查范围说小了。

    每行都带 ``store``：``memory``（只在本进程）/ ``disk``（共享文件，别的进程
    写的）/ ``memory+disk``（同一个 id 两边都有）。`disk` 说的是**能看见、能
    抹掉、不能取用** —— 内存模式的进程下一次带这个 id 的调用仍然从空罐开始。
    """
    here = sessions()
    if uses_disk():
        for entry in here.values():
            entry["store"] = "disk"
        return here
    for entry in here.values():
        entry["store"] = "memory"
    there = sessions_on_disk()
    for entry in there.values():
        entry["store"] = "disk"
    return _merge_census(here, there)


def _merge_census(a: dict, b: dict) -> dict:
    """同一个 id 在两边都有：host/名字取并集，到期取最早，并标明两边都有。"""
    out = {sid: {"hosts": {h: list(names) for h, names in e["hosts"].items()},
                 "cookies": e["cookies"], "expires_in_s": e["expires_in_s"],
                 "store": e.get("store", "memory")}
           for sid, e in a.items()}
    for sid, e in b.items():
        row = out.setdefault(sid, {"hosts": {}, "cookies": 0,
                                   "expires_in_s": 0, "store": "disk"})
        for host, names in e["hosts"].items():
            have = row["hosts"].setdefault(host, [])
            have.extend(n for n in names if n not in have)
        # 同一颗 cookie（同 host 同名）只算一次：两边计数相加会把一行说成两行。
        row["cookies"] = sum(len(v) for v in row["hosts"].values())
        left = e["expires_in_s"]
        row["expires_in_s"] = (left if not row["expires_in_s"]
                               else min(row["expires_in_s"], left))
        row["store"] = "memory+disk" if sid in a else "disk"
    return out


def clear_on_disk(session_id: str = "") -> int:
    """抹掉**磁盘那一份**罐里的行，即使本进程的罐在内存里。

    删行是纯 SQL，不需要解密 —— 所以一个内存模式的进程能对另一个进程留下的持久
    会话做「确认 + 抹掉」，而这正是 `sys_state` 从前缺的那一手。取用不在其中：
    那些 cookie 不会被这个进程发出去（见 :func:`sessions_everywhere` 的 `store`）。
    """
    db = _db_path()
    if not os.path.exists(db):
        return 0
    sid = (session_id or "").strip()
    try:
        conn = sqlite3.connect(db, timeout=5.0)
    except Exception as e:
        logger.debug("disk cookie jar unreadable for clear: %s", e)
        return 0
    try:
        if sid:
            cur = conn.execute("DELETE FROM cookies WHERE session_id=?", (sid,))
        else:
            cur = conn.execute("DELETE FROM cookies")
        n = cur.rowcount
        conn.commit()
        return n
    except Exception as e:
        logger.debug("disk cookie jar clear failed: %s", e)
        return 0
    finally:
        conn.close()


def clear(session_id: str = "", now: float | None = None) -> int:
    """丢掉**本进程**罐里一个会话的 cookie；``session_id`` 为空时清空整个罐。

    磁盘上那一份不在这里（本进程的罐可能就是它，见 :func:`uses_disk`）：
    内存模式要抹另一个进程的持久会话，用 :func:`clear_on_disk`。
    """
    sid = (session_id or "").strip()
    with _lock:
        try:
            conn = _connect()
            if sid:
                cur = conn.execute("DELETE FROM cookies WHERE session_id=?", (sid,))
            else:
                cur = conn.execute("DELETE FROM cookies")
            n = cur.rowcount
            conn.commit()
            return n
        except Exception as e:
            logger.debug("cookie jar clear failed: %s", e)
            return 0


def reset_for_tests() -> None:
    """关闭模块级连接，让测试能重定向 ``DHOLE_HOME``。

    DB 路径是惰性读取的，所以一个在首次使用之后才设置随手目录的 fixture，否则会
    一直写真实文件。
    """
    global _conn, _file_backed
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
        _file_backed = False
