"""KB-6 现状测量：dhole 自有的状态文件/目录在 POSIX 上的实际权限。

Windows 上 os.chmod 只能改只读位，stat 报出的 mode 不是真实 ACL —— 所以本脚本
同时打印 os.name，并在非 POSIX 上把 mode 断言降级为"记录 + 断言 harden_file 被调用"。

用法：
    PYTHONPATH=src PYTHONIOENCODING=utf-8 python refactor_artifacts/tools/measure_state_perms.py [标签]
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LABEL = sys.argv[1] if len(sys.argv) > 1 else "unlabeled"

HARDEN_CALLS: list[str] = []
HARDEN_DIR_CALLS: list[str] = []
sys.path.insert(0, str(REPO / "src"))

from dhole_mcp import paths  # noqa: E402

_real_harden = paths.harden_file
_real_harden_dir = getattr(paths, "harden_dir", None)


def _spy_harden(path) -> None:  # type: ignore[no-untyped-def]
    HARDEN_CALLS.append(Path(path).name)
    return _real_harden(path)


paths.harden_file = _spy_harden  # type: ignore[assignment]

if _real_harden_dir is not None:
    def _spy_harden_dir(path) -> None:  # type: ignore[no-untyped-def]
        HARDEN_DIR_CALLS.append(Path(path).name)
        return _real_harden_dir(path)

    paths.harden_dir = _spy_harden_dir  # type: ignore[assignment]


def mode_of(p: Path) -> str:
    try:
        return oct(stat.S_IMODE(p.stat().st_mode))
    except FileNotFoundError:
        return "MISSING"


def fresh_home() -> Path:
    """A DHOLE_HOME that does not exist yet, so we observe how it gets created."""
    d = Path(tempfile.mkdtemp(prefix="dhole_perms_")) / "fresh_home"
    os.environ["DHOLE_HOME"] = str(d)
    return d


def report(what: str, home: Path) -> None:
    print(f"  {what}")
    print(f"    home dir  {mode_of(home):>8}  {home}")
    for f in ("search_proxies.json", "usage.jsonl", "search_feedback.json",
              "circuit_breaker.json", "engine_stats.json"):
        p = home / f
        if p.exists():
            print(f"    file      {mode_of(p):>8}  {f}")


print(f"=== KB-6 现状测量 [{LABEL}] ===")
print(f"os.name = {os.name}  (POSIX 才真正吃 chmod)")
print(f"umask   = {oct(os.umask(0o022))} (读取时会被重置为 0o022)")
os.umask(0o022)  # 显式固定，让结果可复现
print()

# ─── 1. 代理配置：含明文凭据 ─────────────────────────────────────────────
import dhole_mcp.search_proxy as sp  # noqa: E402

home1 = fresh_home()
sp.save_proxies(["http://user:secret@10.0.0.1:8080"])
print("[1] search_proxy.save_proxies()  -- 含明文凭据")
report("", home1)
print()

# ─── 2. usage.jsonl ─────────────────────────────────────────────────────
import dhole_mcp.server as srv  # noqa: E402

home2 = fresh_home()
os.environ["DHOLE_USAGE_LOG"] = "1"
srv._log_tool_call("smart_fetch", True, 12.3)
print("[2] server._log_tool_call()  -- DHOLE_USAGE_LOG=1")
report("", home2)
print()

# ─── 3. search_feedback.json ────────────────────────────────────────────
import dhole_mcp.search as search  # noqa: E402

home3 = fresh_home()
os.environ["DHOLE_SEARCH_FEEDBACK"] = "1"
search.record_search_feedback("https://example.org/page")
print("[3] search.record_search_feedback()  -- DHOLE_SEARCH_FEEDBACK=1")
report("", home3)
print()

# ─── 4. 对照组：已知走了 ensure_private_dir 的路径 ───────────────────────
home4 = fresh_home()
paths.ensure_private_dir(home4)
paths.harden_file(home4 / "circuit_breaker.json")
(home4 / "circuit_breaker.json").write_text("{}", encoding="utf-8")
_real_harden(home4 / "circuit_breaker.json")
print("[4] 对照组：ensure_private_dir + harden_file（circuit_breaker 走的就是这条）")
report("", home4)
print()

print(f"harden_file 被调用的次数: {len(HARDEN_CALLS)}  明细: {HARDEN_CALLS}")
print(f"harden_dir  被调用的次数: {len(HARDEN_DIR_CALLS)}  明细: {HARDEN_DIR_CALLS}")
print()
print("读法：")
print("  - [1][2] 的 home 若是 0o755 / 文件是 0o644，说明裸 mkdir + 裸 open 没套权限收紧。")
print("  - [3] 用 mkstemp+os.replace，POSIX 下 tmp 自带 0600，会被 rename 带过来 —— 需实测确认。")
print("  - [4] 是对照组：同机同 umask 下，走了收紧路径应该拿到 0o700 / 0o600。")
print("  - Windows 上所有 mode 都不可信，只能看 harden_file 有没有被调用。")
