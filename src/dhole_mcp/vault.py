"""凭据来源读取（计划第七章「凭据来源」）。

**不自造 vault。** 读用户**已有的**密码管理器 CLI：

* **Bitwarden** —— ``bw list items --search <q>`` / ``bw get item <ref>`` /
  ``bw status``
* **1Password** —— ``op item list --format json`` / ``op item get <ref>
  --format json`` / ``op whoami``

红线（计划安全红线 2，与 :mod:`dhole_mcp.sessions` 同标准）：**凭据值永不进
响应 / log / cache**。这条不靠调用方自觉，靠 API 形状：

* :func:`list_items` / :func:`match_for_url` / :func:`status` 只回**元数据** ——
  条目名、用户名、有无密码、有无 TOTP、关联 URL。**密码与 TOTP 的值在任何 op
  里都不出现。**
* :func:`secret_for` 回值，但它是**登录流内部**的接口：``server.py`` 的
  ``login`` 方法拿到值后直接构造浏览器 fill 动作，用完即弃。值不进任何
  ResponseModel、不进 ``metadata.actions`` 回执、不进 ``DHOLE_USAGE_LOG`` 审计
  行、不进 cache.db。

``vault`` 工具因此**没有**能返回值的方法被暴露出来 —— "凭据不出境"由接口形状
保证，而不是由每个调用点记得脱敏。

**边界（明说）**：用户名算标识符、不算秘密，所以 :func:`list_items` 会回它 ——
一个没有用户名的凭据选择器没法用（同一个站的两个账号靠它区分）。密码与 TOTP
是秘密，任何 op 都不回。

会话令牌（``BW_SESSION`` / ``OP_SERVICE_ACCOUNT_TOKEN`` / ``OP_SESSION_*``）
从**进程环境继承**，永不进 argv —— argv 对同机其它进程可见。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from urllib.parse import urlparse

logger = logging.getLogger("dhole-mcp.vault")

# CLI 调用上限。密码管理器 CLI 可能要解锁 / 生物识别，慢；但一次工具调用不能被
# 它无限挂住。
CLI_TIMEOUT_S = 20.0
# 一次列回的条目上限：一个密码管理器动辄上千条，全量回给 agent 没有意义。
MAX_ITEMS = 300
# 关联 URL 每条目回几个。
MAX_URIS = 5

SOURCES = ("bitwarden", "onepassword")
_BINARY = {"bitwarden": "bw", "onepassword": "op"}
_LABEL = {"bitwarden": "Bitwarden CLI (bw)", "onepassword": "1Password CLI (op)"}
_INSTALL_HINT = {
    "bitwarden": "install the Bitwarden CLI (bw), then unlock: "
                 "export BW_SESSION=\"$(bw unlock --raw)\"",
    "onepassword": "install the 1Password CLI (op), then sign in (op signin) or "
                   "set OP_SERVICE_ACCOUNT_TOKEN",
}


def _which(source: str) -> str | None:
    """Resolved path of a manager's CLI, or None when it is not installed."""
    name = _BINARY.get(source)
    return shutil.which(name) if name else None


def _scrub(text: str) -> str:
    """Strip session tokens out of a string that is about to be returned.

    ``bw`` / ``op`` error text occasionally echoes the session key verbatim, and
    that string ends up in the tool response — i.e. in the agent's transcript.
    Replacing every token we know about is cheaper than trusting "it probably
    won't".
    """
    if not text:
        return text
    for key, val in os.environ.items():
        if not val or len(val) < 8:
            continue
        if key == "BW_SESSION" or key == "OP_SERVICE_ACCOUNT_TOKEN" \
                or key.startswith("OP_SESSION_"):
            text = text.replace(val, "***")
    return text


def _run(source: str, args: list[str],
         timeout: float = CLI_TIMEOUT_S) -> tuple[int, str, str]:
    """Run a manager CLI. Returns ``(returncode, stdout, stderr)``; never raises.

    ``-1`` means "could not start" (missing binary, timeout, spawn failure). The
    environment is inherited on purpose — the unlock token lives there, and
    putting it on argv would publish it to every process on the machine.

    Callers must treat ``stdout`` as potentially secret (``bw get item`` /
    ``op item get`` print the password): it is fine to parse, never to log or to
    splice into an error message.
    """
    path = _which(source)
    if not path:
        return -1, "", f"{_LABEL.get(source, source)} not found on PATH"
    argv = [path, *args]
    if os.name == "nt" and path.lower().endswith((".cmd", ".bat")):
        # npm-installed `bw` / `op` ship as .cmd shims, which CreateProcess
        # cannot start directly. Go through the command processor.
        argv = [os.environ.get("COMSPEC", "cmd.exe"), "/c", *argv]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, env=os.environ.copy(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return -1, "", f"{type(e).__name__}: {e}"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _first_line(text: str, cap: int = 160) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return _scrub(line.strip())[:cap]
    return ""


# ─── status ──────────────────────────────────────────────────────────────────


def _bw_status() -> tuple[bool, str]:
    rc, out, err = _run("bitwarden", ["status"])
    if rc != 0:
        return False, _first_line(err) or "bw status failed"
    try:
        data = json.loads(out)
    except ValueError:
        return False, "bw status did not return JSON"
    state = str((data or {}).get("status") or "").strip().lower()
    return state == "unlocked", state or "unknown"


def _op_status() -> tuple[bool, str]:
    rc, out, err = _run("onepassword", ["whoami", "--format", "json"])
    if rc != 0:
        return False, _first_line(err) or "not signed in"
    return True, "signed in"


def status(source: str = "auto") -> list[dict]:
    """Per-manager availability + lock state. Never touches an item's values.

    ``unlocked`` is ``None`` when the CLI is not installed (we did not check, so
    saying False would turn "not asked" into "locked").
    """
    out: list[dict] = []
    for s in _sources(source):
        if s not in SOURCES:
            out.append({"source": s, "available": False, "unlocked": None,
                        "detail": f"unknown source {s!r}; use {list(SOURCES)} "
                                  "or 'auto'"})
            continue
        if not _which(s):
            out.append({"source": s, "available": False, "unlocked": None,
                        "detail": f"{_LABEL[s]} not found on PATH",
                        "hint": _INSTALL_HINT[s]})
            continue
        unlocked, detail = _bw_status() if s == "bitwarden" else _op_status()
        row = {"source": s, "available": True, "unlocked": unlocked,
               "detail": detail}
        if not unlocked:
            row["hint"] = _INSTALL_HINT[s]
        out.append(row)
    return out


def _sources(source: str) -> tuple[str, ...]:
    s = (source or "").strip().lower()
    return SOURCES if s in ("", "auto") else (s,)


# ─── listing (metadata only) ─────────────────────────────────────────────────


def _bw_item_meta(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    iid = str(raw.get("id") or "")
    if not iid:
        return None
    login = raw.get("login") if isinstance(raw.get("login"), dict) else {}
    uris = [u.get("uri") for u in (login.get("uris") or [])
            if isinstance(u, dict) and u.get("uri")]
    return {
        "id": iid,
        "name": str(raw.get("name") or "")[:120],
        "username": str(login.get("username") or "")[:120],
        "has_password": bool(login.get("password")),
        "has_totp": bool(login.get("totp")),
        "uris": [str(u) for u in uris[:MAX_URIS]],
        "source": "bitwarden",
    }


def _bw_list(query: str) -> tuple[list[dict], str]:
    args = ["list", "items"]
    if query.strip():
        args += ["--search", query.strip()]
    rc, out, err = _run("bitwarden", args)
    if rc != 0:
        msg = _first_line(err) or "unknown error"
        note = f"bw list items failed: {msg}"
        if "lock" in msg.lower():
            note += " - the vault is locked; " + _INSTALL_HINT["bitwarden"]
        return [], note
    try:
        data = json.loads(out)
    except ValueError:
        return [], "bw list items did not return JSON"
    if not isinstance(data, list):
        return [], "bw list items returned an unexpected shape"
    return [m for m in (_bw_item_meta(r) for r in data) if m], ""


def _op_list(query: str) -> tuple[list[dict], str]:
    rc, out, err = _run("onepassword", ["item", "list", "--format", "json"])
    if rc != 0:
        msg = _first_line(err) or "unknown error"
        return [], f"op item list failed: {msg} - is the CLI signed in?"
    try:
        data = json.loads(out)
    except ValueError:
        return [], "op item list did not return JSON"
    if not isinstance(data, list):
        return [], "op item list returned an unexpected shape"
    q = query.strip().lower()
    items: list[dict] = []
    for r in data:
        if not isinstance(r, dict):
            continue
        iid = str(r.get("id") or "")
        title = str(r.get("title") or "")
        if not iid or (q and q not in title.lower()):
            continue
        urls = [u.get("href") for u in (r.get("urls") or [])
                if isinstance(u, dict) and u.get("href")]
        items.append({
            "id": iid,
            "name": title[:120],
            # `op item list` does not carry field values, so "has a password" is
            # UNKNOWN at this layer, not absent. None says "not checked"; False
            # would be the same failure class as reporting "no change" for a
            # watch that never had a baseline.
            "username": "",
            "has_password": None,
            "has_totp": None,
            "uris": [str(u) for u in urls[:MAX_URIS]],
            "source": "onepassword",
        })
    return items, ""


def list_items(query: str = "", source: str = "auto",
               limit: int = MAX_ITEMS) -> dict:
    """Credential **metadata** from the available managers.

    Returns ``{"items": [...], "total": n, "truncated": bool, "notes": [...]}``.
    No password or TOTP value is ever in here — only whether one exists.
    """
    items: list[dict] = []
    notes: list[str] = []
    for s in _sources(source):
        if s not in SOURCES:
            notes.append(f"unknown source {s!r}; use {list(SOURCES)} or 'auto'")
            continue
        if not _which(s):
            notes.append(f"{_LABEL[s]} not found on PATH - {_INSTALL_HINT[s]}")
            continue
        got, note = _bw_list(query) if s == "bitwarden" else _op_list(query)
        items.extend(got)
        if note:
            notes.append(note)
    total = len(items)
    return {"items": items[:max(1, limit)], "total": total,
            "truncated": total > max(1, limit), "notes": notes}


def _host_of(url: str) -> str:
    """Lowercased host of a URL, tolerating a scheme-less vault URI."""
    if not url:
        return ""
    u = str(url).strip()
    if "//" not in u:
        u = "https://" + u
    try:
        host = urlparse(u).netloc.lower()
    except Exception:
        return ""
    if "@" in host:
        host = host.split("@", 1)[1]
    if ":" in host:
        host = host.split(":", 1)[0]
    return host


def _host_matches(a: str, b: str) -> bool:
    """True when one host is the other or a subdomain of it.

    ``app.example.com`` matches an item stored for ``example.com`` — the vault
    URI is whatever the user saved, and demanding an exact host means missing
    the login on ``www.`` and ``app.`` every time. Suffix matching is bounded by
    a dot so ``notexample.com`` does NOT match ``example.com``.
    """
    if not a or not b:
        return False
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def match_for_url(url: str, source: str = "auto") -> dict:
    """Metadata for the credentials whose stored URI is this URL's host."""
    host = _host_of(url)
    if not host:
        return {"items": [], "total": 0, "host": "",
                "notes": ["could not parse a host from that URL"]}
    listed = list_items("", source)
    hits: list[dict] = []
    for it in listed["items"]:
        for u in it.get("uris") or []:
            if _host_matches(_host_of(u), host):
                hits.append({**it, "matched_uri": u})
                break
    return {"items": hits, "total": len(hits), "host": host,
            "notes": listed["notes"]}


# ─── secret (internal to the login flow) ─────────────────────────────────────


def _bw_secret(ref: str) -> dict | None:
    rc, out, err = _run("bitwarden", ["get", "item", ref])
    if rc != 0:
        return None                      # not this manager's item; try the next
    try:
        raw = json.loads(out)
    except ValueError:
        # `out` may be the password itself for other bw verbs - never echo it.
        return {"error": "bw get item returned something that is not JSON"}
    if not isinstance(raw, dict):
        return {"error": "bw get item returned an unexpected shape"}
    name = str(raw.get("name") or ref)[:120]
    login = raw.get("login") if isinstance(raw.get("login"), dict) else {}
    if not login.get("password"):
        return {"error": f"item {name!r} has no stored password"}
    return {"username": str(login.get("username") or ""),
            "password": str(login["password"]),
            "totp": str(login.get("totp") or ""),
            "item": name, "source": "bitwarden"}


def _op_secret(ref: str) -> dict | None:
    rc, out, err = _run("onepassword", ["item", "get", ref, "--format", "json"])
    if rc != 0:
        return None
    try:
        raw = json.loads(out)
    except ValueError:
        return {"error": "op item get returned something that is not JSON"}
    if not isinstance(raw, dict):
        return {"error": "op item get returned an unexpected shape"}
    name = str(raw.get("title") or ref)[:120]
    username = password = totp = ""
    concealed = ""
    for f in raw.get("fields") or []:
        if not isinstance(f, dict) or f.get("value") is None:
            continue
        key = str(f.get("id") or f.get("label") or "").lower()
        val = str(f["value"])
        if not username and key in ("username", "user", "email"):
            username = val
        elif not totp and key in ("totp", "otp", "one-time password", "onetimepassword"):
            totp = val
        elif not password and key in ("password", "pass"):
            password = val
        elif not concealed and str(f.get("type") or "") == "CONCEALED":
            concealed = val
    password = password or concealed
    if not password:
        return {"error": f"item {name!r} has no stored password"}
    return {"username": username, "password": password, "totp": totp,
            "item": name, "source": "onepassword"}


def secret_for(ref: str, source: str = "auto") -> dict:
    """**Internal API**: resolve one credential to its values.

    Called only by ``server.py``'s ``login`` method. The returned dict must never
    be placed into a ResponseModel — the caller builds a browser ``fill`` from it
    and drops it.

    Failure returns ``{"error": ...}`` and the error text **never contains
    stdout** — for a password-reading command, stdout IS the password.
    """
    ref = (ref or "").strip()
    if not ref:
        return {"error": "credential reference is empty"}
    srcs = _sources(source)
    for s in srcs:
        if s not in SOURCES:
            return {"error": f"unknown source {s!r}; use {list(SOURCES)} or 'auto'"}
    # "Nothing is installed" is a different answer from "no item by that name",
    # and the first one needs the install step, not a list of managers that were
    # never consulted.
    installed = [s for s in srcs if _which(s)]
    if not installed:
        return {"error": "no credential manager is available - "
                         + "; ".join(_INSTALL_HINT[s] for s in srcs)}
    tried: list[str] = []
    for s in installed:
        got = _bw_secret(ref) if s == "bitwarden" else _op_secret(ref)
        if got is not None:
            return got
        tried.append(f"{s} (no item {ref!r})")
    return {"error": f"no credential named {ref!r} found via " + ", ".join(tried)}
