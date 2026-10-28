"""按域记住「上次哪一档抓取成功」，只活在这个进程里。

研究循环经常在同一站点连读多页：第一页用 HTTP 档被墙挡下，浏览器档成功；第二页
又从 HTTP 档开始，再付一次同样的失败。这里记住的是一条 **advisory** 提示：
``http`` 或 ``stealthy``。它不改任何参数、不进任何响应字段、不落盘 —— 进程退出就
消失，所以不会把一次陈旧的判断带进下一次启动。

四条纪律：

* key 是 hostname（小写、去 ``www.``），**不**合并子域 —— ``docs.example.com``
  与 ``example.com`` 的策略经常不同；
* 只有成功才记；失败由调用方 ``forget``，下一次从完整阶梯重新学；
* TTL 30 分钟、LRU 200 条，过期条目在读取时清掉；
* 时间用 ``monotonic()``，系统时钟回拨不会把记录变年轻。

它服务的只有长驻的 MCP 会话；CLI 每条命令是新进程，读不到也写不进 —— 这是接受
的代价，换来的是零落盘、零清理、零跨进程污染。
"""

from __future__ import annotations

import time
from collections import OrderedDict
from urllib.parse import urlparse

TTL_S = 1800.0
MAX_ENTRIES = 200
MODES = ("http", "stealthy")

_MEMORY: "OrderedDict[str, tuple[str, float]]" = OrderedDict()


def _host(url: str) -> str:
    try:
        host = (urlparse(url or "").hostname or "").lower().strip(".")
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def hint(url: str) -> str:
    """这个 host 上次成功的档位；没有、过期或未知都返回空串。"""
    host = _host(url)
    if not host:
        return ""
    entry = _MEMORY.get(host)
    if entry is None:
        return ""
    mode, stamp = entry
    if time.monotonic() - stamp > TTL_S:
        _MEMORY.pop(host, None)
        return ""
    _MEMORY.move_to_end(host)
    return mode


def remember(url: str, mode: str) -> None:
    """记下一次成功。只接受 :data:`MODES` 里的值，别的静默不记。"""
    host = _host(url)
    if not host or mode not in MODES:
        return
    _MEMORY[host] = (mode, time.monotonic())
    _MEMORY.move_to_end(host)
    while len(_MEMORY) > MAX_ENTRIES:
        _MEMORY.popitem(last=False)


def forget(url: str) -> None:
    """丢掉这个 host 的记录（提示失败后调用，让下一次重新学）。"""
    _MEMORY.pop(_host(url), None)


def reset() -> None:
    """清空全部记录。测试与 ``sys_state`` 之外没有人该调用它。"""
    _MEMORY.clear()


def snapshot() -> dict[str, str]:
    """当前未过期的 host → mode，按最近使用排序（诊断用，不含时间戳）。"""
    now = time.monotonic()
    stale = [host for host, (_mode, stamp) in _MEMORY.items()
             if now - stamp > TTL_S]
    for host in stale:
        _MEMORY.pop(host, None)
    return {host: mode for host, (mode, _stamp) in _MEMORY.items()}
