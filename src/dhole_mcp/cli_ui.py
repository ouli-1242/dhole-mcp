"""dhole CLI 的零依赖 ANSI 渲染器。

不引入 rich 也给命令一层干净、专业、跨平台的观感（dhole 保持轻量 —— 在断网 /
Docker-scratch 环境里也能跑）。它处理：

- 颜色支持：尊重 ``NO_COLOR``（任何值都关闭）与 ``FORCE_COLOR``；只在 stdout 是
  TTY 时着色；在 Windows 10+ 上启用 VT 处理。
- Unicode 边框，stdout 不是 UTF-8（老终端）时自动退化为 ASCII，所以任何机器上
  面板都不会输出乱码。
- 紧凑的带框面板，行可左/右对齐（对齐安全：可见长度计算排除 ANSI 码；内容是
  ASCII，宽度可预测 —— 边框内不放 emoji / 宽字符）。
- 品牌化单行（字标 + 标签 + 暗色元信息）。

配色（按项目审美）：洋红 + 青绿点缀，次要信息暗灰，错误用红。不用琥珀金，
不用森林绿。关闭颜色时（管道、NO_COLOR、老终端）所有样式退化为纯文本，版面
仍然读得清楚。

只用标准库（ctypes/os/re/shutil/sys）。服务器路径没有 import 成本
（server.py 只在 CLI 函数内部惰性 import 本模块）。
"""
from __future__ import annotations

import os
import re
import sys

# ── ANSI 码 ────────────────────────────────────────────────────────
_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_MAGENTA = "\033[95m"   # 亮洋红 —— 字标 / 需要被注意
_CYAN = "\033[96m"      # 亮青（偏青绿）—— 版本 / 成功 / 链接
_RED = "\033[91m"       # 亮红 —— 错误
_GREEN = "\033[92m"     # 亮绿 —— 留给 ✓（不是森林绿）
_UNDER = "\033[4m"      # 下划线 —— 命令 / URL

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")

_color_cache: bool | None = None
_unicode_cache: bool | None = None


def _color_ok() -> bool:
    """是否该输出 ANSI 颜色；首次调用后缓存。"""
    global _color_cache
    if _color_cache is not None:
        return _color_cache
    # NO_COLOR（no-color.org）：任何值都关闭颜色，空串也算。
    if os.environ.get("NO_COLOR") is not None:
        _color_cache = False
        return False
    if os.environ.get("FORCE_COLOR"):
        _color_cache = True
    elif not sys.stdout.isatty():
        _color_cache = False
    else:
        _color_cache = True
    if _color_cache and sys.platform == "win32":
        _enable_windows_vt()
    return _color_cache


def _enable_windows_vt() -> None:
    """在 Windows 10+ 上启用虚拟终端处理，让 ANSI 码真正渲染。

    尽力而为：老 Windows 或非控制台句柄上静默 no-op。没有这一步，老 conhost
    会把 ANSI 转义原样打出来。缓存结果在 ``_color_cache`` 里。
    """
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        STD_OUTPUT_HANDLE = -11
        ENABLE_VT = 0x0004
        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        mode = wintypes.DWORD()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | ENABLE_VT)
    except Exception:
        pass


def _unicode_ok() -> bool:
    """stdout 能不能渲染 UTF-8 制表符；结果缓存。"""
    global _unicode_cache
    if _unicode_cache is not None:
        return _unicode_cache
    # 尝试把 stdout 升级到 UTF-8（Python 3.7+ 的 reconfigure）。成功就假定控制台
    # 按 UTF-8 解码（Windows Terminal / 现代终端都是）。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    _unicode_cache = "utf" in enc
    return _unicode_cache


def _visible(s: str) -> str:
    """算可见长度前先剥掉 ANSI 转义码。"""
    return _ANSI_RE.sub("", s)


def _sty(text: str, *codes: str) -> str:
    """把文本包进 codes + reset；只在着色开启时这么做。"""
    if not _color_ok() or not codes:
        return text
    return "".join(codes) + text + _RESET


def _borders() -> tuple[str, str, str, str, str, str]:
    if _unicode_ok():
        return "╭", "╮", "╰", "╯", "─", "│"
    return "+", "+", "+", "+", "-", "|"


# ── 带样式的片段 ───────────────────────────────────────────────────

def wordmark() -> str:
    """The styled "Dhole" wordmark in the page header.
    """
    return _sty("Dhole", _BOLD, _MAGENTA)


def ver(v: str) -> str:
    """Format a version number as a cyan ``v<number>``.
    """
    return _sty(f"v{v}", _CYAN)


def ver_transition(a: str, b: str) -> str:
    """更新进度行里的 v{a} → v{b}：两端版本都着色，箭头变暗，非 UTF-8 终端退回 ASCII。"""
    return ver(a) + " " + dim(_glyph("→", "->")) + " " + ver(b)


def dim(s: str) -> str:
    """Dim ``s`` when colour is on; return it unchanged otherwise.
    """
    return _sty(s, _DIM)


def cyan(s: str) -> str:
    """Turn ``s`` cyan when colour is on; return it unchanged otherwise.
    """
    return _sty(s, _CYAN)


def magenta(s: str) -> str:
    """Turn ``s`` magenta when colour is on; return it unchanged otherwise.
    """
    return _sty(s, _MAGENTA)


def red(s: str) -> str:
    """Turn ``s`` red when colour is on; return it unchanged otherwise.
    """
    return _sty(s, _RED)


def _glyph(uni: str, asc: str) -> str:
    """状态字形；stdout 不是 UTF-8 时退化为 ASCII。"""
    return uni if _unicode_ok() else asc


def ok(s: str) -> str:
    """成功标记 + 标签：绿色对勾，标签变暗。"""
    return _sty(_glyph("✓", "+"), _GREEN) + " " + _sty(s, _DIM)


def warn(s: str) -> str:
    """Magenta arrow plus a dim message (an ASCII glyph on non-UTF-8 terminals).
    """
    return _sty(_glyph("→", ">"), _MAGENTA) + " " + _sty(s, _DIM)


def err(s: str) -> str:
    """Red cross plus a red message (an ASCII glyph on non-UTF-8 terminals).
    """
    return _sty(_glyph("✗", "x"), _RED) + " " + _sty(s, _RED)


def cmd(s: str) -> str:
    """一条 shell 命令，样式上突出到适合直接复制。"""
    return _sty(s, _UNDER, _CYAN)


# ── 组合输出 ───────────────────────────────────────────────────────

def lr(left: str, right: str, inner_width: int) -> str:
    """拼一行面板内容：左对齐 + 右对齐。中间空格填满，让 ``right`` 贴右边缘；
    计算按可见长度，ANSI 码不算。"""
    gap = inner_width - len(_visible(left)) - len(_visible(right))
    if gap < 1:
        gap = 1
    return f"{left}{' ' * gap}{right}"


def panel(rows: list[str], width: int = 50, indent: int = 2) -> str:
    """画一个紧凑的带框面板。每一行是已经拼好的内部字符串（左右两列用 ``lr``，
    只有左列时直接传带样式的字符串）。行会补到内部宽度；超宽不截断（很少见 ——
    内容按设计都很短）。"""
    co = _color_ok()
    d = _DIM if co else ""
    rr = _RESET if co else ""
    TL, TR, BL, BR, H, V = _borders()
    inner = width - 4  # inside "│ " ... " │"
    pad = " " * indent
    out = [f"{pad}{d}{TL}{H * (width - 2)}{TR}{rr}"]
    for ln in rows:
        extra = inner - len(_visible(ln))
        if extra < 0:
            extra = 0
        out.append(f"{pad}{d}{V}{rr} {ln}{' ' * extra} {d}{V}{rr}")
    out.append(f"{pad}{d}{BL}{H * (width - 2)}{BR}{rr}")
    return "\n".join(out)


def branded(label: str, meta: str = "") -> str:
    """品牌化单行：``  Dhole  <标签>  <暗色元信息>``。"""
    s = f"  {wordmark()}  {label}"
    if meta:
        s += f"  {meta}"
    return s
