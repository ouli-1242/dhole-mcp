"""每条抓取路径的 robots.txt 合规检查（v16.0，缺口 G3）。

v16.0 之前，dhole 对 agent 点名的任何 URL 都直接抓，完全不看站点的 robots.txt
（README 当时也这么写）。本模块就是检查器：每个源站一份、带 TTL、单飞
（single-flight）的解析规则缓存，由每条抓取路径共享；``web_read`` 在任何档位
开跑之前问它，``web_crawl`` 在把发现的 URL 入队之前问它。

策略（刻意收窄，写在这里因为这是行为变更）：

* ``200`` 且有正文 -> 规则被解析，并对 ``USER_AGENT`` 执行。
* ``401``/``403`` -> 视为"无访问权"：主机拒绝把规则交给这个客户端，RFC 9309 把它
  读成完全禁止。保守选择。
* 其它 ``4xx`` -> 这个主机没有 robots.txt = 没有限制。
* ``5xx`` / ``429`` / 网络错误 / 超时 -> 失败开放（允许），带短 TTL。dhole 是替人
  抓单页的按需抓取器，不是爬虫；因为 /robots.txt 一时出错就拒绝那一页，会让 agent
  把它报成站点故障，怪错对象。短 TTL 保证下一次调用重新读。
* 非 http(s) URL，或 ``ignore_robots`` / ``DHOLE_IGNORE_ROBOTS`` -> 允许。

匹配的 UA token 是 ``USER_AGENT``（"dhole-mcp"）：写给 ``*`` 的规则适用，屏蔽某个
具名 bot token 的站点会屏蔽该 token。抓 robots.txt 的那一发 HTTP 也用同一个 token
自报家门，所以看日志的运维知道是谁在问。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, NamedTuple, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from dhole_mcp import env_registry

logger = logging.getLogger("dhole-mcp.robots")

# 设为 1/true 就完全跳过 robots.txt 检查（给与站点另有约定的调用方留的、
# 写在文档里的逃生门）。
ENV_IGNORE_ROBOTS = "DHOLE_IGNORE_ROBOTS"
# 用于匹配 User-agent 组的 token，也作为我们的 HTTP UA 发出去。
USER_AGENT = "dhole-mcp"
ROBOTS_PATH = "/robots.txt"
# 一个主机的规则每小时最多重读一次……
TTL_S = 3600.0
# ……但失败（5xx / 超时）一分钟后重试，避免一次瞬时错误把"没有规则"钉在一个
# 主机上一整小时。
UNAVAILABLE_TTL_S = 60.0
# 抓回规则正文的上限：真实 robots.txt 只有几 KB，半个 GB 的"robots.txt"不能
# 变成内存问题。
MAX_BYTES = 512 * 1024
FETCH_TIMEOUT_S = 8.0
# 遵守的 ``Crawl-delay`` 上限（G27）。这个数字是有关负载的请求，不是等待合同：
# 站点要求每次请求间隔 10 分钟，否则爬取会变成调用方无法与死服务器区分的挂起。
# 夹住，并在爬取 summary 里说明用了哪个值。
MAX_CRAWL_DELAY_S = 60.0

# Verdict.reason 里返回的原因。"allowed" 是主机规则给出的真实答案；
# "unavailable" 表示没有可用规则（失败开放）。
ALLOWED = "allowed"
DISALLOWED = "disallowed"
UNAVAILABLE = "unavailable"
DISABLED = "disabled"
# 这是哪一种"没有答案"，好让回执说一句真话。两种都按文档策略失败开放；不能
# 混淆的是**原因**——"那个文件读不到"与"dhole 拒绝在这里拨号"指向不同的修复
# 对象，也指向审计里不同的写法。
FETCH_FAILED = "fetch_failed"
REFUSED_BY_POLICY = "refused_by_policy"


class Verdict(NamedTuple):
    """一次 robots.txt 检查的结局。

    ``reason`` 是 ``allowed`` | ``disallowed`` | ``unavailable`` | ``disabled``；
    ``robots_url`` 是被查的文件（未知时为空）；``unavailable_reason`` 在
    ``reason`` 为 ``unavailable`` 时是 ``fetch_failed`` | ``refused_by_policy``，
    其它情况为 ""。
    """

    allowed: bool
    reason: str
    robots_url: str = ""
    unavailable_reason: str = ""


def env_disabled() -> bool:
    """``DHOLE_IGNORE_ROBOTS`` 是否在进程级关掉合规检查。

    D1（失败安全方向）：无法识别的值——例如把 ``false`` 拼成 ``flase``——现在读成
    "不要忽略 robots"（合规保持开启），并且失败会进 ``env_registry`` 的警告表，
    让 ``dhole --doctor`` 告诉运维他的设置没生效。改动之前，一次拼写错误会静默
    把进程切到不合规状态，运维看不到任何信号。

    ``ON_TOKENS``（1/true/yes/on）仍然关闭合规；``OFF_TOKENS`` 与空串仍然保持
    开启——既有的 ``test_env_var_disables_the_check_process_wide`` /
    ``test_env_var_falsey_values_keep_compliance_on`` 用例仍然成立。
    """
    return env_registry.read_flag(ENV_IGNORE_ROBOTS, default=False, fail_safe=True)


def robots_url_for(url: str) -> str:
    """页面 URL 对应的 ``https://host/robots.txt``；不是 http(s) 时返回 ""。"""
    try:
        parts = urlparse(url or "")
    except Exception:
        return ""
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}{ROBOTS_PATH}"


def _origin(url: str) -> str:
    """缓存键：scheme://netloc，转小写。规则按源站存。"""
    try:
        parts = urlparse(url or "")
    except Exception:
        return ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def _parse(text: str, robots_url: str) -> RobotFileParser:
    """解析一份 robots.txt 正文。从不抛：解析不了意味着"没有可执行的规则"，
    与本模块其它失败路径一样失败开放。"""
    rp = RobotFileParser()
    try:
        rp.set_url(robots_url)
    except Exception:
        pass
    try:
        rp.parse((text or "").splitlines())
        return rp
    except Exception:
        return _no_rules(robots_url)


def _no_rules(robots_url: str) -> RobotFileParser:
    rp = RobotFileParser()
    try:
        rp.set_url(robots_url)
    except Exception:
        pass
    return rp


def _disallow_all(robots_url: str) -> RobotFileParser:
    """拒绝一切的解析器（用于 robots.txt 返回 401/403 的情况）。"""
    return _parse("User-agent: *\nDisallow: /\n", robots_url)


def _ua_header() -> str:
    try:
        from dhole_mcp import __version__ as _v
    except Exception:
        _v = "0"
    return f"{USER_AGENT}/{_v} (+https://github.com/ouli-1242/dhole-mcp)"


def _blocking_fetch(robots_url: str, proxy: Optional[str], timeout_s: float
                    ) -> Optional[tuple[int, bytes]]:
    """一次阻塞式 GET robots.txt：``(status, body)``，没有答案时返回 None。

    形状与 sitemap 的抓取器一致（先 primp，对拒绝 primp 指纹的主机退回 stdlib），
    让 dhole 两条旁路抓取行为一致。

    它还必须在这里让抓取器自己的 loopback 规则成立：那条规则在 `fetcher` import
    时把 localhost/127.0.0.1/::1 合进 NO_PROXY，而 robots 检查跑在任何档位 import
    fetcher 之前。没有这一句，进程第一次抓私网目标时会把
    ``http://127.0.0.1:PORT/robots.txt`` 交给系统代理——代理到不了调用方自己的
    loopback，答 502，于是主机规则没被执行，回执却怪文件。实测 2026-10-14
    （round 01，R01-020）：NO_PROXY 缺失时 502，先跑守卫时 200，同一 URL、
    同一服务器。
    """
    from dhole_mcp.fetcher import _ensure_loopback_no_proxy
    _ensure_loopback_no_proxy()
    try:
        import primp  # type: ignore
    except Exception:
        primp = None  # type: ignore
    if primp is not None:
        try:
            kwargs = {
                "timeout": max(3, int(timeout_s)),
                "impersonate": "random", "impersonate_os": "random", "verify": True,
            }
            if proxy:
                kwargs["proxy"] = proxy
            client = primp.Client(**kwargs)
            resp = client.get(robots_url, headers={"User-Agent": _ua_header()})
            return int(resp.status_code), bytes(resp.content or b"")[:MAX_BYTES]
        except Exception:
            pass  # 落到 stdlib
    import urllib.request as _urllib_req
    try:
        handlers = []
        if proxy:
            handlers.append(_urllib_req.ProxyHandler({"http": proxy, "https": proxy}))
        opener = _urllib_req.build_opener(*handlers)
        req = _urllib_req.Request(robots_url, headers={"User-Agent": _ua_header()})
        with opener.open(req, timeout=timeout_s) as resp:  # noqa: S310
            return int(resp.status), resp.read(MAX_BYTES)
    except _urllib_req.HTTPError as e:
        return int(e.code), b""
    except Exception:
        return None


class _Entry(NamedTuple):
    expires: float
    parser: Optional[RobotFileParser]  # None = 这个源站没有规则（全部允许）
    disallow_all: bool = False


class RobotsCache:
    """按源站缓存 robots.txt 规则，带 TTL、单飞与失败开放。

    ``fetch``（可选）替换网络调用：``async (url, proxy) -> (status, body) | None``。
    测试注入假抓取器；默认在工作线程里跑阻塞式抓取。
    """

    def __init__(
        self,
        *,
        ttl: float = TTL_S,
        unavailable_ttl: float = UNAVAILABLE_TTL_S,
        fetch: Optional[Callable[[str, Optional[str]], object]] = None,
    ) -> None:
        self._ttl = ttl
        self._unavailable_ttl = unavailable_ttl
        self._fetch = fetch
        self._entries: dict[str, _Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # origin -> 缓存项代表的是哪一种"没有答案"。住在 _entries 旁边，因为丢掉
        # 的正是答案本身。
        self._why: dict[str, str] = {}

    # ─── 缓存面 ─────────────────────────────────────────────────────

    def clear(self) -> None:
        """Drop the per-origin cached rules and the "no answer" reasons.
        """
        self._entries.clear()
        self._why.clear()

    def _fresh(self, origin: str) -> Optional[_Entry]:
        entry = self._entries.get(origin)
        if entry is None:
            return None
        if entry.expires <= time.monotonic():
            self._entries.pop(origin, None)
            return None
        return entry

    def cached(self, url: str, *, user_agent: str = USER_AGENT) -> Optional[bool]:
        """只从缓存回答：True/False，尚未读过时返回 None。

        ``web_crawl`` 用它不花网络调用就丢掉发现的 URL：到那时种子页的规则已经
        缓存，所以过滤同源候选是免费的。
        """
        entry = self._fresh(_origin(url))
        if entry is None:
            return None
        if entry.disallow_all:
            return False
        if entry.parser is None:
            return True
        try:
            return bool(entry.parser.can_fetch(user_agent, url))
        except Exception:
            return None

    def cached_delay(self, url: str, *, user_agent: str = USER_AGENT) -> Optional[float]:
        """``Crawl-delay``，只从**已在缓存里**的规则读；None = 未知/未设。

        爬取逐页问。读缓存（而非抓取）才让这件事免费：那时种子页的规则已经在
        里面，所以 100 页的爬取仍然只读一次 robots.txt。
        """
        entry = self._fresh(_origin(url))
        if entry is None or entry.disallow_all or entry.parser is None:
            return None
        return self._delay_of(entry.parser, user_agent)

    async def crawl_delay(self, url: str, *, proxy: Optional[str] = None,
                          user_agent: str = USER_AGENT) -> Optional[float]:
        """站点为 ``url`` 自己要求的请求间隔（None = 它没要求）。

        与 allowed() 检查共用源站/TTL/单飞，所以对一条已经读过一次的路，不会多花
        一次 robots.txt 读取。
        """
        robots_url = robots_url_for(url)
        if not robots_url:
            return None
        origin = _origin(url)
        entry = self._fresh(origin)
        if entry is None:
            entry = await self._load(origin, robots_url, proxy)
        if entry is None or entry.disallow_all or entry.parser is None:
            return None
        return self._delay_of(entry.parser, user_agent)

    @staticmethod
    def _delay_of(parser: RobotFileParser, user_agent: str) -> Optional[float]:
        """我们 user-agent 的 ``Crawl-delay``，夹到调用预算能承受的范围。

        urllib 已经优先我们的组而不是 ``*``，两者都没设时返回 None。它也只读
        **整秒**（3.14 会卡在 isdigit() 守卫后面），所以站点写
        ``Crawl-delay: 0.5`` 会被读成"什么也没要求"。自从 17.0 删掉调用方的
        ``delay=`` 之后，这就是全部答案：dhole 里没有任何地方把请求间隔压到一秒
        以下。

        要求几小时的站点不会被逐字遵守：这个数字是有关负载的请求，不夹住会把
        爬取变成挂起。
        """
        try:
            raw = parser.crawl_delay(user_agent)
        except Exception:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        return min(value, MAX_CRAWL_DELAY_S)

    def prime(self, url: str, text: str, *, robots_url: str = "") -> None:
        """用一份 robots.txt 正文预置规则（测试，或已经拿到文件的调用方）。"""
        target = robots_url or robots_url_for(url)
        origin = _origin(target or url)
        if not origin:
            return
        self._entries[origin] = _Entry(time.monotonic() + self._ttl, _parse(text, target))


    # ─── 检查 ───────────────────────────────────────────────────────

    async def allowed(self, url: str, *, proxy: Optional[str] = None,
                      user_agent: str = USER_AGENT) -> Verdict:
        """``url`` 在它源站的 robots.txt 下能不能抓？"""
        robots_url = robots_url_for(url)
        if not robots_url:
            return Verdict(True, UNAVAILABLE, "")
        origin = _origin(url)

        def _no_answer(extra: str = "") -> Verdict:
            # "unavailable" 是一个答案、四种故事；说出是哪种故事的字段，才让
            # 回执可审计。
            return Verdict(True, UNAVAILABLE, robots_url,
                           self._why.get(origin, "") or extra or FETCH_FAILED)

        entry = self._fresh(origin)
        if entry is None:
            entry = await self._load(origin, robots_url, proxy)
        if entry is None:
            return _no_answer()
        if entry.disallow_all:
            return Verdict(False, DISALLOWED, robots_url)
        if entry.parser is None:
            return _no_answer()
        try:
            ok = bool(entry.parser.can_fetch(user_agent, url))
        except Exception:
            return _no_answer()
        return Verdict(ok, ALLOWED if ok else DISALLOWED, robots_url)

    async def _load(self, origin: str, robots_url: str, proxy: Optional[str]
                    ) -> Optional[_Entry]:
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            entry = self._fresh(origin)  # 并发任务可能已经填好了
            if entry is not None:
                return entry
            status, text, answered, why = await self._fetch_rules(robots_url, proxy)
            if not answered:
                # 完全没有答案：允许，并且很快重试，而不是把"允许"缓存满一个 TTL。
                self._why[origin] = why
                self._entries[origin] = _Entry(
                    time.monotonic() + self._unavailable_ttl, None)
                return None
            if status in (401, 403):
                # 主机拒绝把规则交给这个客户端。RFC 9309 把它读成无访问权；照做是
                # 保守选择，回执也按这个说法报。
                self._why.pop(origin, None)
                entry = _Entry(time.monotonic() + self._ttl,
                               _disallow_all(robots_url))
                self._entries[origin] = entry
                return entry
            if status != 200:
                # 404/410/451 -> 这里没有 robots.txt，一小时内不会变：满 TTL。
                # 5xx/429 -> 主机只是暂时坏了或在限流，一分钟后的答案可能不同：短
                # TTL，与完全没有答案一样。（策略一直这么说；代码从前把 5xx 缓存满
                # 一小时，于是一个坏瞬间就让主机规则未知——因而未执行——跟真规则
                # 文件一样久。）
                short = status >= 500 or status == 429
                self._why[origin] = FETCH_FAILED if short else ""
                self._entries[origin] = _Entry(
                    time.monotonic() + (self._unavailable_ttl if short else self._ttl),
                    None)
                return None
            self._why.pop(origin, None)
            entry = _Entry(time.monotonic() + self._ttl, _parse(text, robots_url))
            self._entries[origin] = entry
            return entry


    async def _fetch_rules(self, robots_url: str, proxy: Optional[str]
                           ) -> tuple[int, str, bool, str]:
        """``(status, text, answered, why)``；``answered=False`` = 没有可用答案。

        ``why`` 说明这是哪一种"没有答案"，也是填 ``Verdict.unavailable_reason``
        时唯一诚实的做法——回执不能把 dhole 自己的拒绝说成文件坏了。

        robots URL 来自一颗已经校验过的页面 URL，但这里会重新校验：一次重定向或
        恶意 DNS 答案不能把这条旁路变成 SSRF 原语。
        """
        try:
            from dhole_mcp.security import validate_url
            validate_url(robots_url)
        except Exception as e:
            logger.debug("robots.txt target refused for %s: %s", robots_url, e)
            return 0, "", False, REFUSED_BY_POLICY
        try:
            if self._fetch is not None:
                result = await self._fetch(robots_url, proxy)  # type: ignore[misc]
            else:
                result = await asyncio.to_thread(
                    _blocking_fetch, robots_url, proxy, FETCH_TIMEOUT_S)
        except Exception as e:
            logger.debug("robots.txt fetch failed for %s: %s", robots_url, e)
            return 0, "", False, FETCH_FAILED
        if not result:
            return 0, "", False, FETCH_FAILED
        status, body = result  # type: ignore[misc]
        try:
            status_code = int(status)
        except (TypeError, ValueError):
            # 不可用的状态是"没有答案"，不是"没有规则"。把它当成答案会让一个坏掉
            # 的抓取器把"全部允许"钉在主机上满一个 TTL。
            return 0, "", False, FETCH_FAILED
        try:
            if isinstance(body, str):
                # 注入的抓取器可能交回文本而不是字节。bytes(str) 会抛，旧的 except
                # 把它吞成一份**空**规则正文——那会解析成"没有规则"，静默允许
                # 每一条路径。合规检查不能在类型混淆上失败开放，所以转换而不是
                # 丢掉正文。
                body = body.encode("utf-8", errors="replace")
            text = bytes(body or b"")[:MAX_BYTES].decode("utf-8", errors="replace")
        except Exception:
            text = ""
        return status_code, text, True, ""


_CACHE: Optional[RobotsCache] = None


def get_robots_cache() -> RobotsCache:
    """进程级缓存：每个源站每个 TTL 只读一次 robots.txt，由每条抓取路径共享，
    所以 100 页的爬取只读一次文件。"""
    global _CACHE
    if _CACHE is None:
        _CACHE = RobotsCache()
    return _CACHE


def reset_robots_cache() -> None:
    """丢掉进程级缓存（测试，以及 ``sys_state`` 的缓存清理）。"""
    global _CACHE
    _CACHE = None


async def check(url: str, *, proxy: Optional[str] = None, ignore: bool = False
                ) -> Verdict:
    """抓取路径调用的单次检查。

    ``ignore`` 是逐次调用的逃生门（``ignore_robots=true``）；
    ``DHOLE_IGNORE_ROBOTS`` 在进程级关闭检查。
    """
    if ignore or env_disabled():
        return Verdict(True, DISABLED, robots_url_for(url))
    return await get_robots_cache().allowed(url, proxy=proxy)


async def crawl_delay(url: str, *, proxy: Optional[str] = None,
                     ignore: bool = False) -> Optional[float]:
    """这个站点要求的请求间隔（G27）；None = 它没要求。

    ``ignore`` 与 check() 同一个退出开关：说了"不遵守这个主机的 robots.txt"的
    调用方，不该反过来被它悄悄限速。
    """
    if ignore or env_disabled():
        return None
    return await get_robots_cache().crawl_delay(url, proxy=proxy)


def cached_crawl_delay(url: str) -> Optional[float]:
    """只从缓存读要求的间隔——永远不发请求。

    爬取逐页问它，所以必须免费；问的时候种子页的规则已经在缓存里。
    """
    if env_disabled():
        return None
    return get_robots_cache().cached_delay(url)
