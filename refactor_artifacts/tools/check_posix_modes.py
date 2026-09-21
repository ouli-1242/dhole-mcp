"""在真实 POSIX 上核对状态文件的权限位（KB-6 的实证）。

背景：本机是 Windows，`os.chmod` 只动只读位，连已经用了 `harden_file` 的对照组
都读作 0o666 —— 那台机器无法验证 POSIX 权限语义。这个脚本在 Linux 容器里跑，
对**同一份代码**给出真实 mode。

用法（容器内，cwd 为工作根）：
    python check_posix_modes.py <src 目录>

对同一批场景跑**修复前**与**修复后**两棵源码树，即可得到 before/after 对照。
只依赖 stdlib + dhole_mcp.paths（`search_proxy.py` 模块级无第三方 import），
所以裸 python 镜像即可，不需要装任何依赖。
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path

SRC = sys.argv[1] if len(sys.argv) > 1 else "src"
sys.path.insert(0, SRC)

# 显式固定 umask，让结果可复现：默认 022 下，裸 open() 得到的是 0644。
os.umask(0o022)

from dhole_mcp import paths  # noqa: E402
from dhole_mcp import search_proxy  # noqa: E402


def mode_of(p: Path) -> str:
    try:
        return oct(stat.S_IMODE(p.stat().st_mode))
    except FileNotFoundError:
        return "MISSING"


def banner(title: str) -> None:
    print(f"\n--- {title} ---")


print(f"python {sys.version.split()[0]}  os.name={os.name}  源码={SRC}")

# ── 场景 1：全新 home（目录与文件都由这次写入创建）──────────────
banner("场景 1  全新 DHOLE_HOME")
home = Path(tempfile.mkdtemp()) / "fresh_home"
os.environ["DHOLE_HOME"] = str(home)
search_proxy.save_proxies(["http://user:secret@10.0.0.1:8080"])
print(f"  home                {mode_of(home)}")
print(f"  search_proxies.json {mode_of(home / 'search_proxies.json')}")

# ── 场景 2：目录已存在且是松的（0777），看会不会被收紧 ──────────
banner("场景 2  已存在的松目录 0777")
home2 = Path(tempfile.mkdtemp()) / "loose_home"
home2.mkdir(parents=True)
os.chmod(home2, 0o777)
os.environ["DHOLE_HOME"] = str(home2)
print(f"  写入前 home         {mode_of(home2)}")
search_proxy.save_proxies(["http://user:secret@10.0.0.1:8080"])
print(f"  写入后 home         {mode_of(home2)}")

# ── 场景 3：文件已存在且是松的（0666），看会不会被收紧 ──────────
banner("场景 3  已存在的松文件 0666")
home3 = Path(tempfile.mkdtemp()) / "loose_file_home"
home3.mkdir(parents=True)
target = home3 / "search_proxies.json"
target.write_text('{"proxies": []}', encoding="utf-8")
os.chmod(target, 0o666)
os.environ["DHOLE_HOME"] = str(home3)
print(f"  写入前 文件         {mode_of(target)}")
search_proxy.save_proxies(["http://user:secret@10.0.0.1:8080"])
print(f"  写入后 文件         {mode_of(target)}")

# ── 行为保持：内容必须逐字节不变 ────────────────────────────────
banner("内容（只改模式，不改字节）")
body = target.read_text(encoding="utf-8")
print("  " + body.replace("\n", "\n  ").rstrip())
print("  解析结果:", json.loads(body))

# ── 辅助函数本身 ────────────────────────────────────────────────
banner("paths.ensure_private_dir / harden_dir / harden_file")
d = Path(tempfile.mkdtemp()) / "helpers"
paths.ensure_private_dir(d)
print(f"  ensure_private_dir -> {mode_of(d)}")
f = d / "x.json"
f.write_text("{}", encoding="utf-8")
os.chmod(f, 0o666)
paths.harden_file(f)
print(f"  harden_file        -> {mode_of(f)}")
os.chmod(d, 0o755)
paths.harden_dir(d)
print(f"  harden_dir         -> {mode_of(d)}")
