"""The one search proxy dhole uses: resolution + credential masking.

17.0（大众化一刀切 B2）：代理**池**整体删除 —— ``~/.dhole/search_proxies.json``、
round-robin 轮转、按失败冷却的健康检查、``dhole proxy add/remove/clear`` 那条会往
用户目录里写明文凭据的 CLI，全部不在了。留下的只有一件事：这台机器的搜索从哪个
出口出去。答案是一个字符串，按这个顺序找：

    DHOLE_SEARCH_PROXY  →  HTTPS_PROXY  →  HTTP_PROXY  →  ALL_PROXY

后面三个是环境里本来就有的东西（沙箱、CI、公司代理常常已经设好），Windows 的
``os.environ`` 大小写不敏感，所以下游的小写 ``https_proxy`` 也会在这里被读到 ——
正因如此，取值来自哪一个变量必须能问出来（``configured_proxy`` 的第二返回值），
不然用户只能靠猜。第一条走 env_registry，所以 ``dhole config set`` 那扇门对它也有效。

一个代理就是一个代理：轮转的卖点是"这个 IP 被盯上就换下一个"，而免密引擎的封锁
从来不看出口 IP 的唯一性（同一台机器换端口不换指纹），代价是一份明文存凭据的状态
文件加一套健康检查和一条写用户机器的命令。判据是同一条：能算清的写死，需要操作的
明确决定才留给配置。
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

from dhole_mcp import env_registry

logger = logging.getLogger("dhole-mcp.search_proxy")

#: 下游（primp / httpx）真能接受的方案。其它值基本都是漏写方案名的笔误
#: （``127.0.0.1:7890``），而把它原样递下去的结果是每一次搜索都失败，且报错
#: 指向代理本身而不是那个笔误 —— 所以宁可当成"没设"，并把这个决定说出来。
VALID_SCHEMES = ("http", "https", "socks5", "socks5h")

_FALLBACK_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY")


def _first_present(names: tuple[str, ...]) -> tuple[str, str]:
    """(变量名, 值)，取第一个非空的；都没有则 ("", "")。"""
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return name, value
    return "", ""


def configured_proxy() -> tuple[str | None, str]:
    """``(proxy_url, source)`` —— 本次进程用的那一个代理，以及它是谁定的。

    什么都没设时返回 ``(None, "")``：直连。方案不认识时返回 ``(None, source)``
    并留一条 warning —— 把"配置写错了"变成"搜索全挂且看不出为什么"是更坏的结局。
    """
    source = "DHOLE_SEARCH_PROXY"
    raw = (env_registry.value_of("DHOLE_SEARCH_PROXY") or "").strip()
    if not raw:
        source, raw = _first_present(_FALLBACK_VARS)
    if not raw:
        return None, ""

    if "," in raw:
        listed = [p.strip() for p in raw.split(",") if p.strip()]
        logger.warning(
            "%s lists %d proxies; 17.0 removed the pool and rotates nothing - "
            "using the first one only", source, len(listed))
        raw = listed[0]

    scheme = urlparse(raw).scheme.lower()
    if scheme not in VALID_SCHEMES:
        logger.warning(
            "Ignoring the search proxy from %s: unsupported scheme %r (expected %s)",
            source, scheme or "(none)", ", ".join(VALID_SCHEMES))
        return None, source
    return raw, source


def redact(proxy: str | None) -> str:
    """代理 URL 里唯一可能被回显的形式：用户名密码一律抹成 ``***``。

    搜索代理常带凭据（``http://user:pass@corp:3128``），而 doctor、错误文案和
    usage 审计都要说"出口是哪个"。只抹凭据、保留主机与端口 —— 说清"走了哪台代理"
    不需要知道"用谁的身份"。整个 userinfo 一律替换成定长的 ``***``，不给出任何
    "有没有密码 / 用户名多长"的信号。
    """
    if not proxy:
        return ""
    parsed = urlparse(proxy)
    host = parsed.hostname or ""
    try:
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:          # 端口不是数字：URL 本身是坏的，原样脱敏后交给上游
        port = ""
    if "@" not in proxy:
        return proxy
    scheme = parsed.scheme or "proxy"
    return f"{scheme}://***@{host}{port}"
