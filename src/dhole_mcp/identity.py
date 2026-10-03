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

Storage is SQLite at ``~/.dhole/identity.db`` (0600 dir/file per ``paths.py``),
the same trust level as ``sessions.db``, which already holds site-set cookies in
plaintext. Surviving a restart is the point: this is state the operator chose,
not state a page handed us.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

# Which keys need a host and which do not. Kept as data because the tool's error
# messages and the sys_state census both have to say it.
SCOPED_KEYS = ("auth", "cookies")
GLOBAL_KEYS = ("proxy", "profile", "cdp", "allow_private", "anti_detect")
KEYS = SCOPED_KEYS + GLOBAL_KEYS

CREDENTIAL_KEYS = SCOPED_KEYS

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _db_path() -> str:
    from dhole_mcp import paths
    return str(paths.file("identity.db"))


def _connect() -> sqlite3.Connection:
    """Open (and create) the store. Caller holds :data:`_lock`."""
    global _conn
    if _conn is not None:
        return _conn
    from dhole_mcp import paths
    db = _db_path()
    paths.ensure_private_dir(os.path.dirname(db) or ".")
    _conn = sqlite3.connect(db, check_same_thread=False)
    _conn.execute(
        "CREATE TABLE IF NOT EXISTS defaults ("
        " key TEXT NOT NULL, scope TEXT NOT NULL DEFAULT '',"
        " value TEXT NOT NULL, updated_at REAL NOT NULL,"
        " PRIMARY KEY (key, scope))")
    _conn.commit()
    return _conn


def close() -> None:
    """Drop the connection (tests repoint DHOLE_HOME and must not keep writing)."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


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
        cur.execute(
            "INSERT OR REPLACE INTO defaults (key, scope, value, updated_at)"
            " VALUES (?, ?, ?, ?)",
            (key, scope.lower(), json.dumps(value), time.time()))
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
        try:
            value = json.loads(raw)
        except Exception:
            value = None
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
        try:
            return json.loads(fetched[0][1])
        except Exception:
            return None
    best: tuple[str, str] | None = None
    for scope, raw in fetched:
        if scope_matches(scope, host):
            if best is None or len(scope) > len(best[0]):
                best = (scope, raw)
    try:
        return json.loads(best[1]) if best else None
    except Exception:
        return None


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
