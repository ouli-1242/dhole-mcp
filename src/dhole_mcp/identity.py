"""`cred_defaults` 背后的已存默认身份（17.0 §3-17）。

一个问题决定设置该放哪：调用方是否**每次调用**都得记住它？`proxy`、`profile`、
`cdp`、`allow_private`、`anti_detect` 与两种凭据形状都需要——一个四十次打到同一
API 的 agent 不该把同一个秘密学四十遍，"再传一次"与"改环境重启宿主"也都不是好
答案。于是它们被设置一次、住进文件，之后每次调用读回来。

两条规则，都是承重墙：

* **凭据带着主机。** `auth` 与 `cookies` 没有主机就不能存。一条全局存储的
  Basic-Auth 头会跟着调用方去读下一个站点，而那是另一个站点的请求——`web_read`
  里逐跳丢弃凭据防的正是这个错误，只是这里把它变成了持久的。按主机匹配的行就是
  全部答案：`scope` 是精确主机或子域（`api.example.com` 匹配 `example.com`，
  `evil-example.com` 不匹配），五颗非凭据键天然是全局的（一个代理、一个 profile、
  一台附加浏览器、一条档位梯、一份私网许可）。
* **值绝不从本模块的读取面出去。** :func:`in_force_rows` 返回脱敏形状——cookie
  的**名字**、认证类型加用户名、代理主机加一个"带凭据"布尔。完整值留在文件里。
  这与 cookie 罐（`sessions.py`）是同一条边界：工具输出会进 agent 的转录，而
  回显"当前生效的是什么"的意义，是让调用方能在不读到值的情况下撤销它。

存储**默认在内存里**（SQLite ``:memory:``）："运维选了这个值"不等于"运维想让它
落盘"——这里每个值都可能是凭据（``auth``、``cookies``、带内嵌 basic 的代理 URL），
最安全的文件是不存在的那个。``DHOLE_PERSIST_CREDENTIALS=1`` 把存储移到
``~/.dhole/identity.db``（目录/文件 0600，见 ``paths.py``），落盘前每个值都用
Windows DPAPI（:mod:`dhole_mcp.secretbox`）加密。pre-17.1 的明文文件会被吸进当前
存储并删除（内存模式）或原地重新加密（持久模式）。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from dhole_mcp import env_registry, secretbox

logger = logging.getLogger("dhole-mcp.identity")

# 哪些键需要主机、哪些不需要。做成数据，因为工具的报错文案与 sys_state 的普查
# 都得说出这件事。
SCOPED_KEYS = ("auth", "cookies")
GLOBAL_KEYS = ("proxy", "profile", "cdp", "allow_private", "anti_detect")
KEYS = SCOPED_KEYS + GLOBAL_KEYS

CREDENTIAL_KEYS = SCOPED_KEYS

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
# import 时冻结，和 cookie 罐自己的开关一样（registry 里 read_at="import"）：
# 下面的存储是模块级单例连接，跑到一半改开关也搬不动它。测试钉住这个属性来
# 保持隔离。
_PERSIST = env_registry.read_flag("DHOLE_PERSIST_CREDENTIALS", fail_safe=True)
_file_backed = False

_CREATE_SQL = (
    "CREATE TABLE IF NOT EXISTS defaults ("
    " key TEXT NOT NULL, scope TEXT NOT NULL DEFAULT '',"
    " value TEXT NOT NULL, updated_at REAL NOT NULL,"
    " PRIMARY KEY (key, scope))")


def _db_path() -> str:
    from dhole_mcp import paths
    return str(paths.file("identity.db"))


def _connect() -> sqlite3.Connection:
    """打开（必要时创建）存储。调用方持着 :data:`_lock`。

    默认是 ``:memory:``——见模块 docstring。持久化还需要一个加密器；没有它
    （非 Windows DPAPI）时开关降级为内存并告警，绝不降级成明文文件。
    """
    global _conn, _file_backed
    if _conn is not None:
        return _conn
    persist = _PERSIST
    if persist and not secretbox.available():
        logger.warning(
            "DHOLE_PERSIST_CREDENTIALS=1 but encrypted persistence only exists "
            "on Windows (DPAPI) - stored credentials stay in memory, nothing "
            "is written to disk")
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
        _conn = sqlite3.connect(":memory:", check_same_thread=False)
        _conn.execute(_CREATE_SQL)
        _conn.commit()
        _file_backed = False
        _absorb_legacy_file(_conn)
    return _conn


def _absorb_legacy_file(conn: sqlite3.Connection) -> None:
    """pre-17.1 的存储曾以明文 ``identity.db`` 存在。内存模式把它读进当前存储
    一次，然后删掉文件：这些是运维选定的默认值，所以本次运行继续有效——以内存
    形式，这正是现在的默认承诺。"""
    try:
        db = _db_path()
        if not os.path.exists(db):
            return
        legacy = sqlite3.connect(db)
        try:
            rows = legacy.execute(
                "SELECT key, scope, value, updated_at FROM defaults").fetchall()
        finally:
            legacy.close()
    except Exception as e:
        logger.debug("legacy identity.db unreadable, left in place: %s", e)
        return
    for key, scope, value, updated_at in rows:
        conn.execute(
            "INSERT INTO defaults (key, scope, value, updated_at) "
            "VALUES (?,?,?,?)", (key, scope, value, updated_at))
    conn.commit()
    try:
        os.remove(db)
        logger.info("absorbed %d stored default(s) from the legacy plaintext "
                    "%s into memory; the file was deleted", len(rows), db)
    except OSError as e:
        logger.debug("could not delete legacy identity.db: %s", e)


def _reencrypt_plaintext_values(conn: sqlite3.Connection) -> None:
    """持久模式的一次性迁移：重写每个不带 ``dpapi:`` 标记的值。靠 LIKE 守卫
    保证幂等。"""
    rows = conn.execute(
        "SELECT rowid, value FROM defaults WHERE value NOT LIKE 'dpapi:%'"
    ).fetchall()
    if not rows:
        return
    for rowid, value in rows:
        conn.execute("UPDATE defaults SET value=? WHERE rowid=?",
                     (secretbox.protect(value), rowid))
    conn.commit()
    logger.info("re-encrypted %d value(s) that the legacy identity.db kept in "
                "plaintext", len(rows))


def _decode(raw: str) -> Any:
    """存储的 TEXT 列到 Python 值：先 unprotect（对旧明文与内存模式是 no-op），
    再 json.loads；任何失败都返回 None——这是加密出现之前每个读取者的形状。"""
    if _file_backed:
        try:
            raw = secretbox.unprotect(raw)
        except Exception:
            return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def close() -> None:
    """丢掉连接（测试会重指 DHOLE_HOME，不能继续往旧库写）。"""
    global _conn, _file_backed
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
        _file_backed = False


def host_of(url: str) -> str:
    """The lower-cased hostname of a URL; ``""`` when it has none.

    Args:
        url: A string of any URL shape.

    Returns:
        The hostname with scheme and port removed, lower-cased. Malformed input and
        host-less URLs give the empty string; this helper never raises, because in a scope
        lookup "no host" means "no match", not an error.
    """
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def scope_matches(scope: str, host: str) -> bool:
    """精确主机或它的子域——绝不是裸子串。

    `evil-example.com` 不能继承给 `example.com` 的东西；这是本函数唯一可能
    输掉的地方。
    """
    scope, host = (scope or "").lower().lstrip("."), (host or "").lower()
    if not scope or not host:
        return False
    return host == scope or host.endswith("." + scope)


def set_value(key: str, value: Any, scope: str = "") -> None:
    """Store one default; an existing ``(key, scope)`` row is replaced.

    Args:
        key: One of :data:`KEYS`. Tool boundaries validate the name, so the caller is
            trusted here.
        value: A JSON-serialisable value. In persistent mode it is DPAPI-encrypted before
            it reaches disk; in memory mode it never leaves the process.
        scope: The host the value is bound to, lower-cased before storage. Global keys use
            ``""``; credential keys should carry a non-empty scope (the tool boundary refuses
            global credentials because they would ride along to the next site).

    Side effects:
        Written and committed immediately. Credential values never appear in plaintext on
        this module's read surface.
    """
    with _lock:
        cur = _connect()
        payload = json.dumps(value)
        if _file_backed:
            # 这里每个值都可能是凭据；落盘时只存加密形式（边界见模块 docstring）。
            payload = secretbox.protect(payload)
        cur.execute(
            "INSERT OR REPLACE INTO defaults (key, scope, value, updated_at)"
            " VALUES (?, ?, ?, ?)",
            (key, scope.lower(), payload, time.time()))
        cur.commit()


def unset(keys: Iterable[str] = (), scope: Optional[str] = None) -> int:
    """忘掉这些键。`scope=None` 表示它们的所有作用域；"" 表示全局。"""
    keys = [k for k in keys]
    if not keys:
        return 0
    with _lock:
        cur = _connect()
        marks = ",".join("?" * len(keys))
        if scope is None:
            n = cur.execute(
                f"DELETE FROM defaults WHERE key IN ({marks})", keys).rowcount
        else:
            n = cur.execute(
                f"DELETE FROM defaults WHERE key IN ({marks}) AND scope = ?",
                [*keys, scope.lower()]).rowcount
        cur.commit()
        return n


def clear() -> int:
    """Delete every default across all scopes.

    Returns:
        The number of rows deleted (0 when the store was already empty).
    """
    with _lock:
        cur = _connect()
        n = cur.execute("DELETE FROM defaults").rowcount
        cur.commit()
        return n


def all_rows() -> List[dict]:
    """每一行存储值，已经脱敏：调用方（或 sys_state）能看的就是这些。

    `stored_s_ago` 是倒计时，不是 epoch 浮点数："3 小时前存的"才是调用方的问题，
    一个裸的 1790995191.36 与 session 普查早已不再犯的错误同形。
    """
    with _lock:
        cur = _connect()
        fetched = cur.execute(
            "SELECT key, scope, value, updated_at FROM defaults"
            " ORDER BY key, scope").fetchall()
    now = time.time()
    out = []
    for key, scope, raw, updated in fetched:
        value = _decode(raw)
        out.append({"key": key, "scope": scope,
                    "stored_s_ago": max(0, int(now - (updated or now))),
                    "value": redact(key, value)})
    return out


def get(key: str, host: str = "") -> Any:
    """`key` 在这台主机上生效的值；什么都没存时返回 None。

    带主机作用域的凭据只对它自己的主机压过全局；全局行永远不为凭据键作答
    （这种形状在设置时就被拒——见 :func:`redact` 的模块 docstring）。
    """
    with _lock:
        cur = _connect()
        fetched = cur.execute(
            "SELECT scope, value FROM defaults WHERE key = ?", (key,)).fetchall()
    if not fetched:
        return None
    if key not in CREDENTIAL_KEYS:
        return _decode(fetched[0][1])
    best: tuple[str, str] | None = None
    for scope, raw in fetched:
        if scope_matches(scope, host):
            if best is None or len(scope) > len(best[0]):
                best = (scope, raw)
    return _decode(best[1]) if best else None


def in_force_rows(host: str = "") -> List[dict]:
    """一次发往 `host` 的调用会取到的东西，已脱敏，每个键一行。"""
    rows = []
    for key in KEYS:
        value = get(key, host)
        if value is None:
            continue
        scope = "" if key not in CREDENTIAL_KEYS else _best_scope(key, host)
        rows.append({"key": key, "scope": scope, "value": redact(key, value)})
    return rows


def in_force(host: str = "") -> Dict[str, Any]:
    """一次发往 `host` 的调用实际会取到的东西，已脱敏的映射。"""
    return {r["key"]: r["value"] for r in in_force_rows(host)}


def _best_scope(key: str, host: str) -> str:
    with _lock:
        cur = _connect()
        fetched = cur.execute(
            "SELECT scope FROM defaults WHERE key = ?", (key,)).fetchall()
    matched = [s for (s,) in fetched if scope_matches(s, host)]
    return max(matched, key=len) if matched else ""


def applied_note(host: str = "") -> str:
    """一行点名一次发往 `host` 的调用带着什么去抓——什么都没带时为空。

    从 :func:`in_force_rows` 派生，所以它只能说出那些值被核对成什么：响应字段
    正是秘密可能去的地方。
    """
    rows = in_force_rows(host)
    if not rows:
        return ""
    return ", ".join(f"{r['key']}={r['scope']}" if r["scope"] else r["key"]
                     for r in rows)


def credentials_held_for(url: str) -> tuple[str, ...]:
    """这台 host 上存着哪几颗**需要 host 的**凭据 —— 只报名，值一眼都不读出去。

    与 :func:`applied_note` 分别是范围：那颗回答"这一次跑的是什么身份"（连全局那几颗
    一起报），这一颗只问"有没有凭据是为这一台机器存的"。它存在的全部理由是一条实测：
    `feed_read` 与 sitemap 那一发都不带凭据出门，而调用方可能已经存过了 —— 要说破这
    个不一致，需要的是"存了什么"，不是"这次用了什么"。
    """
    host = host_of(url or "")
    if not host:
        return ()
    return tuple(k for k in SCOPED_KEYS if get(k, host) not in (None, "", {}, []))


def redact(key: str, value: Any) -> Any:
    """一颗存储值里可以分享的那一半。没列在这里的就不说。

    这份名单刻意很短：密码、bearer token、头值、cookie 值与代理密码都没有脱敏
    形式，所以它们只以"存在"出现。
    """
    if value is None:
        return None
    if key == "auth":
        if not isinstance(value, dict):
            return {"shape": type(value).__name__}
        kind = str(value.get("type", ""))
        out: Dict[str, Any] = {"type": kind}
        if kind == "basic":
            out["username"] = str(value.get("username", ""))
        elif kind == "header":
            out["name"] = str(value.get("name", ""))
        return out
    if key == "cookies":
        names = _cookie_names(value)
        return {"names": sorted(names), "count": len(names)}
    if key == "proxy":
        server = _proxy_server(value)
        return {"server": _strip_credentials(server),
                "credentials": "@" in server or bool(
                    isinstance(value, dict) and value.get("username"))}
    return value


def _proxy_server(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("server", ""))
    return str(value)


def _strip_credentials(server: str) -> str:
    """`http://user:pass@host:port` -> `http://host:port`."""
    if "@" not in server:
        return server
    scheme, _, rest = server.rpartition("//")
    return f"{scheme}//{rest.rpartition('@')[2]}" if scheme else rest.rpartition("@")[2]


def _cookie_names(value: Any) -> List[str]:
    if isinstance(value, dict):
        return [str(k) for k in value]
    if isinstance(value, str):
        return [p.split("=", 1)[0].strip() for p in value.split(";")
                if "=" in p]
    if isinstance(value, (list, tuple)):
        return [str(item.get("name", "")) for item in value
                if isinstance(item, dict) and item.get("name")]
    return []
