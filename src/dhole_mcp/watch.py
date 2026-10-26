"""给 ``web_read(extraction_type='check')`` 用的持久 watchlist。

MCP 工具一次只回答一颗 URL「这页变了吗」。agent 可以反复调用，但想要每晚 / 每 30
分钟一次信号的人，需要一份能跨重启的清单，以及一个 cron 能按 0/1/2 分岔的命令。
本模块就是这份存储与执行器；抓取本身仍走 ``server._check_read`` 那一条检查路
（由 ``web_read`` 调进来），所以 watch 继承同一套基线、ETag 处理、diff 窗口与
robots/SSRF 闸。

状态是 ``~/.dhole`` 下一个仅属主的 JSON 文件（``DHOLE_HOME`` 可移动它）。它只存
URL 与判词，绝不存页面正文或凭据。第一次跑一颗 URL 会记录基线
（``verdict=baseline``），不算变化；第二次及以后才回答 changed/unchanged。
诚实降级比制造告警重要：404 或被拦的抓取是一行 error，绝不是 "unchanged"。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Optional
from urllib.parse import urlsplit

from dhole_mcp import paths


class WatchStateError(Exception):
    """watchlist 文件存在，但读不成一份 watchlist。"""


class WatchBusy(Exception):
    """另一个 ``watch run`` 正持着运行锁。"""


def state_path(path: Optional[Path | str] = None) -> Path:
    """Where the watchlist file lives.

    Args:
        path: An explicit override (tests, alternate state directories). ``None`` means
            ``paths.file("watchlist.json")`` and moves with ``DHOLE_HOME``.

    Returns:
        The path to use; the file need not exist yet.
    """
    return Path(path) if path is not None else paths.file("watchlist.json")


def _empty() -> dict:
    return {"version": 1, "watches": []}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state(path: Optional[Path | str] = None) -> dict:
    """Read and validate the watchlist.

    Args:
        path: An override passed through to :func:`state_path`.

    Returns:
        ``{"version": int, "watches": list[dict]}``. A missing file means an empty list,
        not an error.

    Raises:
        WatchStateError: The file exists but its JSON cannot be read, is not an object with
            a ``watches`` list, or has a row without a non-empty URL. The error names the
            file so the caller can fix or delete it; nothing is guessed, because a wrong
            guess silently drops watches.
    """
    p = state_path(path)
    if not p.exists():
        return _empty()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WatchStateError(
            f"{p.name} is not readable JSON ({exc.__class__.__name__}); fix or "
            "remove it - dhole will not guess what was being watched") from exc
    if not isinstance(data, dict) or not isinstance(data.get("watches"), list):
        raise WatchStateError(
            f"{p.name} does not have the shape dhole writes: a JSON object with "
            "a 'watches' list")
    for row in data["watches"]:
        if not isinstance(row, dict) or not isinstance(row.get("url"), str) \
                or not row["url"]:
            raise WatchStateError(
                f"{p.name} has a watch row without a URL; fix or remove it by hand")
    data.setdefault("version", 1)
    return data


def _save(state: dict, path: Optional[Path | str] = None) -> Path:
    p = state_path(path)
    paths.ensure_private_dir(p.parent)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
    paths.harden_file(p)
    return p


def _clean_url(url: str) -> str:
    from dhole_mcp.security import SecurityError, validate_url

    try:
        # 只做语法/scheme 检查：这台机器能不能**抓**私网目标，由每次调用时同一道
        # SSRF 闸决定；被拦的目标必须在 `run` 里落成 error 行，而不是在 add 时另立
        # 一条与它不一致的规则。
        url = validate_url(url, allow_internal=True)
    except SecurityError as exc:
        raise ValueError(f"not a URL dhole can watch: {exc}") from exc
    parts = urlsplit(url)
    if parts.username or parts.password:
        raise ValueError(
            "not a URL dhole can watch: it carries user:password@ credentials, "
            "and the watchlist is a plaintext local file. Store credentials with "
            "cred_defaults and watch the bare URL")
    # fragment 到不了源站，只会把缓存基线分叉。
    return url.split("#", 1)[0]


def add(url: str, label: str = "", path: Optional[Path | str] = None) -> dict:
    """Add a URL to the watchlist; an existing entry only gets its label updated.

    Args:
        url: An absolute http(s) URL. The fragment is stripped (it never reaches the origin
            and would fork the baseline); embedded ``user:password@`` credentials are
            refused because this file is plaintext.
        label: A human label. An empty string keeps the old label for a URL already in the
            list, and stores an empty label for a new one.
        path: An override passed through to :func:`state_path`.

    Returns:
        ``{"created": bool, "watch": row}`` — ``created`` is False when the URL was already
        listed.

    Raises:
        ValueError: The URL cannot be watched, or carries embedded credentials.
        WatchStateError: The existing state file could not be read.
    """
    url = _clean_url(url)
    state = load_state(path)
    row = next((w for w in state["watches"] if w["url"] == url), None)
    if row is None:
        row = {
            "url": url,
            "label": label.strip(),
            "added_at": _now(),
            "checks": 0,
            "last_checked_at": "",
            "last_verdict": "",
            "last_status": 0,
            "last_changed_at": "",
            "last_error": "",
        }
        state["watches"].append(row)
        created = True
    else:
        row["label"] = label.strip() or row.get("label", "")
        created = False
    _save(state, path)
    return {"created": created, "watch": row}


def remove(url: str, path: Optional[Path | str] = None) -> bool:
    """Forget a watched URL.

    Args:
        url: The exact stored URL (the fragment was already stripped by :func:`add`).
        path: An override passed through to :func:`state_path`.

    Returns:
        True when a row was deleted, False when the URL was not on the list.
    """
    state = load_state(path)
    before = len(state["watches"])
    state["watches"] = [w for w in state["watches"] if w["url"] != url]
    if len(state["watches"]) == before:
        return False
    _save(state, path)
    return True


def _verdict(row: dict) -> str:
    if row.get("changed") is True:
        return "changed"
    if row.get("changed") is False:
        return "unchanged"
    status = int(row.get("status") or 0)
    if row.get("error") or status == 0 or status >= 400:
        return "error"
    if 200 <= status < 400:
        return "baseline"
    return "error"


def _row_error(row: dict) -> str:
    if row.get("error"):
        return str(row["error"])
    status = int(row.get("status") or 0)
    return f"HTTP {status} without a changed/unchanged verdict"


async def run(checker: Callable[[str], Awaitable[dict]], *,
              path: Optional[Path | str] = None,
              only: Optional[Iterable[str]] = None,
              on_row: Optional[Callable[[dict], None]] = None) -> dict:
    """把每颗被 watch 的 URL 检查一次，逐行落盘，返回回执。

    ``checker(url)`` 必须交回单 URL 检查的 dict（``changed``、``status``，可选的
    ``error``/``note``）。每颗 URL 之后都会保存，所以中断的扫描保留已经拿到的判词。
    扫描途中被 remove 掉的 URL 不会被写回来。
    """
    state = load_state(path)
    watched = {w["url"]: w for w in state["watches"]}
    wanted = [u for u in (only if only is not None else watched) if u in watched]
    unknown = [u for u in (only or []) if u not in watched]
    rows: list[dict] = []
    for url in wanted:
        try:
            raw = await checker(url)
        except Exception as exc:  # 一颗 URL 死掉不该终止整轮扫描
            raw = {"changed": None, "status": 0,
                   "error": f"{exc.__class__.__name__}: {exc}"}
        verdict = _verdict(raw)
        checked_at = _now()
        row = {
            "url": url,
            "label": watched[url].get("label", ""),
            "verdict": verdict,
            "status": int(raw.get("status") or 0),
            "changed": raw.get("changed"),
            "note": raw.get("note", ""),
            "error": _row_error(raw) if verdict == "error" else "",
            "checked_at": checked_at,
            "diff_window": raw.get("diff_window", ""),
        }
        rows.append(row)
        # 写回之前重新读一次：另一个进程里的 `watch add`/`remove` 必须能熬过
        # 一轮长扫描。已被 remove 的 URL 不会被复活。
        fresh = load_state(path)
        target = next((w for w in fresh["watches"] if w["url"] == url), None)
        if target is not None:
            target["checks"] = int(target.get("checks") or 0) + 1
            target["last_checked_at"] = checked_at
            target["last_verdict"] = verdict
            target["last_status"] = row["status"]
            target["last_error"] = row["error"]
            if verdict == "changed":
                target["last_changed_at"] = checked_at
            _save(fresh, path)
        if on_row is not None:
            on_row(row)
    changed = [r["url"] for r in rows if r["verdict"] == "changed"]
    unchanged = [r["url"] for r in rows if r["verdict"] == "unchanged"]
    baseline = [r["url"] for r in rows if r["verdict"] == "baseline"]
    errors = [{"url": r["url"], "error": r["error"]} for r in rows
              if r["verdict"] == "error"]
    return {
        "checked": len(rows),
        "changed": changed,
        "unchanged": unchanged,
        "baseline": baseline,
        "errors": errors,
        "unknown": unknown,
        "rows": rows,
        "exit_code": 2 if errors else 1 if changed else 0,
    }


class _Lock:
    def __init__(self, path: Optional[Path | str] = None):
        p = state_path(path)
        self.path = p.with_name(p.name + ".lock")
        self.fd: Optional[int] = None

    def __enter__(self):
        paths.ensure_private_dir(self.path.parent)
        try:
            self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                              0o600)
        except FileExistsError:
            try:
                age = time.time() - self.path.stat().st_mtime
            except OSError:
                age = 0
            if age < 6 * 3600:
                raise WatchBusy(
                    f"another `dhole watch run` is active ({self.path.name}); "
                    "if it crashed, remove that file and retry")
            try:
                self.path.unlink()
            except OSError:
                pass
            self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                              0o600)
        os.write(self.fd, f"{os.getpid()} {_now()}\n".encode())
        os.close(self.fd)
        self.fd = None
        return self

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except OSError:
            pass
        return False


async def run_locked(checker: Callable[[str], Awaitable[dict]], *,
                     path: Optional[Path | str] = None,
                     only: Optional[Iterable[str]] = None,
                     on_row: Optional[Callable[[dict], None]] = None) -> dict:
    """Run one scan round under the cross-process lock.

    Same contract as :func:`run`, except two concurrent ``watch run`` processes may not
    interleave their read-modify-write of the state file.

    Args:
        checker: The single-URL check function ``checker(url) -> dict``.
        path: An override passed through to :func:`state_path`.
        only: Scan only these URLs; unknown ones land in ``unknown`` instead of raising.
        on_row: Called once per completed row, before the next URL starts.

    Returns:
        The same receipt as :func:`run`.

    Raises:
        WatchBusy: A live run holds the lock. A lock file older than six hours is treated
            as a crash leftover and reclaimed.
    """
    with _Lock(path):
        return await run(checker, path=path, only=only, on_row=on_row)


# ─── 桌面通知出口（提升清单 3）────────────────────────────────────────────
#
# watch run 的合同是**退出码**（0/1/2，cron 靠它分岔），而 cron 的盲区是「人不在
# 终端前」：半夜 changed=true 只落进一行日志，早上才被看见。桌面通知补的正是这个
# 消费者——不盯终端的人。**webhook 拒绝**，理由住在这一段的形状里：dhole 的身份是
# 只读的本地工具层，一个服务端 webhook 会把它变成「拿着页面内容往外 POST 的进程」
# ——新的外送面、新的凭据存储、新的 SSRF 审计，而「changed=true 已经交回给调用方」
# 那一半，agent 宿主本来就看得见。缺的只有「不盯终端的人」，桌面通知就是全部。
#
# 三个平台各走自己的无依赖通道（实测 2026-10-24 本机 toast 可弹）：
#   * Windows：WinRT toast 经 PowerShell（AppId 用 PowerShell 自己的，未注册的
#     AppId 在 Win11 上会被通知中心静默丢弃）；
#   * macOS：osascript display notification；
#   * Linux：notify-send（缺席如实说，不装假）。
# 通知**永远不改变退出码**：它是旁路，一次弹窗失败不能把「页面变了」说成「没变」。

_NOTIFY_MAX_ROWS = 3


def _toast_title() -> str:
    return "dhole watch"


def _changed_names(changed_rows: list[dict]) -> str:
    """通知的正文：label 优先（人是给 label 起名字的），没有才退 url；3 行之后
    折叠成计数——toast 是两行文字，不是清单。"""
    names = [(r.get("label") or r.get("url") or "").strip()
             for r in changed_rows]
    names = [n for n in names if n]
    shown = names[:_NOTIFY_MAX_ROWS]
    text = ", ".join(shown)
    hidden = len(names) - len(shown)
    if hidden > 0:
        text += f" and {hidden} more"
    return text


def _ps_quote(text: str) -> str:
    """PowerShell 单引号字面量的转义：翻倍。这是唯一要过的引用关——子进程按
    列表传参（无 shell），Windows 侧只此一处手工引号。"""
    return "'" + text.replace("'", "''") + "'"


def notify_changed(changed_rows: list[dict]) -> Optional[str]:
    """changed 行弹一枚桌面通知。返回给 CLI 印的一行说明；None = 这台机器没有
    通道（Linux 没有 notify-send）。**从不抛**：通知失败返回失败说明，一次弹窗
    不能弄脏退出码的合同。"""
    if not changed_rows:
        return None
    title, body = _toast_title(), _changed_names(changed_rows)
    try:
        sent = _fire(title, body)
    except Exception as exc:  # noqa: BLE001 - 旁路的失败只配一行说明
        return f"desktop notification failed: {exc.__class__.__name__}: {exc}"
    if sent:
        return f"desktop notification sent: {body}"
    return ("no desktop notification outlet found on this machine "
            "(Linux needs notify-send; macOS/Windows have one built in)")


def _fire(title: str, body: str) -> bool:
    """按平台发一枚 toast。True = 已发出；False = 这台机器没有通道。"""
    import shutil
    import subprocess
    import sys

    if sys.platform == "win32":
        script = (
            "[Windows.UI.Notifications.ToastNotificationManager, "
            "Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
            "$t = [Windows.UI.Notifications.ToastNotificationManager]"
            "::GetTemplateContent("
            "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
            f"$null = $t.GetElementsByTagName('text').Item(0).AppendChild("
            f"$t.CreateTextNode({_ps_quote(title)})); "
            f"$null = $t.GetElementsByTagName('text').Item(1).AppendChild("
            f"$t.CreateTextNode({_ps_quote(body)})); "
            "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}"
            "\\WindowsPowerShell\\v1.0\\powershell.exe\'; "
            "$null = [Windows.UI.Notifications.ToastNotificationManager]"
            "::CreateToastNotifier($app).Show("
            "[Windows.UI.Notifications.ToastNotification]::new($t))"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, timeout=30, check=True)
        return True
    if sys.platform == "darwin":
        script = f'display notification {_ps_quote(body)} with title {_ps_quote(title)}'
        subprocess.run(["osascript", "-e", script],
                       capture_output=True, timeout=30, check=True)
        return True
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", title, body],
                       capture_output=True, timeout=30, check=True)
        return True
    return False
