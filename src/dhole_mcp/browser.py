"""Dhole's own browser sessions using patchright directly.

Replaces scrapling's AsyncStealthySession and AsyncDynamicSession with direct
patchright async API usage. Includes the Cloudflare Turnstile solver ported
from scrapling (it's standard Playwright page manipulation, ~80 lines).

Architecture:
- StealthyBrowser: anti-detect browser with fingerprinting, stealth args,
  Cloudflare solver, resource blocking, page pooling
- Async context manager (async with), .start(), .close(), .fetch()
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from random import randint
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse, urlsplit, urlunsplit

from dhole_mcp.install import browser_binary_command

logger = logging.getLogger("dhole_mcp.browser")

# ─── Browser flags (ported from scrapling's constants.py) ─────────────────────

# These args make the browser faster and less detectable. Source:
# https://peter.sh/experiments/chromium-command-line-switches/
DEFAULT_ARGS: Tuple[str, ...] = (
    "--no-pings",
    "--no-first-run",
    "--disable-infobars",
    "--disable-breakpad",
    "--no-service-autorun",
    "--homepage=about:blank",
    "--password-store=basic",
    "--disable-hang-monitor",
    "--no-default-browser-check",
    "--disable-session-crashed-bubble",
    "--disable-search-engine-choice-screen",
    # Memory: limit renderer processes to 1 (we fetch one page at a time,
    # saves ~100-200MB per avoided process). Not JS-detectable.
    "--renderer-process-limit=1",
    # Memory: cap V8 old space at 512MB (default 4GB). Most pages use <100MB.
    # Prevents unbounded heap growth across many sequential fetches.
    "--js-flags=--max-old-space-size=512",
)

# Args to suppress (scrapling found these enable automation signals)
HARMFUL_ARGS: Tuple[str, ...] = (
    "--enable-automation",
    "--disable-popup-blocking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-extensions",
)

STEALTH_ARGS: Tuple[str, ...] = (
    "--test-type",
    "--lang=en-US",
    "--mute-audio",
    "--disable-sync",
    "--hide-scrollbars",
    "--disable-logging",
    "--start-maximized",
    "--enable-async-dns",
    "--accept-lang=en-US",
    "--use-mock-keychain",
    "--disable-translate",
    "--disable-voice-input",
    "--window-position=0,0",
    "--disable-wake-on-wifi",
    "--ignore-gpu-blocklist",
    "--enable-tcp-fast-open",
    "--enable-web-bluetooth",
    "--disable-cloud-import",
    "--disable-print-preview",
    "--disable-dev-shm-usage",
    "--metrics-recording-only",
    "--disable-crash-reporter",
    "--disable-partial-raster",
    "--disable-gesture-typing",
    "--disable-checker-imaging",
    "--disable-prompt-on-repost",
    "--force-color-profile=srgb",
    "--font-render-hinting=none",
    "--aggressive-cache-discard",
    "--disable-cookie-encryption",
    "--disable-domain-reliability",
    "--disable-threaded-animation",
    "--disable-threaded-scrolling",
    "--enable-simple-cache-backend",
    "--disable-background-networking",
    "--enable-surface-synchronization",
    "--disable-image-animation-resync",
    "--disable-renderer-backgrounding",
    "--disable-ipc-flooding-protection",
    "--prerender-from-omnibox=disabled",
    "--safebrowsing-disable-auto-update",
    "--disable-offer-upload-credit-cards",
    "--disable-background-timer-throttling",
    "--disable-new-content-rendering-timeout",
    "--run-all-compositor-stages-before-draw",
    "--disable-client-side-phishing-detection",
    "--disable-backgrounding-occluded-windows",
    "--disable-layer-tree-host-memory-pressure",
    "--autoplay-policy=user-gesture-required",
    "--disable-offer-store-unmasked-wallet-cards",
    "--disable-blink-features=AutomationControlled",
    "--disable-component-extensions-with-background-pages",
    "--enable-features=NetworkService,NetworkServiceInProcess,TrustTokens,TrustTokensAlwaysAllowIssuance",
    "--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4",
    "--disable-features=AudioServiceOutOfProcess,TranslateUI,BlinkGenPropertyTrees",
)

# Resource types to block when disable_resources=True
DISABLED_RESOURCE_TYPES: Set[str] = {
    "font", "image", "media", "beacon", "object",
    "imageset", "texttrack", "websocket", "csp_report", "stylesheet",
}

# ─── 这一发的网络账 ──────────────────────────────────────────────────────────
#
# 为什么只记"有东西没成"：实测 163 正文页实际发出 55 个请求、失败 0；B 站 40 个、
# 失败 0。把这份账每次都带上等于给绝大多数调用加 3-4KB 噪声，而它唯一有用的时刻
# 是页面**没说清为什么**的时候 —— js_shell 的正文缺口背后常常是一个 403 的数据接口。
#
# 为什么没有 console：同一批实测里 163 的 117 条 console error 与 B 站的 11 条全部是
# `Failed to load resource` —— 那是我们自己 abort 图片/样式表的回声，不是站点的问题；
# 过滤掉之后两页各有 0 条真 error（剩的只是 document.domain 弃用、parser-blocking
# 建议这类 warning）。装一个只会产出回声的监听器是纯税，所以第一版不做。

_NET_FAILED_CAP = 10
_NET_URL_MAX = 160


def _mask_url(url: str, *, max_len: int = _NET_URL_MAX) -> str:
    """URL 的骨架，查询串的**值**打码。

    凭据最常见的泄漏面是查询串（`?token=…`、`?sig=…`），而仓库的硬约束是凭据值
    不进 transcript。键名留着 —— "失败的那条请求上带着哪个参数"才是诊断信息，
    "那个参数是什么"不是。
    """
    try:
        parts = urlsplit(url or "")
    except Exception:
        return (url or "")[:max_len]
    if parts.query:
        kept = []
        for pair in parts.query.split("&"):
            if not pair:
                continue
            key = pair.split("=", 1)[0]
            kept.append(f"{key}=***" if "=" in pair else key)
        parts = parts._replace(query="&".join(kept))
    # fragment 不参与请求，留着只会让人以为它被发出去了
    return urlunsplit(parts._replace(fragment=""))[:max_len]


def _note_net_response(watch: dict, resp: Any) -> None:
    """把一条响应记进这一发的账本（作者只有这里，读者是 `_net_payload`）。

    数的是**拿到响应**的请求：被 route abort 的资源根本没有 response 事件，所以
    "页面实际发出的请求"这个口径不用另设过滤。主文档的 4xx 不记 —— 它已经是
    `result.status`，在信封里重复一次只是噪声。
    """
    try:
        req = getattr(resp, "request", None)
        rt = str(getattr(req, "resource_type", "") or "")
        if rt in DISABLED_RESOURCE_TYPES:
            return
        watch["requests"] = int(watch.get("requests") or 0) + 1
        status = int(getattr(resp, "status", 0) or 0)
        if status >= 400 and rt != "document":
            failed = watch.setdefault("failed", [])
            if len(failed) < _NET_FAILED_CAP:
                failed.append({
                    "status": status,
                    "type": rt,
                    "url": _mask_url(str(getattr(resp, "url", "") or "")),
                })
    except Exception:
        pass


def _net_payload(watch: Optional[dict], ssrf_watch: Optional[list] = None) -> Optional[dict]:
    """账本 → 上线的那一格。**什么都没坏就交 None**（缺席即"这一页很干净"）。

    `blocked_by_guard` 从 `ssrf_watch` 派生，不另设收集器：那件事的作者只有一个
    （route handler 的 SSRF 闸），抄第二份就会有两个各自漂走的答案。
    """
    if not watch:
        return None
    failed = watch.get("failed") or []
    guard: List[str] = []
    for item in (ssrf_watch or []):
        try:
            host = str(item[0] or "")
        except Exception:
            continue
        if host:
            guard.append(host)
    if not failed and not guard:
        return None
    out: Dict[str, Any] = {"requests": int(watch.get("requests") or 0)}
    if failed:
        out["failed"] = list(failed)
    if guard:
        out["blocked_by_guard"] = sorted(set(guard))[:5]
    return out

# Cloudflare challenge iframe URL pattern
__CF_PATTERN = re.compile(
    r"^https?://challenges\.cloudflare\.com/cdn-cgi/challenge-platform/.*"
)


# ─── Proxy helper ────────────────────────────────────────────────────────────

def _construct_proxy_dict(proxy: str | Dict[str, str]) -> Dict[str, str]:
    """Convert a proxy string to a Playwright proxy dict."""
    if isinstance(proxy, dict):
        return proxy
    parsed = urlparse(proxy)
    if parsed.scheme not in ("http", "https", "socks4", "socks5"):
        raise ValueError(f"Invalid proxy scheme: {parsed.scheme}")
    result: Dict[str, str] = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        result["username"] = parsed.username
    if parsed.password:
        result["password"] = parsed.password
    return result


def _is_domain_blocked(hostname: str, blocked_domains: frozenset) -> bool:
    """Check if a hostname matches any blocked domain (including subdomains)."""
    if not hostname:
        return False
    for domain in blocked_domains:
        if hostname == domain or hostname.endswith(f".{domain}"):
            return True
    return False


def _landed_host_outside_fence(landed: str,
                               allowed: Optional[frozenset]) -> str:
    """attach 会话落在白名单外时交出那个主机名，否则交回 ""。

    这是围栏的**结果边界**，站在 `_DocumentFence` 那道请求边界之后：主文档那一跳通常
    已经在那里被终止，这一层管的是没装上那只闸的通道（以及 `page.route` 一直看不见的
    形状 —— D-31 实测：靶站按 `Host` 头记的账里，route 从没看见过主文档重定向的下一跳）。
    "把另一个站点的正文当成你点名的那一页交回来"这一步无论如何不该发生 —— 你带着已登录
    的 profile 读到的却是别的站的答案。
    """
    if allowed is None:
        return ""
    if not (landed or "").lower().startswith(("http://", "https://")):
        return ""
    host = (urlparse(landed).hostname or "").lower()
    return "" if (not host or host in allowed) else host


def _landed_url_refused(landed: str, entry_url: str) -> bool:
    """落地 URL 本身指向内网、且不是入口站点 → 这一页的正文不能交出去。"""
    if not (landed or "").lower().startswith(("http://", "https://")):
        return False
    if _is_entry_target(landed, entry_url):
        return False
    from dhole_mcp.security import url_targets_internal
    return url_targets_internal(landed)


def _fence_blocked_error(hosts: List[str], allowed: Optional[frozenset],
                         left_browser: bool) -> "CDPHostNotAllowedError":
    """名册外那一跳的拒绝 —— 请求边界与结果边界共用这一句（唯一的作者）。

    `left_browser` 说的是这一发出没出去：请求边界装上时它是 False（那一跳在发出之前
    就被终止，靶站一个字节都没收到）；只剩结果边界时它是 True（Fetch 监听器没装上的
    那些通道 —— 例如同一条会话被别的通道复用、或 CDP 那一侧报错 —— 承诺必须跟着
    实际发生的走，不跟着愿望走）。
    """
    roster = sorted(allowed or ())
    named = ", ".join(hosts)
    if left_browser:
        what = (f"the page took the attached browser to {named}, outside the "
                f"authorized set {roster} - so its content is not the logged-in "
                "view you asked for and was not returned. The request itself "
                "already left the browser (D-31); ")
    else:
        what = (f"the page tried to take the attached browser to {named}, outside "
                f"the authorized set {roster} - the request was ended before it "
                "left the browser, so nothing was read from it. ")
    return CDPHostNotAllowedError(
        f"cdp_host_not_allowed: {what}"
        f"to read that site on purpose, name it as the url with confirm=true, "
        "which authorizes it for itself.")


class _DocumentFence:
    """围栏的请求边界：把跑出名册的**主文档**请求终止在发出之前（D-31b）。

    为什么需要它：`page.route` 那一层看不见主文档重定向的下一跳（D-31 实测，靶站按
    `Host` 头记的账），所以一条 302 就能带着已登录的 profile 走出名册，只在落地之后
    被结果边界拒绝 —— 内容没交出去，请求却已经发出去了。CDP `Fetch` 域看得见那一跳。

    形状是 2026-10-09 在**真 attach**（`connect_over_cdp` + 临时 profile 副本）上量出来的，
    三条都不容调整（`.scratch/liveverify/attach_fetch_bypass.py`、`attach_fetch_frames.py`）：

      · patterns 必须限死 `resourceType: "Document"`。管所有类型时 route 从 2 笔掉到 1 笔 ——
        先应答的那一侧会把子资源直接放出去，route 里的内网复检与跨源凭据头剥离就没机会了，
        那是拆围栏而不是加围栏。限到 Document 之后 route 两笔都还看得见。
      · 每一笔 paused 都必须应答：故意不回的那一笔把导航吊到 20 秒超时（`Fetch.doNothing`
        在这台 Chrome 上直接报错，也不是选项）。所以应答失败只许吞掉，不许冒出去。
      · 名册内的主文档仍然由 route 说了算：对照实验里这只监听器已经 continue，route 的
        abort 照样把那笔终止（net::ERR_FAILED，靶站一个字节都没收到）。所以这里只管
        "出不出名册"这一件事，其余判据留在 route 那一张表里，不在第二处复述。

    主文档与 iframe 用 `Page.getFrameTree` 的根 frame id 分开（实测：主文档跳转的下一跳
    带着根 id，iframe 的文档带着另一个 id）。混为一谈会把"嵌了一张名册外 iframe"的正常
    站点整条拒掉 —— 那是回归，那种形状今天只是 iframe 被拦、页面照返。取不到根 id 时
    按 fail-closed 处理（每一笔 Document 都当主文档），因为结果边界还在下一行等着，
    而漏报一笔跑得掉的跳转比多拒一个 iframe 严重得多。
    """

    def __init__(self, page: Any, allowed_hosts: frozenset, watch: List[tuple]):
        self._page = page
        self._allowed = allowed_hosts
        self._watch = watch
        self._session: Any = None
        self._root_frame = ""
        self._escaped = asyncio.Event()

    async def wait_for_escape(self) -> None:
        """等到这只闸拦下一笔主文档导航（调用方拿它跟稳定等待赛跑）。"""
        await self._escaped.wait()

    async def install(self) -> None:
        """打开这只监听器。失败就抛 —— 调用方退回 route + 结果边界那条线。"""
        self._session = await self._page.context.new_cdp_session(self._page)
        try:
            tree = await self._session.send("Page.getFrameTree")
            self._root_frame = ((tree or {}).get("frameTree") or {}).get(
                "frame", {}).get("id") or ""
        except Exception:
            self._root_frame = ""
        self._session.on("Fetch.requestPaused", self._on_paused)
        await self._session.send("Fetch.enable", {"patterns": [
            {"urlPattern": "http://*", "requestStage": "Request",
             "resourceType": "Document"},
            {"urlPattern": "https://*", "requestStage": "Request",
             "resourceType": "Document"}]})

    async def remove(self) -> None:
        """摘掉监听器。页面已经关了就算摘成 —— 那意味着这一趟已经没有请求会被它拦。"""
        if self._session is None:
            return
        for method in ("Fetch.disable", "detach"):
            try:
                await self._session.send(method)
            except Exception:
                break
        self._session = None

    def _on_paused(self, params: dict) -> None:
        asyncio.ensure_future(self._resolve(params))

    async def _resolve(self, params: dict) -> None:
        if self._session is None:
            # 摘掉的闸既不应答，也不该替这次导航作证。少了这一行，晚到的那笔事件会
            # 只把主机记进围栏账上而没人把它掐掉 —— 而 `fetch` 读这本账时说的是
            # "那一跳在发出之前就被终止"（left_browser=False）。那一跳其实出去了，
            # 只是被结果边界拦住的 —— 账本写着 True/False 的那一位，必须由真应答过
            # 请求的那一侧来填。
            return
        url = ((params.get("request") or {}).get("url")) or ""
        host = (urlparse(url).hostname or "").lower()
        request_id = params.get("requestId")
        if not host or host in self._allowed:
            await self._send("Fetch.continueRequest", request_id)
            return
        is_main = bool(self._root_frame) and params.get("frameId") == self._root_frame
        if not self._root_frame or is_main:
            self._watch.append((host or urlparse(url).netloc, True))
            self._escaped.set()
        await self._send("Fetch.failRequest", request_id,
                         errorReason="BlockedByClient")

    async def _send(self, method: str, request_id, **extra) -> None:
        """应答这一笔。失败一律吞掉 —— 页面关完之后还有事件进来是实测有的事
        （TargetClosedError），这里没有可救的请求，让它冒出去只会把一次关闭竞态
        变成一条 traceback。
        """
        if self._session is None or request_id is None:
            return
        try:
            await self._session.send(method, {"requestId": request_id, **extra})
        except Exception:
            pass


async def _await_stable_or_escape(stability_coro, fence: "_DocumentFence") -> None:
    """等页面稳定，或者等围栏拦下一笔主文档 —— 谁先到算谁。

    不这么写的话发生在真机上（2026-10-09 `-m e2e`）：主文档那一跳被掐之后，导航
    永远不会再报 load / domcontentloaded，于是稳定等待把整个调用预算花光，最后说话的
    是那句"这一档超时了"。围栏实际上成功了，可回执把它说成了一次慢网络。
    """
    tasks = [asyncio.ensure_future(stability_coro),
             asyncio.ensure_future(fence.wait_for_escape())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


# ─── Resource blocking handler ───────────────────────────────────────────────

# Header names that ARE a credential, so they belong to one origin only. Kept in
# step with fetcher._CREDENTIAL_HEADERS, plus the API-key spellings callers
# actually use — those are the ones whose name is chosen by the caller, so they
# cannot be recognised from a built-in list at the HTTP layer either.
# `cookie` is deliberately absent: the browser's own jar is already domain-scoped,
# and rewriting a Cookie header here would fight that model instead of protecting
# anything (carry cookies with session_id/cookies=, which is scoped per host).
_BROWSER_CREDENTIAL_HEADERS = frozenset({
    "authorization", "proxy-authorization",
    "x-api-key", "api-key", "x-goog-api-key", "x-auth-token", "x-access-token",
})

# ─── 渲染时机：认出壳页才付的那 5 秒（17.0 §6 三条新规矩第 1 条）──────────
#
# `network_idle` 原来是调用方的一颗开关，而它唯一的用户就是"SPA 抽到空壳"这一种
# 失败 —— 那种失败现在认得出来：load 都过了，可见文本还没长出来。所以这 5 秒改成
# **认出壳页才付**，参数随之删掉。探针只报数，结论在 Python 这边下（阈值散进页面
# 脚本就等于没人能测它、也没人能改它）。200 与 server 侧抽取后的壳页判定同值：两边
# 一错位，就会出现"一条轴认得出、另一条轴白等 5 秒"的裂缝。
_UNRENDERED_MAX_TEXT_CHARS = 200
SETTLE_IDLE_MS = 5000

#: 起一台浏览器进程在这台机器上要多久：3–5 秒量级（`LAUNCH_PROBE_TIMEOUT_S` 那句注释
#: 记的就是这一格实测）。这颗数的用途只有一个 —— 给"这次调用装不装得下一张截图"
#: （`server._min_visual_call_ms`）提供一个**有出处的**加数，而不是把 5 秒写成直觉。
#: 它不是超时：真的慢机器由调用方点名的预算与那道兜底管。
BROWSER_LAUNCH_TYPICAL_MS = 5_000

_UNRENDERED_PROBE_JS = """() => {
  const b = document.body;
  const text = b ? (b.innerText || b.textContent || '') : '';
  return { text: String(text).trim().length,
           scripts: document.querySelectorAll('script').length };
}"""


def _looks_unrendered(reading) -> bool:
    """DOM 读数 → 这页是不是还没长出来。读不到 = 不算（猜一次就花读者 5 秒）。"""
    if not isinstance(reading, dict):
        return False
    try:
        text = int(reading.get("text", -1))
        scripts = int(reading.get("scripts", 0))
    except (TypeError, ValueError):
        return False
    return 0 <= text < _UNRENDERED_MAX_TEXT_CHARS and scripts > 0


class BrowserSSRFBlockedError(RuntimeError):
    """浏览器被指到一个内网/回环/元数据地址 —— 已拦下，且不会重试。

    单独一个类型是为了让 fetch() 的重试循环**不要重试**：这不是网络抖动，重试
    只会把同一个内网目标再打三次。
    """


def _effective_port(parsed: Any) -> int:
    if parsed.port:
        return parsed.port
    return 443 if (parsed.scheme or "").lower() == "https" else 80


def _is_entry_target(request_url: str, entry_url: str) -> bool:
    """这次请求是不是"入口站点自己" —— 同主机，且同端口或 http↔https 默认端口升级。

    入口 URL 在 MCP 边界已过 validate_url（含 DNS 复查、hosts 钉位策略），浏览器层
    不重复否决它；但**只豁免同一个站点**：内网跳板最常见的形态就是
    `localhost:8080` 页面把浏览器引向 `localhost:9222`（同主机、异端口），那一类必须拦。
    """
    try:
        req = urlparse(request_url)
        entry = urlparse(entry_url)
    except Exception:
        return False
    if (req.hostname or "").lower() != (entry.hostname or "").lower():
        return False
    rp, ep = _effective_port(req), _effective_port(entry)
    if rp == ep:
        return True
    return {rp, ep} == {80, 443}


class CDPHostNotAllowedError(RuntimeError):
    """CDP attach 模式下，目标 host 不在用户授权过的名册里（cdp_hosts.json）。

    与 SSRF 拦截同类：这是用户给的授权边界，不是网络错误——不重试，原样抛。
    """


class CDPNoLoggedInContextError(RuntimeError):
    """attach 连上了端点，可端点没有报出任何 browser context —— 没有登录态可借。

    批次 8。这一态从前**不是失败**：`_resolve_cdp_context` 退回 `new_context()`，
    那是一个隔离 profile，页面照样 200、内容照样是真的，只是没有你的 cookie；而
    `cdp_attached` 默认 False 会被 wire 折叠掉，于是「登录态没跟上」与「这颗字段
    不适用」字节相同。调用方点名 attach 只有一个理由——要那个登录态，所以答一个
    匿名视图就是答非所问。判失败，一个字节都不抓。
    """


class BrowserBusyError(RuntimeError):
    """要一台**看得见**的浏览器，可这个 profile 上已经有一台 headless 的在跑。

    批次 11。两条老路都不对：接管它（用户看不见的那个浏览器带着他的 profile 变成
    可见的，行为像魔法）或者关掉再起一台（扔掉的可能正是他正在用的标签页）。所以这里
    拒绝，并说清把窗口交回来的那一步。反向不冲突：可见的那台能干 headless 的活，所以
    后台请求照常复用热的可见浏览器。
    """


class CDPUnreachableError(RuntimeError):
    """attach 要的端点根本没在听。批次 9：这句话由知道端点的那一层写完。

    从前这里什么都没写，Playwright 的 `connect_over_cdp` 异常一路逃到 transport 的
    通用处理器，于是整条响应退化成裸 ``{"error": ...}``：没有 summary / next_action /
    content_ok，正文里还带着 call log，尾巴被 [:300] 剪掉。补建议的那段话曾经挂在
    transport 层，条件是「这次调用的 `arguments` 里有 `cdp_url`」—— 只认**显式**传的
    端点，而端点也可以来自 `cred_defaults(set={'cdp': …})`（操作员存的那份），那条路
    上一个字的建议都不会出现。一个事实两个名字，检查只读了一个：第 7 批同一个洞。
    """


class BrowserDepsUnavailableError(RuntimeError):
    """patchright / playwright 不可导入（HTTP-only 形态是被支持的服务姿态）。

    从前这条以裸 RuntimeError 抛出：auto 升级路在升级前自己查过 deps 折了信封，
    但 `force_fetcher='stealthy'` 与动作车道让它带着安装建议逃到 call_tool 兜底
    —— is_error 裸错，[:300] 恰好剪掉安装建议的尾巴。进 TIER_REFUSALS 后，每条
    车道按同一只 `_refusal_result` 折信封。
    """


#: dhole **设计过的**拒绝（不是故障）。两份读法共用这一份名单：
#: `server._tier_error_text` 按它决定"全文过闸还是剪到 200"（句子的出路都写在结尾，
#: 帽子吃掉结尾就等于把话说一半），`server.stealthy_fetch` 按它决定"折成信封还是照旧
#: 抛出"。加一类拒绝只改这一行 —— 两处各列一遍是第 7、9 批反复踩的那个洞。
TIER_REFUSALS: tuple = (CDPHostNotAllowedError, CDPNoLoggedInContextError,
                        CDPUnreachableError, BrowserBusyError,
                        BrowserDepsUnavailableError)


def _cdp_unreachable_text(endpoint: str, exc: BaseException) -> str:
    """CDP 端点连不上时那一句完整的话。作者只有这一处。

    只留原因的第一行（Playwright 把 call log 拼在同一条消息里，整段上线是噪声），
    出路按**浏览器中立**的说法写：这里不能假设用户起的是 Chrome —— Edge 同样是
    CDP 端点，而那句"Start Chrome with --remote-debugging-port"对 Edge 用户是错的。
    """
    reason = (str(exc).strip().splitlines() or ["did not answer"])[0][:160]
    refused = "ECONNREFUSED" in reason or "Connection refused" in reason
    how = (
        "nothing is listening on it - remote debugging lives and dies with the "
        "browser process, so this is not a timeout to raise"
        if refused else
        "it refused the CDP handshake"
    )
    return (
        f"cdp_endpoint_unreachable: the CDP endpoint {str(endpoint)!r} {how} "
        f"({reason}). NOTHING was fetched - no page was opened and no cookie was "
        "read. To fix it on the browser side: start the browser you are actually "
        "logged into (Chrome or Edge) with --remote-debugging-port=<this port> "
        "plus the --user-data-dir that holds those logins, then re-call the same "
        "url. No tool parameter reaches this; if the endpoint moved, cdp_url= is "
        "the thing to change (and the hosts approved for the old one stay "
        "authorized until sys_state(clear=['cdp_hosts']) says otherwise)."
    )


_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# 17.0：``DHOLE_BROWSER_PROFILE`` 已注销（运行时静默忽略）。命名 profile 的
# 能力随 B5 的 `cred_defaults` 以参数形态回来（§3-15/§3-17）；在那之前
# launch 一律用一次性临时目录，storage-state 持久化经 session 桥仍可用。


async def _resolve_cdp_context(browser: Any) -> Any:
    """attach 语义的核心（G33 → 批次 8）：复用 CDP 端点已有的 default context。

    Playwright 语义：``connect_over_cdp`` 之后 ``browser.contexts[0]`` 是远端
    浏览器的 default context——用户 profile 的 cookie / storage 都在那里；
    ``new_context()`` 则新建一个隔离 profile，什么登录态都拿不到（"跑通、返回
    200、但登录态是空的"）。MVP-0 证伪实验（tests/e2e_cdp_attach_test.py）
    验证的正是这个差别。

    批次 8 改的是**没有 context 时的那一支**：从前退回 ``new_context()``（一个
    匿名视图，且 `cdp_attached=false` 会被 wire 折叠掉，读起来和成功一模一样），
    现在**拒答**：一个字节都不抓，错误里带上端点报出的 target 数与两种解释
    （端点冷得还没报出 context → 原样重发一次；attach 到的不是装着你登录的那个
    窗口 → 去核对 `--user-data-dir` / `--remote-debugging-port`）。
    """
    contexts = list(getattr(browser, "contexts", None) or [])
    if contexts:
        return contexts[0]
    targets = len(list(getattr(browser, "targets", lambda: [])() or []))
    raise CDPNoLoggedInContextError(
        f"cdp_no_logged_in_context: connected to the CDP endpoint, but it exposed "
        f"0 browser context(s) ({targets} target(s)), so there was no logged-in "
        "profile to ride and NOTHING was fetched. attach exists only to reuse the "
        "cookies already in that browser, and an isolated context would answer "
        "with an anonymous view that is only visible as an absent field - so it "
        "is refused instead. Re-call the same url once (a just-started endpoint "
        "sometimes reports its contexts late); if it repeats, the endpoint you "
        "attached is not the window holding your logins - check that Chrome was "
        "started with --user-data-dir pointing at that profile and "
        "--remote-debugging-port at this port."
    )


# 登录探测（G35 check_login）。判定全在页面形状与 cookie 名上，不引入任何模型、
# 不读取任何 cookie 值。三态结论在 _login_verdict 里聚合成纯函数。
_LOGIN_PROBE_JS = """() => {
  const q = (sel) => { try { return !!document.querySelector(sel); } catch (e) { return false; } };
  const text = ((document.body && document.body.innerText) || '').toLowerCase();
  return {
    password_field: q('input[type=password]'),
    login_form: q('form[action*="login" i], form[action*="signin" i], form[action*="auth" i]'),
    logout_control: /\\bsign out\\b|\\blog ?out\\b|\\bsignout\\b|\\blogout\\b/.test(text)
                    || text.includes('退出登录') || text.includes('注销登录') || text.includes('退出账号'),
    login_control: /\\bsign in\\b|\\blog ?in\\b|\\bsignin\\b|\\blogin\\b/.test(text)
                   || text.includes('登录') || text.includes('登入'),
  };
}"""


def _login_verdict(probe: dict, cookie_names: List[str]) -> dict:
    """把 DOM 信号与 cookie 名聚合成可判读的结论（纯函数，可单测）。

    logged_in 三态：True / False / None——None 表示页面形状不足以定论，把
    信号交回外层 agent 判断（它有语言能力，dhole 没有，也不该有）。cookie
    只出 NAMES：值是凭据，工具输出会进 transcript 与缓存（sessions.py 同纪律）。
    """
    probe = probe if isinstance(probe, dict) else {}
    logout = bool(probe.get("logout_control"))
    password = bool(probe.get("password_field"))
    login_form = bool(probe.get("login_form"))
    signals: List[str] = []
    if logout:
        signals.append("logout_control_present")
    if password:
        signals.append("password_field_present")
    if login_form:
        signals.append("login_form_present")
    if probe.get("login_control"):
        signals.append("login_control_present")
    if not password and not login_form:
        signals.append("no_login_form")
    if logout and not password:
        verdict: Optional[bool] = True
    elif password and login_form and not logout:
        verdict = False
    else:
        verdict = None
    return {
        "logged_in": verdict,
        "signals": signals,
        "cookie_names": sorted({str(n) for n in (cookie_names or []) if n}),
        "method": "dom+cookie-names",
    }


def _create_route_handler(
    disable_resources: bool,
    blocked_domains: Optional[Set[str]] = None,
    ssrf_watch: Optional[list] = None,
    entry_url: str = "",
    main_frame: Any = None,
    sent_headers: Optional[Dict[str, str]] = None,
    allowed_hosts: Optional[frozenset] = None,
) -> Callable[[Any], Awaitable[None]]:
    """Create an async route handler for resource blocking + SSRF guarding.

    ssrf_watch（传入时启用）：每个 http(s) 请求先问一次"目标是内网吗"，
    是就 abort 并把 (host, 是否主文档) 记进这个 list 交给调用方判定。
    子资源（图片/脚本/XHR）与重定向都在这里过一道 —— 浏览器内部这些请求不走
    validate_url，而页面 JS 自己发起的 fetch 更是只有这条路能拦。

    sent_headers（传入时启用）：把调用方挂上来的**凭据**头绑在入口主机上。
    `page.set_extra_http_headers` 的语义是「这个页面发出的每个请求都带上这些头」，
    于是一个 staging 站点的 `Authorization: Basic …` 会跟着它的图片、脚本、字体和
    分析端点一起发给每一个第三方 origin。HTTP 层在跨源重定向时已经不分发凭据了
    （fetcher._hop_headers）；两层口径不一致的话，「升级到隐身浏览器」就等于「把
    凭据撒出去」，而那恰恰是隐身层存在的理由。只动调用方自己声明过的那几个头名，
    页面 JS 自己设的 Authorization 一律不碰。

    allowed_hosts（传入时启用，G33 host scope）：CDP attach 模式下浏览器的每个
    请求都带着用户 profile 的已认证 cookie，所以白名单外的导航必须在**机制层**
    拦下——entry URL 的检查只挡得住入口，重定向和页面 JS 发起的跨站请求只有
    route 这一条路能拦。「有白名单配置但没人执行它」不是实现，是摆设。
    """
    disabled = DISABLED_RESOURCE_TYPES if disable_resources else set()
    domains = frozenset(blocked_domains) if blocked_domains else frozenset()
    credential_names = frozenset(
        k.lower() for k in (sent_headers or {})
        if k.lower() in _BROWSER_CREDENTIAL_HEADERS)
    entry_host = (urlparse(entry_url).hostname or "").lower() if credential_names else ""

    async def handler(route: Any) -> None:
        try:
            request_url = route.request.url
            rt = route.request.resource_type
            if rt in disabled:
                await route.abort()
                return
            if allowed_hosts is not None:
                ap = urlparse(request_url)
                if (ap.scheme or "").lower() in ("http", "https"):
                    ahost = (ap.hostname or "").lower()
                    if ahost not in allowed_hosts:
                        logger.warning(
                            "host scope blocked a request in CDP-attach mode: %s",
                            ap.netloc or "?")
                        try:
                            await route.abort()
                        except Exception:
                            pass
                        return
            if domains:
                hostname = urlparse(request_url).hostname or ""
                if _is_domain_blocked(hostname, domains):
                    await route.abort()
                    return
            if ssrf_watch is not None:
                parsed = urlparse(request_url)
                # 只查真正会发起网络连接的 scheme：data:/blob:/about: 之类不是网络
                # 请求，拒掉它们只会弄坏页面（Cloudflare 的挑战页自己就用 blob:/data:）。
                if (parsed.scheme or "").lower() in ("http", "https") \
                        and not _is_entry_target(request_url, entry_url):
                    from dhole_mcp.security import url_targets_internal
                    # DNS 解析是阻塞调用，route handler 跑在事件循环上 —— 挪到线程里，
                    # 别让一次判定卡住整个浏览器（同一个答案由 validate_url 缓存兜底）。
                    if await asyncio.to_thread(url_targets_internal, request_url):
                        is_main_doc = False
                        try:
                            is_main_doc = bool(
                                rt == "document"
                                and route.request.is_navigation_request()
                                and (main_frame is None or route.request.frame == main_frame)
                            )
                        except Exception:
                            pass
                        ssrf_watch.append(
                            ((parsed.netloc or parsed.hostname or "").lower(), is_main_doc))
                        logger.warning(
                            "browser layer blocked a request to an internal target: %s",
                            parsed.netloc or "?")
                        await route.abort()
                        return
            if credential_names and entry_host:
                host = (urlparse(request_url).hostname or "").lower()
                if host and host != entry_host:
                    mine = dict(route.request.headers or {})
                    kept = {k: v for k, v in mine.items() if k not in credential_names}
                    if len(kept) != len(mine):
                        try:
                            await route.continue_(headers=kept)
                            return
                        except Exception:
                            # 覆盖失败也不能把请求挂掉：宁可放行（凭据可能被带出去），
                            # 也不要用一次抓取的中断来惩罚一个本来就配置了凭据的调用。
                            logger.warning(
                                "could not rebind credential headers for %s", host)
            await route.continue_()
        except Exception:
            try:
                await route.continue_()
            except Exception:
                pass

    return handler


# ─── Cloudflare solver ───────────────────────────────────────────────────────

async def _get_page_content(page: Any, max_retries: int = 20) -> str:
    """Get page.content() with retry workaround for Windows Playwright bug.

    See: https://github.com/microsoft/playwright/issues/16108
    """
    for _ in range(max_retries):
        try:
            return (await page.content()) or ""
        except Exception:
            await page.wait_for_timeout(500)
    return ""


def _detect_cloudflare(page_content: str) -> Optional[str]:
    """Detect the type of Cloudflare challenge in the page content.

    Returns: 'non-interactive', 'managed', 'interactive', 'embedded', or None.
    """
    challenge_types = ("non-interactive", "managed", "interactive")
    for ctype in challenge_types:
        if f"cType: '{ctype}'" in page_content:
            return ctype

    # Check for embedded turnstile captcha
    if 'challenges.cloudflare.com/turnstile/v' in page_content:
        return "embedded"

    return None


async def _solve_cloudflare(page: Any) -> None:
    """Solve Cloudflare Turnstile/Interstitial challenge on a Playwright page.

    Ported from scrapling's StealthySessionMixin._cloudflare_solver.
    Handles: non-interactive, managed, interactive, and embedded challenges.

    The solver:
    1. Waits for network idle
    2. Detects challenge type
    3. For non-interactive: waits for the "Just a moment" page to clear
    4. For interactive/embedded: clicks the Turnstile checkbox at the
       right coordinates (calculated from the iframe bounding box)
    5. Waits for the challenge to resolve, retries if needed
    """
    # Wait for network to settle
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass

    challenge_type = _detect_cloudflare(await _get_page_content(page))
    if not challenge_type:
        logger.debug("No Cloudflare challenge found")
        return

    logger.info(f"Cloudflare challenge type: {challenge_type}")

    if challenge_type == "non-interactive":
        # Just wait for the challenge to auto-resolve
        attempts = 0
        while "<title>Just a moment...</title>" in (await _get_page_content(page)):
            if attempts >= 30:
                logger.info("Non-interactive challenge still present after 30s, continuing")
                break
            await page.wait_for_timeout(1000)
            try:
                await page.wait_for_load_state()
            except Exception:
                pass
            attempts += 1
        logger.info("Cloudflare non-interactive challenge resolved")
        return

    # Interactive/managed/embedded: need to click the Turnstile checkbox
    box_selector = "#cf_turnstile div, #cf-turnstile div, .turnstile>div>div"
    if challenge_type != "embedded":
        box_selector = ".main-content p+div>div>div"
        # Wait for the "Verifying you are human" spinner to disappear
        spinner_attempts = 0
        while "Verifying you are human." in (await _get_page_content(page)):
            if spinner_attempts >= 20:
                break
            await page.wait_for_timeout(500)
            spinner_attempts += 1

    # Find the Cloudflare iframe
    outer_box: Any = {}
    iframe = page.frame(url=__CF_PATTERN)
    if iframe is not None:
        # Wait for iframe stability
        try:
            await iframe.wait_for_load_state("load")
        except Exception:
            pass

        if challenge_type != "embedded":
            # Wait for iframe to be visible
            iframe_visible_attempts = 0
            while not await (await iframe.frame_element()).is_visible():
                if iframe_visible_attempts >= 20:
                    break
                await page.wait_for_timeout(500)
                iframe_visible_attempts += 1

        try:
            outer_box = await (await iframe.frame_element()).bounding_box()
        except Exception:
            outer_box = {}

    if not iframe or not outer_box:
        # Check if challenge already solved
        if "<title>Just a moment...</title>" not in (await _get_page_content(page)):
            logger.info("Cloudflare challenge resolved without clicking")
            return
        # Try to find the box on the page directly
        try:
            outer_box = await page.locator(box_selector).last.bounding_box()
        except Exception:
            logger.warning("Could not find Cloudflare checkbox to click")
            return

    if not outer_box:
        logger.warning("Cloudflare checkbox bounding box is empty")
        return

    # Calculate click coordinates (offset into the checkbox area)
    captcha_x = outer_box["x"] + randint(26, 28)
    captcha_y = outer_box["y"] + randint(25, 27)

    # Click the checkbox with human-like mouse movement
    try:
        # Move to the checkbox via bezier curve (not a straight line)
        await _human_mouse_move(page, captcha_x, captcha_y)
        await page.wait_for_timeout(randint(100, 300))
        await page.mouse.click(captcha_x, captcha_y, delay=randint(100, 200), button="left")
    except Exception as e:
        logger.warning(f"Cloudflare click failed: {e}")
        return

    # Wait for network to settle after click
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass

    # Wait for the challenge page to clear
    if challenge_type != "embedded":
        attempts = 0
        while "<title>Just a moment...</title>" in (await _get_page_content(page)):
            if attempts >= 100:
                logger.info("Cloudflare page didn't disappear after 10s, continuing")
                break
            await page.wait_for_timeout(100)
            attempts += 1

    # Final stability wait
    try:
        await page.wait_for_load_state("load")
        await page.wait_for_load_state("domcontentloaded")
    except Exception:
        pass

    # Check if solved
    if "<title>Just a moment...</title>" not in (await _get_page_content(page)):
        logger.info("Cloudflare challenge solved")
        return

    # Not solved: retry
    logger.info("Cloudflare challenge still present, retrying...")
    await _solve_cloudflare(page)


# ─── Channel detection: prefer system Chrome over bundled Chromium ───────────────

def _win_local_appdata() -> Path:
    """这台机器的 Local 目录，与 driver 的 `LOCALAPPDATA || homedir()/AppData/Local` 同式。

    没有兜底就是本次事故的成因：宿主 spawn 的进程 env 里常常没有 LOCALAPPDATA，那时
    `Path("") / "ms-playwright"` 得到的是**相对**路径，后面的 `is_dir()` 按进程 CWD
    解析 —— 于是"盘上明明有"和"我没找到"可以同时为真。
    """
    raw = (os.environ.get("LOCALAPPDATA") or "").strip()
    if raw:
        return Path(raw)
    return Path.home() / "AppData" / "Local"


def _program_files_roots() -> list[Path]:
    """Program Files 两根；env 没给就从 SystemRoot 的父目录推（被剥干净的进程常这样）。"""
    roots: list[Path] = []
    for name in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        raw = (os.environ.get(name) or "").strip()
        if raw:
            roots.append(Path(raw))
    if not roots:
        sysroot = (os.environ.get("SystemRoot") or "").strip() or r"C:\Windows"
        parent = Path(sysroot).parent
        roots = [parent / "Program Files", parent / "Program Files (x86)"]
    return roots


def _win_browser_roots() -> list[Path]:
    """系统浏览器的安装根：Program Files 两根 + per-user 的 Local。这份清单只列一次。"""
    return _program_files_roots() + [_win_local_appdata()]


_APP_PATHS_BASES = (
    "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths",
    "SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\App Paths",
)


def _winreg_app_paths(exe_name: str) -> list[str]:
    """注册表 App Paths 里这颗 exe 记的位置：不读 env、不起进程，被剥干净的环境也能答。

    HKLM 两条（64 位与 WOW6432Node）加 HKCU 一条，覆盖"机器级装"与"只给当前用户装"
    两种。这里的答案不保证文件还在（卸载残留、手动搬过），所以调用方仍要 `isfile`。
    """
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:  # pragma: no cover - 非 Windows 没有这个模块
        return []
    found: list[str] = []
    hives = [(winreg.HKEY_LOCAL_MACHINE, base) for base in _APP_PATHS_BASES]
    hives.append((winreg.HKEY_CURRENT_USER, _APP_PATHS_BASES[0]))
    for root, base in hives:
        try:
            with winreg.OpenKey(root, base + "\\" + exe_name, 0,
                                winreg.KEY_QUERY_VALUE) as key:
                value, _ = winreg.QueryValueEx(key, None)
        except OSError:
            continue
        if value:
            found.append(str(value).strip().strip('"'))
    return found


def _win_system_browser_path(rel: tuple) -> Optional[str]:
    """一颗系统浏览器 exe 在哪里：注册表 → 安装根 → PATH。都不中才 None。"""
    exe_name = rel[-1]
    for candidate in _winreg_app_paths(exe_name):
        if os.path.isfile(candidate):
            return candidate
    for base in _win_browser_roots():
        path = os.path.join(str(base), *rel)
        if os.path.isfile(path):
            return path
    import shutil
    found = shutil.which(exe_name)
    return found if found and os.path.isfile(found) else None


_chrome_channel_cache: Optional[str] = None


def _detect_chrome_channel() -> str:
    """Detect an installed system Chromium-family browser. Cache the result.

    A system browser (channel='chrome' or 'msedge') has a real TLS
    fingerprint (JA4) that matches what real users have. Bundled Chromium's
    fingerprint differs and is detectable. The benchmark showed a system
    channel is the bigger lever than patchright's own patches. Edge shares
    Chrome's whole network stack (BoringSSL), so it carries the same lever -
    which matters on machines that never installed Chrome (Edge ships with
    Windows), so the order is chrome → msedge → bundled chromium.

    Returns the patchright channel name: 'chrome', 'msedge', or 'chromium'.
    """
    global _chrome_channel_cache
    if _chrome_channel_cache is not None:
        return _chrome_channel_cache

    import shutil

    # (channel, win32 paths, POSIX PATH names) — Chrome first, Edge second:
    # on a machine with both, either works, and Chrome is the tested default.
    _SYSTEM_BROWSERS = (
        ("chrome",
         ("Google", "Chrome", "Application", "chrome.exe"),
         ("google-chrome", "google-chrome-stable")),
        ("msedge",
         ("Microsoft", "Edge", "Application", "msedge.exe"),
         ("microsoft-edge", "microsoft-edge-stable")),
    )
    if sys.platform == "win32":
        for channel, rel, _ in _SYSTEM_BROWSERS:
            path = _win_system_browser_path(rel)
            if path:
                _chrome_channel_cache = channel
                logger.info("System %s detected: %s", channel, path)
                return channel
    else:
        # POSIX: system browsers by PATH name, then bundled chromium.
        # 'chromium' stays in the Chrome row: a distro chromium is still a
        # system browser with a real fingerprint, just not Google-branded.
        for channel, _, names in _SYSTEM_BROWSERS:
            probes = names + (("chromium", "chromium-browser")
                              if channel == "chrome" else ())
            for name in probes:
                if shutil.which(name):
                    _chrome_channel_cache = channel
                    logger.info("System %s detected: %s", channel, name)
                    return channel

    _chrome_channel_cache = "chromium"
    logger.info("No system Chrome/Edge found, using bundled Chromium")
    return "chromium"


_chrome_version_cache: Dict[str, Optional[str]] = {}


def _win_version_dir_version(exe_path: str) -> Optional[str]:
    """从版本目录读版本号，不起进程、不开 PowerShell。

    Chrome/Edge 在 Windows 上是 `Application\\<x.y.z.w>\\chrome.exe` 的布局，launcher
    与版本号目录同级 —— 版本号就写在目录名里。`_get_chrome_ua()` 原来只问 PowerShell
    拿 `VersionInfo.FileVersion`：宿主把进程创建收紧时那一句会失败，而它的失败被吞成
    None，UA 覆盖于是**静默**不发生，系统浏览器在隐身档里自称 HeadlessChrome ——
    档位还在跑，指纹已经不是那一级了，调用方看不到任何痕迹。
    """
    exe_name = os.path.basename(exe_path)
    parent = Path(exe_path).parent
    try:
        entries = list(parent.iterdir())
    except OSError:
        return None
    versions = []
    for entry in entries:
        match = re.fullmatch(r"(\d+(?:\.\d+){3})", entry.name)
        if not match:
            continue
        try:
            if not (entry / exe_name).is_file():
                continue
        except OSError:
            continue
        versions.append(tuple(int(p) for p in match.group(1).split(".")))
    if not versions:
        return None
    return ".".join(str(p) for p in max(versions))


def _system_browser_path(channel: str) -> Optional[str]:
    """Where the detected system channel's executable lives, or None."""
    import shutil

    _SYSTEM_PATHS = {
        "chrome": (("Google", "Chrome", "Application", "chrome.exe"),
                   ("google-chrome", "google-chrome-stable")),
        "msedge": (("Microsoft", "Edge", "Application", "msedge.exe"),
                   ("microsoft-edge", "microsoft-edge-stable")),
    }
    rel, names = _SYSTEM_PATHS[channel]
    if sys.platform == "win32":
        return _win_system_browser_path(rel)
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _get_chrome_ua(channel: str = "chrome") -> Optional[str]:
    """Get a User-Agent string matching the installed system browser version.

    In headless mode, the browser reports 'HeadlessChrome' / 'HeadlessEdg'
    in the UA which is a dead giveaway. We read the real executable version
    and construct a UA with the normal brand instead. This UA matches the
    TLS fingerprint from the system channel: for Edge that means the
    'Edg/<version>' token rides along, so the UA tells the same story as
    the browser's own Sec-CH-UA brands (a Chrome UA on an Edge TLS
    handshake would be a self-contradicting fingerprint).

    Returns None if the version can't be determined.
    """
    global _chrome_version_cache
    cached = _chrome_version_cache.get(channel)
    if cached is not None:
        return cached

    import subprocess

    chrome_path = _system_browser_path(channel)
    if not chrome_path:
        return None

    try:
        if sys.platform == "win32":
            # 同级版本目录先答（纯文件系统，不起进程）；问不到才退回 PowerShell。
            version_output = _win_version_dir_version(chrome_path) or ""
            if not version_output:
                # On Windows, the exe --version hangs (starts the browser).
                result = subprocess.run(
                    ["powershell", "-Command",
                     f"(Get-Item '{chrome_path}').VersionInfo.FileVersion"],
                    capture_output=True, text=True, timeout=5,
                )
                version_output = (result.stdout or "").strip()
        else:
            result = subprocess.run(
                [chrome_path, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            version_output = (result.stdout or "").strip()
        # Parse version: 'Google Chrome 150.0.0.0' -> '150.0.0.0'
        match = re.search(r'(\d+\.\d+\.\d+\.\d+)', version_output)
        if match:
            version = match.group(1)
            # Edge appends its own brand token; Chrome does not.
            _brand = f" Edg/{version}" if channel == "msedge" else ""
            if sys.platform == "win32":
                ua = (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      f"AppleWebKit/537.36 (KHTML, like Gecko) "
                      f"Chrome/{version} Safari/537.36{_brand}")
            elif sys.platform == "darwin":
                ua = (f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      f"AppleWebKit/537.36 (KHTML, like Gecko) "
                      f"Chrome/{version} Safari/537.36{_brand}")
            else:
                ua = (f"Mozilla/5.0 (X11; Linux x86_64) "
                      f"AppleWebKit/537.36 (KHTML, like Gecko) "
                      f"Chrome/{version} Safari/537.36{_brand}")
            _chrome_version_cache[channel] = ua
            logger.info("%s UA constructed: Chrome/%s%s",
                        "Edge" if channel == "msedge" else "Chrome",
                        version, _brand)
            return ua
    except Exception:
        pass

    return None


# ─── Coherent fingerprint profiles ─────────────────────────────────────────────
# Each profile is a complete, internally consistent identity. All values tell
# the same story: platform matches WebGL renderer matches languages.
# Detectors cross-check for contradictions, so coherence > individual values.

_FINGERPRINT_PROFILES: List[Dict[str, Any]] = [
    {
        "platform": "Win32",
        "languages": ["en-US", "en"],
        "hardware_concurrency": 8,
        "device_memory": 8,
        "webgl_vendor": "Google Inc. (NVIDIA)",
        "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)",
        "plugins": [
            {"name": "PDF Viewer", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
            {"name": "Chrome PDF Viewer", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
            {"name": "Chromium PDF Viewer", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
            {"name": "Microsoft Edge PDF Viewer", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
            {"name": "WebKit built-in PDF", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
        ],
    },
    {
        "platform": "Win32",
        "languages": ["en-US", "en"],
        "hardware_concurrency": 12,
        "device_memory": 16,
        "webgl_vendor": "Google Inc. (Intel)",
        "webgl_renderer": "ANGLE (Intel, Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0)",
        "plugins": [
            {"name": "PDF Viewer", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
            {"name": "Chrome PDF Viewer", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
            {"name": "Chromium PDF Viewer", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
            {"name": "Microsoft Edge PDF Viewer", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
            {"name": "WebKit built-in PDF", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
        ],
    },
    {
        "platform": "Win32",
        "languages": ["en-US", "en"],
        "hardware_concurrency": 8,
        "device_memory": 8,
        "webgl_vendor": "Google Inc. (AMD)",
        "webgl_renderer": "ANGLE (AMD, AMD Radeon RX 6700 XT Direct3D11 vs_5_0 ps_5_0)",
        "plugins": [
            {"name": "PDF Viewer", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
            {"name": "Chrome PDF Viewer", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
            {"name": "Chromium PDF Viewer", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
            {"name": "Microsoft Edge PDF Viewer", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
            {"name": "WebKit built-in PDF", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
        ],
    },
    {
        "platform": "MacIntel",
        "languages": ["en-US", "en"],
        "hardware_concurrency": 8,
        "device_memory": 8,
        "webgl_vendor": "Google Inc. (Apple)",
        "webgl_renderer": "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)",
        "plugins": [
            {"name": "PDF Viewer", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
            {"name": "Chrome PDF Viewer", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
            {"name": "Chromium PDF Viewer", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
            {"name": "Microsoft Edge PDF Viewer", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
            {"name": "WebKit built-in PDF", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
        ],
    },
]


def _generate_fingerprint_profile() -> Dict[str, Any]:
    """Pick a random coherent fingerprint profile.

    Returns a dict with platform, languages, hardware_concurrency,
    device_memory, webgl_vendor, webgl_renderer, plugins. All values
    are internally consistent (e.g., MacIntel platform matches Apple
    WebGL renderer).
    """
    import random
    return random.choice(_FINGERPRINT_PROFILES).copy()


def _build_stealth_init_script(profile: Dict[str, Any], full: bool = True) -> str:
    """Build a JavaScript init script from a fingerprint profile.

    Patches JS-layer signals that patchright does NOT handle:
    - navigator.webdriver (patchright sets False, undefined is stealthier)
    - navigator.languages (add 'en' fallback for consistency)
    - Canvas fingerprint: per-session deterministic noise
    - Permissions API consistency

    When full=True (bundled Chromium), also patches:
    - navigator.plugins (empty in headless, populated in real Chrome)
    - WebGL vendor/renderer (SwiftShader in headless = dead giveaway)
    - navigator.hardwareConcurrency / deviceMemory
    - navigator.platform
    - window.chrome runtime object

    The script runs before any page JavaScript via CDP
    Page.addScriptToEvaluateOnNewDocument, so detection scripts see
    patched values from the first line.
    """
    import json as _json

    plugins_js = _json.dumps(profile["plugins"])
    languages_js = _json.dumps(profile["languages"])
    webgl_vendor = profile["webgl_vendor"]
    webgl_renderer = profile["webgl_renderer"]
    platform = profile["platform"]
    hw_concurrency = profile["hardware_concurrency"]
    device_memory = profile["device_memory"]

    # Essential patches: always applied (even with system Chrome)
    essential = f"""(() => {{
  // ── navigator.webdriver (patchright sets false, undefined is stealthier) ──
  try {{ Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }}); }} catch(e) {{}}

  // ── navigator.userAgent: remove 'HeadlessChrome' (dead giveaway in headless) ──
  // System Chrome reports 'HeadlessChrome/150' in headless mode. Replace with
  // 'Chrome/150' so the UA matches the real Chrome TLS fingerprint.
  try {{
    const _origUA = navigator.userAgent;
    if (_origUA.includes('HeadlessChrome')) {{
      Object.defineProperty(navigator, 'userAgent',
        {{ get: () => _origUA.replace('HeadlessChrome', 'Chrome') }});
    }}
  }} catch(e) {{}}

  // ── navigator.languages (add 'en' fallback for consistency) ──
  try {{ Object.defineProperty(navigator, 'languages', {{ get: () => {languages_js} }}); }} catch(e) {{}}

  // ── Canvas fingerprint: per-session deterministic noise ──
  // Intercepts BOTH toDataURL and getImageData. Many detectors (sannysoft,
  // creepjs) compute canvas hashes via getImageData directly, bypassing
  // toDataURL. Noise is deterministic per session (seeded PRNG) so it's
  // consistent within a session but different across sessions.
  try {{
    let _seed = {randint(1, 999999)};
    function _prng() {{ _seed = (_seed * 16807) % 2147483647; return (_seed - 1) / 2147483646; }}
    function _noisePixels(imgData) {{
      const limit = Math.min(64, imgData.data.length);
      for (let i = 0; i < limit; i += 4) {{
        imgData.data[i] = (imgData.data[i] + (_prng() > 0.5 ? 1 : 0)) & 0xFF;
      }}
      return imgData;
    }}
    // Intercept toDataURL
    const _origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(...args) {{
      const ctx = this.getContext('2d');
      if (ctx) {{
        try {{
          const w = this.width, h = this.height;
          if (w > 0 && h > 0 && w < 4096 && h < 4096) {{
            const img = ctx.getImageData(0, 0, Math.min(w, 16), Math.min(h, 16));
            ctx.putImageData(_noisePixels(img), 0, 0);
          }}
        }} catch(e) {{}}
      }}
      return _origToDataURL.apply(this, args);
    }};
    // Intercept getImageData (used by sannysoft, creepjs for canvas hashing)
    const _origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function(...args) {{
      const imgData = _origGetImageData.apply(this, args);
      // Noise the first 16 pixels regardless of read size (detectors like
      // sannysoft/creepjs read the full canvas, not small regions).
      // Performance: only modifying 16 pixels, not the entire image.
      if (imgData.data.length >= 64) {{
        return _noisePixels(imgData);
      }}
      return imgData;
    }};
  }} catch(e) {{}}

  // ── Permissions API consistency ──
  try {{
    const _origQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = function(params) {{
      if (params.name === 'notifications') {{
        return Promise.resolve({{ state: 'prompt', onchange: null }});
      }}
      return _origQuery.call(this, params);
    }};
  }} catch(e) {{}}
"""

    # Full patches: only for bundled Chromium (channel=chromium)
    # System Chrome already has correct values; overriding them creates
    # contradictions that detectors specifically look for.
    if full:
        essential += f"""
  // ── navigator.platform (bundled Chromium may report wrong platform) ──
  try {{ Object.defineProperty(navigator, 'platform', {{ get: () => {repr(platform)} }}); }} catch(e) {{}}

  // ── navigator.plugins (empty in headless = bot signal) ──
  try {{
    const pluginsData = {plugins_js};
    const fakePlugins = pluginsData.map(p => ({{
      name: p.name, filename: p.filename, description: p.description,
      length: 1, 0: {{ type: 'application/pdf', suffixes: 'pdf', description: p.description }}
    }}));
    Object.defineProperty(navigator, 'plugins', {{
      get: () => {{
        const arr = fakePlugins;
        arr.item = i => arr[i] || null;
        arr.namedItem = n => arr.find(p => p.name === n) || null;
        arr.refresh = () => {{}};
        return arr;
      }}
    }});
  }} catch(e) {{}}

  // ── navigator.hardwareConcurrency ──
  try {{ Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {hw_concurrency} }}); }} catch(e) {{}}

  // ── navigator.deviceMemory ──
  try {{ Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {device_memory} }}); }} catch(e) {{}}

  // ── window.chrome (missing in headless) ──
  try {{
    if (!window.chrome) {{
      window.chrome = {{ runtime: {{}}, loadTimes: () => {{}}, csi: () => {{}} }};
    }} else if (!window.chrome.runtime) {{
      window.chrome.runtime = {{}};
    }}
  }} catch(e) {{}}

  // ── WebGL vendor/renderer (SwiftShader = headless giveaway) ──
  try {{
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(param) {{
      if (param === 37445) return {repr(webgl_vendor)};
      if (param === 37446) return {repr(webgl_renderer)};
      return getParameter.call(this, param);
    }};
    if (typeof WebGL2RenderingContext !== 'undefined') {{
      const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
      WebGL2RenderingContext.prototype.getParameter = function(param) {{
        if (param === 37445) return {repr(webgl_vendor)};
        if (param === 37446) return {repr(webgl_renderer)};
        return getParameter2.call(this, param);
      }};
    }}
  }} catch(e) {{}}
"""

    essential += "})();"
    return essential


# ─── Human behavior simulation ─────────────────────────────────────────────────

def _bezier_point(t: float, p0: tuple, p1: tuple, p2: tuple) -> tuple:
    """Quadratic Bezier curve point at parameter t."""
    x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
    y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
    return (x, y)


async def _human_mouse_move(page: Any, target_x: float, target_y: float) -> None:
    """Move mouse to target via a bezier curve with human-like timing.

    Starts from a random point, curves to the target with a control point
    slightly off the direct path, adds a small overshoot + correction.
    This passes behavioral scoring that checks mouse trajectory entropy.
    """
    import random

    # Random start point (somewhere in the viewport)
    start_x = random.uniform(50, 800)
    start_y = random.uniform(50, 600)

    # Control point: off the direct path, creates a curve
    mid_x = (start_x + target_x) / 2
    mid_y = (start_y + target_y) / 2
    offset = random.uniform(-150, 150)
    ctrl_x = mid_x + offset
    ctrl_y = mid_y + offset

    # Move along the bezier curve with variable speed
    steps = random.randint(15, 30)
    for i in range(steps):
        t = (i + 1) / steps
        # Ease-in-out: slower at start and end
        t_eased = t * t * (3 - 2 * t)
        x, y = _bezier_point(t_eased, (start_x, start_y), (ctrl_x, ctrl_y), (target_x, target_y))
        await page.mouse.move(x, y)
        # Variable delay: faster in the middle, slower at edges
        delay = int(random.uniform(5, 25))
        await page.wait_for_timeout(delay)

    # Small overshoot + correction (mimics human hand wobble)
    overshoot = random.uniform(2, 8)
    await page.mouse.move(target_x + overshoot, target_y + overshoot * 0.5)
    await page.wait_for_timeout(random.randint(30, 80))
    await page.mouse.move(target_x, target_y)


async def _simulate_human_behavior(page: Any) -> None:
    """Simulate human-like behavior after page load, before content extraction.

    Lightweight (~1.5-2.5s total): randomized dwell, one bezier mouse move,
    one smooth scroll. Passes Cloudflare's v9 behavioral scoring that checks
    mouse path entropy, time-on-page, and scroll velocity.
    """
    import random

    # 1. Dwell time: 1-2.5s before any interaction
    await page.wait_for_timeout(random.randint(1000, 2500))

    # 2. Mouse movement: one bezier curve to a random point
    try:
        target_x = random.uniform(200, 1200)
        target_y = random.uniform(200, 800)
        await _human_mouse_move(page, target_x, target_y)
    except Exception:
        pass

    # 3. Smooth scroll: variable speed, slight pauses
    try:
        scroll_amount = random.randint(100, 400)
        await page.evaluate(f"window.scrollBy({{ top: {scroll_amount}, behavior: 'smooth' }})")
        await page.wait_for_timeout(random.randint(200, 500))
    except Exception:
        pass


# ─── Browser session base ─────────────────────────────────────────────────────

# 一张**驻留**页能活多久（刀9-7 · F9）。时长与 `server.AUTO_SESSION_IDLE_TIMEOUT`
# 同一个数，理由也同一个：一个人看完一张图、把答案抄进下一次调用，用的是几十秒，
# 不是几毫秒；而 300 秒之后还把这页留着，占的是一份浏览器上下文与一个标签。
# 到期不是"关掉浏览器"，只是**放掉这一页** —— 复用的那一刻惰性检查（见
# `BrowserSession._pin_take`），因为具名会话刻意不进空闲回收那条监控。
PIN_TTL_S = 300


def _invented_referer(url: str, google_search: Any,
                      request_headers: Dict[str, str]) -> Optional[str]:
    """`google_search` 那一枚编出来的来路该不该发 —— 私网那一支没有作用对象。

    这道门本来只管一条腿：`server.py` 在参数合流处判 `target_is_private`，私网就把 dhole 合成的
    那一整套头摘掉，理由写在那一句注释里（「打回环或内网时没有那道墙，它们只落进你自己的服务
    日志」）。浏览器这一发读的是另一颗旋钮（`google_search`），于是同一句话对它从未被问过 ——
    实测同一只回环 echo 上两发：探活那一发 14 颗无 referer，真 Chromium 导航那一发 17 颗，
    其中 `referer: https://www.google.com/` 是敲进去的字面量。

    只管 dhole 打字打出来的那一颗：那台浏览器自己会发的 UA / `sec-ch-ua*` / `sec-fetch-*`
    一颗都不动（摘掉只会让这一发更像机器人），调用方**点名**给的那颗 referer 也照旧到线上。
    摘了就要说：申报位与 HTTP 腿共用同一个 `_HEADERS_OFF_BY`，两条腿读同一把尺
    （`security.target_is_private`），这样"少了什么、为什么少"在 `summary` 里读得到，
    而不是静默改掉调用方看见的那一颗参数。
    """
    if "referer" in request_headers:
        return None
    if not google_search:
        return None
    from dhole_mcp.security import target_is_private
    if target_is_private(url):
        # 立起来并说出来都在这一颗门里：`_note_referer_off` 往这一发的 notes 列表里 append
        # （改对象，跨复制过的上下文也通），而只 `_HEADERS_OFF_BY.set(...)` 那种"换个值"的写法
        # 在浏览器这一层可能回不到调用方 —— 少发东西却没人说，就是静默截断。
        from dhole_mcp.context import _note_referer_off
        _note_referer_off()
        return None
    return "https://www.google.com/"


class BrowserSession:
    """Base browser session using patchright's async API.

    Manages a persistent browser context with page pooling. Supports:
    - Headless/headful mode
    - Proxy (static or per-request)
    - Resource blocking (disable_resources, blocked_domains)
    - Wait strategies (load, domcontentloaded, networkidle)
    - Page actions (callable executed after navigation)
    - Cookie injection
    - Extra headers
    - Cloudflare solving (stealthy only)

    Lifecycle:
        session = StealthyBrowser(headless=True)
        await session.start()
        response = await session.fetch("https://example.com")
        await session.close()

    Or as async context manager:
        async with StealthyBrowser() as session:
            response = await session.fetch("https://example.com")
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        proxy: Optional[str] = None,
        locale: Optional[str] = None,
        timezone_id: Optional[str] = None,
        timeout: int = 30000,
        cookies: Optional[List[Dict]] = None,
        useragent: Optional[str] = None,
        disable_resources: bool = False,
        extra_headers: Optional[Dict[str, str]] = None,
        google_search: bool = True,
        cdp_url: Optional[str] = None,
        allowed_hosts: Optional[frozenset] = None,
        profile_name: Optional[str] = None,
        real_chrome: bool = True,
        wait_selector: Optional[str] = None,
        wait_selector_state: str = "attached",
        retries: int = 1,
        retry_delay: float = 1.0,
        # Stealthy-specific
        hide_canvas: bool = False,
        block_webrtc: bool = False,
        allow_webgl: bool = True,
        solve_cloudflare: bool = False,
        additional_args: Optional[Dict] = None,
        page_action: Optional[Callable] = None,
        page_setup: Optional[Callable] = None,
        humanize: bool = True,
    ):
        self._headless = headless
        self._proxy = proxy
        self._locale = locale
        self._timezone_id = timezone_id
        self._timeout = timeout
        self._cookies = cookies
        self._useragent = useragent
        self._disable_resources = disable_resources
        self._extra_headers = extra_headers
        self._google_search = google_search
        self._cdp_url = cdp_url
        # G33 host scope：None = 不限制（launch 模式）；frozenset = attach 模式
        # 白名单，entry URL 与 route 层双层执行。
        self._allowed_hosts = allowed_hosts
        self._profile_name = profile_name or None
        self._real_chrome = real_chrome
        self._wait_selector = wait_selector
        self._wait_selector_state = wait_selector_state
        self._retries = retries
        self._retry_delay = retry_delay
        self._hide_canvas = hide_canvas
        self._block_webrtc = block_webrtc
        self._allow_webgl = allow_webgl
        self._solve_cloudflare = solve_cloudflare
        self._additional_args = additional_args or {}
        self._page_action = page_action
        self._page_setup = page_setup
        self._humanize = humanize

        # State
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._user_data_dir: Optional[str] = None
        self._is_alive: bool = False
        self._fingerprint_profile: Optional[Dict[str, Any]] = None
        # True when connect_over_cdp reused the endpoint's own default context
        # (the one holding the user's login state). Gates every invasive
        # behavior: no cookie writes into the user's profile, no stealth
        # patches on the user's pages, no context.close() on detach.
        self._cdp_attached: bool = False
        # True when the launch path runs on a named persistent profile that
        # must survive close. 17.0: nothing names one yet (the env knob is
        # gone), and the plumbing stays for B5's cred_defaults parameter --
        # until then every launch is a scratch dir.
        self._persistent_profile: bool = False
        # 刀9-7（F9）驻留槽：`{"page", "route", "listener", "stamp"}` 或 None。
        # 默认每一次 fetch 都是 `new_page()` + `goto()`，于是"上一调用读到的那一枚
        # token / 那一张图"属于一张已经不存在的页面 —— 靶站 `/captcha` 每次 GET 换新
        # token，人读到的永远是上一张（体验报告 S10："我明明看清了它说我错！"）。
        # `pin=True` 让下一次同会话、同站点的调用**站在这一页上**做事，不导航。
        # 槽挂在这个对象上而不是 server 的 `_SessionEntry`：拆会话那条路本来就经过
        # `session.close()`，多一个字段买不到东西；而 TTL 必须在复用那一刻量，不能
        # 指望空闲监控（它只管 auto 会话）。
        self._pin: Optional[Dict[str, Any]] = None
        # 多页签登记表（2026-10-24 可提升清单 1d）：钉住的调用链里 `{"tab": ...}`
        # 开出来的额外页面，随主槽一起活到下一次钉住的调用（`{"tab": N}` 直达）。
        # 每格 {"page", "stamp"}——这些页是链内 goto 的，没有 route/响应钩子
        # （钩子只装在 fetch 自己导航的那一页上），因此 `_pin_close` 对它们
        # 的 `slot.get("listener")` 天然缺席即跳过。
        self._pin_tabs: List[Dict[str, Any]] = []

    # ── Lifecycle ─────────────────────────────────────────────────

    async def __aenter__(self) -> "BrowserSession":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def start(self) -> None:
        """Launch the browser and create a context."""
        if self._is_alive:
            raise RuntimeError("Session already started")

        from patchright.async_api import async_playwright

        self._playwright = await async_playwright().start()

        # Build browser launch options
        browser_args = list(DEFAULT_ARGS)
        if self._is_stealthy:
            browser_args.extend(STEALTH_ARGS)
            if self._block_webrtc:
                browser_args.extend((
                    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
                    "--force-webrtc-ip-handling-policy",
                ))
            if not self._allow_webgl:
                browser_args.extend((
                    "--disable-webgl",
                    "--disable-webgl-image-chromium",
                    "--disable-webgl2",
                ))
            if self._hide_canvas:
                browser_args.append("--fingerprinting-canvas-image-data-noise")

        browser_options: Dict[str, Any] = {
            "args": browser_args,
            "ignore_default_args": list(HARMFUL_ARGS),
            "headless": self._headless,
            "channel": _launch_channel(self._real_chrome),
            # 渲染沙箱是**关**的，而且这一直是 driver 的默认（`chromiumSandbox !== true`
            # 时它自己 push `--no-sandbox`，dhole 从不设 `chromium_sandbox`）。写成显式不
            # 是为了改变行为，是为了上游哪天翻默认：翻的时候这一行会跟着测试一起变红，
            # 而不是让每一次浏览器抓取静默多一个沙箱、慢一截、在某些 Windows 令牌下起不来。
            # dhole 真正的围栏从来不是渲染沙箱：SSRF 路由闸 + CDP 逐主机授权。
            "chromium_sandbox": False,
        }

        # Build context options
        context_options: Dict[str, Any] = {
            "color_scheme": "dark",
        }
        if self._is_stealthy:
            context_options.update({
                "is_mobile": False,
                "has_touch": False,
                "service_workers": "allow",
                "ignore_https_errors": True,
                "screen": {"width": 1920, "height": 1080},
                "viewport": {"width": 1920, "height": 1080},
                "permissions": ["geolocation", "notifications"],
            })
        if self._proxy:
            context_options["proxy"] = _construct_proxy_dict(self._proxy)
        if self._locale:
            context_options["locale"] = self._locale
        if self._timezone_id:
            context_options["timezone_id"] = self._timezone_id
        if self._extra_headers:
            context_options["extra_http_headers"] = self._extra_headers
        if self._useragent:
            context_options["user_agent"] = self._useragent
        elif self._headless:
            # In headless mode, the browser reports 'HeadlessChrome' /
            # 'HeadlessEdg' in the UA. Fix: get the real executable version
            # and construct a UA without 'Headless'.
            if browser_options["channel"] in ("chrome", "msedge"):
                ua = _get_chrome_ua(browser_options["channel"])
                if ua:
                    context_options["user_agent"] = ua
            else:
                # Bundled Chromium: use browserforge for a realistic UA
                try:
                    from browserforge.headers import HeaderGenerator
                    hg = HeaderGenerator()
                    headers = hg.generate()
                    ua = headers.get("User-Agent") or headers.get("user-agent")
                    if ua:
                        context_options["user_agent"] = ua
                except Exception:
                    pass

        # Merge additional args (highest priority)
        context_options.update(self._additional_args)

        try:
            if self._cdp_url:
                try:
                    self._browser = await self._playwright.chromium.connect_over_cdp(
                        endpoint_url=self._cdp_url
                    )
                except Exception as exc:
                    # 批次 9：这句话在这里写完整。放它逃到 transport 的通用处理器，
                    # 调用方拿到的就是裸 {"error": ...} + 一段 call log + 被 [:300]
                    # 剪掉的尾巴，而"起哪个浏览器"只有这一层说得出名字。
                    raise CDPUnreachableError(
                        _cdp_unreachable_text(self._cdp_url, exc)) from exc
                # G33：复用端点自己的 default context（contexts[0]）——它带着
                # 用户 profile 的全部 cookie / storage。此前这里调 new_context()，
                # 那是新建隔离 profile："连上已登录的 Chrome"实际拿到的永远是
                # 空登录态（请求会通、内容是真的，就是没登录——最难发现的 bug）。
                # 批次 8：端点报不出 context 时不再退回 new_context()，而是拒答。
                self._context = await _resolve_cdp_context(self._browser)
                self._cdp_attached = True
            else:
                # Persistent context: temp dir by default; a named profile under
                # ~/.dhole/profiles/<name>/ when the caller (or
                # DHOLE_BROWSER_PROFILE) opts into storage-state persistence.
                if self._profile_name:
                    from dhole_mcp import paths as _paths
                    pdir = _paths.profiles_dir() / self._profile_name
                    _paths.ensure_private_dir(pdir)
                    self._user_data_dir = str(pdir)
                    self._persistent_profile = True
                else:
                    self._user_data_dir = tempfile.mkdtemp(prefix="dhole_browser_")
                persistent_opts = {**browser_options, **context_options, "user_data_dir": self._user_data_dir}
                # 2.2 · 起进程是这次调用里最像"网络慢"的一段时间（这台机器实测 3–5 秒，
                # `LAUNCH_PROBE_TIMEOUT_S` 那句注释里量的就是它），而它今天一句都不发。
                # 惰性导入与本模块同口径（browser 是重模块，context 只在要说话时进来）。
                from dhole_mcp.context import report_stage
                await report_stage(
                    f"starting a {'stealthy' if self._is_stealthy else 'plain'} "
                    "browser process (a few seconds)")
                self._context = await self._playwright.chromium.launch_persistent_context(
                    **persistent_opts
                )

            # Initialize context. attach 模式不注入：cookies 会写进用户的真实
            # profile，等于替用户改登录态。
            if self._cookies and not self._cdp_attached:
                await self._context.add_cookies(self._cookies)

            # Generate stealth init script (stealthy only) for JS-layer patches
            # that patchright doesn't handle. Injected per-page via CDP
            # Page.addScriptToEvaluateOnNewDocument (not context.add_init_script,
            # which uses Routes and breaks DNS resolution with patchright).
            self._init_script: Optional[str] = None
            # attach 模式不做 stealth 注入：补丁会落在用户自己开着的页面上，
            # 制造与真实指纹的差异（修订二⑪——attach 下 _is_stealthy 视为 False）。
            if self._is_stealthy and not self._cdp_attached:
                self._fingerprint_profile = _generate_fingerprint_profile()
                # Full patches only for bundled Chromium; system Chrome already
                # has correct WebGL, plugins, platform, window.chrome.
                is_chrome_channel = browser_options["channel"] == "chrome"
                self._init_script = _build_stealth_init_script(
                    self._fingerprint_profile, full=not is_chrome_channel
                )
                logger.info("Stealth init script built (profile: %s, full: %s)",
                            self._fingerprint_profile.get("platform", "?"),
                            not is_chrome_channel)

            self._is_alive = True
            logger.info(f"Browser session started (stealthy={self._is_stealthy}, "
                        f"cdp_attached={self._cdp_attached})")
        except (CDPNoLoggedInContextError, CDPUnreachableError):
            # 这两种都是**端点**的事：一个答了话但没有登录态可借，一个根本没在听。
            # 都不是"我们装的浏览器起不来"，所以不许进 launch 故障记录 —— 那块盘是
            # 写给下一个进程读的，把用户的调试端口故障记成 dhole 的安装故障，下一次
            # `dhole -v` 就会说一句假话。
            await self._cleanup_playwright()
            raise
        except Exception as e:
            logger.error(f"Browser session start failed: {e}")
            # 三态里最难自己发现的那一态（在场、起不来）只有写下来才看得见：本进程
            # 马上要把这句错抛给调用方，而下一个进程（`dhole -v`、doctor、别的宿主
            # 会话）读的是这块磁盘。判定与节流都在 record_launch_failure 里，它绝
            # 不会抛 —— 这条热路径上不能多一个故障面。
            record_launch_failure(e, real_chrome=self._real_chrome)
            await self._cleanup_playwright()
            raise

    async def close(self) -> None:
        """Close the browser and cleanup."""
        if not self._is_alive:
            return

        try:
            # 驻留槽先清：context 一关，那一页就不存在了，而槽里若还留着它，
            # 下一次 `_pin_take` 读到的是"页面 is_closed() 了"这种半截事实。
            await self._pin_release()
            if self._context:
                # attach 模式绝不能 close 这个 context——那是用户 Chrome 的
                # default context，close 会带走用户自己开着的页面。对
                # connect_over_cdp，browser.close() 只断开连接，不动用户进程。
                if not self._cdp_attached:
                    await self._context.close()
                self._context = None
            if self._browser:
                await self._browser.close()
                self._browser = None
        except Exception as e:
            logger.debug(f"Error closing browser: {e}")
        finally:
            await self._cleanup_playwright()
            self._is_alive = False

            # Cleanup temp dir (a named persistent profile survives close)
            if self._user_data_dir and not self._persistent_profile:
                try:
                    import shutil
                    shutil.rmtree(self._user_data_dir, ignore_errors=True)
                except Exception:
                    pass
            self._user_data_dir = None

    async def _cleanup_playwright(self) -> None:
        """Stop the playwright instance."""
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    # ── Fetch ─────────────────────────────────────────────────────

    async def seed_cookies(self, url: str, cookies: Dict[str, str]) -> None:
        """MVP-3 cookie 双向桥（播种向）：把 HTTP jar 中该 host 的 cookie 灌进
        本 context，浏览器导航即携带 HTTP 层拿到的登录态。

        只允许在**会话专用**的浏览器上调用（server 侧保证）：共享 auto 会话的
        context 为所有匿名调用共用，往里灌某个 session 的 cookie 等于跨会话
        泄露。cookie 值不出进程（jar → context），只在这个方向存在。
        """
        if not cookies or not self._is_alive or self._context is None:
            return
        payload = [{"name": str(n), "value": str(v), "url": url}
                   for n, v in cookies.items() if n]
        if payload:
            try:
                await self._context.add_cookies(payload)
            except Exception as e:
                logger.debug("cookie seeding failed for %s: %s", url, e)

    async def harvest_cookies(self, url: str) -> Dict[str, str]:
        """MVP-3 cookie 双向桥（回写向）：读本 context 在该 URL host 上的
        cookie，交由调用方写回 jar（按 session_id 隔离存储）。值只进 jar，
        不进任何响应或日志。"""
        if not self._is_alive or self._context is None:
            return {}
        try:
            jar = await self._context.cookies(url)
        except Exception as e:
            logger.debug("cookie harvest failed for %s: %s", url, e)
            return {}
        out: Dict[str, str] = {}
        for c in jar or []:
            if isinstance(c, dict) and c.get("name") and c.get("value") is not None:
                out[str(c["name"])] = str(c["value"])
        return out

    @staticmethod
    def _same_document(a: str, b: str) -> bool:
        """Are these two strings the same **page**?

        Only used to decide whether an action chain may stand on a pinned page.
        Trailing slash and fragment are the same document; a different path or a
        different query is a different one — and so is anything unparseable,
        because "unknown" must not buy the right to act on a page nobody named.
        """
        try:
            pa, pb = urlparse(a or ""), urlparse(b or "")
            if not pa.netloc or not pb.netloc:
                return False
            if pa.netloc.lower() != pb.netloc.lower():
                return False
            if (pa.path or "/").rstrip("/") != (pb.path or "/").rstrip("/"):
                return False
            return (pa.query or "") == (pb.query or "")
        except Exception:
            return False

    async def _pin_take(self, url: str, *, for_actions: bool = False
                        ) -> Tuple[Optional[Dict[str, Any]], str]:
        """驻留的那一页能不能给这一次调用用 —— 判据全在这里，一条不过就交回原因。

        取走即从槽里摘下（`self._pin = None`），所以这一发失败时 finally 会照旧把
        那一页关掉：一个没人复用的驻留页，不该活成第二次调用的意外。

        四条拒绝各是一件具体的事：
          * `attach` —— 那是用户自己的浏览器、用户自己的标签页，dhole 不替他留着；
            而且 attach 那条通道压根不装驻留（见 `fetch` 里存槽那一支的守卫）。
          * `ttl` —— 过了 `PIN_TTL_S`，这一页可能早被站点侧的会话超时换掉了，
            站上去做事比重新导航更容易拿到一个错答案。
          * `closed` —— 页面被人（或 Chrome 自己）关了。
          * `host` —— 这一次要的是别的站点。同站判定用 `_is_entry_target` 那一个作者
            （端口、80↔443 升级都算同一站），不在这里重写第二份。
          * `target` —— 带着动作链的一发点名了**另一张页**（同站不同页）。`index` 是在
            点名的那一页上数出来的，所以这一发宁可重新导航；页仍在槽里，下一发照旧驻留
            （R01-027：实测 `hover index 0` 落在驻留的账户页上就是「退出登录」那颗按钮）。
        """
        slot = self._pin
        if slot is None:
            return None, ""
        self._pin = None
        if self._allowed_hosts is not None or self._cdp_attached:
            await self._pin_close(slot, "attach")
            await self._pin_close_tabs("attach")
            return None, "attach"
        if time.monotonic() - float(slot["stamp"]) > PIN_TTL_S:
            await self._pin_close(slot, "ttl")
            await self._pin_close_tabs("ttl")
            return None, "ttl"
        page = slot["page"]
        try:
            gone = page.is_closed()
        except Exception:
            gone = True
        if gone:
            await self._pin_close_tabs("closed")
            return None, "closed"
        if not _is_entry_target(url, page.url):
            await self._pin_close(slot, "host")
            await self._pin_close_tabs("host")
            return None, "host"
        if for_actions and not self._same_document(url, page.url):
            # R01-027. Same host is not the same page: an action's `index` counts
            # the elements of the page the caller NAMED, so running the chain on a
            # different page of that host acts on a target nobody chose (measured:
            # `hover index 0` on the pinned /account is the 退出登录 button, while
            # the call named /hoverdrag). Refuse the reuse but KEEP the pin — this
            # call navigates to what it named; the page stays in the slot for the
            # next call that stands on it. Dropping the page here would turn a
            # disclosure bug into the loss of H8's whole point.
            self._pin = slot
            return None, "target"
        return slot, ""

    def pin_census(self) -> Dict[str, Any]:
        """驻留这一页的**事前**账：地址 + 还剩几秒（`sys_state` 普查读它）。

        判据不重写：`pinned_left_s` 就是 `_pin_take` 里那道 `ttl` 门的同一个算式
        （`PIN_TTL_S` 减去 `stamp` 的年岁），所以普查说「还剩 40 秒」而下一发被拒
        是办不到的。倒计时而不是时刻，是因为 `stamp` 来自 `time.monotonic()` ——
        它不是一个能对外的钟；同一张表里的 `expires_in_s` 早就是这个形状
        （「还剩 21 小时」能行动，`1790566780.9` 不能）。

        三种状态各自说得出自己：`{"", None}` = 什么都没钉（含别人已把那张页关了）；
        `{地址, 0}` = 窗口过了但那一页还没被下一发收走；`{地址, n}` = 还能站 n 秒。
        只读：不改槽、不关页、不动 `_pin_take` 的判据。
        """
        def nothing_pinned() -> Dict[str, Any]:
            """没钉着那一支不许交出一个 0：压实层省略的是「等于自己的默认值」，而
            带地址的 0 是**判决**（窗口过了），缺席才是「没有这一页」。把缺席写成 0，
            线上就把两种状态压成一种（R02-073，P5 第一趟量到的正是这一格）。"""
            return {"pinned_url": "", "pinned_left_s": None}

        slot = self._pin
        if not slot:
            return nothing_pinned()
        try:
            page = slot.get("page")
            if page is None or page.is_closed():
                return nothing_pinned()
            url = page.url or ""
        except Exception:
            return nothing_pinned()
        if not url:
            return nothing_pinned()
        left = PIN_TTL_S - (time.monotonic() - float(slot["stamp"]))
        return {"pinned_url": url, "pinned_left_s": max(0, int(left))}

    @staticmethod
    async def _pin_close(slot: Dict[str, Any], reason: str) -> None:
        """放掉一张不再被复用的驻留页。摘监听器在关页之前 —— 与摘围栏闸同一条实测
        经验（挂着钩子 `page.close()`，那一页会停在半路直到 Playwright 的默认超时）。"""
        page = slot.get("page")
        if page is None:
            return
        try:
            if slot.get("listener"):
                page.remove_listener("response", slot["listener"])
        except Exception:
            logger.debug("pinned listener for %s would not come off", reason,
                         exc_info=True)
        try:
            if not page.is_closed():
                await page.close()
        except Exception:
            logger.debug("pinned page (%s) would not close", reason, exc_info=True)

    async def _pin_close_tabs(self, reason: str) -> None:
        """放掉登记表里的全部额外页签（驻留被拒、会话收尾时）。摘取即清空：
        留着只会变成"没有主人还活着的页面"。"""
        tabs, self._pin_tabs = self._pin_tabs, []
        for slot in tabs:
            await self._pin_close(slot, reason)

    async def _pin_release(self) -> None:
        """会话收尾时放掉驻留页与页签登记表（`close()` 调；崩溃路径由 context 自己兜）。"""
        slot, self._pin = self._pin, None
        if slot is not None:
            await self._pin_close(slot, "session")
        await self._pin_close_tabs("session")

    async def _install_page_guards(self, page: Any, *, entry_url: str,
                                   disable_resources: bool,
                                   sent_headers: Optional[Dict[str, str]],
                                   ssrf_watch: list,
                                   net_watch: Optional[dict] = None):
        """装上**这一发自己的**两道守卫，并把摘钩要用的三样东西交回去。

        route 那一层（资源拦截 + SSRF + 凭据绑在入口主机上）与 response 捕获都只在这
        里装一次 —— 驻留复用那条分支必须把上一次的两枚钩子摘干净再装，两处写法一漂移，
        复用的那一页就带着上一次的 `entry_url`、上一次的记录本做事：那是 SSRF 边界，
        不接受"大概没事"。

        `entry_url` 由调用方给：新导航是请求的那个 URL，驻留复用是这一页自己的 URL
        （同站豁免的参照物必须是它实际站在哪里）。

        `net_watch` 是这一发的网络账本（调用方每次新建，不跨发复用）。它挂在**已有的**
        response 钩子上，不另装监听器 —— 那个钩子在复用分支上会被摘掉重装，多装一个
        request/console 监听器就得同步改三处摘钩点，而实测 console 的有效信号是 0 条
        （见文件头那段注释）。守卫拦下的主机不在这里记：`ssrf_watch` 本来就是那件事的
        唯一作者，`_net_payload` 从它派生。
        """
        route_handler = _create_route_handler(
            disable_resources, None, ssrf_watch, entry_url=entry_url,
            main_frame=page.main_frame,
            sent_headers=sent_headers,
            allowed_hosts=self._allowed_hosts,
        )
        await page.route("**/*", route_handler)

        final_response: List[Any] = [None]

        async def _handle_response(resp: Any) -> None:
            try:
                if (resp.request.resource_type == "document"
                        and resp.request.is_navigation_request()
                        and resp.request.frame == page.main_frame):
                    final_response[0] = resp
            except Exception:
                pass
            if net_watch is not None:
                _note_net_response(net_watch, resp)

        page.on("response", _handle_response)
        return route_handler, _handle_response, final_response

    async def fetch(
        self,
        url: str,
        *,
        timeout: Optional[int] = None,
        google_search: Optional[bool] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        disable_resources: Optional[bool] = None,
        wait_selector: Optional[str] = None,
        wait_selector_state: Optional[str] = None,
        proxy: Optional[str] = None,
        solve_cloudflare: Optional[bool] = None,
        page_action: Optional[Callable] = None,
        page_setup: Optional[Callable] = None,
        retries: Optional[int] = None,
        probe_out: Optional[dict] = None,
        pin: bool = False,
        pin_out: Optional[dict] = None,
        **kwargs: Any,
    ) -> "Any":
        """Navigate to a URL and return a Response object.

        Parameters override session defaults for this request only. Proxy is
        fixed at session startup and cannot be changed here.

        probe_out（G35 check_login）：传入一个 dict，导航稳定后在页面上做一次
        登录探测（DOM 形状 + cookie 名，无模型、无值出境），结果写进这个 dict。

        pin（刀9-7 · F9）：True = 这一发结束后把页面留在手上；下一次带 pin 的调用若
        落在同一站点，就**站在这一页上**继续做事，不再 new_page、不再 goto。这买回来的
        正是"我读到的那道题就是我答的那一道"—— 每次重新导航的站点（验证码、单次
        token）在跨调用重试时必然答错。pin_out 写回这一发实际发生了什么：
        `{"outcome": "pinned" | "reused" | "dropped", "dropped": "<原因>"}`，
        文案的作者在上层（server.py），这里只交事实。
        """
        if not self._is_alive:
            raise RuntimeError("Session not started")
        if proxy is not None and proxy != self._proxy:
            raise ValueError(
                "Proxy is fixed when a browser session starts; "
                "open a new session to use a different proxy"
            )

        # host scope（G33，attach 模式）第一层：入口 URL 快失败。白名单外的
        # 重定向 / 页面 JS 请求由 route handler 拦——那才是机制层，这里只是
        # 让错误早一点、可读一点。
        if self._allowed_hosts is not None:
            _entry_host = (urlparse(url).hostname or "").lower()
            if _entry_host not in self._allowed_hosts:
                raise CDPHostNotAllowedError(
                    f"cdp_host_not_allowed: '{_entry_host or url}' is outside the "
                    "authorized host fence. In CDP-attach mode every request carries "
                    "the authenticated cookies of the attached profile, so "
                    "navigation is fenced to the hosts the user approved "
                    "(confirm=true on that host authorizes it; the fence lives in "
                    "cdp_hosts.json, and sys_state(clear=['cdp:host=<name>']) "
                    "revokes one host, clear=['cdp_hosts'] all)."
                )

        # Resolve per-request overrides
        actual_timeout = timeout if timeout is not None else self._timeout
        actual_google = google_search if google_search is not None else self._google_search
        actual_extra_headers = extra_headers if extra_headers is not None else self._extra_headers
        actual_disable_resources = disable_resources if disable_resources is not None else self._disable_resources
        actual_wait_selector = wait_selector if wait_selector is not None else self._wait_selector
        actual_wait_selector_state = wait_selector_state or self._wait_selector_state
        actual_solve_cf = solve_cloudflare if solve_cloudflare is not None else self._solve_cloudflare
        actual_page_action = page_action or self._page_action
        actual_page_setup = page_setup or self._page_setup
        actual_retries = retries if retries is not None else self._retries

        # Build referer
        request_headers = {}
        if actual_extra_headers:
            request_headers = {h.lower(): v for h, v in actual_extra_headers.items()}
        referer = _invented_referer(url, actual_google, request_headers)

        # 驻留的车道：dhole 自己起的那台浏览器、没有名册围栏、不是用户自己的 attach。
        # 这三件事在一次调用的长度里都不会变，所以在循环外量一次 —— 取页与存页读同一把
        # 尺（两处各写一遍的话，一处改口就留下"取了不存"或"存到用户的标签页上"）。
        _pin_lane = bool(pin) and self._allowed_hosts is None and not self._cdp_attached
        for attempt in range(actual_retries):
            # SSRF 守卫与围栏的记录本每发**新建**，不再 clear 循环外共享的那两份：
            # 上一发悬在 `asyncio.to_thread(url_targets_internal, …)` 里的 route
            # handler（DNS 慢可到秒级）恢复后先 append 再 abort，那笔迟到账落进
            # 下一发的账本，`blocked_main` 就会对一个本次从未触碰的主机报
            # BrowserSSRFBlockedError（fail-closed，但拒绝与证据皆假，且该类不重试）。
            # fence 侧同族问题有 `_session is None` 守卫；route 账本用每发新 list
            # 隔离。声明在 try 之前 —— except 分支读它们必须永远有定义。
            ssrf_watch: list = []
            fence_watch: list = []
            fence = None
            page = None
            # `keep` 只有一种方式立起来：这一发真的把页面留在了手上（成功路径存槽）。
            # 除此之外 finally 一律关页 —— 与今天逐字一致，包括失败的那一发。
            keep = False
            reused = False
            final_response: List[Any] = [None]
            _route: Any = None
            _listener: Any = None
            first_response: Any = None
            # 链内 tab 交接的页签表：只有 page_action 真跑过才有值。初始化必须在
            # try 之前 —— attach/goto 在那一行之前就能抛（CDP 名册拒绝、SSRF、
            # 围栏），finally 的页签收尾会先于它执行。
            _chain_tabs: Optional[List[Any]] = None
            # 驻留槽的接管旗（finally 读它决定收不收登记表，见 no-keep 分支）。
            # 与 `_chain_tabs` 同一道理由：在 try 内才绑定的话，绑定前的异常会让
            # finally 读到一个不存在的名字。
            slot: Optional[Dict[str, Any]] = None
            try:
                if _pin_lane and attempt == 0:
                    slot, _why = await self._pin_take(
                        url, for_actions=bool(actual_page_action))
                    if _why and pin_out is not None:
                        pin_out["dropped"] = _why
                        if _why == "target":
                            # 那句话要说得出两页各是谁，而只有这一层看得见驻留页自己。
                            _kept = self._pin or {}
                            pin_out["target"] = {
                                "pinned": getattr(_kept.get("page"), "url", "") or "",
                                "named": url}
                if slot is not None:
                    # 复用 = **不导航**，这就是这一刀的全部意义：那张题、那枚 token、
                    # 那个填了一半的表单都还在。但守卫必须是这一发自己的 —— 先把上一次
                    # 那两枚钩子摘干净（route 里坐着上一次的 ssrf_watch 和上一次的
                    # entry_url）；摘不掉就宁可弃页：带着一张旧地图开车是 SSRF 边界，
                    # 不接受"大概没事"。
                    try:
                        # 链内切过页签的驻留主槽不带钩子（route/listener 只在
                        # fetch 自己导航的那一页上）——缺席即跳过，别 KeyError。
                        if slot.get("route") is not None:
                            await slot["page"].unroute("**/*", slot["route"])
                        if slot.get("listener") is not None:
                            slot["page"].remove_listener("response",
                                                         slot["listener"])
                    except Exception:
                        await self._pin_close(slot, "unhook")
                        if pin_out is not None:
                            pin_out["dropped"] = "unhook"
                        slot = None
                    else:
                        page = slot["page"]
                        reused = True
                if not reused:
                    page = await self._context.new_page()
                page.set_default_navigation_timeout(actual_timeout)
                page.set_default_timeout(actual_timeout)

                # Stealth init script injection mechanism:
                # CDP Page.addScriptToEvaluateOnNewDocument does NOT work with
                # patchright (it patches Runtime.enable). add_init_script uses
                # Routes which breaks DNS. Instead, we use wait_until='commit'
                # + page.evaluate() to inject patches before page JS runs.

                if reused:
                    # 这一发的凭据头由这一发决定：上一页留下的那份不许悄悄跟着走
                    # （`set_extra_http_headers` 是替换语义，传空就是把它们摘掉）。
                    await page.set_extra_http_headers(actual_extra_headers or {})
                elif actual_extra_headers:
                    await page.set_extra_http_headers(actual_extra_headers)

                # Route handler: 资源拦截 + SSRF 守卫（每条路径都装 —— 截图/交互
                # session 里浏览器同样会被重定向走，那条路径此前一道防线都没有）。
                # 新导航按请求的 URL 判同站，驻留复用按这一页自己的 URL 判。
                # `net_watch` 每次 fetch 新建：账本记的是**这一发**发生了什么，驻留
                # 复用的那一页上还留着上一发的历史，串过来就是一句假话。
                net_watch: Dict[str, Any] = {"requests": 0, "failed": []}
                _route, _listener, final_response = await self._install_page_guards(
                    page, entry_url=(page.url if reused else url),
                    disable_resources=actual_disable_resources,
                    sent_headers=actual_extra_headers, ssrf_watch=ssrf_watch,
                    net_watch=net_watch)
                if reused:
                    first_response = slot["response"]

                # 围栏的请求边界（D-31b）：route 那一层看不见主文档重定向的下一跳，
                # 所以那条 302 会带着已登录的 profile 走出名册（今天只拦得住落地之后的
                # 正文）。名册只存在于 CDP attach 这一条通道，闸也就只在这里装 ——
                # 没有名册可判时它一分钱也不该花。装不上就退回今天这条线（route +
                # 结果边界），并且让拒绝话说实话（见 _fence_blocked_error 的 left_browser）。
                if self._allowed_hosts is not None:
                    candidate = _DocumentFence(page, self._allowed_hosts, fence_watch)
                    try:
                        await candidate.install()
                        fence = candidate
                    except Exception:
                        fence = None
                        await candidate.remove()
                        logger.debug("host fence (CDP Fetch) not installed", exc_info=True)

                # Page setup callback / Navigate / stealth injection —— 三件都是
                # "为这一张新文档"做的事，驻留复用那一页早就做完了一轮，再跑一遍等于
                # 把同一份补丁往同一个 document 上第二次按下去。响应捕获不在这里：
                # 那枚钩子与 route 同一次装上（`_install_page_guards`），两条分支共用
                # 一个作者，否则复用的那一发会带着上一次留下的监听器干活。
                if not reused:
                    if actual_page_setup:
                        try:
                            await actual_page_setup(page)
                        except Exception as e:
                            logger.warning(f"page_setup callback error: {e}")

                    # Navigate - use 'commit' to get control as soon as HTML is
                    # received but BEFORE page JS runs. Then immediately inject
                    # stealth patches via page.evaluate(). This is the only
                    # injection mechanism that works with patchright (CDP
                    # Page.addScriptToEvaluateOnNewDocument requires Runtime.enable
                    # which patchright patches out; add_init_script uses Routes
                    # which breaks DNS).
                    try:
                        first_response = await page.goto(url, referer=referer,
                                                          wait_until="commit")
                    except Exception:
                        # 'commit' may not be supported in older versions, fall back
                        first_response = await page.goto(url, referer=referer)

                    # Inject stealth patches IMMEDIATELY after commit, before
                    # the page's own scripts execute.
                    if self._init_script and first_response:
                        try:
                            await page.evaluate(self._init_script)
                        except Exception as e:
                            logger.debug(f"Stealth script injection error: {e}")

                # Wait for page stability. 围栏装着的时候这一等要与
                # "拦下了一笔主文档"赛跑：被掐掉的导航不会再有 load 事件，实测
                # （2026-10-09 的 `-m e2e`）那一次等待把 30 秒预算整个花光，于是调用级预算
                # 先说话，交回的是"这一档超时了"而不是那条拒绝 —— 围栏做了它的
                # 工作，回执说的却是另一件事。
                # 驻留复用只等"它当前这一笔导航（若有）落地"，不再跑那个 5 秒的
                # "看着还没渲染"探针 —— 那一等是给新文档的，而这一页不是新文档。
                if fence is not None:
                    await _await_stable_or_escape(self._wait_for_stability(page), fence)
                elif reused:
                    for _state in ("load", "domcontentloaded"):
                        try:
                            await page.wait_for_load_state(_state)
                        except Exception:
                            pass
                else:
                    await self._wait_for_stability(page)

                if not first_response:
                    raise RuntimeError(f"Failed to get response for {url}")

                # Solve Cloudflare if requested（那是一次导航的事：驻留页要么已经解过，
                # 要么正等着人来看，重跑一次解法会把这一页的形态再改一遍）
                if actual_solve_cf and not reused:
                    await _solve_cloudflare(page)
                    await self._wait_for_stability(page)

                # 驻留页要告诉动作链它自己站在哪：那句"重跑同一串动作"的合同在这一页
                # 上是假话（前面的 fill 已经跑过、页也变过了）。动作链读的是挂在它自己
                # 身上的那一格状态（`actions.build_page_action` 的 `pin_state`），不是
                # 某个全局 —— 浏览器层与动作层之间不许有隐形的共享真相。
                # 两个键各有各的时候：`reused` = 这一发站在上一发留下的那一页上；
                # `pinned` = 这一发结束后这一页要被留下（下一发不必重新导航）。提交闸要
                # 的是后一件 —— 只要这一页还会被人接着用，"把同一串动作再跑一遍"就不再
                # 是全部的真话。
                _pin_state = getattr(actual_page_action, "pin_state", None)
                if isinstance(_pin_state, dict):
                    _pin_state["reused"] = reused
                    _pin_state["pinned"] = _pin_lane
                    # 页签（1d）的供给：attach 的会话不许开页签（那是用户自己的
                    # 浏览器）；入口 URL 是 tab.open 落地检查的锚；prior_tabs 把
                    # 上一次钉住的登记表交给这条链（`{"tab": N}` 直达）。
                    _pin_state["attach"] = (self._allowed_hosts is not None
                                            or self._cdp_attached)
                    _pin_state["entry_url"] = url
                    if _pin_lane:
                        # 页签交接：0 号位永远是导航去的那个 URL，但钉住的那一页
                        # （含「target 拒复用、链点名了同站另一页」的情形）与登记表
                        # 里的页签按号递补进 1..N——`{"tab": 1}` 直达。这些页从此
                        # 归这一次调用管：存槽时重建状态，失败由 finally 收。
                        _prior: List[Any] = []
                        _slot_page = (slot or self._pin or {}).get("page")
                        if _slot_page is not None:
                            _prior.append(_slot_page)
                        _prior.extend(s.get("page") for s in self._pin_tabs)
                        _pin_state["prior_tabs"] = _prior
                        if slot is None and self._pin is not None:
                            self._pin = None      # 链接管了这一页
                        self._pin_tabs = []

                # Page action callback
                orig_page = page
                if actual_page_action:
                    try:
                        result = actual_page_action(page)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.warning(f"page_action callback error: {e}")
                    # 链内 tab 动作重绑了活动页：读回/落账/截图从此跟着新活动页走
                    # （`active_page` 只在链真的切过页时才在）。原页的 route/响应
                    # 钩子不属于新活动页——落账那一发诚实地不带导航响应（见下）。
                    _active = _pin_state.get("active_page") \
                        if isinstance(_pin_state, dict) else None
                    if _active is not None and _active is not page:
                        try:
                            if not _active.is_closed():
                                page = _active
                        except Exception:
                            pass
                    if isinstance(_pin_state, dict):
                        _chain_tabs = _pin_state.get("tab_pages")

                # Wait for selector
                if actual_wait_selector:
                    try:
                        waiter = page.locator(actual_wait_selector)
                        await waiter.first.wait_for(state=actual_wait_selector_state)
                        await self._wait_for_stability(page)
                    except Exception as e:
                        logger.warning(f"Wait for selector '{actual_wait_selector}' failed: {e}")

                # Human behavior simulation (stealthy only, if enabled)
                # Adds ~1.5-2.5s of mouse movement + scroll + dwell.
                # Passes Cloudflare v9 behavioral scoring.
                # attach 模式不做：那是在驱动用户自己的浏览器。
                # 驻留复用也不做：那是一段"刚到站"的动作轨迹，而这一页是上一发就来过
                # 的那一页 —— 再滚一遍只会让下一次截图站在不同的滚动位置上。
                # 链内切过页签也不做：轨迹属于"刚到站的那一页"，活动页是链里挑的。
                if self._is_stealthy and self._humanize and not self._cdp_attached \
                        and not reused and page is orig_page:
                    try:
                        await _simulate_human_behavior(page)
                    except Exception as e:
                        logger.debug(f"Human behavior simulation error: {e}")

                # SSRF 终检：主文档被拦下（页面把我们重定向/导航到内网），或落地 URL
                # 本身就是内网（服务端 3xx 之外的换址、rebinding 之类）—— 这一页的正文
                # 不能交出去。宁可报错，也不回传内网内容。
                blocked_main = any(is_main for _, is_main in ssrf_watch)
                landed = getattr(page, "url", "") or ""
                if blocked_main or _landed_url_refused(landed, url):
                    hosts = sorted({h for h, _ in ssrf_watch}) or [urlparse(landed).netloc or landed]
                    raise BrowserSSRFBlockedError(
                        "ssrf_blocked: the entry page sent the browser to an internal/"
                        f"loopback/metadata address ({', '.join(h for h in hosts if h)}); "
                        "refused to load it. The entry URL was validated, but a redirect or a "
                        "page-script request pointed inside - nothing was fetched from it."
                    )

                # 围栏终检（G33 host scope）。两段判据读的不是同一件事，顺序不能反：
                # 先问这只请求边界闸拦下了什么（那一跳根本没出去，落地地址自然还留在
                # 名册里 —— 浏览器停在一张错误页上），再问有没有漏网的真的落在了名册外。
                if fence_watch:
                    raise _fence_blocked_error(
                        sorted({h for h, _ in fence_watch}),
                        self._allowed_hosts, left_browser=False)
                _fence_out = _landed_host_outside_fence(landed, self._allowed_hosts)
                if _fence_out:
                    raise _fence_blocked_error([_fence_out], self._allowed_hosts,
                                               left_browser=True)

                # 登录探测（G35 check_login）：SSRF 终检之后——被拦的页面没有
                # 探测价值；构建响应之前——探测的是导航稳定后的最终页面。
                if probe_out is not None:
                    try:
                        raw = await page.evaluate(_LOGIN_PROBE_JS)
                        jar = await self._context.cookies(page.url)
                        names = [str(c.get("name")) for c in (jar or [])
                                 if isinstance(c, dict) and c.get("name")]
                        probe_out.clear()
                        probe_out.update(_login_verdict(
                            raw if isinstance(raw, dict) else {}, names))
                    except Exception as e:
                        logger.debug(f"login probe failed: {e}")

                # Build response
                from dhole_mcp.fetcher import response_from_browser_page
                if page is orig_page:
                    response = await response_from_browser_page(
                        page, first_response, final_response[0]
                    )
                else:
                    # 活动页是链里切过去的页签：first/final response 是**入口页**
                    # 那一笔导航的，安在它头上就是"状态行说 A、正文说 B"。tab.open
                    # 在开页时把这一页自己的导航响应挂在页上（`_dhole_nav_response`），
                    # 落账用它；没有（链内切到一枚没导航过的页）就诚实缺席。
                    response = await response_from_browser_page(
                        page, getattr(page, "_dhole_nav_response", None), None)
                # 网络账挂在抓取层的响应上（`Response.network`，slots 里的一格）：
                # 抓取层不知道线格式长什么样，转成 ResponseModel 字段是 server 层的事
                # （`_translate_response`）。干净的一页这里是 None。
                try:
                    response.network = _net_payload(net_watch, ssrf_watch)
                except Exception:
                    logger.debug("could not attach the network ledger", exc_info=True)

                # Memory cleanup: trigger Chrome's internal GC + cache drop
                # via CDP Memory.simulatePressureNotification. This releases
                # V8 heap, image caches, and discardable memory in all
                # processes. Lightweight (~5ms), prevents RAM creep across
                # many sequential fetches. Non-disruptive: "moderate" level
                # does not crash tabs or affect page content.
                # 驻留的那一页不做：它下一次要被站着用，而"丢掉可回收的图像缓存"对
                # 一张还要截图的页面是一次重取资源的机会 —— 那个机会属于站点，不属于人。
                if not _pin_lane:
                    try:
                        cdp = await page.context.new_cdp_session(page)
                        await cdp.send("Memory.simulatePressureNotification",
                                       {"level": "moderate"})
                        await cdp.detach()
                    except Exception:
                        pass

                # 只有成功路径存槽（异常路径的页面在 finally 里已经关了）。存下来的
                # 是这一页 + 这一发的两枚钩子 + 它当前那一笔导航的响应：下一次复用
                # 时这三样都要被换掉，而 `response` 是 `first_response` 的缺席兜底 ——
                # 这一发若因动作而导航了，留下的就该是那一笔的答案，不是上一发的。
                if _pin_lane:
                    self._pin = {"page": page,
                                 # 钩子只长在 fetch 自己导航的那一页上；活动页是
                                 # 链里切过去的页签时，原页的 route/response 不许
                                 # 冒充它的（下一次复用的摘钩会按缺席跳过）。
                                 "route": _route if page is orig_page else None,
                                 "listener": (_listener
                                              if page is orig_page else None),
                                 # 链切页的槽存**这一页自己**的导航响应（tab.open
                                 # 开页时挂在 `_dhole_nav_response` 上）：从前硬编码
                                 # None，下一次复用这条槽 `first_response=None` 而
                                 # 复用分支不导航 → "Failed to get response" 硬失败，
                                 # finally 再把整份驻留产业收掉 —— pin 机制要保住的
                                 # 那页（填了一半的表单/验证码）恰恰死在这一格上。
                                 "response": ((final_response[0] or first_response)
                                              if page is orig_page else
                                              getattr(page, "_dhole_nav_response",
                                                      None)),
                                 "stamp": time.monotonic()}
                    # 链交回来的页签表（原页之外的每一页）留进登记表，下一次钉住
                    # 的调用 `{"tab": N}` 直达；已关/已死的条目就地摘除。
                    kept: List[Dict[str, Any]] = []
                    for tp in (_chain_tabs or []):
                        try:
                            if tp is not page and not tp.is_closed():
                                if tp is orig_page and page is not orig_page:
                                    # 原页降级进登记表：它身上的本发钩子（route 里
                                    # 坐着本发的 ssrf_watch）随行摘掉 —— 摘不掉的
                                    # 钩子在复用那步"按缺席跳过"的摘钩里永远活着，
                                    # `_install_page_guards` 每轮再装一套，无上界。
                                    if _route is not None:
                                        await tp.unroute("**/*", _route)
                                    if _listener is not None:
                                        tp.remove_listener("response", _listener)
                                kept.append({"page": tp,
                                             "stamp": time.monotonic()})
                        except Exception:
                            pass
                    # 覆盖登记表前收掉没进 kept 的旧条目：不带 actions 的 pin 发
                    # _chain_tabs 为 None → kept 为空，直接覆盖会把上一任的页签变成
                    # 孤儿页（不在 _pin、不在 _pin_tabs、无人 close，renderer 活到
                    # 会话关闭）——与 finally 的"提前收"恰成一对，同一份产业两种
                    # 矛盾下场。活动页在 kept 之外但归新槽，不许收。
                    _kept_pages = {id(e["page"]) for e in kept}
                    for _old in self._pin_tabs:
                        try:
                            if (id(_old.get("page")) not in _kept_pages
                                    and _old.get("page") is not page
                                    and not _old["page"].is_closed()):
                                await self._pin_close(_old, "superseded")
                        except Exception:
                            pass
                    self._pin_tabs = kept
                    keep = True
                    if pin_out is not None:
                        pin_out["outcome"] = "reused" if reused else "pinned"
                elif pin and pin_out is not None:
                    # 调用方要驻留、这一条车道不给留（用户自己的 attach、或名册围栏）。
                    # 上层normally已经说过这句 —— 这里补一次，是为了"页没留下"这件事
                    # 永远有一个出处，而不是靠上游记得。
                    pin_out["dropped"] = "attach"
                return response

            except BrowserSSRFBlockedError:
                # 安全拒绝不是网络抖动：不重试，原样抛（消息里已经说明了原因）。
                raise
            except CDPHostNotAllowedError:
                # 同上：名册边界是用户给的授权范围，不是可重试的失败 —— 重试等于朝
                # 同一台名册外的主机再打三次，而每一次都带着已登录的 profile。
                raise
            except Exception as e:
                # 主文档被围栏掐在请求边界上时（D-31b），goto 报的就是那一次
                # net::ERR_BLOCKED_BY_CLIENT —— 这是拒绝，不是网络抖动，也不许重试；
                # 判据读在 SSRF 之前：这一跳是被名册拦下的，route 那侧根本没看见它。
                if fence_watch:
                    raise _fence_blocked_error(
                        sorted({h for h, _ in fence_watch}),
                        self._allowed_hosts, left_browser=False) from e
                # 主文档被 SSRF 守卫拦下时，goto 会以 ERR_ABORTED 之类的形式报出来 ——
                # 别把它当可重试的网络抖动（那会朝同一个内网目标再打三次），也别让
                # 最终错误丢掉"这是被拦下的内网跳转"这个信息。
                if any(is_main for _, is_main in ssrf_watch):
                    hosts = sorted({h for h, _ in ssrf_watch})
                    raise BrowserSSRFBlockedError(
                        "ssrf_blocked: the browser was redirected to an internal/loopback/"
                        f"metadata address ({', '.join(h for h in hosts if h)}) from {url}; "
                        "refused to load it."
                    ) from e
                if attempt < actual_retries - 1:
                    logger.warning(
                        f"Browser fetch attempt {attempt + 1} failed for {url}: {str(e)[:200]}. "
                        f"Retrying in {self._retry_delay}s..."
                    )
                    await asyncio.sleep(self._retry_delay)
                else:
                    # Classify the network error for agent-actionable diagnostics.
                    # Prepend a category tag so downstream (server.py _agent_hints)
                    # can identify the failure type without re-parsing.
                    from dhole_mcp.errors import classify_network_error
                    err_str = str(e)
                    category, _ = classify_network_error(err_str)
                    if category != "unknown":
                        raise RuntimeError(f"[{category}] {err_str[:150]}") from e
                    raise
            finally:
                # 收尾的顺序是实测出来的（2026-10-09 真 attach）：**先摘闸，再关页**。
                # 挂着 Fetch 拦截器去 `page.close()`，那一页会停在半路直到 Playwright 的
                # 默认超时 —— 拒绝本身 1.2 秒就判出来了，账却记在 20 秒之后，交出去的
                # 还是"这一档超时了"。摘掉监听器既让页面能正常关，也让下一次调用按它
                # 自己的名册判断（`Fetch.enable` 是页级的，留着就是一支按旧名册行事的钩子）。
                if fence is not None:
                    await fence.remove()
                # `keep=False`（没驻留、或这一发失败）：链开出来的额外页签连同
                # 登记表里任何残留一起收——没驻留就没主人。keep=True 时页签刚
                # 存进登记表（存槽那一步），这里一步都不碰。
                if not keep:
                    for tp in (_chain_tabs or []):
                        if tp is page:
                            continue
                        try:
                            if not tp.is_closed():
                                await tp.close()
                        except Exception:
                            pass
                    # 登记表只由真正接管了它的那一发收：slot 取走 = 接管。一个普通
                    # fetch（_pin_lane=False）或被 "target" 拒回的 pin 发（slot 归还）
                    # 收走别人还活着的页签，登记表与活着的槽位就互相矛盾 —— 之后
                    # 任何 `{"tab": N}` 直达都报 "tab N does not exist"。fresh 车道
                    # （slot=None 且本发自建）的 _pin_tabs 本就是空的，跳过无代价。
                    if slot is not None:
                        for s in self._pin_tabs:
                            await self._pin_close(s, "no-keep")
                        self._pin_tabs = []
                # `keep` 只在这一发真的把页面存进驻留槽时才立起来。除此之外（包括
                # 失败的那一发、包括调用方没要驻留的每一发）关页与今天逐字一致 ——
                # 默认行为一寸不动，是这一刀的合同。
                if page is not None and not keep:
                    try:
                        await page.close()
                    except Exception:
                        pass

        raise RuntimeError(f"Browser fetch failed for {url}")

    async def _wait_for_stability(self, page: Any) -> bool:
        """load + domcontentloaded, then the idle wait ONLY for a page that
        rendered nothing. Returns whether that 5s was paid.

        The cap and the silence are the existing implementation: 17.0 deleted the
        caller's switch, not the bound. An idle timeout here is not a failure - the
        page is what it is, and extraction reads whatever DOM survived.
        """
        for state in ("load", "domcontentloaded"):
            try:
                await page.wait_for_load_state(state)
            except Exception:
                pass
        try:
            reading = await page.evaluate(_UNRENDERED_PROBE_JS)
        except Exception:
            reading = None
        if not _looks_unrendered(reading):
            return False
        # 2.2 · 这一等最多 5 秒，而且只在"页面看着还没渲染"的时候花 —— 它是体验报告
        # 里那五个"≥5 秒静默窗口"之一，等待的人看到的却是同一段沉默。说的这句带上
        # 帽是多少：`SETTLE_IDLE_MS` 是这一等的属主，别在话里再抄一个 5。
        from dhole_mcp.context import report_stage
        await report_stage(
            f"the page rendered nothing yet - waiting up to "
            f"{SETTLE_IDLE_MS}ms for its network to go quiet")
        try:
            await page.wait_for_load_state("networkidle", timeout=SETTLE_IDLE_MS)
        except Exception:
            pass
        return True

    # ── Properties ────────────────────────────────────────────────

    @property
    def _is_stealthy(self) -> bool:
        """Override in subclasses."""
        return False

    @property
    def is_alive(self) -> bool:
        return self._is_alive

    @property
    def cdp_attached(self) -> bool:
        """True when this session attached to an existing CDP endpoint and is
        driving the endpoint's own default context — the one holding the user's
        login state. Response payloads surface this (cdp_attached=true) so every
        attach is visible to the caller, not just to the audit log."""
        return self._cdp_attached

    @property
    def launch_note(self) -> str:
        """这台浏览器**实际**是怎么起来的 —— 一句回执，只在看得见时说。

        批次 11 把它从"读旋钮"改成"问实例"：可见与否是一台正在跑的浏览器的属性，不是
        每次调用的属性（第一次把它起来的那次决定形态，后来的调用复用同一台）。读旋钮的
        那句会这样骗人：旋钮后来关掉了，热着的窗口还在屏幕上，回执却开始说后台。问实例
        就没有这个分叉。

        看不见（headless）是这条梯子的常态、也是档位表每一档写的值，所以返回空串：
        不说 == 不是可见的。
        """
        if self._headless:
            return ""
        if self._persistent_profile and self._profile_name:
            return (f"visible window, profile '{self._profile_name}' - logins typed "
                    "here survive later calls and this window is not closed by the "
                    "idle reaper; sys_state(clear=['<session id>']) hands it back")
        return ("visible window on a THROWAWAY profile - nothing you log in here "
                "survives this browser; store a `profile` with cred_defaults to keep "
                "the login")


class StealthyBrowser(BrowserSession):
    """Anti-detect stealthy browser session with Cloudflare solving.

    Uses stealth browser args (anti-fingerprinting, automation bypass),
    a realistic user agent, and optionally solves Cloudflare challenges.
    """

    @property
    def _is_stealthy(self) -> bool:
        return True


# ─── chromium 首用安装（17.0 B3） ──────────────────────────────────────────
#
# 在此之前，"零配置"那句话是空的：`check_browser_available()` 只问 patchright 能不能
# import，二进制不在就直接 launch 失败，然后把 `python -m playwright install chromium`
# 印在错误里让用户自己去敲。这一节把那句话做成真的。
#
# 四条规矩各自都有对应的测试，因为它们在代码评审里都长得"显然正确"：
#
# 1. 绝不在事件循环上装。一次下载几分钟，客户端拿到的是 -32001 REQUEST_TIMEOUT 而不是
#    答复 —— 这正是被删掉的 `paths.migrate_legacy_cache_dir` 那条 BLOCKING 注释要守的坑。
#    这次不写注释，写运行期跳闸（`ensure_chromium` 在循环线程上直接拒绝）。
# 2. 下载时长不吃调用预算：装完之后页面该拿到的时间要还是原来的时间。
# 3. 装不上要诚实拒绝并把命令给全：把"没装上"说成"这个站读不到"是最坏的一种错误分类。
# 4. 装的那一个必须正是起得来的那一个，判据只能有一份（`_launch_channel`）。第一版按
#    headless 挑了 119MB 的 shell，启动那行传的却是显式 channel='chromium'，于是下载
#    完成、复核通过、当场起不来 —— 这条是干净环境验收抓的。

#: 安装事实的落盘处：另一个进程（以及 `dhole doctor`）看的是它，不是本进程的内存。
_INSTALL_STATE_FILE = "browser_install.json"
#: 一次安装的总时限。要下的那一份（默认那条路是 193MB 的 chromium）在同一台机器上实测过
#: 100KB/s 与 2.4MB/s 两端 —— 差 24 倍，快的那端 80 秒，慢的那端 33 分钟，比这个上限还
#: 高。所以到点的意思不是"产品坏了"，而是"这条链路今天搬不动"，那句超时文案因此直接给
#: 换源的出口（npmmirror 镜像同版本同制品；干净环境验收走它，193MB 下载 + 起浏览器 +
#: 抓到一页共 82.2s）。它同时是 `_BudgetCredit` 放宽调用预算的上限（见 server 那侧）。
INSTALL_TIMEOUT_S = 1800.0

#: 一次失败之后多久之内不再重试安装。193MB 不该被每一个 web_act 重付一遍。
INSTALL_BACKOFF_S = 900.0

_install_lock = threading.Lock()
_install_async_lock: Optional[asyncio.Lock] = None
#: 退避到什么时候（进程内）。必须有模块级默认值：和 `_install_state` 同一个理由 ——
#: 一次都没装过的进程里读它会炸。
_install_retry_after: float = 0.0
#: 本进程的安装事实镜像：磁盘上没有状态文件时 `read_install_state()` 读它。没有这一行，
#: 一次都没装过的进程里那次读取是 NameError —— doctor 会把它报成"读不到"。
_install_state: Dict[str, Any] = {}
#: 上一次把"起不来"写进状态文件是什么时候。它在启动热路径上，不节流就等于每一次失败
#: 调用都开一次文件写。
_launch_record_at: float = 0.0
#: 期望修订号来自包内清单，读一次就够（pip 升级 patchright 才会变，而那会重启进程）。
_expected_revision_cache: Optional[int] = None
_revision_read = False

#: 装不上时给用户的命令。与 doctor、错误文案同源（install.py 那一处）。
_INSTALL_COMMAND_STR = browser_binary_command()


@dataclass
class InstallOutcome:
    """`ensure_chromium_async()` 的结果。

    ``waited_s`` 是**这次调用为等一个安装实际花掉的时间**，调用方用它把 deadline 往后
    挪 —— 下载是装机成本，不是这个页面的响应时间。已经就绪时是 0.0。
    """
    ok: bool
    detail: str
    waited_s: float = 0.0


def _package_dir() -> Path:
    """patchright 包的目录（browsers.json 在它里面）。"""
    import patchright
    return Path(patchright.__file__).resolve().parent


def _expected_chromium_revision() -> Optional[int]:
    """driver 会去找的那一版。

    磁盘上"有 chromium-XXXX"不等于"能启动"：pip 升级过 patchright 之后旧目录还在，
    新 driver 却只认清单里的 revision。照目录名猜会把一次必然失败的 launch 报成就绪。
    """
    global _expected_revision_cache, _revision_read
    if _revision_read:
        return _expected_revision_cache
    _revision_read = True
    try:
        import json as _json
        data = _json.loads((_package_dir() / "driver" / "package"
                            / "browsers.json").read_text(encoding="utf-8"))
        for entry in data.get("browsers", []):
            if entry.get("name") == "chromium":
                _expected_revision_cache = int(entry["revision"])
                break
    except Exception as exc:  # noqa: BLE001
        logger.debug("browsers.json unreadable: %r", exc)
        _expected_revision_cache = None
    return _expected_revision_cache


def _browsers_root() -> Path:
    """playwright/patchright 的浏览器落盘根。

    与 driver 同式：`PLAYWRIGHT_BROWSERS_PATH` 优先（`"0"` 是上游"落在包内
    `.local-browsers`"的约定），否则 win32 用 `_win_local_appdata()/ms-playwright`。
    返回值**永远是绝对路径**：一次 launch 用 driver 的解析、一次存在性检查用自己写的
    相对路径，两边各看一个目录 —— 2026-10-05 那次"安装 exit 0 却说二进制没装上"就是它。
    """
    override = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if override == "0":
        try:
            root = _package_dir() / "driver" / "package" / ".local-browsers"
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "PLAYWRIGHT_BROWSERS_PATH=0 means 'the patchright package's own "
                ".local-browsers', but patchright is not importable here: %r"
                % (exc,)) from None
    elif override:
        root = Path(override)
    elif sys.platform == "win32":
        root = _win_local_appdata() / "ms-playwright"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Caches" / "ms-playwright"
    else:
        root = Path.home() / ".cache" / "ms-playwright"
    if not root.is_absolute():
        # driver 也是这么展开相对 override 的（INIT_CWD || process.cwd()）。
        root = Path(os.environ.get("INIT_CWD") or os.getcwd()) / root
    return Path(os.path.normpath(str(root)))


#: 一次 launch 到底要哪个目录。判据**不是** headless，而是"有没有显式传 channel"：驱动
#: 源码 `getExecutableName()`（patchright driver/package/lib/coreBundle.js）的顺序是显式
#: channel 优先，只有 channel 缺省时 headless 才落到 shell。dhole 两条路都会传 channel
#: （`StealthyBrowser.start()` 里是 'chrome' 或 'chromium'），所以默认路径要的始终是完整的
#: `chromium-<rev>`。实测（patchright 1228 / win64，空 PLAYWRIGHT_BROWSERS_PATH 里让驱动
#: 自己报缺失路径）：channel='chromium' + headless=True → `chromium-1228\chrome-win64\
#: chrome.exe`；不传 channel + headless=True → `chromium_headless_shell-1228\…`；
#: channel='chrome' → 用户自己那个 Chrome，我们的目录根本不参与。
#: 按 headless 判就会去装那份启动根本不用的 shell：下载完成、复核通过、当场起不来。
#: 这条真发生过，就是干净环境验收的那一次红。
_ARTIFACT_DIRS = {"chromium": "chromium-{rev}",
                  "headless_shell": "chromium_headless_shell-{rev}"}

#: 每份多大（实测 win64 两个 zip 的 Content-Length 与本机解包后的目录：chromium
#: 193MB → 416MB，headless_shell 119MB → 270MB）。
INSTALL_DOWNLOAD_MB = {"chromium": 193, "headless_shell": 119}

#: `launch_artifact()` 的一个返回值：这次起的是用户自己的系统浏览器
#: （Chrome 或 Edge），没有东西要下载。值保持 "system-chrome" 不变：
#: updater 与干净环境 e2e 都按这个字符串读。
SYSTEM_CHROME = "system-chrome"


def _launch_channel(real_chrome: bool = False) -> str:
    """`StealthyBrowser.start()` 交给 launch 的那个 channel —— 只在这里判一次。

    安装侧与启动侧必须共用它：两边各写一遍同一个条件，就是"装了 119MB 却起不来"的来路。
    `real_chrome=True` 仍钉死 'chrome' —— 档位表（rungs.py）没有任何一档传它，
    见该文件"两档之外不留逃生门"那段；这里不为一个没人走的参数扩语义。
    点名哪一台浏览器**不是**旋钮也不是参数（批次 11 的裁决）：检测顺序
    chrome → msedge → 内置 chromium 已经保证"这台机器上有什么就用什么"，而让 agent
    点名下层可执行文件，等于把"指纹成套"这一件事（`anti_detect` 那一轴把 UA / TLS /
    插件配成一组）拆到两个地方各说一半。真要用 Edge 读它里面已登录的页面，走 attach ——
    那台浏览器本来就是用户自己起的。
    """
    return "chrome" if real_chrome else _detect_chrome_channel()


# 批次 11：``DHOLE_BROWSER_HEADED`` 与 ``DHOLE_BROWSER_CHANNEL`` 已注销（注册表里没有
# 名字，设了也不读 —— 与 ``DHOLE_BROWSER_PROFILE`` 同一处置）。它们管的两件事各自回了
# 自己该待的层：看得见与否是**一次调用**的问题（`web_act(headed=true)`），而"用哪一台
# 浏览器"根本不是一个需要谁来回答的问题（检测顺序已经保证这台机器上有什么就用什么）。


def launch_artifact(real_chrome: bool = False) -> str:
    """这次 launch 需要哪个落盘 artifact —— 用的就是 `_launch_channel()` 那一个判据。

    注意这里**没有** headless：dhole 两条路都显式传 channel（'chrome' / 'msedge'
    或 'chromium'），而驱动里显式 channel 优先于 headless 的 shell 选择。所以按
    headless 挑 artifact 是错的 —— 那个版本装完 119MB shell、复核通过、当场起不来
    （实测见 `_ARTIFACT_DIRS` 上方那段实测）。shell 那一支仍留在表里，是因为 doctor
    要说清"磁盘上有 shell 不等于能用"。
    """
    if _launch_channel(real_chrome) in ("chrome", "msedge"):
        # 用户自己的系统浏览器（Chrome 或 Edge）：没有任何东西要下载。
        return SYSTEM_CHROME
    return "chromium"


def _install_argv(artifact: str) -> list[str]:
    """只下这次真正要启动的那一份（实测：`--no-shell` 下 chromium-<rev> + ffmpeg + winldd，
    `--only-shell` 下 shell + 同样那两个小依赖）。

    首用等待是这款功能最贵的一刻，把不需要的那一份也拖下来，等于让每个干净机器多等一倍
    时间（默认路径上 193MB → 313MB）。
    """
    return [sys.executable, "-m", "patchright", "install", "chromium",
            "--only-shell" if artifact == "headless_shell" else "--no-shell"]


def _resolved_browsers_root() -> Optional[Path]:
    """`_browsers_root()`，或 None —— 给那些"只是想知道现状"的读者用。

    `_browsers_root()` 会抛（override 是 `"0"` 而 patchright 又没装时），而抛在一个
    **诊断**读者里等于把一次可解释的缺失误升级成一次崩溃：doctor 的那一行会写成
    "not inspectable"，调用方拿到的是一句 traceback。
    """
    try:
        return _browsers_root()
    except RuntimeError as exc:
        logger.debug("browsers root unresolvable: %s", exc)
        return None


def _installed_artifact(kind: str, revision: Optional[int]) -> Optional[Path]:
    """清单那一版的该当 artifact 在不在。半个安装（被打断的下载）不算存在。"""
    if revision is None:
        return None
    root = _resolved_browsers_root()
    if root is None:
        return None
    d = root / _ARTIFACT_DIRS[kind].format(rev=revision)
    # 上游用这个标记文件表示"解包完成"。没有它 = 下载中途被打断。
    if d.is_dir() and (d / "INSTALLATION_COMPLETE").exists():
        return d
    return None


def _system_browser_search_roots() -> list:
    """系统浏览器检测搜过哪些目录 —— 诊断用。

    为什么值得单独报：检测给出 chrome/msedge 时 `_browsers_root()` 根本不会被读到，
    所以"明明装了 Edge 却下载 193MB"这种故障，只看 `searched_in` 是看不出来的。POSIX
    没有固定的安装根（只走 PATH），那里回空表而不是回一个假装搜过的目录。
    """
    if sys.platform != "win32":
        return []
    return [str(p) for p in _win_browser_roots()]


def chromium_status(real_chrome: bool = False) -> dict:
    """这台机器现在能不能起浏览器。纯读，几毫秒，绝不下载。

    问的是 `launch_artifact()` 算出来的那一个目录，而它和启动共用 `_launch_channel()`。

    `searched_in` / `browser_path` / `reason` 是 2026-10-05 那次故障之后加的：调用方收到
    "not installed" 时，唯一能自查的问题是**它去哪儿看的**。`detail` 保持逐字节不变 ——
    它同时是数据源（`ensure_chromium()` 与 doctor 都按这个字符串读），改它等于把一条
    可读性修复变成又一次"两处各读一遍"的分叉。
    """
    artifact = launch_artifact(real_chrome)
    if artifact == SYSTEM_CHROME:
        # 系统浏览器（Chrome 或 Edge）承担两种模式，没有东西要下载。
        #
        # 这一支**不读** `_browsers_root()`，`searched_in` 因此是空的：这次决策没有看过
        # 那个目录，报一个没参与的路径就是给操作员一条把他带去改错 env 的线索。更重要的是
        # 它不能抛 —— `PLAYWRIGHT_BROWSERS_PATH=0` 而 patchright 又没装时，解析 bundled
        # 根会抛 RuntimeError，而这一次明明有一个能用的浏览器。
        channel = _launch_channel(real_chrome)
        return {"present": True, "source": "system-chrome", "artifact": artifact,
                "expected_revision": _expected_chromium_revision(),
                "path": "", "detail": f"system browser "
                                      f"({channel}) - no download needed",
                "reason": "system_browser", "searched_in": "",
                "browser_roots_searched": _system_browser_search_roots(),
                "browser_path": _system_browser_path(channel) or ""}
    resolved = _resolved_browsers_root()
    roots = _system_browser_search_roots()
    if resolved is None:
        # 第四态：不是"没装"，是"没地方可看"。把它并进 bundled_absent 会让调用方去跑
        # 一次必败的安装（`patchright install` 自己会写到它解析得出来的地方去）。
        return {"present": False, "source": "unknown", "artifact": artifact,
                "expected_revision": _expected_chromium_revision(), "path": "",
                "detail": "the browser cache root could not be resolved from this "
                          "process - check PLAYWRIGHT_BROWSERS_PATH",
                "reason": "root_unresolvable", "searched_in": "",
                "browser_roots_searched": roots, "browser_path": ""}
    searched = str(resolved)
    rev = _expected_chromium_revision()
    if rev is None:
        return {"present": False, "source": "unknown", "artifact": artifact,
                "expected_revision": None, "path": "",
                "detail": "patchright's browsers.json is unreadable - cannot tell "
                          "which chromium revision this driver launches",
                "reason": "manifest_unreadable", "searched_in": searched,
                "browser_roots_searched": roots,
                "browser_path": ""}
    found = _installed_artifact(artifact, rev)
    if found is not None:
        return {"present": True, "source": "bundled", "artifact": artifact,
                "expected_revision": rev, "path": str(found),
                "detail": found.name,
                "reason": "bundled_present", "searched_in": searched,
                "browser_roots_searched": roots,
                "browser_path": ""}
    return {"present": False, "source": "missing", "artifact": artifact,
            "expected_revision": rev, "path": "",
            "detail": f"{_ARTIFACT_DIRS[artifact].format(rev=rev)} not installed",
            "reason": "bundled_absent", "searched_in": searched,
            "browser_roots_searched": roots,
            "browser_path": ""}


def chromium_artifacts() -> dict:
    """两个 artifact 各自在不在。抓取只问它这次要用的那一个，doctor 要说全。"""
    rev = _expected_chromium_revision()
    out: Dict[str, Any] = {"revision": rev,
                           # 名字沿用 system_chrome（updater 与测试读这个键），
                           # 语义已是"系统 Chromium 系浏览器在场"——含 Edge。
                           "system_chrome": _detect_chrome_channel() in ("chrome", "msedge"),
                           "launch_channel": _detect_chrome_channel(),
                           "launch_needs": launch_artifact(),
                           # doctor 要说"在哪个目录里找的"：只报 artifact 名字的话，
                           # "没找到"和"找错了地方"在操作员眼里一模一样。
                           "searched_in": str(_resolved_browsers_root() or ""),
                           "browser_roots_searched": _system_browser_search_roots(),
                           "expected": {k: _ARTIFACT_DIRS[k].format(rev=rev)
                                        for k in _ARTIFACT_DIRS}}
    for _kind in _ARTIFACT_DIRS:
        found = _installed_artifact(_kind, rev)
        out[_kind] = found.name if found is not None else None
    return out


def _platform_can_install() -> tuple[bool, str]:
    """这台机器能不能自己装。False 时那句 reason 会原样进错误文案。"""
    if not _has_patchright():
        return False, ("patchright is not importable - the browser tier is absent "
                       "for real, install the extras first")
    if "com.termux" in os.environ.get("PREFIX", ""):
        return False, ("Termux ships no playwright/patchright wheel or driver, so "
                       "there is no chromium to install here")
    if sys.platform not in ("win32", "darwin", "linux"):
        return False, f"no chromium build for {sys.platform}"
    return True, ""


def _has_patchright() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("patchright") is not None
    except Exception:  # noqa: BLE001
        return False


def _install_state_path() -> str:
    from dhole_mcp import paths
    return str(paths.file(_INSTALL_STATE_FILE))


def _record_install(state: dict) -> None:
    """把安装事实写进 ~/.dhole/browser_install.json（另一个进程与 doctor 读它）。

    同一个文件里还有 `launch` 那一段，写者是 `record_launch_failure`。这里必须把它
    带着一起写，否则一次安装尝试会抹掉"盘上有、起不来"这条唯一的证据 —— 而那条恰恰
    是安装已经成功过之后才会出现的状态。
    """
    global _install_state
    _install_state = dict(state)
    try:
        from dhole_mcp import paths
        path = _install_state_path()
        paths.ensure_private_dir(Path(os.path.dirname(path)))
        payload = dict(state)
        prior = _read_install_file().get("launch")
        if isinstance(prior, dict):
            payload["launch"] = prior
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        paths.harden_file(path)
    except Exception as exc:  # noqa: BLE001 - 记录失败不能改变安装结论
        logger.debug("install state not written: %r", exc)


def _read_install_file() -> dict:
    """只读磁盘那一份，不回退到内存镜像（两段各自的写者都要看见对方的段落）。"""
    try:
        path = _install_state_path()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:  # noqa: BLE001
        pass
    return {}


def read_install_state() -> dict:
    """磁盘上的安装记录；没有就回退到本进程的内存镜像。"""
    data = _read_install_file()
    if data:
        return data
    return dict(_install_state)


#: "在场但起不来"的措辞窄表。只记这几类，因为它们都是**进程创建/权限**故障 —— 也就是
#: 二进制在场、driver 也找到了它，但操作系统不让它跑起来。反过来说，命中不了的
#: 首行（网络、站点慢、driver 内部超时）不该被写成一个浏览器层的判词：那会把一次
#: 偶发的等待说成"这台机器的浏览器坏了"。
_LAUNCH_FAILURE_MARKERS = (
    "cannot access executable",
    "access is denied",
    "拒绝访问",
    "0x5",
    "failed to launch",
    "executable doesn't exist",
)
#: 一次失败之后多久之内不再重复记录。60 秒足够覆盖一次调用里的多次重试，
#: 又不至于把几分钟后换了一种死法的故障盖掉。
LAUNCH_RECORD_THROTTLE_S = 60.0


def _is_launch_denied(text: str) -> bool:
    """首行是否命中窄表。只看首行：driver 的错误第一行是判词，后面是回栈与命令行。"""
    lines = (text or "").strip().splitlines()
    head = lines[0].lower() if lines else ""
    return any(marker.lower() in head for marker in _LAUNCH_FAILURE_MARKERS)


def record_launch_failure(exc: BaseException, real_chrome: bool = False) -> None:
    """把"盘上有、起不来"写进状态文件的 `launch` 段。绝不让它抛出。

    为什么要专门记这一态：`browser_binary_missing` 这个词覆盖不了它，而它恰好是本次
    事故之后最难自己发现的一种 —— 安装记录是 `done`，`present` 是真，调用方看到的
    却是一句起不来。另一个进程（`dhole -v`、`dhole --doctor`）想知道浏览器层究竟怎么
    样，只有这块磁盘可读。

    两个条件都在收窄误报空间：(i) 当时确实判为在场，否则那是安装问题，安装段已经记了；
    (ii) 首行命中 `_LAUNCH_FAILURE_MARKERS`，否则那是等待或站点问题，与二进制无关。
    """
    global _launch_record_at
    try:
        text = f"{type(exc).__name__}: {exc}"
        if not _is_launch_denied(text):
            return
        now = time.time()
        if _launch_record_at and now - _launch_record_at < LAUNCH_RECORD_THROTTLE_S:
            return
        status = chromium_status(real_chrome)
        if not status["present"]:
            return
        _launch_record_at = now
        from dhole_mcp import paths
        path = _install_state_path()
        paths.ensure_private_dir(Path(os.path.dirname(path)))
        payload = _read_install_file()
        payload["launch"] = {
            "status": "failed",
            "error": text.strip().splitlines()[0][:300],
            "artifact": str(status.get("path") or ""),
            "searched_in": str(status.get("searched_in")
                          or _resolved_browsers_root() or ""),
            "at": now,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        paths.harden_file(path)
    except Exception as inner:  # noqa: BLE001 - 诊断管路不能自己变成新故障面
        logger.debug("launch failure not recorded: %r", inner)


def recorded_launch_failure() -> dict:
    """磁盘上那段"起不来"的判词；没有就回空 dict。"""
    seg = _read_install_file().get("launch")
    return seg if isinstance(seg, dict) else {}


def reset_install_state() -> None:
    """清掉进程内的"装过了"状态。测试要能重来；生产不需要。

    异步那把锁一起丢：`asyncio.Lock` 第一次 acquire 就认死了当时那个 loop，留着它会让
    下一个事件循环拿到一把属于已关闭循环的锁。
    """
    global _install_state, _expected_revision_cache, _revision_read, _install_async_lock
    global _install_retry_after, _launch_record_at
    _install_state = {}
    _install_async_lock = None
    _expected_revision_cache = None
    _revision_read = False
    _install_retry_after = 0.0
    _launch_record_at = 0.0


def _loop_is_running_here() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def _arm_install_backoff() -> None:
    """这一次失败之后 INSTALL_BACKOFF_S 内不再重试安装（判据见 `ensure_chromium`）。"""
    global _install_retry_after
    _install_retry_after = time.time() + INSTALL_BACKOFF_S


def _artifact_elsewhere(wanted: str) -> str:
    """这一份 artifact 是否在**别的**根目录里躺着。找到就报那个路径，找不到空串。

    这是给"安装 exit 0、复查仍然说没装"那种自相矛盾准备的：两边各按自己的规则解析
    根目录时，文件真的装上了、检查真的看不到。不修好 `_browsers_root()` 之前，这句
    话是唯一的诊断线索；修好之后它是跳闸器。注意这里绝不把 present 翻真 ——
    driver 起的是它自己那一份，替它认一个别处的目录等于放行一次必败的 launch。
    """
    resolved = _resolved_browsers_root()
    candidates: list[Path] = [resolved] if resolved is not None else []
    if sys.platform == "win32":
        candidates.append(_win_local_appdata() / "ms-playwright")
    candidates.append(Path.home() / ".cache" / "ms-playwright")
    candidates.append(Path.home() / "Library" / "Caches" / "ms-playwright")
    candidates.append(Path.cwd() / "ms-playwright")
    try:
        candidates.append(_package_dir() / "driver" / "package" / ".local-browsers")
    except Exception:  # noqa: BLE001
        pass
    for root in candidates:
        d = root / wanted
        try:
            if d.is_dir() and (d / "INSTALLATION_COMPLETE").exists():
                return str(d)
        except OSError:
            continue
    return ""


def ensure_chromium(real_chrome: bool = False,
                    timeout_s: float = INSTALL_TIMEOUT_S) -> tuple[bool, str]:
    """需要就装，装完给结论。**同步阻塞**：只能在 worker 线程里调用。

    `real_chrome` 是调用方这次会不会传 channel='chrome'：它决定该装哪一个 artifact
    （`launch_artifact()`，与启动同源）。

    在事件循环线程上被直接调用时拒绝并说明为什么（见文件顶部第 1 条规矩）—— 这个坑
    有过一次真实事故，注释没守住，所以这次做成运行期跳闸。
    """
    global _install_retry_after
    if _loop_is_running_here():
        return False, ("ensure_chromium() refused: it blocks for minutes and this is "
                       "the event loop thread - await ensure_chromium_async() instead "
                       "(it runs this in a worker thread)")
    status = chromium_status(real_chrome)
    if status["present"]:
        return True, status["detail"]

    # 刚失败过就别再来一遍：一次失败的"装"可能是 193MB。今天没有负面缓存，
    # 于是浏览器层坏掉的那台机器上，每一次 web_act 都重新付一遍安装的钱（锁内，
    # 最多 INSTALL_TIMEOUT_S），而调用方看到的还是同一条错。退避只在本进程生效：
    # 跨进程抑制会把"刚在另一个终端手动装好了"的人晾在原地 —— 两扇门会各自漂。
    if _install_retry_after and time.time() < _install_retry_after:
        left = int(_install_retry_after - time.time())
        last = (_install_state.get("error") or "").strip()[:160]
        return False, (f"browser install failed {left}s ago and is not being retried "
                      f"for now (last error: {last or 'unknown'}; looked in "
                      f"{status.get('searched_in') or _resolved_browsers_root() or '(unresolved)'}) - "
                      f"run {_INSTALL_COMMAND_STR} yourself, or wait for the backoff "
                      f"to expire")

    can, why = _platform_can_install()
    if not can:
        return False, (f"cannot install the browser here - {why}; "
                       f"if this platform should support it, run: {_INSTALL_COMMAND_STR}")

    with _install_lock:
        # 双重检查：等锁的那段时间里另一个线程可能已经装完了。
        status = chromium_status(real_chrome)
        if status["present"]:
            return True, status["detail"]
        artifact = status["artifact"]
        rev = status["expected_revision"]
        # 想要的那一份直接从 artifact + revision 拼，不再从 detail 反解：那是一句
        # 给人看的话，被第二个读者当数据源用就是第二扇会漂的门。
        wanted = (_ARTIFACT_DIRS[artifact].format(rev=rev) if rev is not None
                  else str(status["detail"]).replace(" not installed", ""))
        cmd = _install_argv(artifact)
        mb = INSTALL_DOWNLOAD_MB[artifact]
        _record_install({"status": "installing", "revision": rev,
                         "command": _INSTALL_COMMAND_STR,
                         "started_at": time.time()})
        logger.info("Installing %s (revision %s) - ~%dMB, once",
                    artifact, rev, mb)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout_s)
        except subprocess.TimeoutExpired:
            # 慢链路是唯一现实的失败原因：这一份在同一台机器上实测过 100KB/s 与
            # 2.4MB/s 两个极端（差 24 倍）。到点是链路今天搬不动，不是产品坏了，所以
            # 这句话要说清"还要重下"并把上游换源的出口给出来 —— playwright 自己认
            # PLAYWRIGHT_DOWNLOAD_HOST，而它不是 dhole 的旋钮。
            detail = (f"chromium install did not finish within {int(timeout_s)}s "
                      f"(~{mb}MB for {wanted} from cdn.playwright.dev) - the "
                      "download does not resume, so a retry starts over. Run "
                      f"manually: {_INSTALL_COMMAND_STR}, or set "
                      "PLAYWRIGHT_DOWNLOAD_HOST to a faster mirror first")
            _record_install({"status": "failed", "revision": rev,
                             "command": _INSTALL_COMMAND_STR,
                             "error": f"timeout after {int(timeout_s)}s",
                             "finished_at": time.time()})
            _arm_install_backoff()
            return False, detail
        except Exception as exc:  # noqa: BLE001
            detail = (f"chromium install could not run: {type(exc).__name__}: {exc}; "
                      f"run manually: {_INSTALL_COMMAND_STR}")
            _record_install({"status": "failed", "revision": rev,
                             "command": _INSTALL_COMMAND_STR,
                             "error": str(exc)[:400], "finished_at": time.time()})
            _arm_install_backoff()
            return False, detail
        after = chromium_status(real_chrome)
        if proc.returncode == 0 and after["present"]:
            _install_retry_after = 0.0
            _record_install({"status": "done", "revision": rev,
                             "command": _INSTALL_COMMAND_STR,
                             "path": after["path"], "finished_at": time.time()})
            return True, f"installed {after['detail']}"
        tail = ((proc.stderr or proc.stdout or "") or "").strip()[-200:]
        # exit 0 而复查说没有：文件多半真装上去了，只是装在**另一个**根目录里。
        # 那必须写进这一句里，否则调用方唯一的线索是"再跑一遍 install"——
        # 而再跑一百遍也还是 exit 0、还是"still missing"。
        elsewhere = _artifact_elsewhere(wanted) if proc.returncode == 0 else ""
        looked = str(after.get("searched_in") or _resolved_browsers_root()
                    or "(unresolved)")
        _record_install({"status": "failed", "revision": rev,
                         "command": _INSTALL_COMMAND_STR,
                         "error": tail or f"exit {proc.returncode}",
                         "reason": "installed_elsewhere" if elsewhere else "verify_failed",
                         "found_at": elsewhere, "looked_in": looked,
                         "finished_at": time.time()})
        _arm_install_backoff()
        return False, (f"chromium install failed (exit {proc.returncode}) - "
                       f"{wanted} is still missing"
                       + (f": {tail}" if tail else "")
                       + (f" - the installer put it in {elsewhere} - dhole looks in "
                          f"{looked}" if elsewhere else
                          f" - dhole looked in {looked}")
                       + f" - run manually: {_INSTALL_COMMAND_STR}")


async def ensure_chromium_async(real_chrome: bool = False,
                                timeout_s: float = INSTALL_TIMEOUT_S) -> InstallOutcome:
    """事件循环安全的那一面：安装跑在 worker 线程，并发调用共享同一次下载。

    第二个调用者等的是同一把锁，所以它拿到的是已经装好的浏览器，而不是第二份几百 MB。
    """
    global _install_async_lock
    status = chromium_status(real_chrome)
    if status["present"]:
        return InstallOutcome(True, status["detail"], 0.0)
    started = time.perf_counter()
    if _install_async_lock is None:
        _install_async_lock = asyncio.Lock()
    async with _install_async_lock:
        ok, detail = await asyncio.to_thread(ensure_chromium, real_chrome, timeout_s)
    return InstallOutcome(ok, detail, time.perf_counter() - started)


#: 一次启动探针的总时限。真起一次浏览器在这台机器上是 3–5 秒量级，而探针的价值就在
#: "它到底起不起得来" —— 给到 25 秒是为了慢机器，同时不许把 doctor 挂死。
LAUNCH_PROBE_TIMEOUT_S = 25.0


async def launch_probe_async(timeout_s: float = LAUNCH_PROBE_TIMEOUT_S) -> dict:
    """真起一次浏览器、读一个 about:blank、关掉。不碰网络，永不触发下载。

    `chromium_status()` 只回答"盘上有没有"；这次事故教的是**"盘上有"与"起得来"是两件
    事**，而后一件事只有真的起一次才知道。探针起的形状与抓取那条路一致（`real_chrome`
    取档位表唯一会发的 False —— `rungs._anti_detect`），否则它验的不是用户会用到的那一台。

    调用方负责门控：这只在 `dhole --doctor` 里跑，`-v` 绝不起浏览器（一次启动是每一个
    宿主的税）。这里也不查 present —— "什么都没有就别下载"那条规矩由调用方守着。
    """
    started = time.perf_counter()
    session = StealthyBrowser(headless=True, real_chrome=False)
    try:
        await asyncio.wait_for(session.start(), timeout_s)
        page = await asyncio.wait_for(session._context.new_page(), timeout_s)
        try:
            await asyncio.wait_for(page.goto("about:blank"), timeout_s)
        finally:
            await page.close()
    except BaseException:
        # 超时是 CancelledError（BaseException），而 `close()` 在 `_is_alive` 为假时直接
        # 返回 —— 那会把 driver 的子进程留在原地。`start()` 自己失败时已经清过一遍，
        # 这里再清一次是幂等的。
        try:
            await session._cleanup_playwright()
        except Exception:  # noqa: BLE001
            pass
        raise
    await session.close()
    return {"channel": _launch_channel(False),
            "artifact": launch_artifact(False),
            "elapsed_s": round(time.perf_counter() - started, 2)}


# ─── Browser availability check ───────────────────────────────────────────────

_browser_available: Optional[bool] = None
_browser_import_error: Optional[str] = None


def check_browser_available() -> bool:
    """Check if browser deps (patchright) are importable. Cached."""
    global _browser_available, _browser_import_error
    if _browser_available is not None:
        return _browser_available
    try:
        import patchright  # noqa: F401
        _browser_available = True
        _browser_import_error = None
        return True
    except ImportError as e:
        _browser_available = False
        _browser_import_error = str(e)
        return False


def browser_import_error() -> Optional[str]:
    """Get the import error if browser deps are unavailable."""
    return _browser_import_error


def is_browser_available_cached() -> Optional[bool]:
    """Read the cached browser availability WITHOUT triggering an import.

    Returns True/False if check_browser_available() has been called at least
    once (by the prewarm thread). Returns None if not yet checked.

    This is safe to call on the asyncio event loop: it never blocks.
    Use check_browser_available() for the first check (from a worker thread).
    """
    return _browser_available
