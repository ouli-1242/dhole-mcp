"""Network error classification for agent-actionable diagnostics.

Maps raw network error strings (from primp, patchright/playwright, OS socket
layer) into categories with actionable hints so AI agents know whether to
retry, switch sources, or give up — instead of seeing opaque "fetch failed".

Pure utility: no dependencies beyond the standard library. Safe to import
anywhere (event loop, worker thread, module level).
"""

from __future__ import annotations

import re
from typing import Tuple

# ─── Pattern table ────────────────────────────────────────────────────────────
# Order matters: first match wins. Patterns are checked case-insensitively
# against the raw error string.

_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Proxy errors (check FIRST: "proxy connection refused" should be proxy, not conn_refused)
    (re.compile(
        r"ERR_PROXY_CONNECTION_FAILED|proxy.*(?:refused|failed|error)|"
        r"tunnel connection failed|proxy_connect",
        re.IGNORECASE,
    ), "proxy_error"),

    # Connection refused (target actively rejecting TCP connections)
    (re.compile(
        r"ERR_CONNECTION_REFUSED|ConnectionRefusedError|os error 10061|"
        r"ECONNREFUSED|connection refused",
        re.IGNORECASE,
    ), "connection_refused"),

    # Connection reset (remote host forcibly closed an established connection)
    (re.compile(
        r"os error 10054|ERR_CONNECTION_RESET|ConnectionResetError|"
        r"ECONNRESET|远程主机强迫关闭|connection (?:was )?(?:forcibly )?(?:closed|reset)|"
        r"an existing connection was forcibly closed",
        re.IGNORECASE,
    ), "connection_reset"),

    # Fake-IP tunnel ranges (Clash / sing-box TUN hand out 198.18.0.0/15). A
    # failure naming such an address is the tunnel not reaching the real
    # network: "site down or outbound blocked" is the truthful framing, the
    # same verdict as a refused connection. Must precede `timeout`: the phrase
    # "timed out" shows up in these errors too, and the tunnel is the cause.
    (re.compile(r"\b198\.1[89]\.\d+\.\d+\b"), "connection_refused"),

    # DNS resolution failure
    (re.compile(
        r"ERR_NAME_NOT_RESOLVED|NameResolutionError|getaddrinfo failed|"
        r"nodename nor servname|Name or service not known|"
        r"DNS (?:resolution )?(?:failed|error)|resolve.*failed",
        re.IGNORECASE,
    ), "dns_failure"),

    # Browser process could not be started. Must come BEFORE `timeout`: the real
    # driver sentence is "Timed out while launching browser", and reading that as
    # a site timeout sends the caller to raise `timeout=` and retry against the
    # target — while the actual fact is that the OS refused to create the process.
    # Narrow on purpose (`(0x5)` with its parentheses): a bare `0x5` would match
    # the hex addresses that appear in other crash text.
    (re.compile(
        r"browserType\.[a-zA-Z]*launch|failed to launch|could not launch|"
        r"cannot access executable|executable doesn't exist|"
        r"access is denied|拒绝访问|\(0x5\)|spawn [^\s]+ ENOENT|"
        # 裸的那句驱动原话（不带 `browserType.launch:` 前缀）也会到分类器这里：
        # actions.py 早就按这个片段认它，两份名单不该有出入。
        r"while launching browser",
        re.IGNORECASE,
    ), "browser_launch"),

    # Timeout
    (re.compile(
        r"TimeoutError|ERR_TIMED_OUT|ERR_CONNECTION_TIMED_OUT|"
        r"ERR_NAVIGATION_TIMEOUT|timed? ?out|Navigation timeout|"
        r"Timeout \d+ms exceeded|deadline exceeded",
        re.IGNORECASE,
    ), "timeout"),

    # TLS / SSL errors
    (re.compile(
        r"CERTIFICATE|SSL[:\s]|TLS[:\s]|ssl_error|"
        r"handshake fail|certificate verify failed|"
        r"ERR_CERT_",
        re.IGNORECASE,
    ), "tls_error"),
]

# ─── Actionable hints per category ───────────────────────────────────────────

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
    """retryable for a network category, from the same table that writes its prose."""
    return _RETRYABLE_BY_CATEGORY.get(category, False)


def classify_network_error(error_str: str) -> Tuple[str, str]:
    """Classify a raw network error string into (category, actionable_hint).

    Categories:
    - connection_refused: target refused TCP connection (site down / env blocked)
    - connection_reset: remote host forcibly closed connection (anti-bot / firewall)
    - dns_failure: domain name resolution failed (a tier's own error text)
    - dns_unresolvable: the TCP probe's two 150ms-apart attempts both failed
    - private_target_blocked: dhole declined to dial a private target (no request
      was sent — this is a policy verdict, not a network one)
    - browser_launch: the browser process itself could not be started (not a site
      problem — checked before `timeout` so "Timed out while launching browser"
      does not get read as a slow target)
    - timeout: request timed out (site slow / network restricted)
    - tls_error: TLS/SSL handshake failure
    - proxy_error: proxy connection failure
    - unknown: cannot classify

    The hint is an agent-friendly sentence explaining what happened and what
    to do next (retry / switch / give up).

    Never raises. Returns ("unknown", generic_hint) for empty or unparseable input.
    """
    if not error_str:
        return "unknown", _HINTS["unknown"]

    # Match dhole's OWN category labels before pattern-guessing at someone else's.
    # The regexes below are written for the spellings an OS or browser emits
    # ("getaddrinfo failed", "ERR_NAME_NOT_RESOLVED"), and the cheap TCP probe
    # labels a failure with the snake_case token instead - `network_error:
    # dns_failure (TCP preflight)`. Measured: that string matched none of them, so
    # a domain that cannot exist got the "unknown" hint, "try a different source or
    # retry with different parameters", while the correct hint two lines below it
    # says "do NOT retry". Retrying a dead hostname is the one action that cannot
    # work, and the response recommended it.
    #
    # "timeout" is deliberately absent: `timed? ?out` already catches it.
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
    """Get the actionable hint for a category (fallback to 'unknown')."""
    return _HINTS.get(category, _HINTS["unknown"])
