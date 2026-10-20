"""Runtime path layout — every file dhole persists lives under ONE directory.

Everything dhole writes is regenerable state, and it lives under exactly one
root: "what does this tool leave on my machine?" has to be answerable in one
directory, and an uninstall instruction that names two is a lie about one.
(``~/.dhole_mcp_cache/`` was the second root until 17.0 B2 - the migration that
folded it in is gone, and nothing reads that path any more. A pre-14.3 install
that still has it keeps it until the user deletes it.) This module is the single
source of truth for those paths:

    ~/.dhole/
      cache.db                 content cache (SQLite; + -wal/-shm sidecars)
      sessions.db              cookie jar (17.1+: only when DHOLE_PERSIST_CREDENTIALS=1;
                               values DPAPI-encrypted — the default store is memory)
      identity.db              stored cred_defaults (same switch, same encryption)
      models/                  downloaded model files (neural reranker)
      circuit_breaker.json     engine cooldown state
      engine_stats.json        per-engine parse yield (who returned 0 usable results)
      usage.jsonl              opt-in local call log

Not in here, on purpose:

* the browser profile — a per-session ``tempfile.mkdtemp()`` directory that is
  removed on close (transient scratch, not state; a crash leaves it to the OS
  temp cleaner rather than to this directory forever);
* ``rapidocr``'s OCR models — bundled with that package, not downloaded here.

Stdlib only, and imported by cache/reranker/search/updater, so it must never
import a third-party dependency.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_HOME_DIR_NAME = ".dhole"


def home() -> Path:
    """The one directory dhole owns: ``~/.dhole``, or ``DHOLE_HOME`` when set.

    Why the override exists: this directory holds every fetched page body in
    plaintext (cache.db), the search queries, and the reranker models. On a shared
    machine that is content the user may not want next to their other files, and
    the location used to be unmovable. ``DHOLE_HOME`` also lets a test or a
    sandbox point everything at a throwaway directory.

    (17.0 B2 deleted the pre-14.3 cache migration, so a custom home is now
    exactly what it says: nothing is folded in from ``~/.dhole_mcp_cache``.)
    """
    raw = (os.environ.get("DHOLE_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / _HOME_DIR_NAME


def ensure_private_dir(path: Path | str) -> Path:
    """mkdir -p + best-effort owner-only mode. Never raises.

    POSIX gets a real 0700. Windows largely ignores chmod (NTFS ACLs govern), so
    there this is a no-op and ``DHOLE_HOME`` is the actual lever — said out loud
    rather than pretending the bit was set.

    str callers exist (`sessions` / `identity` pass `os.path.dirname`), so the path
    is normalized here rather than at each caller. `except` is OSError-only on
    purpose: when this swallowed every exception, a `str.mkdir` AttributeError hid
    inside it and the store died later as sqlite's "unable to open database file".
    """
    path = Path(path)
    try:
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def harden_file(path: Path | str) -> None:
    """Best-effort 0600 on a state file we just wrote. Never raises.

    Applied after the atomic-replace so a temp file can't linger world-readable
    with page content in it.
    """
    try:
        os.chmod(str(path), 0o600)
    except Exception:
        pass


def harden_dir(path: Path | str) -> None:
    """Best-effort 0700 on a directory dhole owns. Never raises.

    Deliberately does NOT create the directory, unlike :func:`ensure_private_dir`:
    callers keep their own ``mkdir`` so a failure to create it still raises the
    way it always did, and this only tightens what already exists.
    """
    try:
        os.chmod(str(path), 0o700)
    except Exception:
        pass


def cache_dir() -> Path:
    """Directory holding the content cache DB (the dhole home itself)."""
    return home()


def db_path() -> Path:
    """The content cache SQLite file."""
    return home() / "cache.db"


def models_dir() -> Path:
    """Directory for downloaded model files (neural reranker)."""
    return home() / "models"


def file(name: str) -> Path:
    """A file directly inside the dhole home (``circuit_breaker.json``, ...)."""
    return home() / name


def profiles_dir() -> Path:
    """Parent of named persistent browser profiles (``~/.dhole/profiles/``).

    Each profile is a full Chrome user-data-dir: cookies, localStorage, login
    state. 17.0 没有任何调用方给它取名（按环境的 ``DHOLE_BROWSER_PROFILE`` 已
    注销，按次参数要等 B5 那颗「设一次的默认身份」工具回来才有人传），所以这个
    目录通常不存在。
    它一旦存在就是"keep me signed in"的产物，里面每一项都是凭据级：created 0700 (owner-only on POSIX; on Windows the
    profile ACL under the user's home directory is the real boundary).
    """
    return ensure_private_dir(home() / "profiles")


#: 服务器侧两个根的**名字**。回执里写它们的绝对形式，就是把这台机器的目录布局与
#: 用户名交给调用方 —— 契约 C15（错误不泄漏本机绝对路径）在 `File not found:`
#: 那句上曾经被放宽（2026-10-09 审计 D11：实测错误里逐字带着
#: `D:\tools\dhole-mcp\.scratch\...`，而进程 cwd 在 MCP 宿主下就是宿主自己的安装
#: 目录）。可诊断性不该靠泄漏来换：调用方真正需要的是"你按什么顺序试了哪几处"，
#: 而那件事用名字说得清。
MASKED_CWD = "<server cwd>"
MASKED_HOME = "~"
#: 用户名出现在**任何一段**里都要折成这个名字（评审 R02-090）。
MASKED_USER = "<user>"


def _identity_names() -> tuple[str, ...]:
    """这台机器"是谁"的那几个写法：home 的最后一段 + `USERNAME` / `USER`。

    三个来源都要：Windows 的 `USERNAME` 与 POSIX 的 `USER` 在不同启动路径下各可能为空
    （服务化启动时 `USER` 常缺，`expanduser` 又可能给出改写过的家目录），而漏一个来源就是
    漏一次遮蔽。长名排在前面，免得短名先把长名的一段吃掉。少于两个字符的名字不参与：
    那种"名字"在任何一段正文里都会撞到，遮蔽会退化成涂掉句子。
    """
    names: set[str] = set()
    for candidate in (os.path.basename(os.path.expanduser("~") or ""),
                      (os.environ.get("USERNAME") or "").strip(),
                      (os.environ.get("USER") or "").strip()):
        if candidate and len(candidate) >= 2 and candidate not in ("~", ".", ".."):
            names.add(candidate)
    return tuple(sorted(names, key=len, reverse=True))


def mask_machine_paths(text: str) -> str:
    """Replace THIS machine's own roots in ``text`` with symbolic names.

    Masks the two roots the *server* owns: the process working directory (under an
    MCP host that is the host's own install directory — the very path a caller
    never mentioned, see ``_resolve_local_path``) and the user's home. The home is
    masked even when the caller typed it, because the username is part of what the
    path leaks and ``~/report.csv`` is a *better* diagnostic than the full form.

    A path with no machine root in it is returned unchanged: an absolute path the
    caller invented is theirs to read back.

    …except for the username, which is masked wherever it sits (:func:
    ``_identity_names``, review R02-090). That exception is not a new root: the
    reason the home is masked above is "the username rides along with the path",
    and a *prefix* rule can't see a username that lives outside both roots.
    Measured on the wire (`review/.runtime/p1/r98b_user_segment.json`): a file
    under a temp root on another drive, in a directory named ``probe-of-<user>``
    (pytest's own naming is exactly this shape), came back verbatim in both the
    successful ``url`` and the ``File not found`` sentence.

    Longest root first, so a cwd nested inside home keeps its own, more specific
    label. The boundary after a root has to be a real stopping point — anything
    that is not a path-name character (separator, quote, bracket, space, end of
    text): without it a shorter root would fold a longer directory that merely
    starts with it (``C:\\Users\\mel`` read as a hit on ``C:\\Users\\me``), while
    a stricter one (separator-or-end only) leaves the quoted form
    (``at 'D:\\tools\\dhole-mcp'``) and the POSIX spelling of the SAME root
    untouched, which is how a root leaks. Both spellings are masked because a
    normalized path is the same directory wearing another hat. Matching is
    case-insensitive because on Windows the same root
    comes back spelled ``c:\\users\\ouli`` as often as not, and under-masking is
    the failure that leaks.
    """
    if not text:
        return text
    roots: list[tuple[str, str]] = []
    try:
        home_dir = os.path.expanduser("~")
        if home_dir and home_dir not in ("~", "/", "\\"):
            roots.append((home_dir, MASKED_HOME))
    except Exception:
        pass
    try:
        cwd = os.getcwd()
        if cwd:
            roots.append((cwd, MASKED_CWD))
    except Exception:
        pass
    #: 根之后必须**不是路径名字符**才算收尾：分隔符、引号、括号、空格、串尾都算，
    #: 而字母/数字/`-`/`_` 不算 —— 这一格守的仍是原来那个意图（`C:\Users\me` 不许把
    #: `C:\Users\mel` 折掉），只是把"只认分隔符或串尾"放宽到"认任何真正的收尾"：库交出的
    #: 消息常把路径打在引号里（`Package not found at 'D:\tools\dhole-mcp'`），那种形状
    #: 从前一个字都不改。
    #:
    #: `.` 单独一条判据（评审 R02-049）：从前它和字母数字并列，于是**句子里收尾的根**
    #: 整格漏掉（`searched under D:\tools\dhole-mcp.` —— 库里最常见的「在这里没找到」
    #: 就长这样，而同一句里的逗号冒号引号都遮）。句号是不是"名字的一部分"，看它后面
    #: 还有没有名字字符：`.mirror` / `.db` 是另一颗目录或扩展名（不许折），
    #: `.` 接串尾、空白或别的标点就是句子（要遮）。
    _root_boundary = r"(?![A-Za-z0-9_\-]|\.[A-Za-z0-9_\-])"
    for root, label in sorted(roots, key=lambda rl: -len(rl[0])):
        # 同一个根的两种拼法都要遮。`re.escape(root)` 在 Windows 上只生成反斜杠形式，
        # 而响应里到处是归一化过的 POSIX 路径（pathlib 的 `.as_posix()`、zip/fsspec 内部
        # 路径、`file:///` 那一格的形状）—— 换一种写法不是换一台机器。
        for spelling in dict.fromkeys((root, root.replace("\\", "/"),
                                       root.replace("/", "\\"))):
            text = re.sub(re.escape(spelling) + _root_boundary, label, text,
                          flags=re.IGNORECASE)
    #: 身份那一条：用户名是**内容**，不是前缀，所以它在任何一段里都算。边界只认字母/数字/
    #: 下划线连着的不算一段（`<user>project` 是另一颗目录，反向守卫钉着这一条），而 `-` 与 `/`
    #: 都算收尾 —— `probe-of-<user>` 正是被 `-` 连起来的一段，也是这条判据存在的全部理由。
    for name in _identity_names():
        text = re.sub(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])",
                      MASKED_USER, text, flags=re.IGNORECASE)
    return text



