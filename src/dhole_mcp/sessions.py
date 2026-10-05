"""Per-session cookie jar (v16.0, gap G7).

Until now cookies existed for exactly one call: ``options.cookies`` built a
``Cookie`` header, the call ended, and the site's ``Set-Cookie`` answer was read
off the response and dropped. Anything needing a login (form POST then the
pages behind it, a CSRF pair, a consent banner) was impossible: the second call
started from zero. This module is the missing store — cookies a host gave us are
kept under a caller-chosen ``session_id`` and sent back to that host on the next
call.

Scope, and what is deliberately NOT here:

* **Host-scoped, not domain-scoped.** ``Response.cookies`` carries names and
  values only; the ``Domain=`` / ``Path=`` / ``Secure`` attributes are gone by the
  time a fetch finishes (fetcher.py builds that dict from the client's cookie
  list). Re-deriving them means parsing raw ``Set-Cookie`` headers per hop, and a
  jar wider than the exact host that set the cookie is a cookie sent to a
  subdomain that never asked for it. So a cookie set by ``example.com`` is sent
  back to ``example.com`` only.
* **Values never leave the jar toward the model.** Responses report the cookie
  NAMES for the host (``session_cookie_names``), never values: a session cookie
  is a credential, and tool output lands in the agent's transcript — and in the
  SQLite content cache, and possibly in a log. An agent that needs to know
  "did my login survive?" is answered by the name list.
* **Expires after** the fixed :data:`COOKIE_TTL_S` (24h, hardcoded in 17.0).
  Written once per ``Set-Cookie``, not
  refreshed on send, so a forgotten session cannot accumulate credentials
  forever. ``sys_state`` (the tool) drops the jar, one named id or every one.

Storage is **in memory by default** (SQLite ``:memory:`` — the safest file is
one that does not exist): the jar lives as long as the process, and a restart
starts from zero. ``DHOLE_PERSIST_CREDENTIALS=1`` moves the jar to
``~/.dhole/sessions.db`` (0600 dir/file per paths.py), where every cookie VALUE
is encrypted with Windows DPAPI (:mod:`dhole_mcp.secretbox`) before it touches
disk — the key is bound to the Windows user account, which is exactly the
boundary a copied or synced file loses. A pre-17.1 plaintext file is read into
the live store once and then deleted (memory mode) or re-encrypted in place
(persist mode).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from urllib.parse import urlparse

from dhole_mcp import env_registry, secretbox

logger = logging.getLogger("dhole-mcp.sessions")

# A stored cookie lives this long from when the site set it.
# 17.0 写死（``DHOLE_COOKIE_TTL_S`` 已注销）：一个 cookie 该活多久是能算清的
# （"隔天还认不认"），需要改它的频率低于发版频率。过期仍存在 —— 废弃的会话
# 不能无限期持有凭据。
COOKIE_TTL_S = 24 * 3600.0


def _cookie_ttl() -> float:
    """Effective TTL in seconds. 17.0 写死为 :data:`COOKIE_TTL_S`（24 小时）。"""
    return COOKIE_TTL_S
# Bound on the jar, so a crawl that hops through 500 hosts cannot grow it
# without limit. Oldest-expiring rows go first.
MAX_COOKIES = 2000
MAX_SESSIONS = 50


def _db_path() -> str:
    from dhole_mcp import paths
    return str(paths.file("sessions.db"))


# The persist decision binds once per process: the jar below is a module-level
# singleton connection, so a mid-run flip could not move an open store anyway.
# Frozen at import — same "restart the host" tier as DHOLE_TOOLS (read_at is
# "import" in the registry). Tests pin this attr for hermeticity.
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
    """Open (and create) the jar. Caller holds :data:`_lock`.

    Default is ``:memory:``: cookies are credentials and the file that never
    exists cannot leak. Persisting additionally needs an encryptor — on a
    platform without one (no Windows DPAPI) the flag degrades to memory with a
    warning, never to a plaintext file.
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
        # check_same_thread=False: the fetch path harvests Set-Cookie on a
        # worker thread; the jar was always shared across threads.
        _conn = sqlite3.connect(":memory:", check_same_thread=False)
        _conn.execute(_CREATE_SQL)
        _conn.commit()
        _file_backed = False
        _absorb_legacy_file(_conn)
    return _conn


def _absorb_legacy_file(conn: sqlite3.Connection) -> None:
    """A pre-17.1 jar lived in plaintext ``sessions.db``. Memory mode reads it
    into the live jar once and deletes the file: leaving plaintext credentials
    on disk would defeat the point of not persisting. Live (unexpired) rows
    only — the jar's TTL discipline applies to a resurrected jar too."""
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
    """One-time migration for persist mode: a pre-17.1 file holds cookie values
    in plaintext; rewrite every value that does not carry the ``dpapi:``
    marker. Idempotent by the LIKE guard."""
    rows = conn.execute(
        "SELECT rowid, value FROM cookies WHERE value NOT LIKE 'dpapi:%'"
    ).fetchall()
    if not rows:
        return
    for rowid, value in rows:
        conn.execute("UPDATE cookies SET value=? WHERE rowid=?",
                     (secretbox.protect(value), rowid))
    conn.commit()
    logger.info("re-encrypted %d plaintext cookie value(s) from a pre-17.1 "
                "sessions.db", len(rows))


def _hosts_of(url: str) -> str:
    """The URL's host, lowercased and port-stripped ('' when unparseable)."""
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
    # Sessions are ranked by the newest cookie they hold, so the ones still in
    # use are the ones that survive; an abandoned login dies as a whole instead
    # of losing half its cookies (a half-cleared jar is a broken session).
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
    """Remember the cookies a host just set. Returns how many were stored.

    ``cookies`` is the name->value dict ``Response.cookies`` already gives us
    (attributes are not available there — see the module docstring).
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
                    # Values are credentials; on disk they are only ever the
                    # encrypted form. Memory mode stores plaintext because RAM
                    # is what everything else in the process already is.
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
    """``{name: value}`` the jar holds for exactly this URL's host ('' if none)."""
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
    """Cookie NAMES held for this host. Values are deliberately not returned."""
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


def sessions() -> dict:
    """A name-only census: ``{session_id: {"hosts": {host: [names]},
    "cookies": n, "expires_in_s": s}}``.

    Diagnostics and the ``sys_state`` census are the consumers, and both need
    the same answer to a different question: which ids hold credentials, for
    which hosts, and how long before they lapse on their own. So the soonest
    expiry is reported as a countdown rather than an epoch float — "expires in
    21h" is actionable ("let it ride"), a raw ``1790566780.9`` is not.

    Values are never in here. A cookie is a credential and every field of a tool
    response ends up in the agent's transcript.
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
    out: dict = {}
    for sid, host, name, expires_at in rows:
        entry = out.setdefault(sid, {"hosts": {}, "cookies": 0, "expires_in_s": 0})
        entry["hosts"].setdefault(host, []).append(name)
        entry["cookies"] += 1
        left = int(expires_at - now)
        if not entry["expires_in_s"] or left < entry["expires_in_s"]:
            entry["expires_in_s"] = max(0, left)
    return out


def clear(session_id: str = "", now: float | None = None) -> int:
    """Drop one session's cookies, or the whole jar when ``session_id`` is empty."""
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
    """Close the module-level connection so a test can redirect ``DHOLE_HOME``.

    The DB path is read lazily, so a fixture that sets a throwaway home after the
    first use would otherwise keep writing to the real file.
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
