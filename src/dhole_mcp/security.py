"""Input validation and security utilities for Dhole.

URL validation with SSRF protection, input sanitization,
and safe defaults for all external-facing parameters.
"""

import contextlib
import contextvars
import ipaddress
import os
import re
import time
from typing import Any, Optional
from urllib.parse import urlparse

# Maximum URL length to prevent DoS via oversized URLs
MAX_URL_LENGTH = 8192

# ─── 私网 / 本机 allowlist（G9，默认关闭）─────────────────────────────
#
# 开发者要抓自己的 dev / staging / Docker 服务，这些地址本来就被 SSRF 守卫挡住。
# 放开必须是**点名**：一个布尔开关把整个局域网（路由器、NAS、打印机）和云元数据
# 一起打开，不是「显式开启」能覆盖的风险。
#
#   DHOLE_ALLOW_PRIVATE_HOSTS=localhost,my-service.local   （进程级）
#   cred_defaults set={allow_private: ["my-service.local"], ...}  （落盘，每次读回）
#
# 匹配用的是 URL 里的**字面**主机名，不做归一化：allowlist 里写了 127.0.0.1，
# 不代表 0177.0.0.1（八进制写法）也被放行 —— 那条路是给绕过准备的。
ALLOW_PRIVATE_ENV = "DHOLE_ALLOW_PRIVATE_HOSTS"

# `allow_private: true` 的语义。本机 dev 服务是最常见的诉求，而回环**不是**整个
# 局域网：它有明确的边界（只有本机），放行它不会把路由器 / NAS / 云元数据一起打开。
_LOOPBACK_ALLOWED = frozenset({"localhost", "127.0.0.1", "::1", "0:0:0:0:0:0:0:1"})

# 云元数据端点：无论 allowlist 怎么写都不放行。它是 SSRF 的第一目标，一次误判就把
# 整机凭据交出去了，不属于「我的 dev 服务」这类诉求。
_METADATA_HOSTS = frozenset({
    "169.254.169.254", "metadata.google.internal", "metadata",
    "fd00:ec2::254", "100.100.100.200",
})

# 当前生效的 allowlist 有三个来源，合并在**守卫**这一步（见 private_hosts_allowed）：
#   · DHOLE_ALLOW_PRIVATE_HOSTS —— 进程级；
#   · cred_defaults 的 allow_private —— 落盘的存量，每次判定读回；
#   · _ALLOW_PRIVATE 这颗 ContextVar —— 一层作用域内的加宽（allow_private_scope）。
# 放在守卫而不是当作参数层层下传、也不是让每扇门自己从存储里读一次再 push，是为了让
# **每一个** validate_url 调用点 —— 包括抓取循环里的重定向复校验、robots.txt 那条旁路、
# tcp_preflight 的裸连接预检（它走 url_targets_internal -> validate_url）、以及从来没
# 推过作用域的那几扇门（web_search 的 find_similar 取源页那条腿就是 R02-141 量到的漏扇）
# —— 共用同一个答案，不会出现「主路径放行、复校验又拒」，也不会出现「五扇里有四扇记得」。
# 值是 (点名的主机集合, 是否放开整个 127.0.0.0/8)；默认空 = 这一层什么也没加。
_ALLOW_PRIVATE: contextvars.ContextVar[tuple] = contextvars.ContextVar(
    "_allow_private", default=(frozenset(), False))

_TRUE_WORDS = frozenset({"1", "true", "yes", "on"})


def _env_private_hosts() -> frozenset[str]:
    """DHOLE_ALLOW_PRIVATE_HOSTS，逗号分隔。默认空 = 不放行任何私网地址。"""
    raw = (os.environ.get(ALLOW_PRIVATE_ENV) or "").strip()
    if not raw:
        return frozenset()
    return frozenset(h.strip().lower().strip("[]") for h in raw.split(",") if h.strip())


def parse_allow_private(value) -> tuple[frozenset, bool]:
    """Normalize ``allow_private`` into ``(hosts, loopback_net)``.

    ``true``  -> 只放开回环。**不是整个局域网**：一次布尔开关把 RFC1918 全打开，
    等于把路由器、NAS、打印机和云元数据一起打开，那是「允许我的 dev 服务」没有
    请求的风险。回环有明确边界（只有本机），所以它是布尔开关能覆盖的唯一范围。

    列表 / 逗号分隔字符串 -> 点名那些主机。匹配用 URL 里的字面主机名，不做 IP
    归一化：allowlist 里有 127.0.0.1 不代表 0177.0.0.1（八进制写法）也被放行，
    那条路本来就是给绕过准备的。

    不抛异常：无法理解的值返回空集，调用方通过 _ARG_NOTES 把「这个值没生效」
    写进 summary —— 安全相关的选项被静默忽略是最糟的形态。
    """
    if value is None or value is False or value == "":
        return frozenset(), False
    if isinstance(value, bool):  # True
        return frozenset(_LOOPBACK_ALLOWED), True
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_WORDS:
            return frozenset(_LOOPBACK_ALLOWED), True
        return frozenset(h.strip().lower().strip("[]") for h in value.split(",") if h.strip()), False
    if isinstance(value, (list, tuple, set, frozenset)):
        hosts = frozenset(str(h).strip().lower().strip("[]") for h in value if str(h).strip())
        if hosts and hosts <= _TRUE_WORDS:
            return frozenset(_LOOPBACK_ALLOWED), True
        return hosts, False
    return frozenset(), False


def set_allow_private(value) -> Any:
    """Push the per-call private-host policy. Returns a reset token."""
    return _ALLOW_PRIVATE.set(parse_allow_private(value))


@contextlib.contextmanager
def allow_private_scope(value):
    """Run a block with one more host allowed than the standing policy names.

    This layer is **additive**: the guard merges it with the environment list and
    the stored `cred_defaults` value (see private_hosts_allowed), so a block can
    name a target that neither of them names, and can never un-name one that
    they do. That is the whole contract — nothing reads the stored value on
    behalf of a tool any more, so no door has to remember to do it.

    ``None``/``False``/``""`` is a no-op push (empty allowlist).
    """
    token = set_allow_private(value)
    try:
        yield
    finally:
        _ALLOW_PRIVATE.reset(token)


def stored_allow_private():
    """The raw stored `cred_defaults` value for the private-host door, or None.

    The one place the store is asked about this key. A caller that has to *report*
    the stored value (web_read's "that one does nothing" note) asks here, so the
    report and the gate can never read different rows.

    Fail-closed: a store that cannot be read widens nothing — the answer is the
    same one a process with no stored defaults gives.
    """
    try:
        from dhole_mcp import identity
        return identity.get("allow_private")
    except Exception:
        return None


def private_hosts_allowed() -> tuple[frozenset, bool]:
    """``(hosts, loopback_net)``：作用域那一层 并上 环境变量名单 并上 存量默认。

    三位一起并，是因为它们回答的是同一个问题（这台机器是不是调用方点名过的），而
    一个答案不许有三位读者：从前存量那一半只由四扇门各自读一次再 push，第五扇
    （`web_search` 取源页那条腿）就永久读不到它（评审 R02-141 · r329 同进程读数）。
    """
    scope_hosts, scope_loopback = _ALLOW_PRIVATE.get()
    stored_hosts, stored_loopback = parse_allow_private(stored_allow_private())
    hosts = scope_hosts | stored_hosts | _env_private_hosts()
    loopback = scope_loopback or stored_loopback
    if loopback:
        hosts = hosts | _LOOPBACK_ALLOWED
    return hosts, loopback


def _private_host_allowed(hostname: str, addr=None) -> bool:
    """Whether this internal target was explicitly allowed (G9).

    云元数据端点（169.254.169.254 等）**永远**不在这里返回 True：它是 SSRF 的第一
    目标，一次误判就把整机凭据交出去，不属于「我的 dev 服务」这类诉求。
    """
    hosts, loopback = private_hosts_allowed()
    if not hosts and not loopback:
        return False
    host = (hostname or "").strip().lower()
    if host in _METADATA_HOSTS:
        return False
    if host in hosts:
        return True
    # `true` 按**解析后的地址**判定：它放开的是整个 127.0.0.0/8，那一段里的每个
    # 地址都是同一台机器，所以 0177.0.0.1（八进制写法）在这里也放行 —— 并不比
    # 放开 127.0.0.1 更宽。点名名单才是字面匹配（见 parse_allow_private）。
    if loopback and addr is not None:
        try:
            if addr in ipaddress.ip_network("127.0.0.0/8"):
                return True
            if addr == ipaddress.ip_address("::1"):
                return True
        except Exception:
            return False
    return False

# Blocked URL schemes (SSRF, local file access, etc.)
_BLOCKED_SCHEMES = frozenset({
    "file", "ftp", "gopher", "data", "javascript", "vbscript",
    "about", "chrome", "chrome-extension", "jar",
})

# Hostnames that must never be targeted (bypass IP checks via name resolution)
_BLOCKED_HOSTNAMES = frozenset({
    "localhost", "metadata.google.internal", "169.254.169.254",
    "0.0.0.0", "0",  # "0" from bare IPv6 without brackets (e.g. http://0:0:0:...)
})

# DNS rebinding service suffixes (nip.io, sslip.io, etc.)
# These services resolve subdomains to arbitrary IPs, enabling SSRF bypass.
_DNS_REBINDING_SUFFIXES = (
    ".nip.io", ".sslip.io", ".xip.io", ".nip.name",
    ".1u.ms",  # resolves to loopback
)

# Private/reserved IP ranges that must never be targeted
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),       # Loopback
    ipaddress.ip_network("10.0.0.0/8"),        # Private
    ipaddress.ip_network("172.16.0.0/12"),     # Private
    ipaddress.ip_network("192.168.0.0/16"),    # Private
    ipaddress.ip_network("169.254.0.0/16"),    # Link-local
    ipaddress.ip_network("0.0.0.0/8"),         # Current network
    ipaddress.ip_network("224.0.0.0/4"),       # Multicast
    ipaddress.ip_network("240.0.0.0/4"),       # Reserved
    ipaddress.ip_network("::1/128"),            # IPv6 loopback (compressed)
    ipaddress.ip_network("::ffff:0:0/96"),     # IPv4-mapped IPv6 — extract mapped v4 for re-check
    ipaddress.ip_network("fc00::/7"),           # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),          # IPv6 link-local
    ipaddress.ip_network("::/128"),             # IPv6 unspecified
]

# Max CSS selector length to prevent ReDoS
MAX_CSS_SELECTOR_LENGTH = 4096

# Max header count and value length
MAX_HEADER_COUNT = 50
MAX_HEADER_VALUE_LENGTH = 8192


class SecurityError(ValueError):
    """Raised when input fails security validation."""
    pass


# ─── the resolver reality of this machine ────────────────────────────────
#
# DNS recheck is the right default: this tool's input is a URL an agent picked up
# somewhere untrusted, and "public hostname pointing at 169.254.169.254" is the
# main SSRF path. But a Clash / mihomo / sing-box TUN with a fake-ip resolver
# hands EVERY public hostname an address from a reserved block by design, so
# fail-closed here reads to the user as "nothing works" — and the only way out
# used to be knowing to set an env var before making the first call.
#
# The asymmetry makes this detectable rather than configurable: a genuine public
# resolver never answers 198.18.0.0/15 (RFC 2544 benchmarking) or a ULA
# fc00::/7 AAAA for example.com.
#
# 17.0 R3刀2 把三件事改了口径，都是往**严**的方向：
#   1. 判定不再由一个域名的答案决定 —— 两颗独立注册的域名合取（见 probe_fake_ip）。
#   2. 判成隧道也**不关掉这一层**：豁免精确到那两段地址本身。以前 verdict=fake-ip 时
#      整个复验停摆，公网名解析到 127.0.0.1 或 169.254.169.254 也不再有人看；现在
#      那些答案照拒，无论这台机器被判成什么。放宽守卫需要证据，而证据能支撑的只有
#      "这两段是隧道的产物"这一句，支撑不了"整层不必看"。
#   3. 判词仍是进程级缓存，但一次**因隧道段而起的拒绝**之前会重探一次（限流），
#      会话中途才挂上 TUN 的机器因此不必重启宿主。

_FAKE_IP_V4 = "198.18.0.0/15"
_FAKE_IP_V6 = "fc00::/7"
# 两颗不同 TLD、不同注册名的域名：一个被投毒、被 hosts 钉死、被某条 DIRECT 规则漏掉，
# 都不再足以决定整台机器的复验层。
_PROBE_DOMAINS = ("example.com", "example.org")
_TUNNEL_NETWORKS = (ipaddress.ip_network(_FAKE_IP_V4), ipaddress.ip_network(_FAKE_IP_V6))
_fake_ip_cache: tuple[str, str, str] | None = None
_auto_note = ""
# 重探的限流：一次拒绝付一次 `getaddrinfo` × 两颗域名，够便宜；不设限的话，
# 一个持续把公网名领进 ULA 的解析器会让每一次抓取都重探一遍。
_last_reprobe_mono = 0.0
_REPROBE_MIN_GAP_S = 60.0


def _tunnel_exempt_engaged() -> bool:
    """True when this host's resolver is a measured fake-ip tunnel.

    It no longer means "the recheck layer is off" (R3刀2): it means the two tunnel
    ranges are tolerated, because on such a host those addresses ARE every public
    name. 17.0 retired the ``DHOLE_SSRF_DNS_RECHECK`` knob (silently ignored), so
    the exemption is decided by measurement alone — there is no override on purpose:
    the historical reason to want one IS the fake-ip case, and that case is what the
    probe detects.
    """
    return probe_fake_ip()[0] == "fake-ip"


def probe_fake_ip() -> tuple[str, str, str]:
    """``(verdict, address, detail)`` — verdict is public / fake-ip / unprobed.

    Uncertainty is never a reason to relax: a resolver that cannot answer the probe
    (offline, or one that hides the answer) returns ``unprobed`` and the recheck
    applies in full.

    合取（R3刀2-A）：两颗独立注册的域名**都**答进隧道段才判 fake-ip。以前一颗
    （example.com）就决定整台机器的复验层 —— 那条路径当年记在 F-C2-J2 判决里
    （敌意 LAN 控制解析器 → 本进程首次 hostname 校验前毒化探测域），今天补上。
    只有一颗答得出也不判隧道：一颗的答案说明不了"这台机器的解析器给每个公网名
    都发保留地址"。
    """
    global _fake_ip_cache, _auto_note
    if _fake_ip_cache is not None:
        return _fake_ip_cache
    import socket
    answers: list[tuple[str, object]] = []
    for name in _PROBE_DOMAINS:
        try:
            infos = socket.getaddrinfo(name, None)
        except Exception:  # noqa: BLE001 - 问不到就是没有证据
            continue
        for info in infos:
            try:
                addr = ipaddress.ip_address(str(info[4][0]).split("%")[0])
            except ValueError:
                continue
            answers.append((name, addr))

    def in_tunnel(addr) -> bool:
        return any(addr in net for net in _TUNNEL_NETWORKS)

    outside = [f"{name} -> {addr}" for name, addr in answers if not in_tunnel(addr)]
    if outside:
        # 任何一颗答出了隧道段之外的地址，就是"这台机器的解析器正常"的证据 —— 复验全开。
        _fake_ip_cache = ("public", "", "")
        _record_resolver_probe("public", "")
        return _fake_ip_cache
    if {name for name, _a in answers} != set(_PROBE_DOMAINS):
        _fake_ip_cache = ("unprobed", "", "")
        _record_resolver_probe("unprobed", "")
        return _fake_ip_cache

    hit = str(answers[0][1])
    nets = sorted({str(net) for _n, a in answers for net in _TUNNEL_NETWORKS if a in net})
    said = ", ".join(f"{name} -> {addr}" for name, addr in answers)
    _auto_note = (
        f"ssrf: fake-ip tunnel on this host ({said}); only {_FAKE_IP_V4} + "
        f"{_FAKE_IP_V6} are tolerated - loopback, RFC1918, link-local and cloud "
        f"metadata are still refused.")
    _fake_ip_cache = ("fake-ip", hit, f"{said} in {', '.join(nets)}")
    _record_resolver_probe("fake-ip", hit)
    return _fake_ip_cache


def reset_fake_ip_probe() -> None:
    """Forget the cached verdict (tests point the probe at a stubbed resolver)."""
    global _fake_ip_cache, _auto_note
    _fake_ip_cache, _auto_note = None, ""


# The verdict is a process fact, and the two facts an operator can check are in
# different processes: `dhole --doctor` resolves the name again in its own, while
# the guard that answers a fetch uses whatever the server process measured - possibly
# at a startup hours ago, possibly under a different env block. Recording the
# measurement is what lets doctor say WHO answered, instead of reporting its own
# fresh answer under wording that implies it checked the server ("the DNS-recheck
# layer is skipped automatically on this host"). 2026-10-05, batch 6d.
_RESOLVER_RECORD_NAME = "resolver_probe.json"


def _record_resolver_probe(verdict: str, address: str) -> None:
    """Mirror this process's measurement for other processes to read.

    Best-effort and never raising: the verdict is already decided and applied by
    the time this runs, so a file that cannot be written must not change, delay,
    or accompany a security decision with an exception.
    """
    try:
        import json

        from dhole_mcp import paths
        # Resolved through paths.file() even for the WRITE, so the one seam the
        # test harness redirects (tests/conftest.py) covers both directions: a
        # suite whose stubbed DNS decides "public" on every call must not be able
        # to write that conclusion into the developer's real home.
        target = paths.file(_RESOLVER_RECORD_NAME)
        paths.ensure_private_dir(target.parent)
        with open(target, "w", encoding="utf-8") as f:
            json.dump({
                "verdict": verdict,
                "address": address,
                "probe": ", ".join(_PROBE_DOMAINS),
                "pid": os.getpid(),
                "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }, f, ensure_ascii=False)
        paths.harden_file(target)
    except Exception:  # noqa: BLE001 - a diagnostic echo, never a decision
        pass


def resolver_probe_record() -> dict:
    """What the last dhole process to measure this resolver answered, or ``{}``.

    ``{}`` means no fetch has ever probed here (a fresh home, or a process that
    died before its first URL). Callers must say that out loud rather than read
    it as "public": an absent record and a swallowed one look the same on disk.
    """
    try:
        import json

        from dhole_mcp import paths
        target = paths.file(_RESOLVER_RECORD_NAME)
        if not target.exists():
            return {}
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def recheck_posture_note() -> str:
    """Non-empty only when this host's resolver is a measured fake-ip tunnel.

    Absent otherwise, so an ordinary host pays nothing on the wire for a note
    that only means something on a fake-ip one. Runs the probe if nothing has
    asked yet - a diagnostic that answers "what is this process doing?" has to
    know the answer, not just the cached one.
    """
    return _auto_note if _tunnel_exempt_engaged() else ""


def _tunnel_range_refusal(internal: str) -> bool:
    """这道拒绝命中的是不是隧道那两段（也只有那两段值得在拒绝前重探一次）。

    危险段（回环 / RFC1918 / 链路本地 / 元数据）的拒绝不来这里：那种答案不是隧道的
    产物，拒它就是拒对了，重探只会把"拒对了"这件事弄成不确定的。
    """
    return any(str(net) in internal for net in _TUNNEL_NETWORKS)


def _reprobe_before_tunnel_refusal(hostname: str, internal: str) -> str | None:
    """会话中途才挂上的 TUN：判词是启动时探的，如今过期了 —— 现判一次再决定。

    以前这种机器的唯一出路是重启宿主（丢掉内存里的会话与凭据、暖着的浏览器）；R3刀1
    至少让缓存里的正文还能读，这一条把**新的**抓取也救回来。方向上仍然只有隧道段
    能被放宽，且放宽要证据（重探的两颗域名都答进隧道段），限流到每分钟一次。
    """
    global _last_reprobe_mono, _fake_ip_cache, _auto_note
    now = time.monotonic()
    if now - _last_reprobe_mono < _REPROBE_MIN_GAP_S:
        return internal
    _last_reprobe_mono = now
    _fake_ip_cache, _auto_note = None, ""
    if probe_fake_ip()[0] != "fake-ip":
        return internal
    _DNS_CHECK_CACHE.pop(hostname, None)
    return _resolves_to_internal(hostname)


# DNS 复查结果短缓存：一次抓取会对初始 URL + 每一跳重定向各调一次
# validate_url，而 validate_url 会从异步路径被调用 —— 不能每跳都做一次
# 阻塞式解析。被判定为内网的答案缓存久一点（重试时快速失败），放行的答案
# 只缓存几秒（覆盖一条重定向链，又不至于把 DNS rebinding 的 TOCTOU 窗口
# 拉长）。无论如何这只是纵深防御，不是唯一防线。
_DNS_BLOCK_TTL = 300.0
_DNS_ALLOW_TTL = 5.0
_DNS_CACHE_MAX = 1024
_DNS_CHECK_CACHE: dict[str, tuple[float, str | None]] = {}


def _hosts_file_path() -> str:
    """Path of the OS hosts file."""
    if os.name == "nt":
        root = os.environ.get("SystemRoot") or r"C:\Windows"
        return os.path.join(root, "System32", "drivers", "etc", "hosts")
    return "/etc/hosts"


_hosts_cache: tuple[float, dict[str, str]] | None = None


def _hosts_file_pin(hostname: str) -> str | None:
    """Return the IP the OS hosts file pins `hostname` to, else None.

    A hosts-file entry is a DELIBERATE local decision — ad/tracker blockers,
    mirrors, split-horizon overrides and (on many CN desktops) plain
    "don't let this resolve" pins. They look exactly like an attacker's DNS
    answer, so with the recheck on they must not be read as one: an attacker
    cannot write your hosts file, so honoring the pin keeps the protection where
    it matters while not breaking a machine that Google-DNS-blocks some domains
    on purpose.

    Parsed once per file change (mtime), never raises.
    """
    global _hosts_cache
    try:
        path = _hosts_file_path()
        mtime = os.path.getmtime(path)
        if _hosts_cache is not None and _hosts_cache[0] == mtime:
            table = _hosts_cache[1]
        else:
            table: dict[str, str] = {}
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.split("#", 1)[0].strip()
                    if not line:
                        continue
                    parts = line.split()
                    if len(parts) < 2:
                        continue
                    ip = parts[0]
                    for name in parts[1:]:
                        # 首行赢，与 glibc / Windows 的 hosts 语义一致：黑洞钉位
                        # （0.0.0.0）在前、后行改写的文件，OS 拒绝而这里曾放行。
                        table.setdefault(name.rstrip(".").lower(), ip)
            _hosts_cache = (mtime, table)
            _hosts_cache_reset_dns()
        return table.get(hostname.rstrip(".").lower())
    except Exception:
        return None


def _hosts_cache_reset_dns() -> None:
    """Drop DNS verdicts when the hosts file changes (it changes the answers)."""
    _DNS_CHECK_CACHE.clear()


def target_is_private(url: str) -> bool:
    """Geography, not permission: does this URL name a loopback/private target?

    Independent of the allowlist — "allowed" and "private" are different
    questions, and a caller may legitimately allow what is private. Hostnames
    answer False (geography unknown without a DNS roundtrip; the real attempt
    decides those). Never raises.
    """
    try:
        host = (urlparse(url).hostname or "").strip("[]").lower()
    except Exception:
        return False
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private


def allow_private_covers(value: Any, url: str) -> bool:
    """Whether a stored `allow_private` has any bearing on this target.

    Two ways it can. The URL names private/loopback geography literally, and the
    stored value is the policy the gate reads for that class of target. Or the
    target's host is one the value names — which is the other half of why anyone
    lists a host: a `my-service.local` name has no known geography until DNS
    answers, and the listing is what lets the attempt happen at all. A public name
    matched by neither has no door standing over it.

    Matching is the same literal match `_private_host_allowed` uses, so this can
    never claim a broader reach than the gate really has. Never raises.
    """
    if value is None or value is False or value == "":
        return False
    if target_is_private(url):
        return True
    try:
        host = (urlparse(url).hostname or "").strip("[]").lower()
    except Exception:
        return False
    if not host:
        return False
    hosts, _loopback = parse_allow_private(value)
    return host in hosts


def url_targets_internal(url: str, allow_internal: bool = False) -> bool:
    """这个 URL 是否指向内网 / 回环 / 元数据端点。不抛异常，只答是或否。

    刻意复用 validate_url 本体而不是另写一份判定：内网判据已经有 IP 字面量、八/十
    六进制变体、IPv4-mapped、被拦主机名表、DNS 复查五层，复制一份必然和原件漂移 ——
    而调用方（tcp_preflight 的裸 socket、浏览器的落地 URL 复校验）要的正是"和主路径
    同一个答案"。代价是畸形 URL 也算"是"（宁可拒掉裸连接）。
    """
    try:
        validate_url(url, allow_internal=allow_internal)
        return False
    except SecurityError:
        return True
    except Exception:
        # 解析不了的东西不该被当成"安全的内网目标"放过去做直连。
        return True


def _is_blackhole_pin(ip: str) -> bool:
    """hosts 里的"黑洞"钉位：0.0.0.0 / :: 这一类"哪儿也不去"的占位地址。

    它们作为**连接目标**时并不什么都不做 —— Windows 与 macOS 的栈把 0.0.0.0 当回环
    处理，Linux 上则多半直接失败 —— 所以"钉到 0.0.0.0"的屏蔽语义在至少两个平台上
    其实是"钉到本机"。判据用 is_unspecified，覆盖 0.0.0.0 / :: / ::0 / 0::0；
    裸 "0" 不是 hosts 文件里会出现的写法，ipaddress 也不接受，故不特殊处理。
    """
    try:
        return ipaddress.ip_address((ip or "").strip()).is_unspecified
    except ValueError:
        return False


def _resolves_to_internal(hostname: str) -> str | None:
    """Return a description of the internal address `hostname` resolves to, else None.

    A hosts-file pin short-circuits the check (see ``_hosts_file_pin``).
    Resolution failure (gaierror/timeout) is tolerated — treated as "not
    internal" — so an offline or polluted resolver does not turn into a hard
    error for every URL.
    """
    now = time.monotonic()
    cached = _DNS_CHECK_CACHE.get(hostname)
    if cached is not None and now < cached[0]:
        return cached[1]

    pin = _hosts_file_pin(hostname)
    if pin is not None:
        # 豁免要看钉到**哪个值**，不能只看"在不在 hosts 里"。广告/跟踪屏蔽类的
        # hosts 会把上千个域名钉到 0.0.0.0，而 0.0.0.0 作为连接目标在
        # Windows/macOS 上等同回环 —— 按存在性豁免等于把这一整批域名变成访问本机
        # 服务的入口，且攻击者不需要能写 hosts，只需要挑一个已经在里面的名字。
        # 黑洞钉位的语义是"这里什么都没有"，照连并把结果回报才真正违背用户意图。
        # 钉到 127.0.0.1 之类的本地开发覆盖仍按原样豁免：那是"我要打到本机这个服务"
        # 的显式决定，与黑洞的意图相反。
        if _is_blackhole_pin(pin):
            return f"hosts-pinned blackhole ({pin}) - treated as internal"
        _DNS_CHECK_CACHE[hostname] = (now + _DNS_ALLOW_TTL, None)
        return None

    verdict: str | None = None
    exempt = _tunnel_exempt_engaged()
    try:
        import socket as _socket
        infos = _socket.getaddrinfo(hostname, None)
    except Exception:
        infos = []
    for info in infos:
        info_ip = info[4][0]
        try:
            resolved = ipaddress.ip_address(info_ip.split("%")[0])
        except ValueError:
            continue
        for network in _PRIVATE_NETWORKS:
            try:
                if resolved in network:
                    if exempt and network in _TUNNEL_NETWORKS:
                        # 隧道在场时这两段就是"公网名"的样子，不是内网证据。继续扫
                        # 这个名的其余答案：一个名同时给出 ULA 与回环时，回环那一条
                        # 必须仍然算数。
                        continue
                    verdict = f"{info_ip} in {network}"
                    break
            except TypeError:
                pass  # IPv4/IPv6 type mismatch, skip
        if verdict:
            break

    if len(_DNS_CHECK_CACHE) >= _DNS_CACHE_MAX:
        _DNS_CHECK_CACHE.clear()
    _DNS_CHECK_CACHE[hostname] = (now + (_DNS_BLOCK_TTL if verdict else _DNS_ALLOW_TTL), verdict)
    return verdict


def _normalize_ip_notation(host: str) -> str | None:
    """Resolve alternate IP notations that curl/libcurl would resolve.

    curl_cffi uses libcurl, which accepts many IP address formats that
    Python's ipaddress module rejects. This normalizes them so we can
    check against private network ranges.

    Handles:
    - Octal notation: 0177.0.0.1 → 127.0.0.1
    - Hex notation: 0x7f000001 → 127.0.0.1, 0x7f.1 → 127.0.0.1
    - Decimal integer: 2130706433 → 127.0.0.1
    - Short-form: 127.1 → 127.0.0.1

    Returns dotted-decimal string like '127.0.0.1' or None.
    """
    cleaned = host.strip('[]')

    # Case: pure decimal integer (2130706433) or packed octal (017700000001)
    if cleaned.isdigit():
        try:
            # Leading zero → curl treats as octal: 017700000001 → 127.0.0.1
            if len(cleaned) > 1 and cleaned[0] == '0' and all(c in '01234567' for c in cleaned):
                val = int(cleaned, 8)
            else:
                val = int(cleaned)
            if val <= 0xFFFFFFFF:
                return f"{(val >> 24) & 0xFF}.{(val >> 16) & 0xFF}.{(val >> 8) & 0xFF}.{val & 0xFF}"
        except (ValueError, OverflowError):
            pass
        return None

    # Case: packed hex integer in a single segment (0x7f000001 → 127.0.0.1).
    # curl/libcurl's inet_aton accepts this form; ipaddress rejects it. Without
    # this branch, http://0x7f000001/ bypassed SSRF checks and reached loopback
    # (dotted hex like 0x7f.0.0.1 is handled by the parts loop below).
    if cleaned.lower().startswith("0x") and len(cleaned) > 2 \
            and all(c in "0123456789abcdef" for c in cleaned[2:].lower()):
        try:
            val = int(cleaned, 16)
            if val <= 0xFFFFFFFF:
                return f"{(val >> 24) & 0xFF}.{(val >> 16) & 0xFF}.{(val >> 8) & 0xFF}.{val & 0xFF}"
        except (ValueError, OverflowError):
            pass
        return None

    # Case: dotted notation (127.0.0.1, 0177.0.0.1, 0x7f.1, 127.1)
    parts = cleaned.split('.')
    if not (1 <= len(parts) <= 4):
        return None

    try:
        resolved = []
        for p in parts:
            if not p:
                return None
            # Leading zero → octal (curl behavior): '0177' → 127
            if len(p) > 1 and p[0] == '0' and all(c in '01234567' for c in p):
                resolved.append(int(p, 8))
            # Hex prefix: '0x7f' → 127
            elif p.lower().startswith('0x'):
                resolved.append(int(p, 0))
            else:
                resolved.append(int(p))

        # Expand short-form to 4 octets (curl/inet_aton behavior).
        # 2-part: a.b → a.0.0.b
        # 3-part: a.b.c → a.b.0.c
        # 4-part: a.b.c.d → a.b.c.d (no expansion needed)
        if len(resolved) == 2:
            resolved = [resolved[0], 0, 0, resolved[1]]
        elif len(resolved) == 3:
            resolved = [resolved[0], resolved[1], 0, resolved[2]]

        if any(o < 0 or o > 255 for o in resolved):
            return None
        return '.'.join(str(o) for o in resolved)
    except (ValueError, OverflowError):
        return None


# 探测说 public、这台机器现在却把公网名领进保留段时，那条拒绝的识别针与它唯一的出路。
# 写在守卫旁边而不是各工具各写一句：判据只有一份定义（这条拒绝在 web_read / crawl /
# discover 三条路上都出现，措辞一旦各自漂，调用方就读不出"这是同一件事"）。
RESOLVER_GUARD_NEEDLE = "resolves to internal/private IP"
RESOLVER_GUARD_RECOVERY = (
    "call again after a minute - a refusal naming one of the fake-ip ranges "
    "re-probes this machine's resolver before it refuses (at most once per 60s), so a "
    "tunnel that arrived mid-session is adopted without restarting; restarting the MCP "
    "host re-judges immediately (a verdict is otherwise kept for the life of the "
    "process), then confirm with `dhole --doctor`")

# 内网地址被拒时的那句出路。写在守卫旁边、六颗工具共用一份（同一惯例）：从前它落进
# `_invalid_request_result` 的**默认**模板 —— "web_read takes an absolute http(s) URL"，
# 可这条 URL 本来就是绝对 http(s)，那句话把读者推去改一个本来就对的东西（正是
# `_invalid_request_result` 自己 docstring 禁止的那类话）。web_crawl / web_discover
# 退得更远："Fetch failed ... retry with different parameters" —— 一个包都没发。
# 这一支与云元数据那一支不同，它**有**出口（allow_private），所以句子必须把门指出来。
#
# 正文里**不许**焊任何一扇的申报句（评审 R02-100 · 真机 `r119_url_shape_clauses.json`
# 的地址族六格）：这里从前写着 ", and nothing was sent."，六扇里只有写门 `web_request`
# 说对了，`web_read`/`web_discover`/`web_crawl`/`feed_read`（read 类，该说 fetched）与
# `web_act`（action 类，该说 run）全被这一份文案推去听别的门的话。申报那半句由
# `context.nothing_went_out()` 装配，装配点在各调用方（与 `TIMEOUT_CEILING_RECOVERY` 同一分工）。
PRIVATE_ADDRESS_RECOVERY = (
    "The URL is well-formed - what is refused is the address it points at. "
    "If you meant a public site, this one names a private range: "
    "search for the page instead. If this internal target is genuinely yours to "
    "fetch, the door is cred_defaults(set={'allow_private': true}) for loopback or "
    "{'allow_private': ['<host>']} to name one host - it never opens cloud metadata, "
    "which stays refused whatever the spelling.")

# 「这颗 URL 的**拼法**过不了门」那一路的出路本体（评审 R02-098 · 真机同一趟的形状族 14 格：
# ftp / 空串 / 空格 / 反斜杠，六扇门没有一扇说得出本门的动词）。`{tool}` 由装配点填：
# 同一句话在 `web_read` 与 `web_request` 上指的是不同的门，名字必须由站在门外的调用方交进来
# （`_invalid_request_result` 的 `tool=` 参数、crawl 引擎里的 `_TOOL`）。
URL_SHAPE_RECOVERY = ("Correct the argument and call again - no request was made. "
                     "{tool} takes an absolute http(s) URL (e.g. https://example.com).")

# 云元数据 / DNS-rebinding 服务名那一族的识别针与设计边界句（原先住在 `server.py` 的
# `_PERMANENT_URL_REFUSALS`，只有那一条路读得到）。搬到守卫旁边是为了 crawl 引擎也能认：
# 真机 `r119b_boundary_clauses.json` 的 M4/M5 里，`web_discover`/`web_crawl` 对这颗地址
# 交的是通用兜底句 `Fetch failed … retry with different parameters` —— 而这一族**没有**
# 任何参数可以改，那句假出路比不说更坏。
PERMANENT_URL_REFUSAL_PREFIXES = (
    "URL targets internal cloud metadata endpoint",
    "URL uses DNS rebinding service",
)
PERMANENT_URL_RECOVERY = (
    "No argument fixes this - it is a design boundary: dhole never fetches "
    "cloud-metadata endpoints or DNS-rebinding services (SSRF guard), whatever the "
    "spelling. If you meant a public site, the URL itself is wrong.")


# 「这个地址是内网的」那两类拒绝的识别针。判据只有一份定义：web_read / web_request /
# web_act / web_crawl / web_discover / feed_read 六条路都要说这句话，各自漂一份措辞，
# 调用方就读不出"这是同一件事"（与 RESOLVER_GUARD_NEEDLE 同一惯例）。
PRIVATE_ADDRESS_REFUSAL_PREFIXES = (
    "URL targets internal/private IP",
    "URL targets internal service:",
)


def url_refusal_family(msg: str) -> str:
    """一句守卫拒绝属于哪一族：`permanent` / `private` / `resolver` / `timeout` / `shape`。

    以前这一族由四处装配点各自扫两张前缀表来认（`server.py` 两处、`crawl.py` 一处、feed 的
    出路表一处），于是同一个形状修了三轮还在复发（R02-114 / R02-116 / R02-117）。真正住不下的
    是前缀表这一层：`URL targets internal service:` 由 `validate_url` 为 `_BLOCKED_HOSTNAMES`
    **整张名单**发出，那张名单里既有 `localhost`（allow_private 的 loopback 那一支真有门），
    也有 `metadata.google.internal`（永不开门，守卫自己就这么写着）。所以一颗前缀跨两个族，
    按前缀分派必然有一半年份说错话。

    判决因此改由**主机名归属**来定：句子里带着主机名的，先问它在不在元数据/重绑定名单里。
    只认族，不改判 —— 两族照旧全拒，出路句子也仍由 `security` 那几个常量一份写。
    """
    if RESOLVER_GUARD_NEEDLE in msg:
        return "resolver"
    # 认族用**包含**而不是 `startswith`：`feed_read` 那条路把守卫原话包在
    # `feed_read URL 校验失败: …` 里送上来（旧代码用 `in` 正是为此），只按开头匹配会让这一扇
    # 整个落到通用形状提示 —— 也就是 F100 要修的那颗在另一扇上复发。
    if any(p in msg for p in PERMANENT_URL_REFUSAL_PREFIXES):
        return "permanent"
    if any(p in msg for p in PRIVATE_ADDRESS_REFUSAL_PREFIXES):
        name = msg.rsplit(": ", 1)[-1].strip().lower().strip("[]") if ": " in msg else ""
        if name in _METADATA_HOSTS or name.endswith(_DNS_REBINDING_SUFFIXES):
            # 同一句话里的元数据名：这一族没有门（`localhost` 那种才留在 private）
            return "permanent"
        return "private"
    if any(n in msg for n in TIMEOUT_REFUSAL_NEEDLES):
        return "timeout"
    return "shape"


def url_refusal_recovery(msg: str, door: str) -> str:
    """一句守卫拒绝的**整条出路**：本门的申报 + 这一族共用的那段话（评审 R02-131）。

    从前这四支住在 `crawl.py` 的 except 块里（resolver / private / permanent / 其余皆形状），
    搜索门那条 `find_similar` 路要同一件事就得把四支再抄一遍 —— 而"同一颗参数在不同门上
    同一位裁判"正是这条轴上一次缺陷的形状（真机 `r265_bad_url_across_doors.json`：五扇交回
    `invalid_request: <守卫原话>` + 这段出路，`web_search` 那一扇交回的是一句否认调用方
    自己动作的"requires a url"）。句子本体仍只在这里一份，动词仍向 `context.nothing_went_out()`
    按门取；`URL_SHAPE_RECOVERY` 那一句里插的 `{tool}` 是唯一一处按门换字的共用文案。
    """
    from dhole_mcp.context import nothing_went_out
    family = url_refusal_family(msg)
    if family == "resolver":
        body = RESOLVER_GUARD_RECOVERY
    elif family == "private":
        body = PRIVATE_ADDRESS_RECOVERY
    elif family == "permanent":
        body = PERMANENT_URL_RECOVERY
    else:
        body = URL_SHAPE_RECOVERY.format(tool=door)
    return f"{nothing_went_out(door)} {body}"

# `validate_timeout` 那句预算的识别针（评审 R02-096 · R02-097）。同一惯例：判据与句子都只许
# 一份定义 —— 这颗错从前由六条路各自接手，三种前缀（`invalid_request: Timeout…` /
# `invalid_request: timeout= Timeout…` / `feed_read timeout: Timeout…`）加上 crawl 那种
# **根本没有拒绝**（页级汇总把它说成"站点慢"）。读数 `review/.runtime/p1/r110_timeout_gate_sweep.json`。
TIMEOUT_REFUSAL_NEEDLES = (
    "Timeout exceeds maximum",
    "Timeout must be positive",
    "Timeout must be a number",
)

# 出路只有一份，措辞对三种成因都成立（超帽 / 非正数 / 不是数字），所以它点名的是**区间**而不是方向。
TIMEOUT_CEILING_RECOVERY = (
    "timeout= is this call's whole budget in milliseconds: 1-120000 (2 minutes). "
    "The value named in `error` is outside that range, so no retry with a bigger "
    "number can ever be honoured - rewrite the number, or omit timeout= to take "
    "this tool's own default.")

# `deadline_ms=` 的出路（评审 R02-120 · 同一趟 r209）。它点名的是**符号**而不是区间，
# 因为这一颗真没有上限 —— 与上面那句 timeout 的差别是判据，不是抄漏了。默认数不在这里写：
# 那个数住在引擎的签名里，抄进文案就成了「一个数两个家」（R02-093 记的正是这一族的漂移）。
DEADLINE_RECOVERY = (
    "deadline_ms= is this crawl's whole-run budget in milliseconds: any positive "
    "number, and it has no ceiling - omit it to take this tool's own default. "
    "Zero and negatives are not budgets: the value named in `error` would stop the "
    "run before its first page, so rewrite the number rather than retry it.")

# `validate_proxy` 的六类拒绝的识别针（评审 R02-105 · 真机 `r132b_broken_proxy.json`）。
# 六颗都从 `validate_proxy` 一处 raise，所以这份名单与那位守卫同源；调用方需要它是因为
# **出路**不同：被拒的是代理那颗值，而 fall-through 取的是 URL 拼法模板（"web_read takes
# an absolute http(s) URL"），那句话会把读者推去改一颗本来就写对的 URL。
PROXY_REFUSAL_PREFIXES = (
    "Invalid proxy URL",
    "Invalid proxy server URL",
    "Proxy scheme must be",
    "Unknown proxy config keys",
    "Proxy dict must include",
    "Proxy must be a URL string",
)
# 一句要过两道旧闸（本仓已各有判据钉着）：**门话不许焊进共用常量**（申报那半句由各调用方
# 按门装配，见 `context.nothing_went_out`），**退役参数不许以调用形状出现**（17.0 的抓取工具
# 不收 proxy 这颗参数，所以这句说「没有这颗参数」而不写 `名字=` 那种传法 —— 写了就等于让
# 调用方以为自己试过了）。
PROXY_RECOVERY = (
    "The proxy in force is not something this call carries: the 17.0 fetch tools have "
    "no proxy argument of their own - the value belongs to cred_defaults. Replace it "
    "there (set={'proxy': 'http://host:port'}; schemes http/https/socks5/socks5h) or "
    "close it with unset=['proxy']. The value named in `error` is the one that failed "
    "this check.")

# `got:` 那一格引用多少字符。帽是为了回执可读，不是为了省内存：一条超长的串本来就被
# MAX_URL_LENGTH 拦在别处，这一格的职责是**让人认出是自己那发**，不是复述全文。
_SCHEME_FACE_QUOTE_CHARS = 120


def _unsupported_scheme_face(url: str, scheme: str) -> str:
    """「只支持 http/https」那一发的措辞作者：引用**调用方发出去的那串**，不是解析残片。

    从前这句写 `got: {parsed.scheme}`（评审 R02-018）。两张脸都因此说错了话：

      * 不带 scheme 的输入（`上一条里最新的那篇` / `example.com/page` / `/tmp/a.pdf`）
        解析出的 scheme 是空串 ⇒ 回执是 `got: ` —— **冒号后面空的**，读起来像参数没传过去；
      * `localhost:8399/x` 的 scheme 被解析成 `localhost` ⇒ `got: localhost`，
        把一个 host 说成了 scheme，比空白更进一步：它把人往错方向指。

    所以引用的是输入本身，而解析读出来的那个词要写明是**读出来的**（`as read from`），
    不是对输入的判断。三条随引用一起付的成本：截断并说出截断、`repr` 转义控制字符
    （换行原样上路会伪造行）、`redact_api_key`（引用别人的输入不等于把凭据抄进回执）。
    这一格只说看到了什么；怎么改是 `next_action` 那句话的活（一句处方一个作者）。
    """
    quoted = redact_api_key(url)
    hidden = max(len(quoted) - _SCHEME_FACE_QUOTE_CHARS, 0)
    if hidden:
        quoted = quoted[:_SCHEME_FACE_QUOTE_CHARS] + f"…[{hidden} more chars not quoted]"
    seen = (f"no scheme in {quoted!r}" if not scheme
            else f"{scheme!r} as read from {quoted!r}")
    return f"Only http and https URLs are supported, got: {seen}"


# 引用控制字符左右那一小段的帽。与 `_SCHEME_FACE_QUOTE_CHARS` 同一分工：引用是为了让人认得出
# 是自己那一发，不是为了复述全文；超长的输入在更上面已经被 MAX_URL_LENGTH 拦在别处。
_CONTROL_CHAR_QUOTE_CHARS = 60


_CONTROL_CHAR_NAMES = {0x00: "NUL", 0x09: "tab", 0x0a: "newline", 0x0b: "vertical tab",
                       0x0c: "form feed", 0x0d: "carriage return", 0x7f: "delete"}


def _control_char_face(url: str) -> str:
    """第一颗控制字符的名字与它为什么不能被"修好"。没有控制字符 = 空串。

    为什么在这里拒，而不是像过去那样让下面两件事各自发生（实测 `r146` / `r147` · 一台自造的
    echo 服务记它自己收到了什么）：

      * 空格与 `0x7F` 在送出之前被**百分号编码** —— `/r146 x` 到服务端是 `/r146%20x`，
        资源还是那颗资源；
      * LF / CR / TAB 到服务端是 `/r146x` —— 字符被**删掉**了，`/r146\\nx` 与 `/r146x`
        打到同一颗资源，而回执是 200、一切正常。`urlparse` 在这一支上已经与传输层同见
        （它对同一串的 path 视图就是 `/r146x`），所以连校验器自己都不觉得那里有什么。

    静默换目标不是"宽容"：调用方拿着 200 去引用一份他从未点名的资源。拆头/拆行这一族
    实测**没有**打通（服务端始终 14 颗头，与干净基准同数），所以这里按实测记：害处是换目标，
    不写成 CVE。首尾的空白在更上面已被 `strip()` 收掉，那正是"从终端粘一颗 URL 带了个换行"
    的合法形状 —— 这一句只管**地址中间**的那一颗，粘帖那一族不受影响。

    引用形状走码位（`U+000A`）并把那颗字符**左右那一小段**一起交出：转义、有帽、过 redaction。
    三张成本一张都不能少 —— 回执里一颗真换行等于让一次调用伪造一行输出，而只报偏移不报上下文
    等于让读者手里没有可改的东西（`_unsupported_scheme_face` 与
    `test_a_refusal_that_quotes_what_the_caller_actually_sent` 钉的是同一条轴）。
    """
    for offset, ch in enumerate(url):
        code = ord(ch)
        if code <= 0x1F or code == 0x7F:
            name = _CONTROL_CHAR_NAMES.get(code, "control")
            start = max(0, offset - _CONTROL_CHAR_QUOTE_CHARS // 2)
            window = url[start:offset + _CONTROL_CHAR_QUOTE_CHARS]
            hidden = len(url) - start - len(window)
            quoted = redact_api_key(repr(window))
            if hidden > 0:
                quoted += f"…[{hidden} more chars not quoted]"
            return (f"URL contains a control character ({name}, U+{code:04X}) at offset "
                    f"{offset}; around it: {quoted}. A space is percent-encoded on the way "
                    "out and keeps its target; this one is dropped, so the resource fetched "
                    "would not be the one named here. Send the address without it - leading "
                    "and trailing whitespace is already trimmed for you.")
    return ""


def validate_url(url: str, allow_internal: bool = False) -> str:
    """Validate and sanitize a URL. Returns the validated URL.

    Protects against:
    - SSRF attacks (internal IPs, localhost, cloud metadata endpoints)
    - File:// and other dangerous schemes
    - Oversized URLs (DoS)
    - Malformed URLs
    - URL parsing confusion attacks (backslash in authority, bracketed hosts)
    - CVE-2024-11168: urllib.parse bracket-host SSRF bypass
    - CVE-2025-0454-style: backslash-@ authority confusion between parsers

    Args:
        url: The URL to validate.
        allow_internal: If True, allow private/internal IPs (for testing only).

    Returns:
        The validated URL string.

    Raises:
        SecurityError: If the URL fails validation.
    """
    if not url or not isinstance(url, str):
        raise SecurityError("URL must be a non-empty string")

    url = url.strip()

    if not url:
        raise SecurityError("URL must be a non-empty string")

    if len(url) > MAX_URL_LENGTH:
        raise SecurityError(
            f"URL exceeds maximum length of {MAX_URL_LENGTH} characters"
        )

    # CRITICAL: Reject URLs containing backslash in authority section.
    # Backslash causes parsing confusion between urlparse (treats \\ as part
    # of userinfo/netloc) and urllib3/requests (treats \\ as path delimiter).
    # This enables SSRF: http://evil.com\\@127.0.0.1/ passes urlparse validation
    # (hostname=127.0.0.1) but fetches from evil.com.
    # See: corCTF 2025 "Python URL Parsing Confusion"; CVE-2025-0454
    face = _control_char_face(url)
    if face:
        raise SecurityError(face)
    if "\\" in url:
        raise SecurityError("URL contains backslash character (potential SSRF bypass)")

    try:
        parsed = urlparse(url)
    except Exception as e:
        raise SecurityError(f"Malformed URL: {e}")

    # Block dangerous schemes
    scheme = (parsed.scheme or "").lower()
    if scheme in _BLOCKED_SCHEMES:
        raise SecurityError(f"URL scheme '{scheme}' is not allowed")

    if scheme not in ("http", "https"):
        raise SecurityError(_unsupported_scheme_face(url, scheme))

    # Block bracketed hosts that aren't valid IPv6 — CVE-2024-11168
    # urlparse in Python <3.12.7 improperly validated bracketed hosts, allowing
    # arbitrary content in brackets to be treated as valid hostnames.
    netloc = (parsed.netloc or "").lower()
    if "[" in netloc or "]" in netloc:
        if not (netloc.startswith("[") and "]" in netloc):
            raise SecurityError("URL contains malformed bracketed host (potential SSRF bypass)")
        # Extract the bracketed part and verify it's a valid IPv6 address
        bracket_content = netloc[1:netloc.index("]")]
        try:
            ipaddress.IPv6Address(bracket_content)
        except ipaddress.AddressValueError:
            # Allow IPvFuture (v[0-9]+\..+) but nothing else
            if not re.match(r'^v[0-9]+\..+$', bracket_content, re.IGNORECASE):
                raise SecurityError("URL contains invalid bracketed host (potential SSRF bypass)")

    hostname = parsed.hostname
    if not hostname:
        raise SecurityError("URL has no valid hostname")

    # Reject invalid or out-of-range ports (http://host:70000/ or :abc/).
    # urlparse accepts these silently; most HTTP clients refuse or misroute
    # them, so treating them as valid hides config errors and can point at
    # an unexpected service.
    try:
        port = parsed.port
    except ValueError:
        raise SecurityError("URL has invalid port")
    if port is not None and not (0 < port <= 65535):
        raise SecurityError(f"URL port out of range: {port}")

    # Check for internal IPs (only if hostname is an IP)
    if not allow_internal:
        addr = None
        is_ip = False
        try:
            addr = ipaddress.ip_address(hostname)
            is_ip = True
        except ValueError:
            pass  # Not a standard IP — try alternate notations below

        # Handle alternate IP notations that ipaddress rejects but curl resolves
        # (octal, hex, decimal integer, short-form). This is critical: Python's
        # ipaddress module rejects leading zeros (CVE-2021-29921), but curl_cffi
        # (libcurl) resolves them. An attacker could use http://0177.0.0.1 to
        # bypass SSRF protection and reach the loopback interface.
        if not is_ip:
            normalized = _normalize_ip_notation(hostname)
            if normalized is not None:
                try:
                    addr = ipaddress.ip_address(normalized)
                    is_ip = True
                except ValueError:
                    pass  # Not resolvable to a valid IP after normalization

        if is_ip:
            # 元数据字面量按规范化地址拦，且先于 allowlist 询问：100.64.0.0/10
            # （CGNAT，阿里云 IMDS 所在）不在 _PRIVATE_NETWORKS，私网循环对它
            # 不设防，allowlist 分支只在地址命中私网段时才被问 —— 不在这里拒，
            # 点名放行也拦不住它。十进制/十六进制/短格式归一后是同一个地址，
            # 一样命中。
            # IPv4-mapped IPv6 也算同一个地址：`::ffff:169.254.169.254` 与
            # `0xA9FEA9FE` 是同一颗端点，只按字符串比就把"任何拼法都不放行"这件事
            # 交给了写法。剥开映射再比一次，这条保证才是结构性的而不是巧合。
            _mapped = (addr.ipv4_mapped
                       if isinstance(addr, ipaddress.IPv6Address) else None)
            if str(addr) in _METADATA_HOSTS or (
                    _mapped is not None and str(_mapped) in _METADATA_HOSTS):
                raise SecurityError(
                    f"URL targets internal cloud metadata endpoint ({_mapped or addr})"
                )
            # G9: an internal target the operator named in DHOLE_ALLOW_PRIVATE_HOSTS
            # or allow_private is fetched instead of refused. Cloud metadata
            # is never allowed (see _private_host_allowed).
            _allowed = _private_host_allowed(hostname, addr)
            # Check IPv4-mapped IPv6: extract the mapped IPv4 and re-check it
            if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
                ipv4_addr = addr.ipv4_mapped
                for network in _PRIVATE_NETWORKS:
                    if isinstance(network, ipaddress.IPv4Network) and ipv4_addr in network:
                        if _allowed and _private_host_allowed(hostname, ipv4_addr):
                            break
                        raise SecurityError(
                            f"URL targets internal/private IP (IPv4-mapped {hostname} → {ipv4_addr} in {network})"
                        )
            for network in _PRIVATE_NETWORKS:
                try:
                    if addr in network:
                        if _allowed:
                            break
                        raise SecurityError(
                            f"URL targets internal/private IP ({hostname} in {network})"
                        )
                except TypeError:
                    pass  # IPv4/IPv6 type mismatch, skip
        else:
            # Block known internal hostnames (cloud metadata, localhost, DNS rebinding services)
            hostname_lower = hostname.lower()
            if hostname_lower in _BLOCKED_HOSTNAMES and not _private_host_allowed(hostname_lower):
                raise SecurityError(f"URL targets internal service: {hostname}")
            # 裸主机名 `metadata` 不在 _BLOCKED_HOSTNAMES 里，但它在 _METADATA_HOSTS
            # 里 —— 同一道门，没有 allowlist 出口。
            if hostname_lower in _METADATA_HOSTS:
                raise SecurityError(f"URL targets internal cloud metadata endpoint: {hostname}")
            # Block DNS rebinding services that resolve to internal IPs.
            # 没有 allowlist 出口：这类服务把任意域名解析到任意 IP，放行它就等于
            # 把整份内网判定交给了外部解析器。
            if hostname_lower.endswith(_DNS_REBINDING_SUFFIXES):
                raise SecurityError(f"URL uses DNS rebinding service: {hostname}")
            # 纵深防御（报告声明 4）：域名经 DNS 解析到的内网 IP 复查。
            # 恒常开（R3刀2-B）：隧道在场时豁免的也只有 198.18.0.0/15 与 fc00::/7 这两段，
            # 其余段无论这台机器被判成什么都照拒 —— 以前一个 fake-ip 判词会让整层停摆，
            # 那时"公网名解析到 127.0.0.1"已经没人看了。
            # 只拒绝"解析成功且命中内网"；解析失败（gaierror/超时）容忍。
            # 存在 DNS rebinding TOCTOU 竞态，作为纵深防御而非唯一防线。
            # 点名放行的主机跳过这一条：它本来就该解析到内网。
            if not _private_host_allowed(hostname_lower):
                internal = _resolves_to_internal(hostname)
                if internal and _tunnel_range_refusal(internal):
                    # 即将因为隧道段拒绝：先问一次现判 —— 会话中途才挂上的 TUN 走这里自愈。
                    internal = _reprobe_before_tunnel_refusal(hostname, internal)
                if internal:
                    if _tunnel_range_refusal(internal):
                        # 隧道段的拒绝：现判没确认隧道（或限流窗口内还没轮到重探）。
                        # 不许说成"这不是代理的产物" —— 它恰恰是代理最典型的产物。
                        why = (
                            "This guard is fail-closed: the two fake-ip ranges are "
                            "tolerated only while this process measures a fake-ip tunnel, "
                            f"and its probe did not confirm one (a refusal of these ranges "
                            f"re-probes first, at most once per "
                            f"{int(_REPROBE_MIN_GAP_S)}s).")
                    else:
                        why = (
                            "This guard is fail-closed: on a fake-ip host only the tunnel "
                            "ranges (198.18.0.0/15, fc00::/7) are tolerated, and this "
                            "answer is in a range a proxy does not hand out - it is "
                            "refused whatever this machine measured.")
                    raise SecurityError(
                        f"URL hostname {hostname} resolves to internal/private IP "
                        f"({internal}). {why}"
                    )

    return url


def validate_css_selector(selector: Optional[str]) -> Optional[str]:
    """Validate a CSS selector for injection/DoS safety.

    Args:
        selector: The CSS selector string, or None.

    Returns:
        The validated selector, or None.

    Raises:
        SecurityError: If the selector fails validation.
    """
    if selector is None:
        return None

    if not isinstance(selector, str):
        raise SecurityError("CSS selector must be a string or None")

    selector = selector.strip()

    if not selector:
        return None  # Empty/whitespace-only selectors have no effect

    if len(selector) > MAX_CSS_SELECTOR_LENGTH:
        raise SecurityError(
            f"CSS selector exceeds maximum length of {MAX_CSS_SELECTOR_LENGTH}"
        )

    # Block selectors that could cause excessive backtracking (ReDoS)
    # Nested pseudo-classes, deeply nested combinators, etc.
    depth = selector.count(">") + selector.count(" ") + selector.count("+") + selector.count("~")
    if depth > 100:
        raise SecurityError("CSS selector nesting depth too high")

    # Block inline script/style injection attempts
    dangerous = ["<script", "<style", "javascript:", "expression("]
    lowered = selector.lower()
    for d in dangerous:
        if d in lowered:
            raise SecurityError("CSS selector contains potentially dangerous content")

    return selector


def validate_html_css_selector(selector: Optional[str],
                               param: str = "css_selector") -> Optional[str]:
    """Injection guard **plus** compilability, for the selectors the extraction
    engine (lxml + cssselect) will actually run.

    ``validate_css_selector`` answers "is this hostile", not "does this parse".
    The compile used to happen only inside the extractor, where
    ``except Exception -> None`` swallowed it: a selector like ``a >> b {;}``
    produced a 200 plus the **whole unfiltered document**, with nothing in
    ``summary`` or ``next_action`` saying the selector never took effect. A
    silently ignored argument is the failure this project keeps refusing, and it
    is cheaper to refuse before the request goes out than to hand back a page the
    caller asked to narrow.

    The compile runs here, not in ``validate_css_selector``: the browser tier
    (``wait_selector``, an action's ``selector``) speaks Chromium's CSS, which
    accepts ``:visible`` and friends. Judging those selectors by lxml's scale
    would take away a capability that works.

    Args:
        selector: The CSS selector string, or None.
        param: Which argument to name in the error, since one guard serves
            ``css_selector``, ``schema[...]["selector"]`` and ``nested_selector``.

    Raises:
        SecurityError: On the injection guard's own refusals, or when lxml's
            cssselect cannot compile the selector.
    """
    validated = validate_css_selector(selector)
    if validated is None:
        return None
    try:
        from lxml.cssselect import CSSSelector
        CSSSelector(validated)
    except Exception as e:
        raise SecurityError(
            f"{param} {validated!r} is not a selector the extraction engine can "
            f"compile ({type(e).__name__}: {str(e)[:120]}) - it would have been "
            f"ignored and the whole page returned") from e
    return validated


def validate_headers(headers: Optional[dict]) -> Optional[dict]:
    """Validate custom HTTP headers for injection/prevention.

    Args:
        headers: Dict of header name -> value, or None.

    Returns:
        The validated headers dict, or None.

    Raises:
        SecurityError: If headers fail validation.
    """
    if headers is None:
        return None

    if not isinstance(headers, dict):
        raise SecurityError("Headers must be a dictionary or None")

    validated = {}
    for name, value in headers.items():
        if not isinstance(name, str) or not name.strip():
            raise SecurityError("Header names must be non-empty strings")
        if value is not None and not isinstance(value, str):
            raise SecurityError(f"Header '{name}' value must be a string or None")

        name = name.strip()
        value = (value or "").strip()

        if len(value) > MAX_HEADER_VALUE_LENGTH:
            raise SecurityError(
                f"Header '{name}' value exceeds max length of {MAX_HEADER_VALUE_LENGTH}"
            )

        # Block header injection via newline characters in name or value
        if "\n" in name or "\r" in name or "\n" in value or "\r" in value:
            raise SecurityError(
                f"Header '{name}' contains newline characters (header injection)"
            )

        # Block header names with backslashes — parsers disagree on whether a
        # backslash separates fields (some HTTP stacks treat it as a soft fold),
        # so a name like 'X-Foo\Bar' can be interpreted differently downstream.
        if "\\" in name:
            raise SecurityError(
                f"Header name '{name}' contains backslash (field confusion)"
            )

        # Block forbidden headers that could interfere with Scrapling
        forbidden = {"host", "content-length", "transfer-encoding", "connection"}
        if name.lower() in forbidden:
            raise SecurityError(
                f"Header '{name}' is managed internally and cannot be overridden"
            )

        validated[name] = value

    if len(validated) > MAX_HEADER_COUNT:
        raise SecurityError(
            f"Too many custom headers ({len(validated)}, max {MAX_HEADER_COUNT})"
        )

    return validated


def validate_proxy(proxy: Optional[str | dict]) -> Optional[str | dict]:
    """Validate proxy configuration.

    Args:
        proxy: Proxy URL string, dict with server/username/password, or None.

    Returns:
        The validated proxy config.

    Raises:
        SecurityError: If proxy config fails validation.
    """
    if proxy is None:
        return None

    if isinstance(proxy, str):
        proxy = proxy.strip()
        if not proxy:
            return None
        # Basic URL validation for proxy
        try:
            parsed = urlparse(proxy)
            if parsed.scheme not in ("http", "https", "socks5", "socks5h"):
                raise SecurityError(
                    f"Proxy scheme must be http, https, socks5, or socks5h, got: {parsed.scheme}"
                )
        except Exception as e:
            raise SecurityError(f"Invalid proxy URL: {e}")
        return proxy

    if isinstance(proxy, dict):
        allowed_keys = {"server", "username", "password"}
        extra = set(proxy.keys()) - allowed_keys
        if extra:
            raise SecurityError(f"Unknown proxy config keys: {extra}")

        server = proxy.get("server", "").strip()
        if not server:
            raise SecurityError("Proxy dict must include 'server' key")

        # Validate the server URL with the same scheme set accepted for string
        # proxies (http/https/socks5/socks5h). Previously this called validate_url,
        # which only permits http/https — so a dict proxy with a socks5 server was
        # rejected even though the string form was accepted.
        try:
            parsed = urlparse(server)
            if parsed.scheme not in ("http", "https", "socks5", "socks5h"):
                raise SecurityError(
                    f"Proxy scheme must be http, https, socks5, or socks5h, got: {parsed.scheme}"
                )
        except SecurityError:
            raise
        except Exception as e:
            raise SecurityError(f"Invalid proxy server URL: {e}")
        return proxy

    raise SecurityError("Proxy must be a URL string, dict, or None")


#: `timeout=` 能给的最大的那一颗数（120 秒）。它有三处读者：门口拒绝、常驻层的
#: "max 120000" 那句、以及预算用完时给建议的那句话 —— 三处各抄一份就是漂移的开始
#: （同 `_HTTP_ATTEMPT_CAP_MS` 那条对齐守卫的理由）。
TIMEOUT_MAX_MS = 120_000


def validate_timeout(timeout: int | float) -> int | float:
    """Validate timeout value.

    ``TIMEOUT_MAX_MS`` is the one author of the ceiling: the wire descriptions of
    `timeout=` quote "max 120000" and `quality._budget_advice` names it when the
    caller has already spent a large budget, so a number with two homes and no
    test is where this family drifts (see the alignment guard in
    `tests/test_a_timeout_bigger_than_the_per_attempt_cap_says_what_it_bought.py`).

    Args:
        timeout: Timeout in milliseconds (browser) or seconds (HTTP).

    Returns:
        The validated timeout.

    Raises:
        SecurityError: On invalid timeout.
    """
    if not isinstance(timeout, (int, float)):
        raise SecurityError("Timeout must be a number")
    if timeout <= 0:
        raise SecurityError("Timeout must be positive")
    if timeout > TIMEOUT_MAX_MS:
        raise SecurityError(f"Timeout exceeds maximum of {TIMEOUT_MAX_MS}ms (2 minutes)")
    return timeout


def validate_deadline(deadline_ms: int | float) -> int | float:
    """`deadline_ms=` 的取值门（评审 R02-120 · 真机 `r209_deadline_radius.json`）。

    只管符号，**不管上限** —— 这是刻意的不对称，不是漏了一顶帽：`timeout=` 帽住的是单次
    HTTP 尝试（放任它会挂住这台机器），而这一颗只决定整轮什么时候停，行数与单发各自还有帽
    （`max_pages` / `attempt_cap_ms`）。取舍记在 `review/decisions.md`。

    非正数不是一种预算：0 会让这一发在第一页之前停下，而线格式把「0」与「没点这颗」压成
    同一件事（`wire.py` 那张 omittable 名单里 `deadline_ms_applied` 的默认值正是 0），
    所以从前这一发的回执同时说「进了循环并被时钟砍掉」和「从没进到抓取循环」。
    拒在门外，那句「缺席 = 没进循环」的契约就重新成立。
    """
    if deadline_ms <= 0:
        raise SecurityError("Deadline must be positive")
    return deadline_ms


def validate_search_query(query: str) -> str:
    """Validate and sanitize a search query.

    Args:
        query: The search query string.

    Returns:
        The sanitized query.

    Raises:
        SecurityError: On invalid query.
    """
    if not query or not isinstance(query, str):
        raise SecurityError("Search query must be a non-empty string")

    query = query.strip()

    if not query:
        raise SecurityError("Search query is empty after stripping")

    if len(query) > 2048:
        raise SecurityError("Search query exceeds maximum length of 2048 characters")

    # Strip control characters (0x00-0x1F except tab) and delete (0x7F)
    query = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', query)

    return query


def redact_api_key(text: str) -> str:
    """Redact API keys and proxy credentials from error messages/logs.

    Detects TinyFish API key format, generic API key patterns,
    and proxy URLs with embedded credentials.
    """
    # TinyFish keys: sk-tinyfish- followed by alphanumeric
    text = re.sub(r'sk-tinyfish-[a-zA-Z0-9]+', 'sk-tinyfish-[REDACTED]', text)
    # Generic key patterns (sk-, pk-, api_)
    text = re.sub(r'(?:sk|pk|api_key)[-_][a-zA-Z0-9]{20,}', '[API_KEY_REDACTED]', text)
    # Proxy URLs with embedded credentials: http://user:pass@host
    # Catches the full credential portion before @
    text = re.sub(
        r'(https?://)[^:@\s]+:[^@\s]+@',
        r'\1[CREDENTIALS_REDACTED]@',
        text,
    )
    # socks5://user:pass@host
    text = re.sub(
        r'(socks5h?://)[^:@\s]+:[^@\s]+@',
        r'\1[CREDENTIALS_REDACTED]@',
        text,
    )
    return text
