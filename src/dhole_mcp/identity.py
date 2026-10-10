"""The stored default identity behind `cred_defaults` (17.0 §3-17).

One question decides where a setting belongs: does the caller have to remember it
on **every** call? `proxy`, `profile`, `cdp`, `allow_private`, `anti_detect` and
the two credential shapes do — an agent that reaches the same API forty times is
not learning the same secret forty times, and neither "pass it again" nor "edit
the env and restart the host" is a good answer. So they get set once, live in a
file, and every later call reads them back.

Two rules, both load-bearing:

* **A credential carries a host.** `auth` and `cookies` cannot be stored without
  one. A global stored Basic-Auth header rides along to the next site the caller
  reads, which is a different site's request — the same mistake the per-hop
  credential drop in `web_read` exists to prevent, made persistent. The
  host-matched rows are the whole answer: `scope` is exact-host-or-subdomain
  (`api.example.com` matches `example.com`, `evil-example.com` does not), and the
  five non-credential keys are global by nature (one proxy, one profile, one
  attached browser, one ladder, one private-host allowance).
* **Values never go out through this module's read side.** :func:`view` returns
  the redacted shape — cookie NAMES, an auth type plus username, a proxy host
  with a "has credentials" boolean. The full value stays in the file. That is the
  same boundary the cookie jar uses (`sessions.py`): tool output lands in the
  agent's transcript, and the point of echoing "what is in force" is to let the
  caller revoke it without reading it.

Storage is **in memory by default** (SQLite ``:memory:``): "the operator chose
it" is not "the operator wants it on disk" — every value here can be a
credential (``auth``, ``cookies``, a proxy URL with embedded basics), and the
safest file is one that does not exist. ``DHOLE_PERSIST_CREDENTIALS=1`` moves
the store to ``~/.dhole/identity.db`` (0600 dir/file per ``paths.py``), where
every value is encrypted with Windows DPAPI (:mod:`dhole_mcp.secretbox`) before
it touches disk. A pre-17.1 plaintext file is absorbed into the live store and
deleted (memory mode) or re-encrypted in place (persist mode).
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

# Which keys need a host and which do not. Kept as data because the tool's error
# messages and the sys_state census both have to say it.
SCOPED_KEYS = ("auth", "cookies")
GLOBAL_KEYS = ("proxy", "profile", "cdp", "allow_private", "anti_detect")
KEYS = SCOPED_KEYS + GLOBAL_KEYS

CREDENTIAL_KEYS = SCOPED_KEYS

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
# Frozen at import, like the jar's own switch (read_at="import" in the
# registry): the store below is a module-level singleton connection, so a
# mid-run flip could not move it anyway. Tests pin this attr for hermeticity.
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
    """Open (and create) the store. Caller holds :data:`_lock`.

    Default is ``:memory:`` — see the module docstring. Persisting additionally
    needs an encryptor; without one (no Windows DPAPI) the flag degrades to
    memory with a warning, never to a plaintext file.
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
    """A pre-17.1 store lived in plaintext ``identity.db``. Memory mode reads
    it into the live store once and deletes the file: these are the operator's
    chosen defaults, so they keep working this run — as memory, which is what
    the default now promises."""
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
    """One-time migration for persist mode: rewrite every value that does not
    carry the ``dpapi:`` marker. Idempotent by the LIKE guard."""
    rows = conn.execute(
        "SELECT rowid, value FROM defaults WHERE value NOT LIKE 'dpapi:%'"
    ).fetchall()
    if not rows:
        return
    for rowid, value in rows:
        conn.execute("UPDATE defaults SET value=? WHERE rowid=?",
                     (secretbox.protect(value), rowid))
    conn.commit()
    logger.info("re-encrypted %d plaintext value(s) from a pre-17.1 "
                "identity.db", len(rows))


def _decode(raw: str) -> Any:
    """The stored TEXT column to a Python value: unprotect (a no-op on legacy
    plaintext and on memory mode) then json.loads, None on any failure — the
    shape every reader had before encryption existed."""
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
    """Drop the connection (tests repoint DHOLE_HOME and must not keep writing)."""
    global _conn, _file_backed
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
        _file_backed = False


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def scope_matches(scope: str, host: str) -> bool:
    """Exact host or a subdomain of it — never a bare substring.

    `evil-example.com` must not inherit what `example.com` was given, which is the
    one way this function can lose.
    """
    scope, host = (scope or "").lower().lstrip("."), (host or "").lower()
    if not scope or not host:
        return False
    return host == scope or host.endswith("." + scope)


def set_value(key: str, value: Any, scope: str = "") -> None:
    with _lock:
        cur = _connect()
        payload = json.dumps(value)
        if _file_backed:
            # Every value here can be a credential; on disk it is only ever
            # the encrypted form (see the module docstring for the boundary).
            payload = secretbox.protect(payload)
        cur.execute(
            "INSERT OR REPLACE INTO defaults (key, scope, value, updated_at)"
            " VALUES (?, ?, ?, ?)",
            (key, scope.lower(), payload, time.time()))
        cur.commit()


def unset(keys: Iterable[str] = (), scope: Optional[str] = None) -> int:
    """Forget these keys. `scope=None` means every scope of them; "" means global."""
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
    with _lock:
        cur = _connect()
        n = cur.execute("DELETE FROM defaults").rowcount
        cur.commit()
        return n


def all_rows() -> List[dict]:
    """Every stored row, already redacted: what a caller (or sys_state) may see.

    `stored_s_ago` is a countdown, not an epoch float: "stored 3 hours ago" is
    the question a caller has, and a bare 1790995191.36 is the same mistake the
    session census stopped making.
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
    """The value in force for `key` on this host, or None when nothing is stored.

    A host-scoped credential beats a global one for its own host only, and a
    global row never answers for a credential key (that shape is refused at set
    time — see :func:`redact`'s module docstring).
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
    """What a call to `host` would pick up, redacted, one row per key."""
    rows = []
    for key in KEYS:
        value = get(key, host)
        if value is None:
            continue
        scope = "" if key not in CREDENTIAL_KEYS else _best_scope(key, host)
        rows.append({"key": key, "scope": scope, "value": redact(key, value)})
    return rows


def in_force(host: str = "") -> Dict[str, Any]:
    """Redacted map of what a call to `host` would actually pick up."""
    return {r["key"]: r["value"] for r in in_force_rows(host)}


def _best_scope(key: str, host: str) -> str:
    with _lock:
        cur = _connect()
        fetched = cur.execute(
            "SELECT scope FROM defaults WHERE key = ?", (key,)).fetchall()
    matched = [s for (s,) in fetched if scope_matches(s, host)]
    return max(matched, key=len) if matched else ""


def applied_note(host: str = "") -> str:
    """One line naming what a call to `host` was fetched with — empty when nothing was.

    Derived from :func:`in_force_rows`, so it can only ever say what the values
    were checked to be: a response field is a place a secret could go.
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
    """The shareable half of a stored value. Anything not listed here is not said.

    The list is deliberately short: a password, a bearer token, a header value, a
    cookie value and a proxy password all have no redacted form, so they appear
    only as "present".
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
