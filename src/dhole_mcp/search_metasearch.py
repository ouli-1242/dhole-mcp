"""dhole 的元搜索层。

取自 ddgs（https://github.com/deedy5/ddgs），MIT 许可，(c) Pragmatic School /
deedy5，已裁剪改写：只做文本搜索，异步并行聚合、凑够法定数就早退，不带 CLI /
API server / MCP / 图片 / 视频 / 新闻 / 图书 / extract / cache / 网络噪音。
完整署名见 NOTICE.ddgs.txt 里的 ddgs LICENSE 声明。

后端（全部免密、无 API key、无账号）：池子**就是**注册表 —— 12 个名字
（baidu、bing、bing_global、sogou、so360、shenma、seznam、yahoo、yandex、
brave、duckduckgo、marginalia），每一轮全跑。§3-23 起不再有需要点名的免密后端。
它们**并行**跑；某个后端被 CAPTCHA / 限流 / 与主题不匹配就干脆不出来，其余照常
兜住 —— 所以搜索没有任何单点故障。dhole 当年手搓的三引擎抓取器从未有过这种
健壮性。

传输：多数后端走 primp（带浏览器 TLS/头伪装的 Rust HTTP 客户端）；DuckDuckGo
走 httpx（HTTP/2 + 随机 cipher/SETTINGS 帧）。DHOLE_SEARCH_PROXY 环境变量
（http/https/socks5）是调用方唯一能点名的出口，用来对付按 IP 限流 —— 这是单 IP
下任何抓取器（带不带浏览器）都逃不掉的那件事。17.0：一个代理，无池
（见 search_proxy.py）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import ssl
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import cached_property
from random import SystemRandom
from time import time
from typing import Any, ClassVar, Optional, TypeVar
from urllib.parse import urlparse

import h2
import httpcore
import httpx
import primp
from fake_useragent import UserAgent
from lxml import html
from lxml.etree import HTMLParser as LHTMLParser

from dhole_mcp import engine_yield
from dhole_mcp import env_registry
from dhole_mcp import paths
from dhole_mcp import serp_dates
from dhole_mcp.security import redact_api_key

logger = logging.getLogger(__name__)
random = SystemRandom()

T = TypeVar("T")

# 搜索出口：一个变量一个代理（17.0 删了池与轮转，见 search_proxy.py）。
# HTTPS_PROXY / HTTP_PROXY / ALL_PROXY 是环境里已有的兜底，顺序也在那里定义。
# _PROXY 每次搜索调用由 _get_search_proxy() 写一次，让 search_engines 的惰性
# 导入读到同一个出口。
_PROXY: str | None = None  # 本轮搜索用的代理（由 metasearch 写入）


def _get_search_proxy() -> str | None:
    """本轮搜索用的代理（没有就直连）。

    17.0: 池子内不再有状态 —— 同一个进程里每次搜索得到的是同一个答案，所以这里
    不必"取下一个"，只需要"读那一个"。仍然设 ``_PROXY``，因为 find_similar 那条
    路（``search_engines._fetch_url``）读的是模块量而不是参数。
    """
    global _PROXY
    from dhole_mcp.search_proxy import configured_proxy
    proxy, _source = configured_proxy()
    _PROXY = proxy
    return proxy

# 单引擎与整体期限。引擎并行跑且凑够法定数就早退，所以健康的搜索约 1-2 秒；
# 这两个数给它封顶。W4：env_registry.read_float 会把"不是数字"那种输入记下来，
# 让 ``dhole --doctor`` 能点名；兜底值同样是 16 秒。
_SEARCH_DEADLINE = 16.0  # 17.0 写死：整个搜索的硬期限是能算清的数，DHOLE_SEARCH_DEADLINE 已注销
# 软截止：结果凑够就早退，不为慢/死的引擎等到硬截止。提成模块常量只为了让测试能
# 把它压到毫秒级（否则验早退归属的测试每条都要真睡 2s）。
_SOFT_DEADLINE = 2.0
# 只有 **一个索引家族**在说话时，早退之前多给的一小段等待。它买的东西很具体：跨家族
# 共识是这条链上唯一免费、零模型、确定性的权威信号，而一家说话时它按构造不存在
# （每一行都是 "1 of 1 (no corroboration)"）。抬 `_SOFT_DEADLINE` 是把这笔钱花在每一次
# 早退上；这一段只花在买得到的那一次上。
#
# 值落在量出来的空带上。样本是 `second − first`：**第二个答话的家族**相对**第一家答完**
# 的到达偏移。`evals/pool_baseline.py` 那一层没有时钟（`_ENGINE_YIELD` 记 status/节点数，
# 不记时间），所以这两次是临时给每个引擎套一只表跑出来的真联网探测，各 12 问：
#   网络尚可：0.19 / 0.59 / 1.15 / 2.27
#   网络劣化（12/12 轮在 2.0s 时**连一家都没有**，第一家落在 2.6-7.1s）：0.77 / 2.95 / 2.95 / 5.68
# 四个样本挤在 1.15 及以下，下一个已经在 2.27 —— 1.2 坐在 1.15…2.27 这条空带的下沿：
# 收得进挤着的那一簇（8 个里的 4 个），又不为孤零零的长尾付钱。代价上界就是这 1.2 秒，
# 且只付给"到点还没凑出第二个家族"的轮次；到点照旧取消，账记 preempted（不是超时、不是
# 失败）。这台机器的池子延迟在几分钟里能差一个量级，所以这条空带不许被读成常数 —— 改了
# 引擎名单或出口就重跑那两次探测。
_LONE_FAMILY_GRACE = 1.2
# 共识至少要有两家才叫共识：一家说什么都构不成佐证（响应里每一行都写成
# "1 of 1 (no corroboration)"）。地板按**家族**算而不是按实例算，理由见 metasearch 里
# `families_answered` 那段：bing / bing_global / duckduckgo 同一套索引，数它们等于没数。
_MIN_CONSENSUS_FAMILIES = 2
_ua = UserAgent()


# ─── 异常 ────────────────────────────────────────────────────────────────────
class MetaSearchException(Exception):
    """元搜索错误的基类。"""


class MetaTimeoutException(MetaSearchException):
    """某个后端或整次搜索超时。"""


class TavilyAuthError(MetaSearchException):
    """Tavily 拒绝凭据（key 错误/过期、额度用尽）。"""


class BochaAuthError(MetaSearchException):
    """博查拒绝凭据（key 无效或欠费）。"""


class ExaAuthError(MetaSearchException):
    """Exa 拒绝凭据（key 错误/过期、额度用尽）。"""


class DeepSeekAuthError(MetaSearchException):
    """DeepSeek 拒绝凭据（key 无效或余额不足，402 同样算鉴权失败）。"""


class FirecrawlAuthError(MetaSearchException):
    """Firecrawl 拒绝凭据（key 错误/过期、额度用尽）。"""


class BraveApiAuthError(MetaSearchException):
    """Brave 官方 API 拒绝凭据（token 无效或额度用尽）。"""


class MetaBlockedException(MetaSearchException):
    """某个后端拒绝了我们（CAPTCHA / 403 / 限流）。调用方应把该后端熔断一个冷却
    期，免得持续捶一台正在封我们 IP 的主机（有升级成更长 IP 封禁的风险）。

    ``challenge=True`` 说的是另一类拒绝：上游回的是 HTTP 200 的反爬壳（校验页），
    意思是"我们知道你是谁了"，而不是"这会儿忙"。冷却时长因此分开算 —— 见
    `_record_block`。"""

    def __init__(self, *args: Any, challenge: bool = False) -> None:
        super().__init__(*args)
        self.challenge = challenge


# ─── 传输：primp（伪装浏览器 TLS）─────────────────────────────────────────────
class _PrimpResponse:
    """primp 响应的一层薄包装（status、text、content）。"""

    __slots__ = ("_resp", "content", "status_code", "text")

    def __init__(self, resp: Any) -> None:
        self._resp = resp
        self.status_code = resp.status_code
        self.content = resp.content
        self.text = resp.text


class _PrimpClient:
    """基于 primp 的 HTTP 客户端，随机伪装浏览器（反爬）。"""

    def __init__(self, proxy: str | None = None, timeout: int | None = 10, *,
                 verify: bool = True, impersonate: str = "random",
                 impersonate_os: str = "random") -> None:
        self.client = primp.Client(
            proxy=proxy,
            timeout=timeout,
            impersonate=impersonate,
            impersonate_os=impersonate_os,
            verify=verify,
        )

    def request(self, *args: Any, **kwargs: Any) -> _PrimpResponse:
        try:
            return _PrimpResponse(self.client.request(*args, **kwargs))
        except primp.TimeoutError as ex:
            raise MetaTimeoutException(str(ex)) from ex
        except Exception as ex:
            raise MetaSearchException(f"{type(ex).__name__}: {ex!r}") from ex

    def get(self, url: str, *args: Any, **kwargs: Any) -> _PrimpResponse:
        return self.request("GET", url, *args, **kwargs)


# ─── 传输：httpx（HTTP/2 + 随机指纹，给 DuckDuckGo 用）───────────────────────
_DEFAULT_CIPHERS = [  # cloudflare 推荐 + 现代 + 兼容 + 旧版
    "TLS_AES_128_GCM_SHA256", "TLS_AES_256_GCM_SHA384", "TLS_CHACHA20_POLY1305_SHA256",
    "ECDHE-ECDSA-AES128-GCM-SHA256", "ECDHE-ECDSA-CHACHA20-POLY1305", "ECDHE-RSA-AES128-GCM-SHA256",
    "ECDHE-RSA-CHACHA20-POLY1305", "ECDHE-ECDSA-AES256-GCM-SHA384", "ECDHE-RSA-AES256-GCM-SHA384",
    "ECDHE-ECDSA-AES128-GCM-SHA256", "ECDHE-ECDSA-CHACHA20-POLY1305", "ECDHE-RSA-AES128-GCM-SHA256",
    "ECDHE-RSA-CHACHA20-POLY1305", "ECDHE-ECDSA-AES256-GCM-SHA384", "ECDHE-RSA-AES256-GCM-SHA384",
    "ECDHE-ECDSA-AES128-SHA256", "ECDHE-RSA-AES128-SHA256", "ECDHE-ECDSA-AES256-SHA384",
    "ECDHE-RSA-AES256-SHA384", "ECDHE-ECDSA-AES128-SHA", "ECDHE-RSA-AES128-SHA", "AES128-GCM-SHA256",
    "AES128-SHA256", "AES128-SHA", "ECDHE-RSA-AES256-SHA", "AES256-GCM-SHA384", "AES256-SHA256",
    "AES256-SHA", "DES-CBC3-SHA",
]  # fmt: skip


def _random_ssl_context(verify: bool = True) -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=verify if isinstance(verify, str) else None)
    shuffled = random.sample(_DEFAULT_CIPHERS[9:], len(_DEFAULT_CIPHERS) - 9)
    ctx.set_ciphers(":".join(_DEFAULT_CIPHERS[:9] + shuffled))
    commands = [
        None,
        lambda c: setattr(c, "maximum_version", ssl.TLSVersion.TLSv1_2),
        lambda c: setattr(c, "minimum_version", ssl.TLSVersion.TLSv1_3),
        lambda c: setattr(c, "options", c.options | ssl.OP_NO_TICKET),
    ]
    cmd = random.choice(commands)
    if cmd:
        cmd(ctx)
    return ctx


class _H2Patch:
    """随机化 HTTP/2 SETTINGS 帧，躲开 JA3/JA4 指纹（DuckDuckGo）。

    线程安全：补丁挂在 httpcore 类方法上，并发请求必须串行化
    （否则线程交错 patch/restore 会把补丁永久留在类上或恢复错版本）。
    """

    _lock = threading.Lock()

    def __enter__(self) -> None:
        def _send_connection_init(self: httpcore._sync.http2.HTTP2Connection, request: httpcore.Request) -> None:
            self._h2_state.local_settings = h2.settings.Settings(
                client=True,
                initial_values={
                    h2.settings.SettingCodes.INITIAL_WINDOW_SIZE: random.randint(100, 200),
                    h2.settings.SettingCodes.HEADER_TABLE_SIZE: random.randint(4000, 5000),
                    h2.settings.SettingCodes.MAX_FRAME_SIZE: random.randint(16384, 65535),
                    h2.settings.SettingCodes.MAX_CONCURRENT_STREAMS: random.randint(100, 200),
                    h2.settings.SettingCodes.MAX_HEADER_LIST_SIZE: random.randint(65500, 66500),
                    h2.settings.SettingCodes.ENABLE_CONNECT_PROTOCOL: random.randint(0, 1),
                    h2.settings.SettingCodes.ENABLE_PUSH: random.randint(0, 1),
                },
            )
            self._h2_state.initiate_connection()
            self._h2_state.increment_flow_control_window(2**24)
            self._write_outgoing_data(request)

        self._lock.acquire()
        self._orig = httpcore._sync.http2.HTTP2Connection._send_connection_init
        httpcore._sync.http2.HTTP2Connection._send_connection_init = _send_connection_init  # type: ignore[method-assign]

    def __exit__(self, *exc: Any) -> None:
        httpcore._sync.http2.HTTP2Connection._send_connection_init = self._orig  # type: ignore[method-assign]
        self._lock.release()


class _HttpxResponse:
    __slots__ = ("content", "status_code", "text")

    def __init__(self, status_code: int, content: bytes, text: str) -> None:
        self.status_code = status_code
        self.content = content
        self.text = text


class _HttpxClient:
    """给 DuckDuckGo 用的 httpx 客户端（上游说 primp 与 DDG 不合）。"""

    def __init__(self, headers: dict[str, str] | None = None, proxy: str | None = None,
                 timeout: int | None = 10, *, verify: bool = True) -> None:
        self.client = httpx.Client(
            headers=headers, proxy=proxy, timeout=timeout,
            verify=_random_ssl_context(verify=verify) if verify else False,
            follow_redirects=False, http2=True,
        )

    def request(self, *args: Any, **kwargs: Any) -> _HttpxResponse:
        with _H2Patch():
            try:
                resp = self.client.request(*args, **kwargs)
                return _HttpxResponse(resp.status_code, resp.content, resp.text)
            except Exception as ex:
                if "timed out" in f"{ex}":
                    raise MetaTimeoutException(f"Request timed out: {ex!r}") from ex
                raise MetaSearchException(f"{type(ex).__name__}: {ex!r}") from ex


class _StdHttpxClient:
    """标准 httpx client（无随机 ciphers / H2Patch）——用于对 TLS 敏感的站点。

    `_HttpxClient` 的随机化 TLS 指纹偶发触发服务器连接重置（如 Bing 的
    10054）；标准 httpx 客户端指纹稳定，兼容性最好。代价是反爬识别度低，
    仅用于 Bing 这类对 TLS 协商敏感的站点。
    """

    def __init__(self, headers: dict[str, str] | None = None, proxy: str | None = None,
                 timeout: int | None = 10, *, verify: bool = True) -> None:
        self.client = httpx.Client(
            headers=headers, proxy=proxy, timeout=timeout,
            verify=verify, follow_redirects=False, http2=False,
        )

    def request(self, *args: Any, **kwargs: Any) -> _HttpxResponse:
        try:
            resp = self.client.request(*args, **kwargs)
            return _HttpxResponse(resp.status_code, resp.content, resp.text)
        except Exception as ex:
            if "timed out" in f"{ex}":
                raise MetaTimeoutException(f"Request timed out: {ex!r}") from ex
            raise MetaSearchException(f"{type(ex).__name__}: {ex!r}") from ex


# ─── 结果类型 ────────────────────────────────────────────────────────────────
@dataclass
class TextResult:
    """某个后端交回的一条文本搜索结果。"""

    title: str = ""
    href: str = ""
    body: str = ""
    # 期4.1：这一行**版面自带的**发布日期。三颗一组的理由是 4.0 那把尺：一个日期
    # 不带精度就是一次冒充（"3 hours ago" 存成光秃秃的 ISO 等于声称那天发布）。
    # `published` 出链时是本模块唯一作者（`serp_dates.read_row`）写回的 ISO 串，
    # 空串 = 这一行没有日期（线上零字节，见 wire._WIRE_OMIT）。
    # 只有实测过容器的四家（sogou / baidu 两套版面 / so360 / bing 系）会落值；
    # 其余家与 keyed API 交回空串，不是"它没有"，是"dhole 没在它上面量过"。
    published: str = ""
    published_precision: str = ""   # day|month|year|relative（空=无日期）
    published_basis: str = ""       # 命中的版面原文，让读者能复核日期从哪几个字读出来


# ─── 结果可用性谓词 ──────────────────────────────────────────────────────────
def _is_usable(row: Any) -> bool:
    """一条结果是否真的能用：href 与 title 都得有，且 href 是 http(s)。

    scheme 白名单收在聚合入口而不是各家的后处理：ddg / bing / brave / yandex 的
    href 从前不做 scheme 检查，敌意或损坏的 SERP 喂进 javascript:/data:/相对路径
    的 href 时，它们会被当结果交给下游 agent（直到 web_read 才被拦，白耗一轮）。
    观测计数与聚合侧的取舍必须用同一个谓词，否则"引擎产出"和"为什么结果为空"
    会各说各话 —— 一个说引擎产出了 8 条、另一个说一条都没收下。
    """
    href = getattr(row, "href", None)
    if not href or not getattr(row, "title", None):
        return False
    return str(href).lower().startswith(("http://", "https://"))


def _usable_count(rows: Any) -> int:
    return sum(1 for r in rows if _is_usable(r))


def _stamp_row_date(row: Any) -> Any:
    """把行上那段版面日期读成三颗字段（值 + 精度 + 原文），原地盖好。

    读不出日期就交回三个空串：干净行因此在 wire 上零字节，而不是"-published=''”。
    行上没有 `published` 这一格（keyed API 的 SimpleNamespace 形状）就原样退回 —— 那
    是"dhole 没在那家页面上量过日期"，不是"那家没有日期"。
    """
    if not hasattr(row, "published"):
        return row
    rd = serp_dates.read_row(row.published)
    row.published, row.published_precision, row.published_basis = (
        rd.value.isoformat() if rd.value else "", rd.precision, rd.basis)
    return row


def _split_region(region: str) -> tuple[str, str]:
    """``"us-en"`` -> ``("us", "en")``，对调用方能送进来的形状都不炸。

    region 是 dhole 的公开参数（`web_search(region=)`，也由 location+language 派生），
    所以 "cn"、"zh-Hans-CN"、空串都会走到这里。原先两处用户写的是
    `country, lang = region.lower().split("-")` —— 段数不对就 ValueError，而 metasearch
    把它记成 `error:ValueError`，engine_health 报 blocked。一个 region 字符串能让一家
    引擎「被拦」，这是把调用方的笔误升级成基础设施故障。

    单个词按国家处理（本项目其它地方都这么读它），语言默认 en：把 "cn" 当成语言会去
    找 cn.wikipedia.org 这种不存在的站。
    """
    parts = [p for p in (region or "").strip().lower().split("-") if p]
    if len(parts) >= 2:
        return parts[0], parts[-1]
    if len(parts) == 1:
        return parts[0], "en"
    return "us", "en"


# ─── 基础搜索引擎（纯文本、XPath 驱动）──────────────────────────────────────
class BaseSearchEngine:
    """抽象基类：build_payload -> fetch -> 用 XPath extract -> 后处理。"""

    name: ClassVar[str]
    category: ClassVar[str] = "text"
    provider: ClassVar[str]
    disabled: ClassVar[bool] = False
    priority: ClassVar[float] = 1.0

    search_url: str
    search_method: ClassVar[str] = "GET"
    headers_update: ClassVar[Mapping[str, str]] = {}
    items_xpath: ClassVar[str]
    elements_xpath: ClassVar[Mapping[str, str]]
    # 视为"被拦"（进熔断冷却）的 HTTP 状态。202 只属于 DuckDuckGo：它的 html 端点
    # 限流时回 202 + "Please complete the following challenge…"，与 403/503 同类；
    # 其它引擎的 202 语义未观测，不擅自推广（对它们仍按非 200 -> 无结果处理）。
    # 429 与 202 不同：它的含义是 RFC 6585 定死的"Too Many Requests"，不属于要观测
    # 的引擎私货。实测 brave 在新出口 IP 上回 429 + 反爬壳，旧行为把它记成 empty
    # （"这个查询没结果"），既不冷却也不在报告里留痕 —— 引擎在限流却装成没结果。
    _challenge_statuses: ClassVar[tuple[int, ...]] = (403, 429, 503)
    # 200 拦截页判定（几 KB 的壳 + 校验字样）。默认关闭：只在国内引擎上观测到这种
    # 形态，不拿它去猜别家的语义 —— 误判成"被拦"会让一个健康引擎进冷却。
    _detect_challenge_shell: ClassVar[bool] = False
    # 这一家该拿**原始** query 还是意图展开后的变体。展开词是两串英文术语
    # （" paper arxiv benchmark results" / " specifications table data parameters"），
    # 追加到中文索引或国内版面上等于换了一个查询（实测只会搜不到东西）。
    # 判据只写在这一处：它跟着引擎类走，池子/注册表以后怎么改都不会留下第二份
    # 需要有人记得同步的名单 —— `_CORE_QUERY_ENGINES` 是这份名单的第三版，也是
    # 本仓库第三次踩"两处定义漂移"，所以它被删掉了（§6）。
    prefers_raw_query: ClassVar[bool] = False

    # ── 引擎产出计数（静默降级的唯一观测面）────────────────────────────
    # 免密搜索顶部那个失败模式是"引擎还在跑，但解析不出东西"，而它在响应里
    # 完全不可见（empty 既不进 engines_used 也不进 engine_blocked）。下面三个
    # 整数就是用来把它逼出来的。
    # 刻意用类属性做默认值而不是在 __init__ 里赋值：Duckduckgo.__init__ 覆盖了
    # 父类且不调 super()，写在 __init__ 里的话最常用的一些引擎会没有这些字段。
    # -1 / None = 没走到那一步（未解析、被跳过、或该引擎根本不吃 HTML）。
    last_extract: tuple[int, int] = (-1, -1)
    kept_after_filter: int = -1
    http_status: int | None = None

    def __init__(self, proxy: str | None = None, timeout: int | None = None, *, verify: bool = True) -> None:
        self.http_client = _PrimpClient(proxy=proxy, timeout=timeout, verify=verify)
        self.http_client.client.headers_update(self.headers_update)
        self.results: list[Any] = []

    @property
    def result_type(self) -> type:
        """The row type this adapter yields; ``TextResult`` unless overridden.
        """
        return TextResult

    def build_payload(self, query: str, region: str, safesearch: str,
                      timelimit: str | None, page: int, **kwargs: str) -> dict[str, Any]:
        """Translate one call into this engine's query parameters.

        Returns:
            The mapping used as a GET query or POST form. Abstract: every concrete engine
            implements it, because only it knows its own page's parameter names and encodings.
        """
        raise NotImplementedError

    def request(self, *args: Any, **kwargs: Any) -> str | None:
        """Fetch once through this engine's transport.

        Returns:
            The body on HTTP 200; ``None`` for any other non-challenge status (the caller reads
            that as "no body", not "blocked").

        Raises:
            MetaBlockedException: A status in ``_challenge_statuses``; the pool opens this
                backend's circuit instead of retrying further.
            MetaTimeoutException: The transport timed out.
            MetaSearchException: Any other transport failure.
        """
        resp = self.http_client.request(*args, **kwargs)
        self.http_status = resp.status_code
        if resp.status_code in self._challenge_statuses:
            # 反爬挑战 / 拒绝访问 -> 熔断这个后端，而不是当成普通的空结果
            # （后者会每次调用都重试，有把封禁升级的风险）。空结果/超时不算致命。
            raise MetaBlockedException(f"HTTP {resp.status_code}")
        return resp.text if resp.status_code == 200 else None

    @cached_property
    def parser(self) -> LHTMLParser:
        """The shared lxml parser for this engine (blank text and comments dropped).
        """
        return LHTMLParser(remove_blank_text=True, remove_comments=True,
                           remove_pis=True, collect_ids=False)

    def extract_tree(self, html_text: str) -> html.Element:
        """Parse ``html_text`` with :attr:`parser`.

        Raises:
            lxml.etree.ParserError: The document has no parseable root.
        """
        return html.fromstring(html_text, parser=self.parser)

    def pre_process_html(self, html_text: str) -> str:
        """Engine-specific markup repair before parsing; returns its input unchanged.
        """
        return html_text

    def extract_results(self, html_text: str) -> list[Any]:
        """Run this engine's XPath table, one row per result container.

        Side effects:
            Records ``last_extract=(containers, usable)`` so parser drift — containers still
            there, usable rows zero — stays visible. Filtering is :meth:`post_extract_results`'
            job, not this one's.
        """
        html_text = self.pre_process_html(html_text)
        tree = self.extract_tree(html_text)
        nodes = tree.xpath(self.items_xpath)
        results = []
        for item in nodes:
            result = self.result_type()
            for key, value in self.elements_xpath.items():
                data = " ".join("".join(item.xpath(value)).split())
                result.__setattr__(key, data)
            results.append(result)
        # item_nodes>0 而 usable==0 = 容器还在、子元素 xpath 已经错位 —— 这就是
        # 解析器漂移，且它不需要任何历史基线就能判定。
        self.last_extract = (len(nodes), _usable_count(results))
        return results

    def post_extract_results(self, results: list[Any]) -> list[Any]:
        """Engine-specific filtering after extraction; returns its input unchanged.
        """
        return results

    def extract_kept(self, html_text: str) -> list[Any]:
        """版面 → 交出去的最后一批行：解析 → 引擎自滤 → 逐行盖日期。

        这是**唯一**一处读日方的地方：`BaseSearchEngine.search` 与 `Baidu.search`
        （它为了重试挑战页自己调 request，不走 super）都从这一个出口出去。日期只有
        一个作者、一次判决，且发生在引擎自滤之后 —— 被丢掉的本就不该出现在账上。

        xpath 只负责把版面那段字**取回来**；把它读成 (日期, 精度, 原文) 的是
        `serp_dates.read_row`，那里是唯一的一份解析（4.0 的叶子）。
        """
        return [_stamp_row_date(r) for r in
                (self.post_extract_results(self.extract_results(html_text)) or [])]

    def _check_challenge(self, html_text: str) -> None:
        """解析前调用：几 KB 的 200 校验页 = 被拦，不是"没有结果"。

        记成 empty 会被读成"这个查询没结果"，`dhole -v` 的产出面板还会把它算成解析器
        漂移 —— 两种误判都比"被拦，等会儿再试"更糟。
        """
        if _is_challenge_shell(html_text):
            raise MetaBlockedException(f"{self.name} 校验页 (HTTP 200 challenge)",
                                       challenge=True)

    def search(self, query: str, region: str = "us-en", safesearch: str = "moderate",
               timelimit: str | None = None, page: int = 1, **kwargs: str) -> list[Any] | None:
        """One text search end to end: build, fetch, extract, filter.

        Args:
            query: The query exactly as the caller sent it; some adapters quote it first.
            region: A ``<country>-<language>`` hint.
            safesearch: ``on``/``moderate``/``off``; each adapter maps it to a cookie or a
                request field.
            timelimit: The engine's own time-filter token, or ``None``.
            page: The result page, 1-based.
            **kwargs: Extra fields passed through to :meth:`build_payload`.

        Returns:
            The extracted rows after engine-specific filtering. ``None`` means the transport
            gave back no body (not 200 and not blocked); an empty list means the page parsed
            but nothing survived filtering.

        Raises:
            MetaBlockedException: A challenge status, or a challenge shell page detected.
            MetaTimeoutException: The transport timed out.
            MetaSearchException: Any other transport failure.
        """
        payload = self.build_payload(query=query, region=region, safesearch=safesearch,
                                     timelimit=timelimit, page=page, **kwargs)
        if self.search_method == "GET":
            html_text = self.request(self.search_method, self.search_url, params=payload)
        else:
            html_text = self.request(self.search_method, self.search_url, data=payload)
        if not html_text:
            return None
        if self._detect_challenge_shell:
            self._check_challenge(html_text)
        kept = self.extract_kept(html_text)
        # 记的是条数而非可用数：usable 已由 last_extract 承载。两者分开才能分出
        # "解析器坏了"（usable==0）与"过滤器把结果吃光了"（bing 的 ck/a、ddg 的
        # y.js）—— 前者要修 xpath，后者要修解码，是两类不同的故障。
        self.kept_after_filter = len(kept)
        return kept


# ─── DuckDuckGo（httpx 传输）─────────────────────────────────────────────────
class Duckduckgo(BaseSearchEngine):
    """DuckDuckGo's HTML endpoint, over httpx.

    DDG rate-limits with HTTP 202 plus a challenge page (not 403), so 202 is this class's
    challenge status; ``y.js`` ad rows are dropped after extraction.
    """

    name = "duckduckgo"
    provider = "bing"
    search_url = "https://html.duckduckgo.com/html/"
    search_method = "POST"
    # 实测：限流时回 202 + 反爬挑战页（"Unfortunately, bots use DuckDuckGo too…
    # Select all squares containing a duck"），不是 403。记成 empty 会被读成"这个
    # 查询没结果"，所以归到被拦这边。
    _challenge_statuses = (403, 429, 503, 202)
    items_xpath = "//div[contains(@class, 'body')]"
    elements_xpath: ClassVar[Mapping[str, str]] = {
        "title": ".//h2//text()", "href": "./a/@href", "body": "./a//text()",
    }
    headers: ClassVar[dict[str, str]] = {}

    def __init__(self, proxy: str | None = None, timeout: int | None = None, *, verify: bool = True) -> None:
        # DDG 走 httpx 传输（primp 在上游与 DDG 不合）。
        self.headers = {"User-Agent": _ua.random}
        self.http_client = _HttpxClient(headers=self.headers, proxy=proxy, timeout=timeout, verify=verify)  # type: ignore[assignment]
        self.results: list[Any] = []

    def build_payload(self, query: str, region: str, safesearch: str,  # noqa: ARG002
                      timelimit: str | None, page: int = 1, **kwargs: str) -> dict[str, Any]:
        payload = {"q": query, "b": "", "l": region}
        if page > 1:
            payload["s"] = f"{10 + (page - 2) * 15}"
        if timelimit:
            payload["df"] = timelimit
        return payload

    def request(self, *args: Any, **kwargs: Any) -> str | None:
        # httpx 传输：kwargs 用 method= 而不是位置参数 method。
        method = args[0] if args else kwargs.pop("method", "GET")
        url = args[1] if len(args) > 1 else kwargs.pop("url", "")
        resp = self.http_client.request(method=method, url=url, **kwargs)  # type: ignore[attr-defined]
        self.http_status = resp.status_code
        # 走类的 _challenge_statuses（DDG 是 (403, 429, 503, 202)），别在这里另写一份 ——
        # 上一版就是复制了基类的 (403, 503)，于是 202 的挑战页悄悄漏成 empty。
        if resp.status_code in self._challenge_statuses:
            raise MetaBlockedException(f"HTTP {resp.status_code}")
        return resp.text if resp.status_code == 200 else None

    def post_extract_results(self, results: list[Any]) -> list[Any]:
        return [r for r in results if not r.href.startswith("https://duckduckgo.com/y.js?")]


# ─── Brave ───────────────────────────────────────────────────────────────────
class Brave(BaseSearchEngine):
    """Brave's web results page.

    Country and safesearch ride in the shared client's cookies; the time window goes
    through ``tf``, and ``offset`` is a 0-based page number.
    """

    name = "brave"
    provider = "brave"
    search_url = "https://search.brave.com/search"
    search_method = "GET"
    items_xpath = "//div[@data-type='web']"
    elements_xpath: ClassVar[Mapping[str, str]] = {
        "title": ".//div[(contains(@class,'title') or contains(@class,'sitename-container')) and position()=last()]//text()",
        "href": ".//a[div[contains(@class, 'title')]]/@href",
        "body": ".//div[contains(@class, 'snippet')]//div[contains(@class, 'content')]//text()",
    }

    def build_payload(self, query: str, region: str, safesearch: str,
                      timelimit: str | None, page: int = 1, **kwargs: str) -> dict[str, Any]:
        payload = {"q": query, "source": "web"}
        country, _lang = _split_region(region)
        cookies = {country: country, "useLocation": "0"}
        if safesearch != "moderate":
            cookies["safesearch"] = "strict" if safesearch == "on" else "off"
        self.http_client.client.set_cookies("https://search.brave.com", cookies)  # type: ignore[attr-defined]
        if timelimit:
            payload["tf"] = {"d": "pd", "w": "pw", "m": "pm", "y": "py"}[timelimit]
        if page > 1:
            payload["offset"] = f"{page - 1}"
        return payload


# ─── Yandex ──────────────────────────────────────────────────────────────────
class Yandex(BaseSearchEngine):
    """Yandex's domestic search page.

    dhole sends no date filter to this adapter (see
    ``search_engines._DATE_AWARE_ENGINES``); that is a statement about dhole's wiring, not
    a claim that Yandex has no time filter.
    """

    name = "yandex"
    provider = "yandex"
    search_url = "https://yandex.com/search/site/"
    search_method = "GET"
    items_xpath = "//li[contains(@class, 'serp-item')]"
    elements_xpath: ClassVar[Mapping[str, str]] = {
        "title": ".//h3//text()", "href": ".//h3/a/@href", "body": ".//div[contains(@class, 'text')]//text()",
    }

    def build_payload(self, query: str, region: str, safesearch: str,  # noqa: ARG002
                      timelimit: str | None,  # noqa: ARG002 - 故意忽略：dhole 不给这一家
                      # 发日期过滤，也没登记过它能发什么。这不是断言 yandex 没有时间过滤
                      # —— 只说明 dhole 的线路里没有。见 search_engines._DATE_AWARE_ENGINES
                      page: int = 1, **kwargs: str) -> dict[str, Any]:
        payload = {"text": query, "web": "1", "searchid": f"{random.randint(1000000, 9999999)}"}
        if page > 1:
            payload["p"] = f"{page - 1}"
        return payload


# ─── Bing（国内 + 国际；免费、免密、中国大陆可直连）──────────────────────────
# 国内网可用：cn.bing.com 稳定可达；国际版 www.bing.com 作为回退。
# provider 复用 "bing" 索引家族（与 DuckDuckGo 同源，共识合并）。
# Bing 的传输用 httpx（非 primp）：primp 的浏览器 TLS 指纹与 Bing 协商
# 偶发 SelectedUnofferedKxGroup 失败，httpx 稳定。
def _bing_decode_ck_url(href: str) -> str:
    """从 Bing ck/a 跳转链接解码真实 URL。

    形如 https://www.bing.com/ck/a?...&u=a1aHR0cHM6Ly93d3cucHl0aG9uLm9yZy8&ntb=1
    ``u=`` 参数 = "a1" + base64(URL-safe) 编码的真实 URL。解析失败返回原样。
    """
    try:
        from urllib.parse import parse_qs, urlparse
        params = parse_qs(urlparse(href).query)
        u = params.get("u", [""])[0]
        if not u:
            return href
        # 去掉 "a1" 前缀后 base64 解码（URL-safe，可能带 -_ 而非 +/）
        b64 = u[2:] if u.startswith("a1") else u
        b64 += "=" * (-len(b64) % 4)
        import base64
        decoded = base64.urlsafe_b64decode(b64).decode("utf-8", errors="replace")
        return decoded if decoded.startswith(("http://", "https://")) else href
    except Exception:
        return href


class Bing(BaseSearchEngine):
    """Bing's domestic endpoint (cn.bing.com), over httpx.

    Result links are ``ck/a`` redirects, decoded in ``post_extract_results``, which also
    drops Bing's own pages. The international endpoint is :class:`BingGlobal`.
    """

    name = "bing"
    provider = "bing"
    search_url = "https://cn.bing.com/search"
    search_method = "GET"
    items_xpath = "//li[contains(@class, 'b_algo')]"
    elements_xpath: ClassVar[Mapping[str, str]] = {
        "title": ".//h2//text()",
        # Bing 同时在跑两种标题链接版面：A 版 `<h2><a href=ck/a>…</a></h2>`，B 版
        # `<a class="tilk">…<h2>文本</h2></a>`（链接在 h2 的**祖先**上）。只查后代的
        # 那版实测 5 条容器全部读不出 href —— 整轮 bing 结果为空、引擎状态记成
        # empty，且在响应里哪儿都不出现。并集 + [1] 取文档序第一个：A 版命中 h2 内的
        # a，B 版回退到祖先 a。用祖先轴而不是 `@class='tilk'`：后者把这个修复和 Bing
        # 的一个样式类名绑在一起，改名就会再烂一次。
        "href": "(.//h2/a/@href | .//h2/ancestor::a/@href)[1]",
        "body": ".//p//text()",
        # 期4.2（实测）：这一格是新闻/时间卡片的 `span.news_dt`。冻结夹具里
        # bing_global 10 个容器 1 个有，且写的是 "1 day ago"（relative，4.0 的叶子
        # 如实记精度，绝不落成一个光秃秃的 ISO 日）；cn 版那一套版面 A 4/4、B 1/4
        # 是绝对日期（"Oct 11, 2024"）。没有这一格的行交回空串 → wire 上零字节。
        # BingGlobal 继承这张表，两家同一格，不再抄第二份。
        "published": ".//span[contains(@class,'news_dt')]//text()",
    }
    # Bing 对单 IP 高频请求随机限流（连接重置/空结果），重试可显著提高命中率
    _retries = 2
    _retry_delay = 0.6
    # cn.bing.com 是国内版面：内容池以中文站点为主，英文展开词等于换了个查询。
    # 国际版那一家（BingGlobal）不继承这个判断，见下。
    prefers_raw_query = True

    def __init__(self, proxy: str | None = None, timeout: int | None = None, *, verify: bool = True) -> None:
        # Bing 用标准 httpx.Client（非 _HttpxClient）：随机 ciphers / H2Patch
        # 与 Bing 偶发 TLS 重置（10054），标准客户端最稳定。
        self.headers = {"User-Agent": _ua.random}
        self.http_client = _StdHttpxClient(headers=self.headers, proxy=proxy, timeout=timeout, verify=verify)  # type: ignore[assignment]
        self.results: list[Any] = []

    def search(self, query: str, region: str = "us-en", safesearch: str = "moderate",
               timelimit: str | None = None, page: int = 1, **kwargs: str) -> list[Any] | None:
        """Bing 网络抖动/限流时重试（连接重置或空结果都重试）。

        但 `MetaBlockedException` 必须往外走：它是"这家正在拦我们"的判定，冷却的
        唯一触发点就在调用方（metasearch 捕到它就 `_record_block`）。上一版把它和
        普通异常一起吞成 `last = None`，于是被 403/校验页拒绝时不但**重试满 3 次**
        （正是 MetaBlockedException 文档里说的那种 hammering），而且这一轮状态记成
        empty、熔断永远不触发 —— 冷却从我们的角度看等于不存在。
        """
        import time as _time
        last: list[Any] | None = None
        for attempt in range(self._retries + 1):
            try:
                last = super().search(query, region=region, safesearch=safesearch,
                                      timelimit=timelimit, page=page, **kwargs)
            except MetaBlockedException:
                raise
            except Exception:
                last = None
            if last:
                return last
            if attempt < self._retries:
                _time.sleep(self._retry_delay)
        return last

    def build_payload(self, query: str, region: str, safesearch: str,  # noqa: ARG002
                      timelimit: str | None, page: int = 1, **kwargs: str) -> dict[str, Any]:
        payload = {"q": query, "ensearch": "1"}
        if page > 1:
            payload["first"] = f"{(page - 1) * 10 + 1}"
        if timelimit:
            payload["filters"] = f"ex1:\"ez{timelimit}\""
        return payload

    def post_extract_results(self, results: list[Any]) -> list[Any]:
        """解码 Bing ck/a 跳转链接，过滤 bing 自身页面。

        Bing 结果链接形如 https://www.bing.com/ck/a?...&u=a1aHR0cHM6Ly93d3cu...
        ——真实 URL base64 编码在 ``u=`` 参数（a1 头 + base64，URL-safe）。
        """
        out = []
        for r in results:
            href = r.href.strip()
            if not href:
                continue
            if "bing.com/ck/a" in href:
                href = _bing_decode_ck_url(href)
            # 过滤 bing 自身页面
            if href and ("bing.com" in href and "/search" not in href and "/ck/a" not in href):
                continue
            r.href = href
            out.append(r)
        return out


class BingGlobal(Bing):
    """国际版 Bing（www.bing.com）：与 cn.bing.com 同一家、**另一套索引**。

    实测（同一查询 "kubernetes ingress 配置"）：两边结果标题只有 1/17 重合 —— cn 版
    给百度百科 / CSDN 这类中文内容，国际版给全球索引（kubernetes.io / en.wikipedia）。
    所以它不是 cn 版的别名，是给"想要国际结果"的用户的一个选择。国内无代理时
    www.bing.com 会绕道/超时 —— 默认池仍收它（国际 3 席之一），被墙时由连接失败冷却
    兜住（连续 3 次 → 10 分钟），不会每轮陪跑。provider 沿用 bing：结果 URL
    与 bing/ddg 同源，共识家族合并时不会虚报（见 search_engines._INDEX_FAMILY）。
    """

    name = "bing_global"
    search_url = "https://www.bing.com/search"

    # 国际版是另一套索引、内容池全球向 —— 它该拿展开后的变体。
    # 判据是「版面/内容池是不是中文」，不是继承自谁，所以这里显式覆盖。
    prefers_raw_query = False


# ─── 百度（国内、免费、免密、无需 cookie）────────────────────────────────────
def _is_challenge_shell(html_text: str) -> bool:
    """200 拦截页：**只有几 KB 的壳** + 校验字样。

    正常 SERP / 条目页是几百 KB（360 360KB、sogou 540KB、百度 1MB+），所以用体积做
    第一道闸门 —— 只按关键字匹配的话，正常页面里内嵌的脚本文案（前端 bundle 里就写
    着"安全校验"）会把真页面误判成拦截页。实测形态：百度搜索 1.4KB、百科 4.4KB
    （「安全校验中…」）、搜狗 5.4KB（含"验证"）。
    """
    if len(html_text) >= 20000:
        return False
    return any(marker in html_text for marker in
               ("百度安全验证", "安全校验", "请输入验证码", "验证码", "访问过于频繁"))


def _clean_result_href(raw: str, drop_hosts: tuple[str, ...]) -> str:
    """从 ``mu`` / ``data-mdurl`` / ``data-url`` 里取出的目标 URL 的卫生检查。

    实测坑（360）：视频聚合卡把多个 URL 拼在同一个 ``data-mdurl`` 里
    （``...&srcg=...https://www.douyin.com/video/…https://www.bilibili.com/video/…``），
    原样交出去会得到一个打不开的链接。规则：单个 http(s) URL、不含空白、不含第二个
    scheme、且不指向引擎自己 —— 不满足就返回空串（调用方丢掉这条）。
    """
    href = (raw or "").strip()
    if not href.startswith(("http://", "https://")):
        return ""
    if href.count("://") > 1 or any(c.isspace() for c in href):
        return ""
    try:
        host = (urlparse(href).netloc or "").lower().rstrip(".")
    except ValueError:
        return ""
    if not host:
        return ""
    for h in drop_hosts:
        if host == h or host.endswith("." + h):
            return ""
    return href


#: dhole 的短码 → 百度 `gpc=stf=` 那个绝对区间的天数跨度（day/week/month/year 四档，
#: 与 `_FRESHNESS_TO_TIMELIMIT` 同一批取值）。这一颗只描述**我们发出去什么**，
#: 不是百度按它筛了什么；离线可证的只有差分，行级真值归 `published`/`date_coverage`。
_BAIDU_STF_DAYS = {"d": 1, "w": 7, "m": 31, "y": 366}


class Baidu(BaseSearchEngine):
    """百度搜索（www.baidu.com/s?wd=）：国内裸网直连，实测无反爬（1.6s / 1MB 真 SERP，
    无需 stealthy/cookie）。

    百度同时跑两套版面（和 bing 那次真事故同类）：
    * A 版：容器 ``div.result.c-container``，真链在容器的 ``mu`` 属性里；
    * B 版：容器 ``div.c-result``，**没有 mu**，真链埋在 ``data-log`` 的 JSON 里
      （``{"fm":"alop",…,"mu":"https://tokio.rs/"}``）。
    两版的标题都是 ``h3``、摘要都叫 ``summary-text_<构建 hash>``，所以两个容器各出一份
    XPath，href 取 ``(@mu | @data-log)[1]`` 再在后处理里解 JSON。

    结果链接本身是 ``baidu.com/link?url=<token>`` 跳转包装（token 会过期），真实目标
    URL 才是要交出去的东西；拿不到真链、或真链指向百度自家占位（``nourl.ubs.
    baidu.com`` / ``recommend_list.baidu.com`` 等信息流卡片）的一律丢掉 —— 宁可少
    几条，也不能把不可用的 wrapper / 占位链接当成结果 URL。
    """

    name = "baidu"
    provider = "baidu"  # 独立索引家族（既不是 bing 也不是 google 代理）
    search_url = "https://www.baidu.com/s"
    search_method = "GET"
    # 精确类名匹配（concat/normalize-space 是 XPath 里判断"类名列表里含某一项"的写法：
    # contains(@class,'c-result') 会把 c-result-content 也算进来）。
    items_xpath = ("//div[contains(concat(' ', normalize-space(@class), ' '), ' c-container ')"
                   " or contains(concat(' ', normalize-space(@class), ' '), ' c-result ')]")
    elements_xpath: ClassVar[Mapping[str, str]] = {
        "title": ".//h3//text()",
        "href": "(./@mu | ./@data-log)[1]",
        # 摘要：新版叫 summary-text_<构建 hash>（hash 会变，所以按前缀取），
        # 旧版版面叫 c-abstract。两个都不在时该条摘要是空串（不是解析器漂移）。
        "body": (".//*[contains(@class,'summary-text') "
                 "or contains(@class,'c-abstract')]//text()"),
        # 期4.2（实测，冻结夹具 tests/engine_fixtures/baidu_variant_{a,b}.html）：
        # 两套版面各处一格日期 —— A 版 `div.cos-row` 里的 `span.cos-space-mr-3xs`
        # （5 个容器 2 个有，"2024年9月5日"），B 版 `div.cosc-card-footer` 里的
        # `span.cosc-source-caption`（6 个容器 3 个有）。类名带构建 hash，所以按
        # 前缀取并集；读不出来的那一格交回空串（B 版有一条 "4月23日" 没有年份，
        # 4.0 的叶子如实拒判 —— 补一个年份就是替读者编日期）。
        "published": (".//span[contains(@class,'cos-space-mr-3xs') "
                      "or contains(@class,'cosc-source-caption')]//text()"),
    }
    headers_update: ClassVar[Mapping[str, str]] = {"Accept-Language": "zh-CN,zh;q=0.9"}
    _detect_challenge_shell = True
    # 中文索引占优：意图展开追加的是英文术语，对这一家等于换了一个查询。
    prefers_raw_query = True

    def build_payload(self, query: str, region: str, safesearch: str,  # noqa: ARG002
                      timelimit: str | None,
                      page: int = 1, **kwargs: str) -> dict[str, Any]:
        payload = {"wd": query, "ie": "utf-8"}
        if page > 1:
            payload["pn"] = f"{(page - 1) * 10}"
        # 期4.4：`gpc=stf=<起>,<止>|stf_type=2`（Unix 秒）是这一家时间过滤的写法，
        # dhole 现在真的发出去了 —— 判据是差分（`build_payload(timelimit=None)` vs
        # `('w')` 多出的那颗键），由 `tests/test_search_date_window_g24.py` 与
        # `test_a_date_claim_blames_the_side_that_is_ours.py` 钉住，所以 baidu 从
        # `_DATE_PARAM_UNWIRED` 搬进 `_DATE_AWARE_ENGINES`。
        # **说的是"dhole 发了这一颗"，不是"百度按它筛过结果"**：语义（stf 是绝对时间
        # 区间、含不含当天）离线无法核实，所以这一档与其余已接线的家一样只在
        # `date_filter` 里报"问了什么"，行级真值仍由 `published` + `date_coverage` 说。
        if timelimit in _BAIDU_STF_DAYS:
            _now = int(time())
            payload["gpc"] = (f"stf={_now - _BAIDU_STF_DAYS[timelimit] * 86400},{_now}"
                              "|stf_type=2")
        return payload

    @staticmethod
    def _mu_from_data_log(blob: str) -> str:
        """B 版面的真链藏在 data-log 的 JSON 里：{"fm":"alop",…,"mu":"<url>"}。"""
        try:
            return str(json.loads(blob).get("mu") or "")
        except (ValueError, TypeError, AttributeError):
            return ""

    def search(self, query: str, region: str = "us-en", safesearch: str = "moderate",
               timelimit: str | None = None, page: int = 1, **kwargs: str) -> list[Any] | None:
        """百度偶尔回 200 + 1.4KB 拦截页（实测约 1/16，突发/并发请求时更容易出现）。

        重试一次（拦截页很小，代价极低）；两次都被拦就抛 MetaBlockedException 进熔断
        冷却 —— 0 条结果的形态会被读成「这个查询百度没结果」，而 `dhole -v` 的产出
        面板还会把它算成解析器漂移，两种误判都比"被拦，等会儿再试"更糟。
        """
        import time as _time

        payload = self.build_payload(query=query, region=region, safesearch=safesearch,
                                     timelimit=timelimit, page=page, **kwargs)
        for attempt in range(2):
            html_text = self.request(self.search_method, self.search_url, params=payload)
            if not html_text:
                return None
            if not _is_challenge_shell(html_text):
                kept = self.extract_kept(html_text)
                self.kept_after_filter = len(kept)
                return kept
            if attempt == 0:
                _time.sleep(0.5)
        raise MetaBlockedException("baidu 拦截页 (HTTP 200 challenge page)",
                                   challenge=True)

    def post_extract_results(self, results: list[Any]) -> list[Any]:
        out = []
        for r in results:
            href = (r.href or "").strip()
            if href.startswith("{"):
                href = self._mu_from_data_log(href)
            href = _clean_result_href(href, ("baidu.com",))
            if not href:
                continue  # 自家占位/包装/拼接垃圾：没有可用目标 URL
            r.href = href
            out.append(r)
        return out


# ─── 注册表 ──────────────────────────────────────────────────────────────────
# 全部启用的文本后端。Bing 可用（免费/免密，中国大陆免 VPN 直连）。顺序 = 大致
# 偏好；全部并行跑。
_TEXT_ENGINES: dict[str, type[BaseSearchEngine]] = {
    "duckduckgo": Duckduckgo,
    "brave": Brave,
    "yandex": Yandex,
    "bing": Bing,
    "bing_global": BingGlobal,
    "baidu": Baidu,
}
# dhole 的公开引擎名 -> 元搜索后端名。
_DHOLE_TO_BACKEND = {
    "duckduckgo": "duckduckgo", "ddg": "duckduckgo",  # ddg 是常见别名
    "bing": "bing",
    "brave": "brave", "yandex": "yandex",
    "baidu": "baidu",
    # 国内两家独立索引（sogou / so360 同生态位，都按 IP 限流）都在默认池里。
    # "360" 是 so360 的顺手别名（引擎名以数字开头不合本项目的命名习惯）。
    "so360": "so360", "360": "so360", "sogou": "sogou",
    # bing_global 是 www.bing.com 那套国际索引（与 cn 版结果几乎不重合，但国内直连常需
    # 代理），占默认池的国际席位。
    "bing_global": "bing_global",
    # yahoo 与 bing 同一套索引（provider 都是 bing），是 Bing 家族的**第二个入口**：
    # 不增加共识家族，只在 bing/bing_global/ddg 被按 IP 限流时留一条可答的路。
    "yahoo": "yahoo",
    # marginalia 是独立索引（自己的小网爬虫），英文为主、商业长尾很浅；放在池子
    # 末位，答得上时提供独立家族，答不上时由其余引擎兜住。
    "marginalia": "marginalia",
    # seznam 是捷克门户的自有索引（服务端渲染、免密）：中欧语料深、全球查询也能答
    # （实测 python asyncio 首条 docs.python.org）。新接入，先按小众观察位放池尾。
    "seznam": "seznam",
    # 神马是 UC/阿里的移动搜索自有索引（夸克同一套基础设施，一个家族一个入口）：
    # 服务端渲染、国内直连；AliWAF 惩罚页按 IP 限流（so360 校验页同类），归国内
    # 独立索引那一档。
    "shenma": "shenma",
    # 付费 JSON 后端：可按名字点名，走自己那条轨道（见 KeyedApiEngine）—— 刻意
    # 不在 _TEXT_ENGINES 里。名字必须与 KEYED_ENGINES 严格一致：留一个没有实现的
    # 键，点名它会通过校验却什么都不返回。
    "tavily": "tavily",
    "bocha": "bocha",
    "exa": "exa",
    "deepseek": "deepseek",
    "firecrawl": "firecrawl",
    # brave_api 是 Brave 官方付费 API（keyed）；免密抓取版另有 "brave" 一席，
    # 两者同名不同物 —— 名字必须能各答各的，共识家族才分得清。
    "brave_api": "brave_api",
}
# 默认池 = 注册表的全部免密引擎（§3-22）。2026-10 起池序=可信度分层（详见
# search_engines._PRIORITY_TIERS，那张表是唯一的作者）：直连稳定的主力在前
# （baidu/bing），按 IP 限流的国内独立索引（sogou/so360/shenma）与常被地区墙的
# 国际引擎（yandex/brave）居中，小众浅索引垫底（seznam 新观察位、marginalia 段末）。
# 引擎数 12，索引家族 9（bing 家族四入口：bing/bing_global/yahoo/duckduckgo）。
_DEFAULT_BACKENDS = ["baidu", "bing", "bing_global", "duckduckgo",
                     "yahoo", "brave", "sogou", "so360", "shenma", "yandex",
                     "seznam", "marginalia"]


# ─── 360 搜索（so.com，独立索引，默认池） ────────────────────────────────────
class So360(BaseSearchEngine):
    """360 搜索（www.so.com/s?q=）：国内直连、服务端渲染，独立索引（360 自家爬虫）。

    结果 href 是 ``so.com/link?m=<token>`` 跳转包装，真实 URL 在卡片的
    ``data-mdurl`` 属性里（实测直接可读）—— 与百度 ``mu`` 同一套思路：只交真链，
    拿不到真链的卡片丢掉。分页 ``&pn=<页码>``（实测 page1∩page2 = 0）。
    **在默认池里**：与 sogou 同生态位（国内可达的独立索引，两家都按 IP 限流）。
    16.1 时默认池的国内席只点 sogou、把它排在池外；17.0 起 8 家免密引擎全部入池，
    国内这一档两家都在。
    """

    name = "so360"
    provider = "so360"
    search_url = "https://www.so.com/s"
    search_method = "GET"
    items_xpath = "//li[contains(@class,'res-list')]"
    elements_xpath: ClassVar[Mapping[str, str]] = {
        "title": ".//h3//text()",
        "href": ".//@data-mdurl",
        "body": (".//*[contains(@class,'res-desc') "
                 "or contains(@class,'res-list-summary')]//text()"),
        # 期4.2（实测）：辅助信息那一格 `span.g-c-dark` 里带"发贴时间：2026年5月8日"
        # —— 冻结夹具 7 个容器 1 个有这一格。取不到日期时交回空串（该页那一条原文里
        # 还混着上游写坏的 "&nbsp"，4.0 的叶子读不出年份就不判，不替它补）。
        "published": ".//span[contains(@class,'g-c-dark')]//text()",
    }
    headers_update: ClassVar[Mapping[str, str]] = {"Accept-Language": "zh-CN,zh;q=0.9"}
    _detect_challenge_shell = True
    # 中文索引占优：意图展开追加的是英文术语，对这一家等于换了一个查询。
    prefers_raw_query = True

    def __init__(self, proxy: str | None = None, timeout: int | None = None, *,
                 verify: bool = True) -> None:
        # 固定桌面指纹：360 对移动档指纹会换一套没有 li.res-list 的版面 —— 实测
        # impersonate_os="random" 时 3 次里 2 次读不到结果容器（静默 empty），而
        # windows 档 3/3 稳定拿到 7 个容器。
        self.http_client = _PrimpClient(proxy=proxy, timeout=timeout, verify=verify,
                                        impersonate="chrome", impersonate_os="windows")
        self.http_client.client.headers_update(self.headers_update)
        self.results: list[Any] = []

    def build_payload(self, query: str, region: str, safesearch: str,  # noqa: ARG002
                      timelimit: str | None,  # noqa: ARG002 - 故意忽略：dhole 不给这一家
                      # 发日期过滤，也没登记过它能发什么（见 search_engines._DATE_AWARE_ENGINES）
                      page: int = 1, **kwargs: str) -> dict[str, Any]:
        payload = {"q": query}
        if page > 1:
            payload["pn"] = str(page)
        return payload

    def post_extract_results(self, results: list[Any]) -> list[Any]:
        out = []
        for r in results:
            href = _clean_result_href(r.href, ("so.com",))
            if not href:
                continue  # 自家跳转包装 / 站内页 / 拼接垃圾（见 _clean_result_href）
            r.href = href
            out.append(r)
        return out


# ─── 搜狗主站（sogou.com/web，独立索引，默认池） ─────────────────────────────
class Sogou(BaseSearchEngine):
    """搜狗网页搜索（www.sogou.com/web?query=）：国内直连、服务端渲染。

    结果 href 是 ``/link?url=<token>`` 相对包装，真实 URL 在卡片的 ``data-url``
    属性里。选择器带 ``[.//@data-url]`` 不是装饰：同一个 ``div.vrwrap`` 类名也被
    「相关搜索」聚合块复用（实测 9 个里 2 个是它们），没有 data-url 就没有结果目标。

    索引家族是 ``sogou``：17.0 前它还和公众号垂直（sogou_weixin）共用这一家，现在
    只剩它一家。家族按底层索引归，宁可少报共识也不虚报。
    **在默认池的国内 4 席里**（so360 与它同生态位、两家都按 IP 限流，16.x 的默认池
    只点一家，17.0 两家一起进池）。分页 ``&page=<页码>``（实测 page1∩page2 = 0）。
    """

    name = "sogou"
    provider = "sogou"
    search_url = "https://www.sogou.com/web"
    search_method = "GET"
    items_xpath = "//div[contains(@class,'vrwrap')][.//@data-url]"
    # 哪个属性承载**这一条结果**的目标，由结构和 URL 的形状决定 —— 从不看主机名
    # 或关键词。
    #
    # 2026-10-14 在线上 SERP 实测（10 张卡片，`review/.runtime/p4/sogou_serp.html`）：
    # 每张卡片都在 `@data-url` 里带着目标，其中 9 个挂在
    # `div.r-sech…[style="display:none"][data-async="1"]` 槽位上 —— 这是搜狗的正常
    # 机制，所以"隐藏/异步"**不是**不信任它的理由。那 9 张卡片自己的 `<h3>` 锚点
    # 是相对路径 `/link?url=` 包装。第 10 张（一条公众号结果）不同：它的槽位写着
    # `http://mp.weixin.qq.com/count/<sig>/<mid>/<idx>&scene=6` —— 一个点击计数包装，
    # `&` 前面没有 `?`，即一个不指向任何资源、会 404 的 URL（round 01，R01-015）——
    # 而真正的文章链接（`mp.weixin.qq.com/s?src=11&…`）在标题锚点上。
    #
    # 所以：优先取**能构成 URL** 的 data-url，只有卡片没有这样的 data-url 时才取绝对
    # 标题锚点。两个分支按构造互斥，因为锚点分支的守卫与第一分支是同一个谓词。
    # 必须只回来一个节点：`extract_results` 把 XPath 返回的东西无分隔地粘起来，两个
    # 候选会变成一条不可用的字符串 —— 那是丢了一行，不是修好一行。
    _MALFORMED = "contains({attr},'&') and not(contains({attr},'?'))"
    _CARD = ("ancestor::div[contains(concat(' ', normalize-space(@class), ' '),"
             " 'vrwrap')][1]")
    elements_xpath: ClassVar[Mapping[str, str]] = {
        "title": ".//h3//text()",
        "href": (
            "(. | .//*)[@data-url and not("
            + _MALFORMED.format(attr="@data-url") + ")][1]/@data-url"
            " | .//h3//a[@href and starts-with(@href,'http') and not("
            + _MALFORMED.format(attr="@href") + ")"
            " and not(" + _CARD + "/@data-url[not(" + _MALFORMED.format(attr=".") + ")])"
            " and not(" + _CARD + "//*[@data-url and not("
            + _MALFORMED.format(attr="@data-url") + ")])][1]/@href"),
        "body": ".//*[contains(@class,'fz-mid')]//text()",
        # 期4.2（实测）：`span.cite-date` 是绝对到日的那一格（"2020-08-31" /
        # "2024-11-22"）。冻结夹具 8 个结果容器里 4 个带它，其余交回空串 —— 这就是
        # `date_coverage.rows_with_date` 是个真分数而不是全有/全无的形状。
        "published": ".//span[contains(@class,'cite-date')]//text()",
    }
    headers_update: ClassVar[Mapping[str, str]] = {"Accept-Language": "zh-CN,zh;q=0.9"}
    _detect_challenge_shell = True
    # 中文索引占优：意图展开追加的是英文术语，对这一家等于换了一个查询。
    prefers_raw_query = True

    def __init__(self, proxy: str | None = None, timeout: int | None = None, *,
                 verify: bool = True) -> None:
        # 与 360 同样的理由（同属国内 SSR 版面家族）：固定桌面指纹，别让移动档指纹
        # 把结果换成一套没有 vrwrap 的版面。
        self.http_client = _PrimpClient(proxy=proxy, timeout=timeout, verify=verify,
                                        impersonate="chrome", impersonate_os="windows")
        self.http_client.client.headers_update(self.headers_update)
        self.results: list[Any] = []

    def build_payload(self, query: str, region: str, safesearch: str,  # noqa: ARG002
                      timelimit: str | None,  # noqa: ARG002 - 故意忽略：dhole 不给这一家
                      # 发日期过滤，也没登记过它能发什么（见 search_engines._DATE_AWARE_ENGINES）
                      page: int = 1, **kwargs: str) -> dict[str, Any]:
        payload = {"query": query}
        if page > 1:
            payload["page"] = str(page)
        return payload

    def post_extract_results(self, results: list[Any]) -> list[Any]:
        out = []
        for r in results:
            href = _clean_result_href(r.href, ("sogou.com",))
            if not href:
                continue  # /link?url=... 包装或站内页
            r.href = href
            out.append(r)
        return out


# ─── Yahoo（Bing 索引，第二个端点）───────────────────────────────────────────
def _yahoo_decode_url(href: str) -> str:
    """拆开 Yahoo 的 r.search.yahoo.com/.../RU=<百分号编码> 跳转。

    Yahoo 有两套版面（``div.algo.relsrch`` 卡片和 ``section.algo.Sr`` 卡片）；
    两者都把目标包在同一个跳转里。解不开的包装会被丢掉，而不是当结果 URL 发出去
    —— 它会过期，还带着会话令牌。
    """
    from urllib.parse import unquote, urlparse
    host = (urlparse(href).netloc or "").lower()
    if not host.endswith("yahoo.com"):
        return href
    # 目标在 PATH 里（`/RU=https%3a%2f%2f...`），不在 query 里；Yahoo 会在
    # r.search.yahoo.com 和 rd.search.yahoo.com 之间轮换。
    match = re.search(r"/RU=([^/]+)", href)
    if match:
        target = unquote(match.group(1))
    else:
        from urllib.parse import parse_qs
        target = parse_qs(urlparse(href).query).get("RU", [""])[0]
    if target.startswith(("http://", "https://")):
        return target
    # 解不开的跳转丢掉；真正的 yahoo.com 结果 URL 不丢。
    return "" if host.startswith(("r.search.", "rd.search.", "search.")) else href


class Yahoo(BaseSearchEngine):
    """Yahoo 网页搜索：进入 Bing 索引的第二个入口。

    与 bing / bing_global / duckduckgo 同属一个索引家族，所以它增加的是一个
    端点（按 IP 封禁时多一条可用路），不是一张共识票。Yahoo 会轮换两套结果
    版面；下面的并集覆盖两者，夹具集为每套版面各钉一份契约，版面变了不会没人
    发现。
    """

    name = "yahoo"
    provider = "bing"
    search_url = "https://search.yahoo.com/search"
    search_method = "GET"
    # A 版：每条结果一个 div.algo.relsrch。B 版：一个 section.richAlgo 装第一条
    # 结果，后面 section.Sr 卡片互相嵌套。这些 section 会重叠，所以每个元素选择器
    # 都限定在第一个 h3 / 第一个 compText 上 —— 否则一张卡片的标题会串进它嵌套的
    # 兄弟节点。
    items_xpath = (
        "//div[contains(@class,'algo') and contains(@class,'relsrch') and .//h3]"
        " | //section[contains(concat(' ',normalize-space(@class),' '),' Sr ')]"
        " | //section[contains(concat(' ',normalize-space(@class),' '),' richAlgo ')]"
    )
    elements_xpath: ClassVar[Mapping[str, str]] = {
        # B 版的 h3 会在结果前面带上站点/URL 面包屑，装在 span.title-url 里；
        # 只有那个 span 之外的文本才是标题。
        "title": "(.//h3)[1]//text()[not(ancestor::span[contains(@class,'title-url')])]",
        "href": "((.//h3)[1]/a/@href | .//a/@href)[1]",
        "body": "(.//div[contains(@class,'compText')])[1]//text()",
        "published": "(.//span[contains(@class,'fc-smoke')])[1]//text()",
    }
    headers_update: ClassVar[Mapping[str, str]] = {
        "Accept-Language": "en-US,en;q=0.9"}

    def __init__(self, proxy: str | None = None, timeout: int | None = None, *,
                 verify: bool = True) -> None:
        # 固定一套 Chrome 指纹：primp 的随机选择偶尔会撞上 Yahoo 以握手 EOF
        # 回应的 TLS 配置（线上观测到过）。
        self.http_client = _PrimpClient(proxy=proxy, timeout=timeout, verify=verify,
                                        impersonate="chrome", impersonate_os="windows")
        self.http_client.client.headers_update(self.headers_update)
        self.results: list[Any] = []

    def build_payload(self, query: str, region: str, safesearch: str,  # noqa: ARG002
                      timelimit: str | None,  # noqa: ARG002 - 未接线（见 _DATE_AWARE_ENGINES）
                      page: int = 1, **kwargs: str) -> dict[str, Any]:
        payload = {"p": query}
        if page > 1:
            payload["b"] = f"{10 * (page - 1) + 1}"
        return payload

    def post_extract_results(self, results: list[Any]) -> list[Any]:
        out = []
        for r in results:
            href = _yahoo_decode_url((r.href or "").strip())
            if not href.startswith(("http://", "https://")):
                continue
            r.href = href
            out.append(r)
        return out


# ─── Marginalia（独立的小众站点索引）─────────────────────────────────────────
class Marginalia(BaseSearchEngine):
    """Marginalia：独立、非商业的小众站点索引。

    服务端渲染、免密。语料偏英文，对商业长尾查询很浅，所以刻意放在默认池末位：
    答得上时提供多样性，答不上时由其余引擎兜住。分页需要每会话一个 ``sst`` 令牌，
    所以这个后端只答第 1 页；后面的页交回空，而不是假装第 1 页就是第 N 页。
    """

    name = "marginalia"
    provider = "marginalia"
    search_url = "https://search.marginalia.nu/search"
    search_method = "GET"
    items_xpath = (
        "//div[contains(@class,'bg-white') and contains(@class,'p-4') "
        "and .//h2/a]")
    elements_xpath: ClassVar[Mapping[str, str]] = {
        "title": ".//h2//text()",
        "href": ".//h2/a/@href",
        "body": ".//p[contains(@class,'mt-2')]//text()",
    }
    headers_update: ClassVar[Mapping[str, str]] = {
        "Accept-Language": "en-US,en;q=0.9"}
    _detect_challenge_shell = True
    # Marginalia 对被限流的客户端既回 400，也回 200 挑战页；两者都意味着"冷却"，
    # 不是"这个查询没结果"。
    _challenge_statuses = (400, 403, 429, 503)

    def __init__(self, proxy: str | None = None, timeout: int | None = None, *,
                 verify: bool = True) -> None:
        self.http_client = _PrimpClient(proxy=proxy, timeout=timeout, verify=verify,
                                        impersonate="chrome", impersonate_os="windows")
        self.http_client.client.headers_update(self.headers_update)
        self.results: list[Any] = []

    def _check_challenge(self, html_text: str) -> None:
        # Marginalia 的 200 反爬页约 37KB（"Wait A Moment / aggressive
        # bot activity"），高过通用的壳体积闸门。标成被拦，让引擎进入冷却，而不是
        # 报"没有结果"。
        # 标记扫描只在前 2KB 里做：挑战页把它放在靠前的 <title>/meta 里，而一条
        # 带同样字样的敌意 SEO 结果渲染在 SERP 中段 —— 全文档匹配会让一个被索引的
        # 页面把一个健康引擎冷却 600 秒。更宽的真挑战页会退化成"没有结果"（较轻的
        # 那种失败）。
        if ("aggressive bot activity" in html_text[:2048]
                or "Wait A Moment" in html_text[:2048]):
            raise MetaBlockedException(
                "marginalia rate limit (HTTP 200 challenge)", challenge=True)
        super()._check_challenge(html_text)

    def build_payload(self, query: str, region: str, safesearch: str,  # noqa: ARG002
                      timelimit: str | None,  # noqa: ARG002 - 未接线
                      page: int = 1, **kwargs: str) -> dict[str, Any]:
        return {"query": query}

    def search(self, query: str, region: str = "us-en",
               safesearch: str = "moderate", timelimit: str | None = None,
               page: int = 1, **kwargs: str) -> list[Any] | None:
        if page > 1:
            return None  # 没取过，不是"空"
        return super().search(query, region=region, safesearch=safesearch,
                              timelimit=timelimit, page=page, **kwargs)

    def post_extract_results(self, results: list[Any]) -> list[Any]:
        out = []
        for r in results:
            href = (r.href or "").strip()
            if not href.startswith(("http://", "https://")):
                continue
            if urlparse(href).netloc.endswith("marginalia.nu"):
                continue
            r.href = href
            out.append(r)
        return out


# ─── Seznam（search.seznam.cz，自有索引，默认池） ────────────────────────────
class Seznam(BaseSearchEngine):
    """Seznam：捷克门户自有的网页索引，服务端渲染、免密。

    2026-10 实测：全球查询回捷克站与全球站的混合（"python asyncio" 同页里有
    docs.python.org 也有 megaknihy.cz），是自有索引的形状、不是 Bing 换皮 ——
    按 ``seznam`` 独立家族计。结果锚点用**语义 data 属性**（``data-e-a="heading"``
    / ``"reference"``）：整站的 class 都是构建期哈希（``bc3389ab17b`` 一类，随发版
    轮换），当选择器必烂；href 是直链，无跳转包装。分页参数未核实，只答第 1 页
    —— 答不上的页交空，不把第 1 页冒充第 N 页（marginalia 同款纪律）。
    """

    name = "seznam"
    provider = "seznam"
    search_url = "https://search.seznam.cz/"
    search_method = "GET"
    # 每条结果 = 标题链接（a[data-e-a=heading]）的 h3 的父 div：favicon、标题、
    # 绿色 URL 行、摘要在同一个块里。锚 data-e-a 而不是 class，理由见 docstring。
    items_xpath = "//a[@data-e-a='heading']/../.."
    elements_xpath: ClassVar[Mapping[str, str]] = {
        "title": ".//a[@data-e-a='heading']//text()",
        "href": ".//a[@data-e-a='heading']/@href",
        # 摘要块自带 data-e-b-n（语义锚）：绿色 URL 行的嵌套层数不稳（实测隔一层
        # div），相对路径爬祖先层数会烂；语义锚跟着结构走。
        "body": ".//div[@data-e-b-n]//text()",
    }
    headers_update: ClassVar[Mapping[str, str]] = {
        "Accept-Language": "en;q=0.9,cs;q=0.8"}
    _detect_challenge_shell = True

    def __init__(self, proxy: str | None = None, timeout: int | None = None, *,
                 verify: bool = True) -> None:
        self.http_client = _PrimpClient(proxy=proxy, timeout=timeout, verify=verify,
                                        impersonate="chrome", impersonate_os="windows")
        self.http_client.client.headers_update(self.headers_update)
        self.results: list[Any] = []

    def build_payload(self, query: str, region: str, safesearch: str,  # noqa: ARG002
                      timelimit: str | None,  # noqa: ARG002 - 未接线：不知道这家
                      # 有什么可发的日期参数，不替它编
                      page: int = 1, **kwargs: str) -> dict[str, Any]:
        return {"q": query}

    def search(self, query: str, region: str = "us-en",
               safesearch: str = "moderate", timelimit: str | None = None,
               page: int = 1, **kwargs: str) -> list[Any] | None:
        if page > 1:
            return None  # 没取过，不是"空"
        return super().search(query, region=region, safesearch=safesearch,
                              timelimit=timelimit, page=page, **kwargs)

    def post_extract_results(self, results: list[Any]) -> list[Any]:
        out = []
        for r in results:
            href = _clean_result_href(r.href, ("seznam.cz",))
            if not href:
                continue
            r.href = href
            out.append(r)
        return out


# ─── 神马（m.sm.cn，阿里系自有索引，默认池） ──────────────────────────────────
class Shenma(BaseSearchEngine):
    """神马搜索（m.sm.cn/s?q=）：UC/阿里的移动搜索，自有索引，服务端渲染、免密。

    2026-10 实测：中英文都有真结果（直链、无跳转包装，摘要自带来源与日期），
    但 AliWAF 的 ``_____tmd_____/punish`` 惩罚页在连续 1-2 发后出现 —— 静默
    3 分钟仍被罚、8 分钟窗口出清。画像与 so360 的校验页同类（按 IP 限流），
    交给 ``MetaBlockedException(challenge=True)`` 的递增冷却：贡献得了就贡献，
    贡献不了不陪跑。夸克（quark.sm.cn）与它同一套基础设施，一个家族一个入口，
    不另开席。结果卡带全套语义锚：``data-sc``（含 pos/ads）、``data-cmp='ti'``
    （标题）、``qk-source-content``（摘要）；只收带标题链接的卡，百科/结构卡
    没有标题链接就不算 web 行。分页未核实，只答第 1 页。
    """

    name = "shenma"
    provider = "shenma"
    search_url = "https://m.sm.cn/s"
    search_method = "GET"
    items_xpath = ("//div[@id='results']/*[@data-sc]"
                   "[.//div[@data-cmp='ti']//a[@href]]")
    elements_xpath: ClassVar[Mapping[str, str]] = {
        "title": ".//div[@data-cmp='ti']//text()",
        "href": ".//div[@data-cmp='ti']//a/@href",
        "body": ".//div[contains(@class,'qk-source-content')]//text()",
    }
    headers_update: ClassVar[Mapping[str, str]] = {
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    _detect_challenge_shell = True

    def __init__(self, proxy: str | None = None, timeout: int | None = None, *,
                 verify: bool = True) -> None:
        self.http_client = _PrimpClient(proxy=proxy, timeout=timeout, verify=verify,
                                        impersonate="chrome", impersonate_os="windows")
        self.http_client.client.headers_update(self.headers_update)
        self.results: list[Any] = []

    def _check_challenge(self, html_text: str) -> None:
        # AliWAF 惩罚页是 ~1KB 的 HTTP 200 跳转脚本（_____tmd_____/punish），
        # 不是"这个查询没结果"。按校验页记：challenge=True 走递增冷却。
        # 标记只在页面前 4KB 里找：惩罚页整个就 1KB、标记在前部，而 SERP 正文
        # 里敌手 SEO 的标题/摘要可能带着同一个串 —— 全文匹配会让一页进索引的
        # 敌意内容把健康引擎冷却 600s。窗口变宽的代价是真被拦时偶报 "no
        # results"（较轻的失败方向），不会反向误熔断。
        if "_____tmd_____" in html_text[:4096]:
            raise MetaBlockedException(
                "shenma AliWAF punish page (HTTP 200 challenge)", challenge=True)
        super()._check_challenge(html_text)

    def build_payload(self, query: str, region: str, safesearch: str,  # noqa: ARG002
                      timelimit: str | None,  # noqa: ARG002 - 未接线：没核实过这家
                      # 有什么可发的日期参数，不替它编
                      page: int = 1, **kwargs: str) -> dict[str, Any]:
        return {"q": query}

    def search(self, query: str, region: str = "us-en",
               safesearch: str = "moderate", timelimit: str | None = None,
               page: int = 1, **kwargs: str) -> list[Any] | None:
        if page > 1:
            return None  # 没取过，不是"空"
        return super().search(query, region=region, safesearch=safesearch,
                              timelimit=timelimit, page=page, **kwargs)

    def post_extract_results(self, results: list[Any]) -> list[Any]:
        out = []
        for r in results:
            href = _clean_result_href(r.href, ("sm.cn",))
            if not href:
                continue
            r.href = href
            out.append(r)
        return out


_TEXT_ENGINES["so360"] = So360
_TEXT_ENGINES["sogou"] = Sogou
_TEXT_ENGINES["yahoo"] = Yahoo
_TEXT_ENGINES["marginalia"] = Marginalia
_TEXT_ENGINES["seznam"] = Seznam
_TEXT_ENGINES["shenma"] = Shenma

# ─── 带 key 的 JSON 搜索 API（Tavily / 博查）─────────────────────────────────
# 这些后端不走 BaseSearchEngine 的 HTML 抓取契约：都是 POST JSON + Bearer key。
# 策略：**默认不跑**，只有 engines= 显式点名才执行 —— 每次调用都是真金白银。


class KeyedApiEngine:
    """带密钥 JSON 搜索 API 的公共骨架。

    子类只实现 build_request/parse_response；鉴权失败、超时、日志脱敏、
    「非 200 静默返回空」都由 search_json 统一处理。
    """

    name: ClassVar[str]
    env_var: ClassVar[str]
    endpoint: ClassVar[str]
    auth_codes: ClassVar[tuple] = (401, 403)
    timeout_floor: ClassVar[float] = 20.0
    # POST JSON 是常态；Brave 官方 API 是 GET + query params，payload 那一格
    # 对 GET 引擎装的就是 params（骨架里同一份字典，两种线上形状）。
    http_method: ClassVar[str] = "POST"

    AuthError: ClassVar[type] = MetaSearchException

    @classmethod
    def api_key(cls) -> str:
        return os.environ.get(cls.env_var) or ""

    @classmethod
    def mask(cls, text: str) -> str:
        """redact_api_key 的正则只认 sk-/pk-/api_key- 前缀，盖不住各家 key 的
        形状，所以用已知值做定向替换。空 key 时 str.replace("", x) 会把标记
        插进每个字符之间，必须防。"""
        masked = redact_api_key(text)
        key = cls.api_key()
        if key:
            masked = masked.replace(key, "[API_KEY_REDACTED]")
        return masked

    def build_request(self, query: str, max_results: int, timelimit: Optional[str]) -> tuple:
        raise NotImplementedError

    def parse_response(self, data: Any) -> list:
        raise NotImplementedError

    def search_json(self, query: str, max_results: int, timelimit: Optional[str] = None) -> list:
        key = self.api_key()
        if not key:
            raise self.AuthError(f"{self.name} requires {self.env_var}, which is not set")
        try:
            url, headers, payload = self.build_request(query, max_results, timelimit)
            if self.http_method == "GET":
                resp = httpx.get(
                    url, params=payload, headers=headers,
                    timeout=max(_SEARCH_DEADLINE, self.timeout_floor),
                )
            else:
                resp = httpx.post(
                    url, json=payload, headers=headers,
                    timeout=max(_SEARCH_DEADLINE, self.timeout_floor),
                )
            if resp.status_code in self.auth_codes:
                raise self.AuthError(
                    f"{self.name} rejected the credentials (HTTP {resp.status_code})"
                )
            if resp.status_code != 200:
                logger.debug(
                    "%s SERP HTTP %d: %s", self.name, resp.status_code,
                    self.mask(resp.text[:200]),
                )
                return []
            out = []
            for item in self.parse_response(resp.json())[:max_results]:
                if getattr(item, "title", "") and getattr(item, "href", ""):
                    out.append(item)
            return out
        except self.AuthError:
            raise
        except Exception as e:
            logger.debug("%s API error: %s", self.name, self.mask(repr(e)))
            return []


def _ns(title: str, href: str, body: str):
    """SimpleNamespace(title, href, body) —— 与 metasearch 的属性访问兼容。"""
    from types import SimpleNamespace

    return SimpleNamespace(title=title, href=href, body=body)


class _Tavily(KeyedApiEngine):
    name = "tavily"
    env_var = "DHOLE_TAVILY_API_KEY"
    AuthError = TavilyAuthError
    endpoint = "https://api.tavily.com/"
    # basic=1 credit；advanced=2 credits，默认不替用户花钱

    #: dhole 交给构建器的是 `_FRESHNESS_TO_TIMELIMIT` 产出的短码
    #: （"d"/"w"/"m"/"y"）；这个 API 要的是整词。从前拿长名字去比，结果真实调用里
    #: `time_range` 从来没被设上过。
    _TIME_RANGE = {"d": "day", "w": "week", "m": "month", "y": "year"}

    def build_request(self, query, max_results, timelimit):
        payload = {"query": query, "max_results": max_results, "search_depth": "basic"}
        if timelimit in self._TIME_RANGE:
            payload["time_range"] = self._TIME_RANGE[timelimit]
        return (
            self.endpoint,
            {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key()}"},
            payload,
        )

    def parse_response(self, data):
        out = []
        for item in data.get("results", []):
            if item.get("title") and item.get("url"):
                out.append(_ns(item["title"], item["url"], item.get("content", "")))
        return out


class _Bocha(KeyedApiEngine):
    name = "bocha"
    env_var = "DHOLE_BOCHA_API_KEY"
    AuthError = BochaAuthError
    endpoint = "https://api.bochaai.com/v1/web-search"
    # 国内裸网直连；索引与 Bing 同源。summary=true 才有全文摘要（更贵更慢），默认关。

    # 同 _Tavily：进来的是短码，Bocha 的词汇是 oneDay/oneWeek/...
    _FRESHNESS = {"d": "oneDay", "w": "oneWeek", "m": "oneMonth", "y": "oneYear"}

    def build_request(self, query, max_results, timelimit):
        payload = {"query": query, "count": max_results, "summary": False}
        if timelimit in self._FRESHNESS:
            payload["freshness"] = self._FRESHNESS[timelimit]
        return (
            self.endpoint,
            {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key()}"},
            payload,
        )

    def parse_response(self, data):
        pages = (data.get("data") or {}).get("webPages") or {}
        out = []
        for item in pages.get("value", []):
            if item.get("name") and item.get("url"):
                snippet = item.get("summary") or item.get("snippet", "")
                out.append(_ns(item["name"], item["url"], snippet))
        return out


class _Exa(KeyedApiEngine):
    """Exa（api.exa.ai/search）：面向 agent 的神经/关键词混合索引。

    日期是绝对窗口而不是词表：`startPublishedDate` 收 ISO 8601，所以短码先换算成
    「现在减去档位宽度」。numResults 保持请求值（1-100；>10 的部分按上游价目另计，
    dhole 不替用户放大它）。`contents` 不开 —— 正文走 web_read，搜索层只要行。
    """
    name = "exa"
    env_var = "DHOLE_EXA_API_KEY"
    AuthError = ExaAuthError
    endpoint = "https://api.exa.ai/search"

    # 短码 -> 窗口宽度（天）。month 取 30、year 取 365：与其它引擎的档位语义对齐
    # （都是"约莫这一档"），上游按 publishedDate 过滤，不要求整月整年。
    _WINDOW_DAYS = {"d": 1, "w": 7, "m": 30, "y": 365}

    def build_request(self, query, max_results, timelimit):
        payload = {"query": query, "numResults": max_results}
        if timelimit in self._WINDOW_DAYS:
            start = (datetime.now(timezone.utc)
                     - timedelta(days=self._WINDOW_DAYS[timelimit]))
            payload["startPublishedDate"] = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        return (
            self.endpoint,
            {"Content-Type": "application/json", "x-api-key": self.api_key()},
            payload,
        )

    def parse_response(self, data):
        out = []
        for item in data.get("results", []):
            if item.get("title") and item.get("url"):
                out.append(_ns(item["title"], item["url"], item.get("text", "")))
        return out


class _DeepSeek(KeyedApiEngine):
    """DeepSeek Search（api.deepseek.com/v1/tools/search）：独立搜索 API，
    OpenAI 兼容风格的鉴权与错误面（402 余额不足也算"凭据被拒"而不是空结果）。

    日期过滤只有查询算子一途（`after:`/`before:`），改写 query 才能带上它 ——
    那 128 字符的查询预算是调用方的，dhole 不动它：所以这家**不进**
    `_DATE_AWARE_ENGINES`，报告按"dhole 没给它发日期过滤"如实说。
    `need_content` 关着：行只要摘要（summary 是给模型读的那份），正文归 web_read。
    """
    name = "deepseek"
    env_var = "DHOLE_DEEPSEEK_API_KEY"
    AuthError = DeepSeekAuthError
    endpoint = "https://api.deepseek.com/v1/tools/search"
    auth_codes = (401, 402, 403)

    def build_request(self, query, max_results, timelimit):  # noqa: ARG002 - 日期只有
        # 查询算子一途，见类 docstring：payload 里没有它可发的位置
        return (
            self.endpoint,
            {"Content-Type": "application/json",
             "Authorization": f"Bearer {self.api_key()}"},
            {"query": query, "count": max_results, "need_content": False},
        )

    def parse_response(self, data):
        out = []
        for item in data.get("results", []):
            if item.get("title") and item.get("url"):
                snippet = item.get("summary") or item.get("snippet", "")
                out.append(_ns(item["title"], item["url"], snippet))
        return out


class _Firecrawl(KeyedApiEngine):
    """Firecrawl search（api.firecrawl.dev/v2/search）：web/news 等源的聚合检索。

    只取 `web` 一族行（`sources` 不设即默认 web）：news 行按上游形状走 snippet/date，
    与 web 行不同构，而 dhole 的合并表只认一种行。`tbs` 是 Google 风格的时间档，
    短码几乎原样映射（d/w/m/y -> qdr:d/w/m/y）。`scrapeOptions` 不开 —— 抓正文是
    web_read 的职责，search 层开它等于按行计费买一份本要丢掉的东西。
    """
    name = "firecrawl"
    env_var = "DHOLE_FIRECRAWL_API_KEY"
    AuthError = FirecrawlAuthError
    endpoint = "https://api.firecrawl.dev/v2/search"

    _TBS = {"d": "qdr:d", "w": "qdr:w", "m": "qdr:m", "y": "qdr:y"}

    def build_request(self, query, max_results, timelimit):
        payload = {"query": query, "limit": max_results}
        if timelimit in self._TBS:
            payload["tbs"] = self._TBS[timelimit]
        return (
            self.endpoint,
            {"Content-Type": "application/json",
             "Authorization": f"Bearer {self.api_key()}"},
            payload,
        )

    def parse_response(self, data):
        rows = (data.get("data") or {}).get("web") or []
        out = []
        for item in rows:
            if item.get("title") and item.get("url"):
                out.append(_ns(item["title"], item["url"],
                               item.get("description", "")))
        return out


class _BraveApi(KeyedApiEngine):
    """Brave 官方付费 API（api.search.brave.com/res/v1/web/search）。

    与默认池里那颗免密抓取的 ``brave`` 是**同一套索引的两个入口**：抓取版易被
    反爬挡，API 版按查询计费、稳定但有额度 —— 所以索引家族同为 ``brave``
    （同一家索引答两次不算两条共识，yahoo/bing 同理）。鉴权是自定义头
    ``X-Subscription-Token``，不是 Bearer。``count`` 上限 20，超出的一律钳到 20。
    """
    name = "brave_api"
    env_var = "DHOLE_BRAVE_API_KEY"
    AuthError = BraveApiAuthError
    endpoint = "https://api.search.brave.com/res/v1/web/search"
    http_method = "GET"

    _FRESHNESS = {"d": "pd", "w": "pw", "m": "pm", "y": "py"}

    def build_request(self, query, max_results, timelimit):
        payload = {"q": query, "count": min(max_results, 20)}
        if timelimit in self._FRESHNESS:
            payload["freshness"] = self._FRESHNESS[timelimit]
        return (
            self.endpoint,
            {"Accept": "application/json",
             "X-Subscription-Token": self.api_key()},
            payload,
        )

    def parse_response(self, data):
        rows = (data.get("web") or {}).get("results") or []
        out = []
        for item in rows:
            if item.get("title") and item.get("url"):
                out.append(_ns(item["title"], item["url"],
                               item.get("description", "")))
        return out


# keyed 引擎注册表：engines= 按名字选择；不设 key 时选中会得到可诊断的报错
KEYED_ENGINES: dict[str, type[KeyedApiEngine]] = {
    _Tavily.name: _Tavily,
    _Bocha.name: _Bocha,
    _Exa.name: _Exa,
    _DeepSeek.name: _DeepSeek,
    _Firecrawl.name: _Firecrawl,
    _BraveApi.name: _BraveApi,
}


# ─── 熔断器（按后端的封禁冷却）───────────────────────────────────────────────
# 对我们 CAPTCHA / 403 / 限流的后端，会被跳过一个冷却期，免得继续向一台正在封
# 我们 IP 的主机发请求（有升级成更长 IP 封禁的风险，还浪费法定数名额去等一个不会
# 产出的后端）。空结果与超时是暂时性的，**不**触发熔断。下次成功即清除。
_CIRCUIT_COOLDOWN = 60.0  # 秒
# 校验页（HTTP 200 的反爬壳）不是瞬时限流：上游已经把这个 IP 记进观察名单。本机实测
# so.com 在**完全零请求**静默 20 分钟后仍回同一张「访问异常页面」—— 此前那版"每 3 分钟
# 探一次，可能是自己把惩罚续了"的混淆已被这个对照排除。所以 60 秒后再敲一次对这种窗口
# 只等于替它续期，正是 MetaBlockedException 文档说要避免的 hammering。校验页因此按
# **连续次数**递增：前两次仍按 60 秒（单次误判不该把国内主力引擎关掉十分钟），第三次起
# 改用与被墙同档的 10 分钟。10 分钟是**故意短于实测窗口**的：so360 撞墙只花 0.4 秒，
# 而且现在会如实进 engine_blocked；反过来把 baidu 这类主力误关三十分钟的代价大得多。
_CHALLENGE_THRESHOLD = 3
_CHALLENGE_COOLDOWN = 600.0
_CHALLENGE_COUNTS: dict[str, int] = {}
_BACKEND_HEALTH: dict[str, float] = {}  # 名字 -> 封禁到期时间戳


def _circuit_state_file() -> str:
    """惰性取路径，与 `_engine_stats_file()` 同理。

    原先是 import 期的字符串常量，于是 `DHOLE_HOME` 对这个文件半失效——换 home 后
    熔断状态仍写回旧的 `~/.dhole`。惰性求值也让测试能把状态指到临时目录，而不是让
    跑一次套件就改掉用户真实的引擎冷却状态。
    """
    return str(paths.file("circuit_breaker.json"))


def _load_circuit_state() -> None:
    """从磁盘读回持久化的熔断器状态（跨重启存活）。
    过期项丢弃。模块加载时调用一次。"""
    global _BACKEND_HEALTH
    path = _circuit_state_file()
    try:
        if os.path.exists(path):
            import json
            with open(path, "r") as f:
                data = json.load(f)
            now_ts = time()
            # 只恢复尚未过期的项
            _BACKEND_HEALTH = {k: v for k, v in data.items() if v > now_ts}
            if len(_BACKEND_HEALTH) != len(data):
                # 磁盘上留着已经过期的熔断记录，内存里却已经不认它：用户（和任何
                # 拿着这两个文件做诊断的人）读到的是"brave 在冷却"，而实际早就放行
                # 了。读到过期项就顺手回写，让文件与内存说的同一件事。
                _save_circuit_state()
    except Exception:
        pass


def _save_circuit_state() -> None:
    """把熔断器状态持久化到磁盘。尽力而为，从不抛异常。
    用原子写（临时文件 + os.replace），防止并发进程写坏文件。"""
    try:
        path = _circuit_state_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        import json
        import tempfile
        now_ts = time()
        # 只保存未过期的项
        active = {k: v for k, v in _BACKEND_HEALTH.items() if v > now_ts}
        # 原子写：先写临时文件再改名（防止写一半）
        fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(path), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(active, f)
            os.replace(tmp_path, path)
            # 收紧放在 replace 之后：mkstemp 的 0600 会被 rename 带过来，但目录
            # 可能新建、且以后再写时目标已存在——在这里补一次才覆盖两条路径。
            paths.harden_dir(os.path.dirname(path))
            paths.harden_file(path)
        except Exception:
            # 失败时清掉临时文件
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except Exception:
        pass


# 模块导入时加载持久化状态（每进程一次）
_load_circuit_state()


def _is_circuit_open(name: str) -> bool:
    return _BACKEND_HEALTH.get(name, 0.0) > time()


# G6: the backoff tiers. 17.0 写死：冷却时长是能算清的数，``DHOLE_ENGINE_*``
# 三个旋钮已注销（运行时静默忽略）。表仍在函数里（在 _CONN_FAIL_COOLDOWN
# 定义之前），但只剩各档的秒数。
def _cooldown_tiers() -> dict:
    """三档退避（17.0：只剩固定秒数）。"""
    return {
        "block": _CIRCUIT_COOLDOWN,
        "challenge": _CHALLENGE_COOLDOWN,
        "connection": _CONN_FAIL_COOLDOWN,
    }


def _cooldown_seconds(kind: str) -> tuple[float, str]:
    """(effective seconds, note) for one backoff tier. 17.0 写死为各档默认值：
    冷却时长是能算清的数（D-15 的教训刚好相反地证明了它不该随环境浮动）。"""
    return _cooldown_tiers()[kind], ""


def cooldown_settings() -> dict:
    """当前生效的退避档位，供报告使用（G6）。"""
    out: dict = {}
    for kind, seconds in _cooldown_tiers().items():
        out[kind] = {"seconds": seconds}
    return out


def _record_block(name: str, *, challenge: bool = False) -> None:
    """被拒后把这家冷却掉，时长按拒绝的**形态**分档（见 `_CHALLENGE_THRESHOLD`）。"""
    seconds, note = _cooldown_seconds("block")
    if note:
        logger.warning(note)
    if challenge:
        n = _CHALLENGE_COUNTS[name] = _CHALLENGE_COUNTS.get(name, 0) + 1
        if n >= _CHALLENGE_THRESHOLD:
            seconds, note = _cooldown_seconds("challenge")
            if note:
                logger.warning(note)
    _BACKEND_HEALTH[name] = time() + seconds
    _save_circuit_state()


# 连接失败（DNS/拒连/超时）与被反爬封不同：前者往往是持续性的（引擎被墙），
# 却不会触发上面的熔断。连续失败达阈值后冷却更久，避免每轮搜索陪跑。
_CONN_FAIL_THRESHOLD = 3
_CONN_FAIL_COOLDOWN = 600.0
_CONN_FAIL_COUNTS: dict[str, int] = {}


def _record_conn_failure(name: str) -> None:
    _CONN_FAIL_COUNTS[name] = _CONN_FAIL_COUNTS.get(name, 0) + 1
    if _CONN_FAIL_COUNTS[name] >= _CONN_FAIL_THRESHOLD:
        seconds, note = _cooldown_seconds("connection")
        if note:
            logger.warning(note)
        _BACKEND_HEALTH[name] = time() + seconds
        _CONN_FAIL_COUNTS.pop(name, None)
        _save_circuit_state()


def _record_success(name: str) -> None:
    _CONN_FAIL_COUNTS.pop(name, None)
    _CHALLENGE_COUNTS.pop(name, None)
    if name in _BACKEND_HEALTH:
        _BACKEND_HEALTH.pop(name, None)
        _save_circuit_state()


def sweep_expired_cooldowns() -> int:
    """清掉到点的冷却；返回释放了几家。

    冷却一到点就已经失效（`_is_circuit_open` 对着时钟比），但记录还留在内存里
    —— 而且在下次无关的写入之前还留在盘上 —— 于是状态文件在早就放行很久之后
    仍写着"brave 被封"。每轮搜索清扫一次，文件才是池子健康状况的忠实画像，而
    那是用户唯一能看的地方。
    """
    now_ts = time()
    expired = [k for k, v in _BACKEND_HEALTH.items() if v <= now_ts]
    if expired:
        for k in expired:
            _BACKEND_HEALTH.pop(k, None)
        _save_circuit_state()
    return len(expired)


def cooldowns() -> dict[str, float]:
    """当前生效的冷却，{引擎: 剩余秒数}。"""
    now_ts = time()
    return {k: round(v - now_ts, 1) for k, v in _BACKEND_HEALTH.items() if v > now_ts}


# ─── 主动池心跳（G6）─────────────────────────────────────────────────────────
# 上面的熔断器是被动的：引擎只有在冷却窗口结束后又恰好有人搜索时才回来，而一个
# 被误冷却的主力引擎可以在一个一小时前就不再封我们的网络里继续缺席整整十分钟。
# `dhole engines probe`（15.2）能回答这个问题，但得有人记得去问。这里就是同一个
# 探针，由服务器自己跑，而且 —— 要紧的是这一点 —— 它**不**施加新的惩罚：探针失败
# 会恢复**原来的**到期时间，所以检查本身无法把它延长。正是这条规则让它没有变成
# 实测到的 so.com 窗口所显示的"每 3 分钟敲一次，可能是自己把惩罚续了"那种形态；
# 巡检间隔存在的理由也是同一个。
_HEARTBEAT_DEFAULT_S = 300.0
_HEARTBEAT_PROBE_TIMEOUT_S = 12.0
_heartbeat_running = False
_heartbeat_last_at = 0.0
_heartbeat_log: list[dict] = []


def _heartbeat_interval() -> tuple[float, str]:
    """Seconds between proactive probes. 17.0 写死为默认值：巡检节奏是
    能算清的数，``DHOLE_ENGINE_HEARTBEAT`` 已注销（运行时静默忽略）。"""
    return _HEARTBEAT_DEFAULT_S, ""


def heartbeat_settings() -> dict:
    """心跳当前被配置成做什么（给 `engines list` 看）。"""
    seconds, note = _heartbeat_interval()
    out = {"interval_s": seconds, "enabled": seconds > 0,
           "running": _heartbeat_running, "last_result": _heartbeat_log[-1] if _heartbeat_log else {}}
    if note:
        out["note"] = note
    return out


async def engine_heartbeat(query: str = "python asyncio tutorial") -> dict:
    """把每个冷却中的引擎直接、各问一次：现在能答吗。

    返回 ``{"checked": [...], "recovered": [...], "still_cooling": [...]}``。
    永不抛：它无人值守地挂在搜索后面跑，坏掉的诊断不能变成一次失败的搜索。
    """
    global _heartbeat_running
    _heartbeat_running = True
    result: dict = {"checked": [], "recovered": [], "still_cooling": []}
    try:
        cooling = {name: until for name, until in list(_BACKEND_HEALTH.items())
                   if until > time()}
        for name, until in cooling.items():
            cls = _TEXT_ENGINES.get(name)
            if cls is None or getattr(cls, "disabled", False):
                result["still_cooling"].append(name)
                continue
            # 只在**本次**探针期间抬起熔断。引擎是直接调用的，不经过 metasearch，
            # 所以没有法定数、没有抢占、也没有第二个引擎参与：答案只关于这一家。
            _BACKEND_HEALTH.pop(name, None)
            ok = False
            try:
                got = await asyncio.wait_for(
                    asyncio.to_thread(_probe_one_engine, cls, query),
                    timeout=_HEARTBEAT_PROBE_TIMEOUT_S)
                ok = bool(got)
            except Exception:
                ok = False
            result["checked"].append(name)
            if ok:
                _record_success(name)  # 清掉冷却：池子现在就把它收回来
                result["recovered"].append(name)
            else:
                # 恢复探针**之前**生效的那个到期时间。不是新算一个：一次发现墙还
                # 立着的检查，不该把它背后的等待时间变长。
                _BACKEND_HEALTH[name] = until
                result["still_cooling"].append(name)
        if result["checked"]:
            _heartbeat_log.append({"at": time(), **result})
            del _heartbeat_log[:-8]
            _save_circuit_state()
    except Exception as e:  # 跑不起来的心跳必须安静，但要留日志
        logger.debug("engine heartbeat skipped: %s", str(e)[:160])
        result["error"] = str(e)[:120]
    finally:
        _heartbeat_running = False
    return result


def _probe_one_engine(cls, query: str) -> list:
    """走引擎自己的 fetch+parse 路径发一次真实查询，构造方式与 metasearch 相同
    （同一个代理、同一个期限），这样心跳探的就是池子实际会用到的那一台。"""
    eng = cls(proxy=_get_search_proxy(), timeout=int(_SEARCH_DEADLINE), verify=True)
    return eng.search(query) or []


def schedule_engine_heartbeat() -> None:
    """到点就发心跳（在一轮搜索之后调用，从不提前）。

    三个条件，都很便宜：间隔已到、没有探针正在跑、至少一家引擎确实在冷却。池子
    健康时这只花一次时钟比较。
    """
    global _heartbeat_last_at
    interval, note = _heartbeat_interval()
    if note:
        logger.warning(note)
    if interval <= 0 or _heartbeat_running:
        return
    if not any(until > time() for until in _BACKEND_HEALTH.values()):
        return
    if time() - _heartbeat_last_at < interval:
        return
    _heartbeat_last_at = time()
    try:
        import asyncio
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # 没有事件循环（CLI / 同步调用方）：没地方挂
    task = loop.create_task(engine_heartbeat())
    # 留住引用：裸 task 可能在飞行途中被垃圾回收，而那种失败模式是心跳静默地
    # 再也不跑。
    _heartbeat_tasks.add(task)
    task.add_done_callback(_heartbeat_tasks.discard)


_heartbeat_tasks: set = set()


def engine_state_reset() -> dict:
    """忘掉池子记得的一切：冷却、失败计数、产出。

    两个状态文件决定哪些引擎会被问到。当一家引擎是在一个后来变了的网络上被冷却
    （VPN 打开、主机解封），这些记录仍会影响后面好几轮搜索，而用户唯一的杠杆是
    自己找到并删掉这些文件 —— 这也正是"dhole 无视我的 VPN"那次误诊的起点。这里
    就是那根杠杆，做成一次调用。别的什么都不动：内容缓存归 `sys_state` 的
    `cache` 那一类。
    """
    released = {k: round(v - time(), 1) for k, v in _BACKEND_HEALTH.items()}
    forgotten = len(_ENGINE_YIELD)
    _BACKEND_HEALTH.clear()
    _CONN_FAIL_COUNTS.clear()
    _ENGINE_YIELD.clear()
    global _engine_stats_last_save
    _engine_stats_last_save = 0.0
    _save_circuit_state()
    _save_engine_stats()
    return {"released_cooldowns": {k: v for k, v in released.items() if v > 0},
            "engines_forgotten": forgotten,
            "cleared": ["circuit_breaker.json", "engine_stats.json"]}


def engine_state_snapshot() -> dict:
    """只读的池子健康：每引擎的判定 + 当前冷却。

    `cooldown_seconds_left` 现在在**每一行**上：本进程持有冷却记录时是实时剩余
    秒数，没有记录时是 0。从前缺这个键是缺陷（R01-032，并入 R01-016）—— 一行
    写着 `status: circuit_open` 却没有这个键，意味着两种不同的事（"在冷却，不知
    还要多久"和"上一轮被拒，此刻没冷却"），而那位连问四次的调用方据此认定这个数
    不存在，转而去自己写熔断日志了。

    0 的作用域是**本进程**：熔断表在内存里（导入时持久化，由最后一个跑搜索的人
    写入），所以"这里没有记录"是这个字段能诚实声称的东西。
    """
    health = engine_health()
    cool = cooldowns()
    for name in cool:
        # 冷却中有记录、却还没有产出行的一家（例如别处写入的落盘表被本进程读回）：
        # 从前 setdefault 会给它一行，这一步不许在重写这一格时顺手丢掉。
        health.setdefault(name, {})
    for name, row in health.items():
        row["cooldown_seconds_left"] = round(cool.get(name, 0.0), 1)
    return health


# ─── 引擎产出统计（静默降级唯一能看见的地方）────────────────────────────────
# 熔断器记的是"引擎拒不拒绝我们"，它记不到另一种失败：引擎 200 好好答了，我们的
# xpath 却解析出 0 条。那种失败在响应里完全隐形（empty 既不进 engines_used 也不
# 进 engine_blocked），表现是"结果变少"而不是报错 —— 免密搜索最贵的那个失败模式。
# 这里记的就是那一格。开销：每引擎每轮一次 dict 更新 + 至多 60s 一次的原子落盘。
# 判据只用当轮的结构量，不依赖历史基线：新装机器第一次搜索就能判"容器在、条目空"。
_YIELD_EWMA = 0.3            # 新样本权重
# 判据与它的三个阈值住在 engine_yield.py（stdlib-only 叶子模块）：`dhole -v` 的诊断行也
# 要用同一把尺，而它不许 import 这一层（会拖 primp/lxml/httpx/fake_useragent）。两处各抄
# 一遍正是这一族判据最坏的历史故障 —— 同一个引擎在两个读处给出两个结论。
_ENGINE_YIELD: dict[str, dict] = {}
_ENGINE_STATS_SAVE_INTERVAL = 60.0
_engine_stats_last_save = 0.0


def _engine_stats_file() -> str:
    """惰性取路径（不是 import 期常量）：让测试能把状态文件指到临时目录。"""
    return str(paths.file("engine_stats.json"))


def _load_engine_stats() -> None:
    """进程启动时读回历史产出统计。尽力而为，永不抛。"""
    global _ENGINE_YIELD
    try:
        path = _engine_stats_file()
        if os.path.exists(path):
            import json
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                _ENGINE_YIELD = {k: v for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        _ENGINE_YIELD = {}


def _save_engine_stats() -> None:
    """原子落盘，防抖到至多 60s 一次。尽力而为，永不抛。"""
    global _engine_stats_last_save
    now = time()
    if now - _engine_stats_last_save < _ENGINE_STATS_SAVE_INTERVAL:
        return
    _engine_stats_last_save = now
    try:
        import json
        path = _engine_stats_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_ENGINE_YIELD, f)
        os.replace(tmp, path)
        paths.harden_dir(os.path.dirname(path))
        paths.harden_file(path)
    except Exception:
        pass


def _classify_yield(stats: dict, status: str, nodes: int, usable: int,
                    *, now: Optional[float] = None) -> str:
    """这一轮该引擎的健康判定 —— 判据在 engine_yield.classify_yield（唯一的作者）。

    这颗名字留着是这条链的读法。`now` 传了才判新鲜度（超龄的行 = `stale`），这里传，
    因为 engine_health 交的是"池子此刻怎么看它"。阶梯本体不许在这里重写，理由见
    engine_yield 的模块 docstring 与 tests/test_one_engines_yield_verdict_has_one_author.py。
    """
    return engine_yield.classify_yield(stats, status, nodes, usable, now=now)


def _record_engine_outcomes(status: dict[str, str],
                            instances: dict[str, "BaseSearchEngine"]) -> None:
    """每轮 metasearch 结束调用一次。永不抛 —— 观测面不能把搜索弄坏。"""
    try:
        now = time()
        for name, st in status.items():
            eng = instances.get(name)
            nodes, usable = getattr(eng, "last_extract", (-1, -1)) if eng else (-1, -1)
            kept = getattr(eng, "kept_after_filter", -1) if eng else -1
            st_dict = _ENGINE_YIELD.setdefault(name, {})
            n = int(st_dict.get("n", 0)) + 1
            observed = st != "preempted" and nodes >= 0
            mean = float(st_dict.get("mean", 0.0))
            if observed:
                mean = float(usable) if n <= 1 else mean * (1 - _YIELD_EWMA) + usable * _YIELD_EWMA
            drift = int(st_dict.get("drift", 0))
            if nodes > 0 and usable == 0:
                drift += 1
            elif nodes >= 0:
                drift = 0
            zero = int(st_dict.get("zero", 0))
            if observed and usable == 0 and nodes == 0:
                zero += 1
            elif usable > 0:
                zero = 0
            _ENGINE_YIELD[name] = {
                "n": n,
                "mean": round(mean, 2),
                "last": int(usable),
                "last_nodes": int(nodes),
                "last_kept": int(kept),
                "zero": int(zero),
                "drift": int(drift),
                "status": st,
                "http": getattr(eng, "http_status", None) if eng else None,
                "ts": now,
            }
        _save_engine_stats()
    except Exception:
        logger.debug("engine yield stats recording failed", exc_info=True)


def engine_health() -> dict[str, dict]:
    """每引擎产出的只读快照（含 verdict），给诊断命令用。

    verdict 只描述**最近一轮**：超过 `engine_yield.YIELD_TTL_S` 没再出现的引擎标成
    "stale"，否则上一次运行的结论会被当成当前状态 —— 那正是这次要修的毛病。那一步判据
    也在叶模块里（`now=` 传进去才判），这里不再自己比一次时间。
    """
    now = time()
    out: dict[str, dict] = {}
    for name, stats in _ENGINE_YIELD.items():
        row = dict(stats)
        status = str(row.get("status", ""))
        nodes = int(row.get("last_nodes", -1))
        usable = int(row.get("last", -1))
        row["verdict"] = _classify_yield(row, status, nodes, usable, now=now)
        out[name] = row
    return out


_load_engine_stats()


def _configured_default_backends() -> list[str]:
    """DHOLE_DEFAULT_ENGINES 覆盖默认池（逗号分隔）。国内用户可收敛到直连可达的
    引擎，被墙的三个不再每轮陪跑。未知名忽略并告警；全无效则回落上游默认。"""
    raw = (env_registry.value_of("DHOLE_DEFAULT_ENGINES") or "")
    if not raw.strip():
        return list(_DEFAULT_BACKENDS)
    out: list[str] = []
    for name in (x.strip().lower() for x in raw.split(",")):
        b = _DHOLE_TO_BACKEND.get(name)
        if b and b not in out:
            out.append(b)
        elif not b:
            logger.warning("DHOLE_DEFAULT_ENGINES: unknown engine %r skipped", name)
    return out or list(_DEFAULT_BACKENDS)


def _resolve_backends(engines: Optional[list[str]]) -> list[str]:
    """把 dhole 引擎名（或 'auto'/None）映射成 ddgs 后端名，去掉重复与未知项。"""
    if not engines:
        return _configured_default_backends()
    out: list[str] = []
    for e in engines:
        b = _DHOLE_TO_BACKEND.get(e)
        if b and b not in out:
            out.append(b)
    return out or _configured_default_backends()


def _prefer_lane(order: list, lane: frozenset) -> list:
    """合并序的稳定分区（2.4 · 语言车道）：preferred 车道的行在前，一条不丢。

    不是重排：各车道内部保持 first-seen 序（谁的答案先落地谁在前），被换位置的只有
    车道之间的先后。行的归属按 `backends` 集合与车道的交集判——一条被 baidu 与
    bing_global 同时确认的 URL 属于两侧的共识，任何一侧把它选进 preferred 都成立。
    空车道 / 单侧独占时逐字原样交回。
    """
    if not lane or len(order) < 2:
        return order
    head = [e for e in order if lane.intersection(e.get("backends") or ())]
    tail = [e for e in order if not lane.intersection(e.get("backends") or ())]
    if not head or not tail:
        return order
    return head + tail


def _prefer_reliable(order: list, lane: frozenset) -> list:
    """合并序的第二道稳定分区（可信度分层）：车道段内部，主力引擎确认过的行在前。

    判级表住在 search_engines._PRIORITY_TIERS（唯一的作者，池序也读它）：直连稳定
    的主力（baidu/bing）层 0，按 IP 限流/常被反爬挡的居中，小众浅索引垫底。行的
    层级取 `backends` 里**最好**的那一家——一条被 baidu 与 marginalia 同时确认的
    URL 按主力算（共识行的名头跟着最强的证据走）。与 `_prefer_lane` 同一把尺：
    层内保持 first-seen 序，一条不丢。**只段内分层**：preferred 车道的行永远在
    另一侧之前（答非所问语种的伤害比引擎名头大），可信度买不到跨车道的位次。
    这不是相关性宣称：relevance 由重排器判，这张表只决定平局时谁先被看见。
    """
    from dhole_mcp.search_engines import priority_tier  # 惰性：与 language_lane 同一条理由

    def _tiered(rows: list) -> list:
        if len(rows) < 2:
            return rows
        return [e for _t, _i, e in sorted(
            ((min(priority_tier(b) for b in e["backends"]), i, e)
             for i, e in enumerate(rows)), key=lambda t: (t[0], t[1]))]

    if not lane:
        return _tiered(order)
    head = [e for e in order if lane.intersection(e.get("backends") or ())]
    tail = [e for e in order if not lane.intersection(e.get("backends") or ())]
    if not head or not tail:
        return _tiered(order)
    return _tiered(head) + _tiered(tail)


_SEARCH_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "ref_src", "source", "_ga", "mc_cid", "mc_eid",
    "igshid", "si",  # YouTube / 社交平台的分享跟踪参数
}

_GITHUB_REPO_HOSTS = {"github.com", "www.github.com"}

_GITHUB_RESERVED_ROUTES = frozenset({
    "about",
    "apps",
    "codespaces",
    "collections",
    "dashboard",
    "explore",
    "features",
    "issues",
    "login",
    "marketplace",
    "new",
    "notifications",
    "orgs",
    "pricing",
    "pulls",
    "search",
    "security",
    "settings",
    "sponsors",
    "topics",
    "trending",
})


def _normalize_url(url: str) -> str:
    """归一 URL，供跨后端去重。

    剥掉跟踪/分析类 query 参数（utm_*、fbclid、gclid、ref 等），但**保留**真正的
    query 参数：只差一个跟踪标记的两条结果会并成一条，而真正不同的页面
    （例如 ?page=2 与 ?page=3）仍然区分。utm_ 家族按**前缀**匹配，不是固定名单：
    引擎和分享面板会随时造出 utm_id / utm_reader / utm_bid，而名单写好的第二天就
    过时了。同时把 scheme+host 转小写，并去掉非根路径的尾斜杠。
    GitHub 的仓库所有者与仓库名大小写不敏感，所以 github.com 和 www.github.com
    的前两段非空路径会转小写；后面的路径段保持不变，因为分支名和文件路径可能
    大小写敏感。GitHub 的系统路由（topics、settings、explore 等）不参与路径折叠，
    因为它们不是仓库，大小写可能承载含义。带凭据的 URL 跳过 GitHub 专属的路径
    折叠，好让不透明的 userinfo 永远不会成为新引入的规范化碰撞的一部分。
    www.github.com 保留它自己的主机名，而不是再加一套主机别名归一策略。
    """
    if not url:
        return ""
    u = url.strip()
    if u.startswith("//"):
        u = "https:" + u
    p = urlparse(u)
    scheme = (p.scheme or "https").lower()
    host = p.netloc.lower()
    path = p.path.rstrip("/") if len(p.path) > 1 else p.path
    if p.hostname in _GITHUB_REPO_HOSTS and p.username is None and p.password is None:
        segments = path.split("/")
        repo_segment_indexes = [i for i, segment in enumerate(segments) if segment][:2]
        if (
            len(repo_segment_indexes) == 2
            and segments[repo_segment_indexes[0]].lower() not in _GITHUB_RESERVED_ROUTES
        ):
            for i in repo_segment_indexes:
                segments[i] = segments[i].lower()
            path = "/".join(segments)
    if p.query:
        kept = []
        for kv in p.query.split("&"):
            if not kv:
                continue
            name = kv.split("=", 1)[0].lower()
            if name in _SEARCH_TRACKING_PARAMS or name.startswith("utm_"):
                continue
            kept.append(kv)
        query = "&".join(kept)
    else:
        query = ""
    return f"{scheme}://{host}{path}{('?' + query) if query else ''}"


# ─── 异步元搜索聚合器 ────────────────────────────────────────────────────────
async def metasearch(
    query: str,
    max_results: int = 10,
    *,
    region: str = "us-en",
    safesearch: str = "moderate",
    timelimit: Optional[str] = None,
    page: int = 1,
    engines: Optional[list[str]] = None,
    query_map: Optional[dict[str, str]] = None,
    round_stats: Optional[dict] = None,
) -> tuple[list[dict[str, str]], dict[str, str]]:
    """并行跑各后端，返回 (结果, 每后端状态)。

    results: {title, href, body, backend} 的列表，按归一 URL 去重，保持首次见到
    的顺序（交出它的后端就是它的 `backend`）。
    status: 每个试过的后端一条 {backend: "ok" | "empty" | "error:..."}。

    `timelimit` 是调用方对所有后端已经在用的**短码**（"d"/"w"/"m"/"y"，来自
    search_engines._FRESHNESS_TO_TIMELIMIT）；各后端自己映射成它家 API 的拼法。
    这里不接受长词 —— 在这一层再立第二套词汇，正是带 key 的引擎当初声称发过
    日期过滤、实际从没发出去的原因。

    凑够法定数就早退：唯一结果够数后我们取消掉队者，所以健康搜索约 1-2 秒返回，
    被限流的搜索也会在期限之内由那些跑通的后端交差。
    """
    backends = _resolve_backends(engines)
    status: dict[str, str] = {}
    # 已释放的冷却每轮从状态文件里清掉，所以用户在 ~/.dhole 读到的与池子实际做的
    # 是同一件事。
    sweep_expired_cooldowns()
    # 每次搜索调用读一次代理（17.0 不再有池与轮转，见 search_proxy.py）。
    _search_proxy = _get_search_proxy()
    # 每个后端一个引擎实例（便宜；primp/httpx 客户端很轻）。
    # 熔断器：跳过最近拒绝过我们的后端（CAPTCHA/403/限流）一个冷却期，免得继续
    # 向一台正在封我们 IP 的主机发请求。它们会以 'circuit_open' 出现在 status 里
    # （-> engine_blocked）。
    instances: dict[str, BaseSearchEngine] = {}
    for b in backends:
        cls = _TEXT_ENGINES.get(b)
        if not cls or cls.disabled:
            continue
        if _is_circuit_open(b):
            status[b] = "circuit_open"
            continue
        try:
            instances[b] = cls(proxy=_search_proxy, timeout=int(_SEARCH_DEADLINE), verify=True)
        except Exception as ex:  # 构造失败（例如缺 primp）-> 跳过
            logger.debug("engine %s init failed: %r", b, ex)
            status[b] = f"init_error:{type(ex).__name__}"

    keyed_selected = [b for b in backends if b in KEYED_ENGINES]
    keyed_ready = [b for b in keyed_selected if KEYED_ENGINES[b].api_key()]
    for b in keyed_selected:
        if not KEYED_ENGINES[b].api_key():
            status[b] = f"no_key:{KEYED_ENGINES[b].env_var}"

    if not instances and not keyed_ready:
        if status and all(v == "circuit_open" for v in status.values()):
            # 每一家都在冷却，不是一个"起不来"的故障：它有期限、原因已经写在
            # status 里，而上面那条注释承诺的就是 `circuit_open -> engine_blocked`。
            # 抛裸异常会把这条承诺整个抹掉：实测 engines=["so360"] 撞上冷却时
            # error 是一串 Python 字典、engine_blocked 是空列表、consensus_basis
            # 说 single_family，而 next_action 叫用户"改写查询" —— 于是没人会去
            # 等那 20 秒，大家去改一个本来没问题的查询。
            return [], status
        if len(keyed_selected) == 1:
            cls = KEYED_ENGINES[keyed_selected[0]]
            # 点名的只有带 key 的引擎，所以代理不是嫌疑人。
            raise MetaSearchException(
                f"Engine '{cls.name}' requires {cls.env_var}, which is not set."
            )
        # 出口写在报错里，但代理 URL 常带 user:pass —— 这条 message 会进 agent 的
        # 视野和 usage 审计的 error 字段，所以只回抹掉凭据的形式（说清走了哪台代理
        # 不需要出卖"用谁的身份"）。
        from dhole_mcp.search_proxy import redact as _redact_proxy
        proxy_note = f" (proxy in use: {_redact_proxy(_search_proxy)})" if _search_proxy else ""
        raise MetaSearchException(
            f"No search engines could start{proxy_note}. "
            f"Engine status: {status}. "
            f"Check DHOLE_SEARCH_PROXY, or pick keyed engines "
            f"({'/'.join(sorted(KEYED_ENGINES))}) "
            f"via engines= and set their API key env."
        )

    seen: dict[str, dict[str, Any]] = {}
    order: list[dict[str, str]] = []
    # 多样性法定数：至少等 `min_engines` 个后端有贡献（不是只等一个后端凑够条数），
    # 这样单个后端的偏好/限流无法主导 —— 跨后端的多样性才是健壮性。软兜底在
    # `_SOFT_DEADLINE` 时只要结果够就返回，哪怕还有后端已死/被 CAPTCHA（不为它们
    # 等满期限）。
    min_engines = min(3, len(instances))
    soft_deadline = _SOFT_DEADLINE
    quorum_results = max_results + 4  # 给神经重排器留一点余量
    # 配额那一步按**实例**数人（`engines_ok`），而共识那一句按**家族**说话：bing /
    # bing_global / duckduckgo 坐的是同一套索引，三家同时"有贡献"仍然只是一个家族把
    # 同一句话说三遍。早退因此要另外知道自己放掉过谁 —— 下面那把家族账就是为此而记的。
    # 家族表住在 search_engines（dhole 那一层薄适配器），这里按需 import 而不写顶层：
    # 那边惰性 import 回本模块（为了不把 primp/lxml 拖进启动与缓存命中路径），写成顶层
    # 就是一对循环 import；而本层是 vendored 的，"家族"这个概念本来也不属于它。
    from dhole_mcp.search_engines import _INDEX_FAMILY, cn_index_engines, language_lane
    families_answered: set[str] = set()   # 本轮真的说了话的独立索引家族
    # 中文查询要等的那几家（非 CJK 交回空集，下面每一处判断因此对英文逐字无效）。
    cn_wait = cn_index_engines(query)
    # 2.4 · 语言车道：查询的字符构成选 preferred 侧（中间地带空集 = 不选边）。
    # 早退的等待按**引擎**答没答话判（`status == "ok"`），不按家族——bing 与
    # bing_global 同属 bing 家族，cn.bing 答了不该让英文查询的等待提前结束。
    lane_pref = language_lane(query)

    def _merge_rows(name: str, res: list[Any]) -> None:
        """一家引擎的答案并进合并表，并把它的下落记进 status。

        两条路共用这一个函数：主循环里完成的，和**已经答完却撞上取消那一步**的。原先
        只有前者有记法，于是同一个事实有两种下场（见 `_stop_laggards` 上面那段）。
        """
        nonlocal engines_ok
        added = 0
        touched = False  # 交出过一条与既有键相同的有效结果（重复）
        for r in res:
            if not _is_usable(r):
                continue
            key = _normalize_url(r.href)
            if not key:
                continue
            if key in seen:
                # 另一个后端已经交过这个 URL -> 记下这次一致
                # （跨后端共识的权威信号）。
                seen[key]["backends"].add(name)
                touched = True
                continue
            entry = {"title": r.title, "href": r.href, "body": getattr(r, "body", "") or "",
                     # 期4.1：日期跟着行走完整条合并链。这里只**搬运**（值已在
                     # `extract_kept` 那一步由唯一的作者读好），合并时先到先得 ——
                     # 同一 URL 后到的那家带来的日期不进账（两条来源各自的数会被读成
                     # 一次测量；`backends` 记的是谁答过，日期记的是交出那一行的版面）。
                     "published": getattr(r, "published", "") or "",
                     "published_precision": getattr(r, "published_precision", "") or "",
                     "published_basis": getattr(r, "published_basis", "") or "",
                     "backend": name, "backends": {name}}
            seen[key] = entry
            order.append(entry)
            added += 1
        if added:
            engines_ok += 1
        # 'ok' = 贡献了一条有效结果（新的，或确认共识的重复）。'empty' = 什么
        # 可用的都没交回。（唯一结果只是重复的后端仍有贡献 —— 它确认了那个 URL。）
        if added or touched:
            status[name] = "ok"
            families_answered.add(_INDEX_FAMILY.get(name, name))
            _record_success(name)  # 后端健康 -> 清掉之前的封禁
        else:
            status[name] = "empty"

    def _take_answer(pt) -> None:
        """收下已经完成的任务；报错按各家失败归桶，与主循环同一份记法。"""
        name = tasks[pt]
        try:
            _, res = pt.result()
        except MetaBlockedException as ex:
            # 后端拒绝了我们（CAPTCHA/403/限流）-> 熔断它一个冷却期，别继续捶。
            _record_block(name, challenge=getattr(ex, "challenge", False))
            status[name] = "blocked"
            return
        except BaseException as ex:  # py3.11+ 里 CancelledError 是 BaseException
            status[name] = f"error:{type(ex).__name__}"
            _record_conn_failure(name)
            return
        _merge_rows(name, res)

    async def _stop_laggards(pend: set, *, reason: str) -> set:
        """先收已经答完的，再取消还在飞的。交回仍在飞的（keyed 那两家不取消，照旧等）。

        `reason` 分开两笔账是必须的：软截止后取消的叫 preempted（不是失败也不是限速），
        硬截止还没答完的叫 timeout（进 engine_blocked）。把后者写成前者就是把一次超时
        说成一次收工 —— 而这两个桶各自主张相反的修法。

        `Task.cancel()` 对一个**已完成**的任务是 no-op，而原来这一步随后 `await pt` 把它的
        返回值丢掉、还给它记上 `preempted` —— 唯一一家在软截止那一瞬答完的权威家族，于
        是同时被丢了答案、又被说成"根本没轮到它说话"。收账与取消因此必须分成两趟走。
        """
        finished: set = set()
        nonlocal round_wait_s, trip_at
        if trip_at and reason == "preempted":
            # 这一段就是本轮为"第二个家族"实际多花的墙钟：从早退条件成立那刻算到收账。
            round_wait_s = max(round_wait_s, time() - trip_at)
            trip_at = 0.0
        for pt in list(pend):
            if pt.done() and not pt.cancelled():
                _take_answer(pt)
                finished.add(pt)
                continue
            if tasks.get(pt) in KEYED_ENGINES:
                # keyed 不 cancel 是为了不浪费 credits，但收账必须**有帽**：慢滴/
                # 挂起的 keyed API 从前在下面的第二段循环里被无超时 await，"整个
                # 搜索的硬期限"被拖成无限。宽限到它自己那层 socket 帽的墙钟
                # （start + max(16, timeout_floor)），到点弃收并取消等待 —— 任务
                # 里 to_thread 的那根线程自己跑完（credits 已花，取消不退钱），
                # 结果丢弃。在帽内落地的 keyed 答案照常收账，一条不丢。
                _eng = keyed_engines.get(tasks.get(pt))
                _cap = max(_SEARCH_DEADLINE,
                           float(getattr(_eng, "timeout_floor", 0.0) or 0.0))
                _left = max(0.05, start + _cap - time())
                _done, _still = await asyncio.wait({pt}, timeout=_left)
                if pt in _done and not pt.cancelled():
                    _take_answer(pt)
                    finished.add(pt)
                else:
                    pt.cancel()
                continue
            pt.cancel()
        for pt in list(pend):
            nm = tasks[pt]
            if nm not in status:
                status[nm] = reason
            try:
                await pt
            except BaseException:
                pass
        # 已经收过账的必须**离开** pending：留在里面下一轮 `asyncio.wait` 会立刻把它当
        # 新完成的任务再收一遍，而 `if not pending: break` 也就永远不成立（这一族空转
        # 原先靠"取消过的任务才算处理完"躲开，收账这条路一开就要自己说清楚）。
        return {pt for pt in pend if not pt.cancelled() and pt not in finished}

    async def _run(name: str, eng: BaseSearchEngine) -> tuple[str, list[Any]]:
        # 对限流敏感的后端错峰（DuckDuckGo 的抓取接口会节流并发请求；延迟 0.3 秒
        # 可以避开 429）。
        if name == "duckduckgo":
            await asyncio.sleep(0.3)
        # 每引擎的查询：给了 query_map（多查询扇出）时，各引擎搜自己分到的变体；
        # 不在表里的引擎回落到主查询（向后兼容）。
        q = (query_map or {}).get(name, query)
        # engine.search 是同步的（阻塞 HTTP）-> 丢到线程里。
        res = await asyncio.to_thread(
            eng.search, q, region, safesearch, timelimit, page,
        )
        return name, (res or [])

    tasks = {asyncio.ensure_future(_run(n, e)): n for n, e in instances.items()}

    # 带 key 的 JSON 引擎：**只在**点名时才跑（每次调用都花真钱），而且只在
    # key 在场时跑。
    keyed_engines: dict[str, Any] = {}
    for b in keyed_ready:
        eng = KEYED_ENGINES[b]()
        keyed_engines[b] = eng

        async def _run_keyed(name=b, _eng=eng):
            q = (query_map or {}).get(name, query)
            res = await asyncio.to_thread(_eng.search_json, q, max_results + 4, timelimit)
            return name, res

        tasks[asyncio.ensure_future(_run_keyed())] = b

    pending = set(tasks)
    deadline = time() + _SEARCH_DEADLINE
    start = time()
    engines_ok = 0
    grace_until = 0.0        # 0 = 这一轮那段短等还没放行；每轮至多放行一次
    trip_at = 0.0            # 早退条件第一次成立的那一刻（短等就是从这一刻算起的）
    round_wait_s = 0.0       # 本轮为"第二个家族"多等的实际秒数（0 = 一次都没等）

    def _cn_still_flying() -> bool:
        """境内索引里还有谁在飞（非 CJK 查询永远是 False）。

        等谁必须说得出名字，所以这里读的是任务表而不是"家族数"：baidu 已经答完、已经
        熔断、或这一轮根本没被点到时，都不构成继续等的理由 —— 只有"还没回话的那一家
        恰好是中文长尾唯一有的那几家"才是。
        """
        return any(tasks.get(pt) in cn_wait and not pt.done() for pt in pending)

    def _lane_first_answer_missing() -> bool:
        """preferred 车道一家都还没答话，而它还有人在飞（空车道永远 False）。

        与 `_cn_still_flying` 等的量不同：那边等的是**中文长尾完整性**（几家全到），
        这边只等 preferred 侧的**第一条**——车道随后还靠合并序（`_prefer_lane`）把
        preferred 的行排到前面，所以第一条落地之后的等待买不到东西。判据按引擎的
        status 而不是家族：bing（cn）与 bing_global 同属一个家族，cn.bing 先答不该
        结束英文查询对境外侧的等待。
        """
        if not lane_pref:
            return False
        if any(status.get(b) == "ok" for b in lane_pref):
            return False
        return any(tasks.get(pt) in lane_pref and not pt.done() for pt in pending)

    while pending and time() < deadline:
        timeout = max(0.1, deadline - time())
        if grace_until:
            # 短等到点就要收账，所以这一发的 wait 不能按硬截止睡过去。
            timeout = min(timeout, max(0.05, grace_until - time()))
        try:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED, timeout=timeout)
        except Exception:
            break
        if not done:
            if grace_until and pending and time() < deadline:
                # 短等用到头而第二个家族始终没来：它们是我们决定不等的，照旧记
                # preempted（限速/超时是两回事，各自主张相反的修法）。
                pending = await _stop_laggards(pending, reason="preempted")
            break
        for t in done:
            _take_answer(t)
        # 早退：有足够多的引擎贡献了足够多的结果，或者在软截止后结果已经够多
        # （不为死掉的后端干等）。
        elapsed = time() - start
        # 配额数的是**合并后**的结果条数；全都跑完了也放行（别陪一个已经没人跑的
        # 截止时间干等）。这里曾经额外区分"通用 vs 垂直"两条轨：垂直索引（唯一的
        # 那家实测 0.2-0.9s 回 10 条）一次就够满配额，2s 软截止一到就会把还在跑的
        # 通用引擎全 cancel，本机实测 yandex(3.1s) 就是这样被砍掉的 —— 而那家已经
        # 随 §3-23 一起删了，轨也就没有第二条了。
        enough_results = len(order) >= quorum_results or (bool(order) and not pending)
        if enough_results and (
            engines_ok >= min_engines or elapsed >= soft_deadline
        ):
            if _cn_still_flying() or _lane_first_answer_missing():
                # 配额是境外索引填的，而中文那一半不在它们的库里 —— 这一刻早退买到的
                # 是"快"，交回的是答不出用户问题的那一页（2026-10-11 全真模拟 S3：
                # 混搜里 baidu 被这样砍掉，两轮中文查询全 low，手动 engines=["baidu"]
                # 一次就出公众号与知乎）。等的上限不新开：就是本轮本来就有的硬期限，
                # 到点它自己会被记成 timeout（一次真的没等到），而不是 preempted
                # （一次我们决定不等）。纯英文查询走进来的次数是零 —— cn_wait 是空集。
                # 第二个条件的量不一样（2.4）：preferred 车道一条都没落地时，早退交回
                # 的是**答非所问语种**的那一页；等的是第一条，不是完整性。
                continue
            if (pending and len(families_answered) < _MIN_CONSENSUS_FAMILIES
                    and grace_until == 0.0):
                # 只有一家在说话而还有人在飞：这时候的早退买走的是本轮唯一的佐证机会。
                # 等的对象是"第二个家族"，不是"更多条"——条数一家就能填满配额。
                trip_at = time()
                grace_until = trip_at + _LONE_FAMILY_GRACE
                continue
            pending = await _stop_laggards(pending, reason="preempted")
            if not pending:
                break

    # 取消 + 记下仍在 pending（已超时）的后端。到这一步还在飞的，是硬期限
    # 没答完 —— 与上面那笔"我们决定不等了"是两件事，别并成一个 token。
    if pending:
        pending = await _stop_laggards(pending, reason="timeout")
    if round_stats is not None:
        # 本轮真的为第二个家族多等了多久。抬这段预算的人必须先看见它值多少 ——
        # `evals/pool_baseline.py` 报的是响应里已有的四桶，而四桶说不出"多花的那几秒"。
        round_stats["lone_family_wait_s"] = round(round_wait_s, 3)
        round_stats["lone_family_grace_s"] = _LONE_FAMILY_GRACE
        if lane_pref:
            round_stats["language_lane"] = sorted(lane_pref)

    # 产出统计：一处覆盖全部终态 token（ok/empty/blocked/circuit_open/timeout/
    # error:*/init_error:*/no_key:*/preempted），含那些根本没拿到 instance 就被跳过
    # 的引擎。这是 empty（引擎答了、解析出 0 条）唯一的可见机会。
    _record_engine_outcomes(status, instances)

    # 把 backends 集合冻结成有序列表，交给调用方
    for e in order:
        e["backends"] = sorted(e["backends"])
    # 2.4 · 语言车道的最后一步：preferred 侧的行排到前面（稳定分区，一条不丢）。
    # 放在 backends 冻结之后，分区的归属判断读的就是冻结后的那份名单。
    order = _prefer_lane(order, lane_pref)
    # 可信度分层的第二步（2026-10）：车道段内部再按引擎可信度稳定分层，主力先说话。
    order = _prefer_reliable(order, lane_pref)

    # 17.0: 这里原来有一段"整池连不上就把这个代理冷却掉"的追踪。代理池删掉之后
    # 没有可冷却的对象了，而失败本身已经如实写在 status 里（error:/timeout 每家
    # 一条），再加一层"下次悄悄换成直连"只会让人查不到为什么。
    return order, status
