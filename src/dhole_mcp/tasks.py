"""Saved repeat work: recipes (L3, record & replay) and watches (L4, monitor & diff).

计划把编排轴的最后两级定成"复现"与"监控"：

| L3 | 复现：recipe / workflow 录制回放 | 待建 |
| L4 | 监控：watch / diff | 待建 |

两级共用一个本地库 ``~/.dhole/tasks.db``（0600 目录/文件，与 ``sessions.db``
同信任级别），因为它们是同一件事的两半：**recipe 存"怎么做"，watch 存"盯着哪、
上次看到什么"**。分成两个库只会多一份连接与迁移逻辑。

设计上刻意留下的边界
--------------------
* **recipe 的动作在录制时就过执行器的验证**（``actions.validate_actions``）。
  存一个执行器会拒绝的动作，等于存一个永远不会成功的配方——比没有更糟，
  因为调用方会以为"存下来了就是对的"。
* **watch 的"没有基线"不等于"没有变化"。** 第一次 check 只建立基线，返回
  ``changed=null``；把首跑报成"有变化"是凭空造一个事实出来。
* **快照有上限**（``MAX_SNAPSHOT_CHARS``）：watch 长期运行，无上限的快照会让
  这个库长成一个没人管的正文归档。
* 这里**不存凭据**。recipe / watch 只记 URL 与动作，``session_id`` 由调用方在
  每次 run/check 时传入（与 sessions.py 的"值不出境"同纪律）。
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger("dhole-mcp.tasks")

# Bound the library so a long-lived agent cannot grow it without limit. The
# oldest-updated row goes first (a recipe nobody ran in months is the one to
# lose), and the cap is reported rather than silently enforced — see save_*.
MAX_RECIPES = 200
MAX_WATCHES = 200

# A watch baseline is a diff reference, not an archive. 200k chars is ~50k
# tokens of page text: past that the stored snapshot costs more than the diff
# is worth, and the caller is told it was cut.
MAX_SNAPSHOT_CHARS = 200_000

# One diff answer is capped too: a page that rewrote itself entirely produces a
# diff as long as the page, which is the whole document again with +/- in front.
MAX_DIFF_LINES = 200

NAME_MAX = 64
NOTE_MAX = 500

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _db_path() -> str:
    from dhole_mcp import paths
    return str(paths.file("tasks.db"))


def _connect() -> sqlite3.Connection:
    """Open (and create) the library. Caller holds :data:`_lock`."""
    global _conn
    if _conn is not None:
        return _conn
    from dhole_mcp import paths
    db = _db_path()
    paths.ensure_private_dir(os.path.dirname(db) or ".")
    _conn = sqlite3.connect(db, check_same_thread=False)
    _conn.execute(
        "CREATE TABLE IF NOT EXISTS recipes ("
        "  name TEXT PRIMARY KEY,"
        "  url TEXT NOT NULL,"
        "  actions TEXT NOT NULL,"          # JSON list, already validated
        "  note TEXT NOT NULL DEFAULT '',"
        "  signature TEXT NOT NULL DEFAULT '',"
        "  created_at REAL NOT NULL,"
        "  used_at REAL NOT NULL DEFAULT 0,"
        "  use_count INTEGER NOT NULL DEFAULT 0,"
        "  last_ok INTEGER NOT NULL DEFAULT 0,"
        "  last_error TEXT NOT NULL DEFAULT '')"
    )
    _conn.execute(
        "CREATE TABLE IF NOT EXISTS watches ("
        "  name TEXT PRIMARY KEY,"
        "  url TEXT NOT NULL,"
        "  focus TEXT NOT NULL DEFAULT '',"
        "  max_chars INTEGER NOT NULL DEFAULT 0,"
        "  note TEXT NOT NULL DEFAULT '',"
        "  baseline TEXT NOT NULL DEFAULT '',"
        "  baseline_at REAL NOT NULL DEFAULT 0,"
        "  baseline_cut INTEGER NOT NULL DEFAULT 0,"
        "  check_count INTEGER NOT NULL DEFAULT 0,"
        "  change_count INTEGER NOT NULL DEFAULT 0,"
        "  checked_at REAL NOT NULL DEFAULT 0,"
        "  last_error TEXT NOT NULL DEFAULT '')"
    )
    _conn.commit()
    paths.harden_file(db)
    return _conn


def reset_connection() -> None:
    """Drop the cached connection (tests, and after a DB file is removed)."""
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None


# ── 通用 ────────────────────────────────────────────────────────────────

def clean_name(name) -> str:
    """A recipe/watch name: non-empty, bounded, no whitespace-only."""
    n = (name or "").strip() if isinstance(name, str) else ""
    if not n:
        raise ValueError("name must be a non-empty string")
    if len(n) > NAME_MAX:
        raise ValueError(f"name is too long ({len(n)} chars, max {NAME_MAX})")
    return n


def _clean_note(note) -> str:
    n = (note or "").strip() if isinstance(note, str) else ""
    return n[:NOTE_MAX]


def _iso(ts) -> str:
    """Epoch float -> the ISO-8601 UTC string every other response field uses.

    A raw 1.7e9 in a listing reads as noise next to `fetched_at`; the listing is
    read by an agent that has to decide whether a recipe is stale, so the
    timestamp has to look like the ones it already knows how to compare.
    """
    try:
        t = float(ts or 0)
    except (TypeError, ValueError):
        return ""
    if t <= 0:
        return ""
    return datetime.fromtimestamp(t, timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def _prune(conn: sqlite3.Connection, table: str, cap: int, order_col: str) -> None:
    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    if n > cap:
        # 按"最近被用过"排序：很久没碰的那个才是该丢的。table/order_col 是
        # 模块内的字面量常量，不是外部输入。
        conn.execute(
            f"DELETE FROM {table} WHERE name IN ("
            f"  SELECT name FROM {table} ORDER BY {order_col} ASC LIMIT ?)",
            (n - cap,),
        )


# ── recipe（L3）──────────────────────────────────────────────────────────

def save_recipe(name, url, actions, note="") -> dict:
    """Record a replayable (url, actions) pair.

    ``actions`` is validated by the SAME validator the executor uses
    (``actions.validate_actions``), so a stored recipe can never contain an
    action that would be refused at run time. Raises ValueError on bad input —
    the caller (the tool) turns that into an envelope, it is not swallowed.
    """
    from dhole_mcp.actions import validate_actions

    n = clean_name(name)
    u = (url or "").strip() if isinstance(url, str) else ""
    if not u:
        raise ValueError("url must be a non-empty string")
    validated = validate_actions(actions)          # raises on a bad chain
    payload = json.dumps(validated, ensure_ascii=False)
    ts = time.time()
    with _lock:
        conn = _connect()
        existed = conn.execute(
            "SELECT created_at, use_count FROM recipes WHERE name=?", (n,)
        ).fetchone()
        # 覆盖保存保留使用统计：重录一条配方是修它，不是把它变成新配方。
        created = existed[0] if existed else ts
        uses = existed[1] if existed else 0
        conn.execute(
            "INSERT INTO recipes (name, url, actions, note, signature, created_at,"
            " used_at, use_count, last_ok, last_error)"
            " VALUES (?,?,?,?,'',?,0,?,0,'')"
            " ON CONFLICT(name) DO UPDATE SET url=excluded.url,"
            "   actions=excluded.actions, note=excluded.note",
            (n, u, payload, _clean_note(note), created, uses),
        )
        _prune(conn, "recipes", MAX_RECIPES, "used_at")
        conn.commit()
    return {"name": n, "url": u, "actions": len(validated), "updated": bool(existed)}


def get_recipe(name) -> dict | None:
    n = clean_name(name)
    with _lock:
        conn = _connect()
        row = conn.execute(
            "SELECT name, url, actions, note, signature, created_at, used_at,"
            " use_count, last_ok, last_error FROM recipes WHERE name=?", (n,)
        ).fetchone()
    if row is None:
        return None
    return {
        "name": row[0], "url": row[1], "actions": json.loads(row[2]),
        "note": row[3], "signature": row[4], "created_at": row[5],
        "used_at": row[6], "use_count": row[7],
        "last_ok": bool(row[8]), "last_error": row[9],
    }


def list_recipes() -> list[dict]:
    with _lock:
        conn = _connect()
        rows = conn.execute(
            "SELECT name, url, actions, note, use_count, last_ok, last_error,"
            " created_at, used_at FROM recipes ORDER BY used_at DESC, created_at DESC"
        ).fetchall()
    out = []
    for r in rows:
        try:
            n_actions = len(json.loads(r[2]))
        except Exception:
            n_actions = 0
        out.append({
            "name": r[0], "url": r[1], "actions": n_actions, "note": r[3],
            "use_count": r[4], "last_ok": bool(r[5]), "last_error": r[6],
            "created_at": _iso(r[7]), "used_at": _iso(r[8]),
        })
    return out


def delete_recipe(name) -> bool:
    n = clean_name(name)
    with _lock:
        conn = _connect()
        cur = conn.execute("DELETE FROM recipes WHERE name=?", (n,))
        conn.commit()
        return cur.rowcount > 0


def note_use(name, ok: bool, error: str = "", signature: str = "") -> None:
    """Record the outcome of one replay. Best-effort: a stats write must never
    turn a successful fetch into a failed tool call."""
    try:
        n = clean_name(name)
        with _lock:
            conn = _connect()
            conn.execute(
                "UPDATE recipes SET used_at=?, use_count=use_count+1, last_ok=?,"
                " last_error=?, signature=CASE WHEN ?='' THEN signature ELSE ? END"
                " WHERE name=?",
                (time.time(), 1 if ok else 0, (error or "")[:300],
                 signature, signature, n),
            )
            conn.commit()
    except Exception as e:
        logger.debug("recipe use-note failed for %s: %s", name, e)


# ── watch（L4）───────────────────────────────────────────────────────────

def save_watch(name, url, focus="", max_chars=0, note="") -> dict:
    n = clean_name(name)
    u = (url or "").strip() if isinstance(url, str) else ""
    if not u:
        raise ValueError("url must be a non-empty string")
    f = (focus or "").strip() if isinstance(focus, str) else ""
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 0:
        raise ValueError("max_chars must be a non-negative int (0 = the built-in cap)")
    with _lock:
        conn = _connect()
        existed = conn.execute("SELECT baseline, baseline_at, baseline_cut,"
                               " check_count, change_count FROM watches"
                               " WHERE name=?", (n,)).fetchone()
        if existed is None:
            conn.execute(
                "INSERT INTO watches (name, url, focus, max_chars, note, baseline,"
                " baseline_at, baseline_cut, check_count, change_count, checked_at,"
                " last_error) VALUES (?,?,?,?,?,'',0,0,0,0,0,'')",
                (n, u, f, max_chars, _clean_note(note)),
            )
            kept = {"baseline_kept": False, "check_count": 0, "change_count": 0}
        else:
            # 改 URL / focus 会让旧基线失去意义——留着它，下一次 check 就会拿
            # 一个"别的页面"的快照去比，把整页都报成新增。所以换靶就清基线。
            changed_target = conn.execute(
                "SELECT url, focus, max_chars FROM watches WHERE name=?", (n,)
            ).fetchone()
            same = (changed_target[0] == u and changed_target[1] == f
                    and changed_target[2] == max_chars)
            if same:
                conn.execute("UPDATE watches SET note=? WHERE name=?",
                             (_clean_note(note), n))
                kept = {"baseline_kept": bool(existed[0]),
                        "check_count": existed[3], "change_count": existed[4]}
            else:
                conn.execute(
                    "UPDATE watches SET url=?, focus=?, max_chars=?, note=?,"
                    " baseline='', baseline_at=0, baseline_cut=0,"
                    " check_count=0, change_count=0, checked_at=0, last_error=''"
                    " WHERE name=?", (u, f, max_chars, _clean_note(note), n))
                kept = {"baseline_kept": False, "check_count": 0, "change_count": 0}
        _prune(conn, "watches", MAX_WATCHES, "checked_at")
        conn.commit()
    return {"name": n, "url": u, "focus": f, "max_chars": max_chars,
            "updated": bool(existed), **kept}


def get_watch(name) -> dict | None:
    n = clean_name(name)
    with _lock:
        conn = _connect()
        row = conn.execute(
            "SELECT name, url, focus, max_chars, note, baseline, baseline_at,"
            " baseline_cut, check_count, change_count, checked_at, last_error"
            " FROM watches WHERE name=?", (n,)
        ).fetchone()
    if row is None:
        return None
    return {
        "name": row[0], "url": row[1], "focus": row[2], "max_chars": row[3],
        "note": row[4], "baseline": row[5], "baseline_at": row[6],
        "baseline_cut": bool(row[7]), "check_count": row[8],
        "change_count": row[9], "checked_at": row[10], "last_error": row[11],
    }


def list_watches() -> list[dict]:
    with _lock:
        conn = _connect()
        rows = conn.execute(
            "SELECT name, url, focus, length(baseline), baseline_at, check_count,"
            " change_count, checked_at, last_error, note FROM watches"
            " ORDER BY checked_at DESC, name ASC"
        ).fetchall()
    return [
        {"name": r[0], "url": r[1], "focus": r[2], "baseline_chars": r[3],
         "baseline_at": _iso(r[4]), "check_count": r[5], "change_count": r[6],
         "checked_at": _iso(r[7]), "last_error": r[8], "note": r[9]}
        for r in rows
    ]


def delete_watch(name) -> bool:
    n = clean_name(name)
    with _lock:
        conn = _connect()
        cur = conn.execute("DELETE FROM watches WHERE name=?", (n,))
        conn.commit()
        return cur.rowcount > 0


def set_baseline(name, text: str, cut: bool = False) -> None:
    n = clean_name(name)
    with _lock:
        conn = _connect()
        conn.execute(
            "UPDATE watches SET baseline=?, baseline_at=?, baseline_cut=?,"
            " checked_at=? WHERE name=?",
            (text[:MAX_SNAPSHOT_CHARS], time.time(), 1 if cut else 0, time.time(), n),
        )
        conn.commit()


def note_check(name, changed: bool | None, error: str = "") -> None:
    """Record one check. ``changed=None`` means the baseline was just created —
    it is NOT a change, so ``change_count`` must not move."""
    try:
        n = clean_name(name)
        with _lock:
            conn = _connect()
            conn.execute(
                "UPDATE watches SET check_count=check_count+1,"
                " change_count=change_count+?, checked_at=?, last_error=?"
                " WHERE name=?",
                (1 if changed else 0, time.time(), (error or "")[:300], n),
            )
            conn.commit()
    except Exception as e:
        logger.debug("watch check-note failed for %s: %s", name, e)


def diff_text(old: str, new: str, max_lines: int = MAX_DIFF_LINES) -> dict:
    """Line diff of two snapshots, capped, with the cap reported.

    ``added`` / ``removed`` count the real +/- lines even when the rendered diff
    is cut, so a caller that only reads the counts still learns the size of the
    change. A cut diff says so rather than looking complete.
    """
    old_lines = (old or "").splitlines()
    new_lines = (new or "").splitlines()
    added = removed = 0
    rendered: list[str] = []
    truncated = False
    for line in difflib.unified_diff(old_lines, new_lines, lineterm="", n=1):
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
        else:
            continue
        if len(rendered) < max_lines:
            rendered.append(line[:400])
        else:
            truncated = True
    return {"added": added, "removed": removed, "diff": rendered,
            "diff_truncated": truncated}
