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

# 当前生效的 allowlist（抓取入口把 cred_defaults 的存储值 push 进来，
# web_act / feed_read 用 allow_private_scope 就地开）。放在这里而不是当作
# 参数层层下传，是为了让**每一个** validate_url 调用点 —— 包括抓取循环里的重定向
# 复校验、robots.txt 那条旁路、tcp_preflight 的裸连接预检（它走
# url_targets_internal -> validate_url）—— 共用同一个答案，不会出现
# 「主路径放行、复校验又拒」。
# 值是 (点名的主机集合, 是否放开整个 127.0.0.0/8)。
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
    """Run a block under a per-call private-host policy.

    ``web_read`` gets this from its options decorator, which pushes the
    contextvar for the whole call. Tools that never went through that decorator
    (``screenshot``, ``feed_read``) had no way to name a private target at all,
    so the same product decision read as "loopback is fetchable" in one tool and
    "loopback is refused outright" in the next. This gives them the identical
    mechanism instead of a second policy to keep in sync.

    ``None``/``False``/``""`` is a no-op push (empty allowlist), which is the
    default state — so an absent option changes nothing.
    """
    token = set_allow_private(value)
    try:
        yield
    finally:
        _ALLOW_PRIVATE.reset(token)


def private_hosts_allowed() -> tuple[frozenset, bool]:
    """``(hosts, loopback_net)``：环境变量名单 并上 本次调用的选项。"""
    hosts, loopback = _ALLOW_PRIVATE.get()
    hosts = hosts | _env_private_hosts()
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
        f"ssrf: DNS recheck tolerates the fake-ip ranges on this host - {said}, "
        f"which is what a fake-ip TUN proxy (Clash / sing-box) hands out. Tolerated: "
        f"{_FAKE_IP_V4} and {_FAKE_IP_V6} only. The recheck itself still runs: "
        f"loopback, RFC1918, link-local, cloud metadata and every other reserved "
        f"range are refused whatever this host measured.")
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
PRIVATE_ADDRESS_RECOVERY = (
    "The URL is well-formed - what is refused is the address it points at, and "
    "nothing was sent. If you meant a public site, this one names a private range: "
    "search for the page instead. If this internal target is genuinely yours to "
    "fetch, the door is cred_defaults(set={'allow_private': true}) for loopback or "
    "{'allow_private': ['<host>']} to name one host - it never opens cloud metadata, "
    "which stays refused whatever the spelling.")

# 「这个地址是内网的」那两类拒绝的识别针。判据只有一份定义：web_read / web_request /
# web_act / web_crawl / web_discover / feed_read 六条路都要说这句话，各自漂一份措辞，
# 调用方就读不出"这是同一件事"（与 RESOLVER_GUARD_NEEDLE 同一惯例）。
PRIVATE_ADDRESS_REFUSAL_PREFIXES = (
    "URL targets internal/private IP",
    "URL targets internal service:",
)


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
        raise SecurityError(f"Only http and https URLs are supported, got: {scheme}")

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


def validate_timeout(timeout: int | float) -> int | float:
    """Validate timeout value.

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
    if timeout > 120_000:  # 120 seconds max for browser
        raise SecurityError("Timeout exceeds maximum of 120000ms (2 minutes)")
    return timeout


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
