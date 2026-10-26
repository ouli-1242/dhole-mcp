"""网络错误分类，给 agent 可行动的诊断。

把原始网络错误串（来自 primp、patchright/playwright、操作系统 socket 层）映射成
带可行动提示的类别，让 AI agent 知道该重试、换源还是放弃 —— 而不是只看到一句
看不懂的 "fetch failed"。

纯工具模块：除标准库外零依赖，任何地方都能安全导入（事件循环、工作线程、模块级）。
"""

from __future__ import annotations

import re
from typing import Tuple

# ─── 模式表 ──────────────────────────────────────────────────────────────────
# 顺序有意义：先匹配者胜。模式对原始错误串做大小写不敏感匹配。

_PATTERNS: list[tuple[re.Pattern, str]] = [
    # 代理错误（**最先**检查："proxy connection refused" 该算代理，不是连接被拒）
    (re.compile(
        r"ERR_PROXY_CONNECTION_FAILED|proxy.*(?:refused|failed|error)|"
        r"tunnel connection failed|proxy_connect",
        re.IGNORECASE,
    ), "proxy_error"),

    # 连接被拒（目标主动拒绝 TCP 连接）
    (re.compile(
        r"ERR_CONNECTION_REFUSED|ConnectionRefusedError|os error 10061|"
        r"ECONNREFUSED|connection refused",
        re.IGNORECASE,
    ), "connection_refused"),

    # 连接被重置（远端强制关闭已建立的连接）
    (re.compile(
        r"os error 10054|ERR_CONNECTION_RESET|ConnectionResetError|"
        r"ECONNRESET|远程主机强迫关闭|connection (?:was )?(?:forcibly )?(?:closed|reset)|"
        r"an existing connection was forcibly closed",
        re.IGNORECASE,
    ), "connection_reset"),

    # Fake-IP 隧道网段（Clash / sing-box TUN 会发 198.18.0.0/15）。错误里点名
    # 这类地址，说明是隧道没到达真实网络："站点挂了或出站被挡"是诚实的定性，
    # 与连接被拒同一裁决。必须排在 `timeout` 前面：这些错误里也常出现
    # "timed out"，而隧道才是原因。
    (re.compile(r"\b198\.1[89]\.\d+\.\d+\b"), "connection_refused"),

    # DNS 解析失败
    (re.compile(
        r"ERR_NAME_NOT_RESOLVED|NameResolutionError|getaddrinfo failed|"
        r"nodename nor servname|Name or service not known|"
        r"DNS (?:resolution )?(?:failed|error)|resolve.*failed",
        re.IGNORECASE,
    ), "dns_failure"),

    # 浏览器进程没能启动。必须排在 `timeout` 之前：驱动的原话是
    # "Timed out while launching browser"，把它读成站点超时会把调用方送去调大
    # `timeout=` 再打目标 —— 而事实是操作系统拒绝创建进程。
    # 刻意收窄（带括号的 `(0x5)`）：裸 `0x5` 会命中其他崩溃文本里的十六进制地址。
    (re.compile(
        r"browserType\.[a-zA-Z]*launch|failed to launch|could not launch|"
        r"cannot access executable|executable doesn't exist|"
        r"access is denied|拒绝访问|\(0x5\)|spawn [^\s]+ ENOENT|"
        # 裸的那句驱动原话（不带 `browserType.launch:` 前缀）也会到分类器这里：
        # actions.py 早就按这个片段认它，两份名单不该有出入。
        r"while launching browser",
        re.IGNORECASE,
    ), "browser_launch"),

    # 超时
    (re.compile(
        r"TimeoutError|ERR_TIMED_OUT|ERR_CONNECTION_TIMED_OUT|"
        r"ERR_NAVIGATION_TIMEOUT|timed? ?out|Navigation timeout|"
        r"Timeout \d+ms exceeded|deadline exceeded",
        re.IGNORECASE,
    ), "timeout"),

    # TLS / SSL 错误
    (re.compile(
        r"CERTIFICATE|SSL[:\s]|TLS[:\s]|ssl_error|"
        r"handshake fail|certificate verify failed|"
        r"ERR_CERT_",
        re.IGNORECASE,
    ), "tls_error"),
]

# ─── 各类别的可行动提示 ──────────────────────────────────────────────────────

#: 回环 / 私网目标一次 HTTP 应答都没拿到时的那句出路。通用那几句（"站点可能挂了、
#: 换个源"）对调用者自己机器上的一个端口不是答案 —— 换源换不掉一个没起的服务。
#: 三个读者共用它：TCP 预检定论的两条支路（server），以及 HTTP 档自己拨了却被拒/
#: 没答的那一条（quality —— 实测同一件事在两处回执里有两种说法，D-08b）。
PRIVATE_TARGET_NO_ANSWER = (
    "this private target answered nothing - no HTTP response came back from it. "
    "Is the local service actually running, and on this port?")

_HINTS: dict[str, str] = {
    "connection_refused": (
        "Target refused the connection. The site may be down, or your network "
        "environment blocks outbound connections to this host. Do NOT retry the "
        "same URL - try a different source or check network connectivity."
    ),
    "connection_reset": (
        "Connection was forcibly closed by the remote host (likely anti-bot "
        "firewall or network policy). Retry once after a few seconds; if it "
        "persists, switch sources or use a proxy."
    ),
    "dns_failure": (
        "DNS resolution failed. The domain may not exist or your DNS is blocked. "
        "Verify the URL is correct; do NOT retry."
    ),
    # 预探测自己的两级结论。分成两颗而不是一颗，是因为一次 gaierror 和两次是两件
    # 不同的事：实测 lwn.net 抖动时，一次失败换来的回执写着 `retryable: false` 和
    # "do NOT retry"，而调用方原样重试 1643ms 就拿到了页面。
    "dns_unresolvable": (
        "The name did not resolve on two probes 150ms apart, so this is not one "
        "blip in the resolver - either the domain does not exist or this network "
        "cannot see it. The TCP probe does not get the last word: the HTTP and "
        "browser tiers still ran, so read `escalation_path` for what the real "
        "attempt said. Fix the name or the resolver - resending the same URL "
        "changes neither."
    ),
    "private_target_blocked": (
        "dhole did not dial this address: it is a private / loopback / metadata "
        "target and the waiver does not cover it. No request was sent, so nothing "
        "about the service itself is known yet. If this is a machine you operate, "
        "open the door deliberately in cred_defaults - its allow_private setting "
        "accepts this host; otherwise point at the public URL."
    ),
    "timeout": (
        "Request timed out. The site may be slow or your network is restricted. "
        "Retry once with a longer timeout; if it persists, switch sources."
    ),
    "browser_launch": (
        "The browser process could not be started - this is not a site problem, so "
        "retrying the URL or raising timeout= cannot change the outcome. Check the "
        "three states in `dhole --doctor`: nothing on disk (run the install command "
        "it prints), present but never launched, or present and refused by the "
        "operating system (that one is recorded in ~/.dhole/browser_install.json). "
        "If your machine already runs Chrome or Edge, dhole uses it automatically; "
        "otherwise attach to a browser you started yourself with "
        "cdp_url='http://127.0.0.1:9222' plus confirm=true."
    ),
    "tls_error": (
        "TLS/SSL handshake failed. Possible certificate issue or network "
        "interception. Do NOT retry without changing network/proxy."
    ),
    "proxy_error": (
        "Proxy connection failed. Check DHOLE_SEARCH_PROXY, or the proxy stored with cred_defaults."
    ),
    "unknown": (
        "Fetch failed. Check the error field for details; try a different "
        "source or retry with different parameters."
    ),
}

# 「重发这一条一模一样的调用能否改变答案」——`diagnosis.retryable` 的取值，
# 按网络类别给。它必须和上面那段人话同处一地：两边分家时漂走过两次，实测
# （2026-10-08）dns_failure 的 hint 写着 "do NOT retry" 而枚举那侧给 True，5xx
# 的 next_action 写着 "Retry once after a pause" 而枚举那侧给 False —— 同一个
# 响应里两只手互相否决，人读着困惑，脚本按其中一个字段分支就按另一个字段做错事。
# 值按各类别的物理成因定：名字不存在、对端明确拒绝、TLS 证书被拒、代理没起来、
# 浏览器进程起不来——重发同一字节都不改变结果；重置与超时可能是瞬变的。
_RETRYABLE_BY_CATEGORY: dict[str, bool] = {
    "connection_refused": False,
    "connection_reset": True,
    "dns_failure": False,
    # 两颗新结论的"不可重试"有各自的依据，都不是"网络类失败一律如此"：
    # dns_unresolvable 是**两次**探针都失败（一次不算，见上），private_target_blocked
    # 是一次策略决定 —— 重发同一字节不会改动名册。
    "dns_unresolvable": False,
    "private_target_blocked": False,
    "timeout": True,
    "browser_launch": False,
    "tls_error": False,
    "proxy_error": False,
    # 分类不出来时也不许许诺"重试会变"：那句话的唯一依据是类别本身。
    "unknown": False,
}


def is_retryable(category: str) -> bool:
    """某个网络类别能否重试 —— 与写它那段人话的是同一张表。"""
    return _RETRYABLE_BY_CATEGORY.get(category, False)


def classify_network_error(error_str: str) -> Tuple[str, str]:
    """把原始网络错误串分类成 (category, actionable_hint)。

    类别：
    - connection_refused：目标拒绝 TCP 连接（站点挂了 / 环境被挡）
    - connection_reset：远端强制关闭连接（反爬 / 防火墙）
    - dns_failure：域名解析失败（某一档自己报的错误文本）
    - dns_unresolvable：TCP 预检两次相隔 150ms 的探测都失败
    - private_target_blocked：dhole 拒绝拨号到私网目标（一个请求都没发出 ——
      这是策略裁决，不是网络故障）
    - browser_launch：浏览器进程本身起不来（不是站点问题 —— 排在 `timeout`
      之前检查，免得 "Timed out while launching browser" 被读成慢站点）
    - timeout：请求超时（站点慢 / 网络受限）
    - tls_error：TLS/SSL 握手失败
    - proxy_error：代理连接失败
    - unknown：无法分类

    hint 是给 agent 看的句子，说明发生了什么、下一步做什么（重试 / 换源 / 放弃）。

    从不抛异常。空输入或无法解析的输入返回 ("unknown", 通用提示)。
    """
    if not error_str:
        return "unknown", _HINTS["unknown"]

    # 先匹配 dhole **自己**的类别标签，再拿正则去猜别人家的拼法。
    # 下面的正则写的是操作系统或浏览器吐出的写法（"getaddrinfo failed"、
    # "ERR_NAME_NOT_RESOLVED"），而廉价的 TCP 预检用 snake_case 记号打标签 ——
    # `network_error: dns_failure (TCP preflight)`。实测：这串一个正则都不命中，
    # 于是不存在的域名拿到了 "unknown" 提示"try a different source or retry with
    # different parameters"，而它下面两行正确的提示写着 "do NOT retry"。重发一个
    # 死掉的主机名是唯一不可能奏效的动作，响应却在推荐它。
    #
    # "timeout" 刻意不在名单里：`timed? ?out` 已经能接住它。
    _lc = error_str.lower()
    for _token in ("connection_refused", "connection_reset", "tls_error",
                   "proxy_error", "dns_failure", "dns_unresolvable",
                   "private_target_blocked"):
        if _token in _lc:
            return _token, _HINTS[_token]

    for pattern, category in _PATTERNS:
        if pattern.search(error_str):
            return category, _HINTS[category]

    return "unknown", _HINTS["unknown"]


def get_hint(category: str) -> str:
    """取某个类别的可行动提示（回退到 'unknown'）。"""
    return _HINTS.get(category, _HINTS["unknown"])
