"""Dhole 自己的 HTTP 取回器与 Response 类。

用基于 primp 的直接实现取代 scrapling 的 FetcherSession（后者包着 curl_cffi）。
primp 提供与 curl_cffi 相同的 TLS 冒充（JA3/JA4 指纹、HTTP/2 设置随机化），但
API 更干净，除已装的依赖外不需要额外的 C 依赖。

Response 类模仿 scrapling 的 Response 接口：.status、.url、.headers、.body（字节）、
.encoding、.content（解码后的 HTML）、.css()（经 lxml 的 CSS 选择器）、.reason、
.cookies。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Union
from urllib.parse import urljoin, urlparse

import primp

from dhole_mcp.charset import misdecode_score as _misdecode_score
from dhole_mcp.extractor import _is_framed_document_content_type
from dhole_mcp import sessions
from dhole_mcp.security import SecurityError, redact_api_key

logger = logging.getLogger("dhole_mcp.fetcher")

# ─── 回环目标不走代理（G31）───────────────────────────────────────────────────
#
# primp（reqwest 底座）、httpx（trust_env 默认开）和浏览器都会读环境里的
# HTTPS_PROXY / HTTP_PROXY / ALL_PROXY。系统代理或 fake-IP TUN 在场时，抓
# 127.0.0.1 / localhost 的请求会被交给代理——而代理**不可能**到达调用方自己的
# 回环，实测只回 502（本机活着的 dev 服务、无监听的端口，一律同一句
# "server returned error status"）。代理 reach 不到别人的回环是定义使然，所以把
# 回环排除出代理是**无条件安全**的：已写的 NO_PROXY 条目原样保留并合并，只补缺。
# 条目里没有 `[::1]`（带括号写法）：httpx 的 trust_env 把 NO_PROXY 条目按
# host:port 拆，`[::1]` 被拆出端口 `1]` 直接 InvalidURL，**毒化进程里每一个
# trust_env 的 httpx.Client**（实测）。裸 `::1` 对 httpx 与 reqwest 都安全。
_LOOPBACK_NO_PROXY_ENTRIES = ("localhost", "127.0.0.1", "::1")


def _ensure_loopback_no_proxy() -> None:
    """把回环主机并进 NO_PROXY，任何档位都不代理它们。永不抛异常。"""
    try:
        existing: list[str] = []
        for name in ("NO_PROXY", "no_proxy"):
            for part in os.environ.get(name, "").split(","):
                part = part.strip()
                if part and part.lower() not in (p.lower() for p in existing):
                    existing.append(part)
        merged = list(existing)
        for entry in _LOOPBACK_NO_PROXY_ENTRIES:
            if entry.lower() not in (p.lower() for p in merged):
                merged.append(entry)
        if merged != existing:
            value = ",".join(merged)
            # 两种拼写都写：POSIX 下是两个变量（有的工具只认小写），Windows 下
            # 是同一个（大小写不敏感，同值覆写无副作用）。
            os.environ["NO_PROXY"] = value
            os.environ["no_proxy"] = value
    except Exception:  # pragma: no cover - 环境变量修改永远不许弄坏启动
        pass


_ensure_loopback_no_proxy()


def _no_proxy_entries() -> list[str]:
    """各档位会看到的全部 NO_PROXY 条目，两种拼写合并后的样子。"""
    out: list[str] = []
    for name in ("NO_PROXY", "no_proxy"):
        for part in os.environ.get(name, "").split(","):
            part = part.strip().lower().lstrip(".")
            if part and part not in out:
                out.append(part)
    return out


def system_proxy_for(url: str) -> str:
    """这台机器会把 ``url`` 交给哪个代理；直连时返回 ""。

    读的是各档位继承的同一处两源（`urllib.getproxies()` 覆盖环境变量，Windows 上还有
    注册表里的系统代理），并遵守各档位认的同一套豁免（`NO_PROXY`，加上 G31 无条件的
    回环排除）。这是**地理**，不是判决：它回答「dhole 与那台主机之间有没有中间人」，
    而 5xx 归因只允许建立在这个事实上。永不抛异常。
    """
    try:
        import urllib.request as _urllib_req
        proxies = dict(_urllib_req.getproxies_environment())
        # `getproxies()` 的口径是「环境非空就用环境，否则用注册表」，而单有 `no_proxy`
        # 也算非空 —— 它解析成一颗名叫 "no" 的键。G31 让本进程始终设着 NO_PROXY，于是
        # 那条路会在注册表明明写着代理的机器上答「没有代理」（2026-10-14 实测：
        # getproxies() == {'no': 'localhost,127.0.0.1,::1'}，而注册表里是
        # http://127.0.0.1:7890）。环境里有真代理就用环境的，否则读注册表。
        if not any(v for k, v in proxies.items() if k != "no"):
            registry = getattr(_urllib_req, "getproxies_registry", None)
            if registry is not None:
                try:
                    for k, v in registry().items():
                        if k != "no" and not proxies.get(k):
                            proxies[k] = v
                except Exception:
                    pass
    except Exception:
        return ""
    if not proxies:
        return ""
    try:
        parts = urlparse(url or "")
        host = (parts.hostname or "").lower().strip("[]")
        scheme = (parts.scheme or "http").lower()
    except Exception:
        return ""
    if not host:
        return ""
    loopback = host in _LOOPBACK_NO_PROXY_ENTRIES
    if not loopback:
        # G31 的豁免判据与 SSRF 门同一套判定（security 的客户端视图 + 记法归一）：
        # 只认三个字面串的话，allow_private 放行的 127.0.0.2 / 0x7f000002 这类
        # 写法仍被当成"该走代理"—— 代理同样 reach 不到那些回环地址，502 会被
        # 5xx 归因误读成站点故障。env 层的 NO_PROXY 条目维持三个字面拼写不变
        # （全 127/8 逐条写不现实，[::1] 的拆分毒化问题见上）。
        try:
            from dhole_mcp.security import _client_view_host, _normalize_ip_notation
            import ipaddress as _ipaddr
            _view = _client_view_host(host).lower()
            try:
                _addr = _ipaddr.ip_address(_view)
            except ValueError:
                _norm = _normalize_ip_notation(_view)
                _addr = _ipaddr.ip_address(_norm) if _norm else None
            loopback = (_view in _LOOPBACK_NO_PROXY_ENTRIES
                        or (_addr is not None and _addr.is_loopback))
        except Exception:
            loopback = False
    if loopback:
        return ""
    bypass = _no_proxy_entries()
    if "*" in bypass:
        return ""
    for entry in bypass:
        if host == entry or host.endswith("." + entry):
            return ""
    return proxies.get(scheme) or proxies.get("all") or proxies.get("http") or ""


def _urljoin(base: str, location: str) -> str:
    """解析重定向 Location（相对/绝对）为完整 URL。"""
    return urljoin(base, location)


# ─── Response ────────────────────────────────────────────────────────────────

class ElementWrapper:
    """包一颗 lxml 元素，模仿 scrapling 的 Selector 元素接口。

    暴露 ._root（lxml 节点），让 trafilatura_extractor 的 CSS 选择器收窄路径
    （tostring(el._root)）继续可用。
    """

    __slots__ = ("_root", "_url")

    def __init__(self, root, url: str = ""):
        self._root = root
        self._url = url

    @property
    def url(self) -> str:
        return self._url

    def css(self, selector: str) -> List["ElementWrapper"]:
        """在这颗元素的子树上做 CSS 选择器查询。"""
        from lxml.cssselect import CSSSelector
        try:
            sel = CSSSelector(selector)
            matches = sel(self._root)
            return [ElementWrapper(m, self._url) for m in matches]
        except Exception:
            return []

    def text_content(self) -> str:
        """这颗元素**连同全部后代**的文字（与 lxml 原生的 .text_content() 一致）。

        从前那句 `.text or ""` 只回元素自己开头的那一个文本节点，嵌套子元素被悄悄丢掉了。
        """
        return "".join(self._root.itertext())


class Response:
    """带 CSS 选择器支持的 HTTP 响应。

    模仿 server.py 各处在用的 scrapling Response 接口：
        .status (int)          - HTTP 状态码
        .url (str)             - 跟随重定向后的最终 URL
        .headers (dict)        - 响应头
        .body (bytes)          - 原始响应体
        .encoding (str)        - 检测到的编码
        .content (str)         - 解码后的正文（惰性）
        .css(selector) -> list - CSS 选择器查询
        .reason (str)          - 状态短语
        .cookies (dict)        - 响应里的 cookie
    """

    __slots__ = (
        "_status", "_url", "_headers", "_body", "_encoding",
        "_reason", "_cookies", "_root", "_content_cached", "_meta_refresh",
        # 浏览器档的网络账：只有浏览器那一档会写它（server 层再抄进
        # ResponseModel.network）。放进 slots 是因为 Response 用 slots ——
        # 没有这一格时 setattr 会抛 AttributeError，而那正是"收集器装好了却
        # 传不出去"的失败形态。公开名而不是 property：一个 getter+setter 同名对
        # 是合法 Python，但 `test_server_10` 的 AST 守卫会把它读成"后者遮蔽前者"，
        # 而那条守卫护的是真缺陷 —— 不为一个属性去放宽它。
        "network",
    )

    def __init__(
        self,
        url: str,
        body: bytes,
        status: int,
        headers: Optional[Dict[str, str]] = None,
        encoding: str = "utf-8",
        reason: str = "",
        cookies: Optional[Dict[str, str]] = None,
        meta_refresh: Optional[Dict[str, Any]] = None,
    ):
        self._url = url
        self._body = body if isinstance(body, bytes) else (body or b"")
        self._status = status
        self._headers = headers or {}
        self._encoding = encoding or "utf-8"
        self._reason = reason or ""
        self._cookies = cookies or {}
        self._meta_refresh = meta_refresh
        self.network: Optional[Dict[str, Any]] = None
        self._root: Any = None  # 惰性构建的 lxml 树
        self._content_cached: Optional[str] = None

    # ── 与 scrapling 接口对齐的属性 ────────────────────────────────

    @property
    def status(self) -> int:
        return self._status

    @property
    def url(self) -> str:
        return self._url

    @property
    def headers(self) -> Dict[str, str]:
        return self._headers

    @property
    def body(self) -> bytes:
        return self._body

    @property
    def encoding(self) -> str:
        """声明的字符集；头部没说时问页面自己的 <meta>（R16-001）。

        静态站常常只发一个裸 `text/html`，编码只写在 `<meta>` 里——浏览器读那里，
        读页面的这一层也得读那里，否则 GBK 页会被当成二进制、Latin-1 重音会成
        U+FFFD。头部**明确**声明 charset 时仍以头部为准。
        """
        content_type = ""
        for key, value in self._headers.items():
            if key.lower() == "content-type":
                content_type = value or ""
                break
        if "charset=" in content_type.lower():
            return self._encoding
        sniffed = sniff_html_charset(self._body)
        return sniffed or self._encoding

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def cookies(self) -> Dict[str, str]:
        return self._cookies

    @property
    def meta_refresh(self) -> Optional[Dict[str, Any]]:
        """被 ``<meta http-equiv=refresh>`` 挪动过时设置（G29）：``{from, delay_s, hops}``。"""
        return self._meta_refresh

    @property
    def content(self) -> str:
        """解码后的正文（惰性、带缓存）。

        整条读取路径只有一套解码口径：声明的字符集加上字节的一票（见
        _decode_html_bytes）—— 实测有服务器声明 ISO-8859-1 或 us-ascii，实际发的是
        UTF-8。在这里重新解码的消费者就是同一个问题的第二位裁判，而那正是 schema
        那条路出现乱码的原因（BUG-1）。
        """
        if self._content_cached is None:
            self._content_cached = _decode_html_bytes(self._body, self.encoding)
        return self._content_cached

    @property
    def html_content(self) -> str:
        """.content 的别名（兼容 scrapling）。"""
        return self.content

    # ── CSS 选择器支持 ─────────────────────────────────────────────

    def _ensure_parsed(self):
        """把正文惰性解析成 lxml 树。"""
        if self._root is not None:
            return
        if not self._body:
            from lxml import etree
            self._root = etree.fromstring(b"<html></html>")
            return
        try:
            from io import BytesIO
            from lxml import html as lxml_html
            # 用 BytesIO 走 lxml.html.parse()，换跨平台一致。lxml.html.fromstring()
            # 在不同平台上返回不同的根元素（例如 ubuntu CI 返回第一个子元素，而不是
            # <html> 根），这会弄坏 CSSSelector（它只搜后代，不搜根）。doc_parse()
            # 始终返回以 <html> 为根的完整树。
            #
            # 喂 self.content 重新编码后的字节，并把解析器钉在那个字符集上：直接把原始
            # body 交给 libxml2，它会用文档的 <meta charset>（没有时用宿主 locale）压过
            # 我们已经采纳的响应头 —— U+2019 变成 "â" 就是这么来的（BUG-1）。特意用
            # lxml.html 的解析器而不是 lxml.etree 的：etree 那个建的是裸 _Element 节点，
            # 没有 text_content()。
            tree = lxml_html.parse(
                BytesIO(self.content.encode("utf-8", errors="replace")),
                parser=lxml_html.HTMLParser(encoding="utf-8"))
            self._root = tree.getroot()
            if self._root is None:
                raise ValueError("parse returned empty tree")
        except Exception:
            try:
                from lxml import html as lxml_html
                self._root = lxml_html.fromstring(self.content)
            except Exception:
                from lxml import etree
                self._root = etree.fromstring(b"<html></html>")

    def css(self, selector: str) -> List[ElementWrapper]:
        """CSS 选择器查询。返回 ElementWrapper 列表。

        每个 ElementWrapper 都带 ._root（lxml 元素），调用方可以
        tostring(el._root, encoding='unicode') 拿到那段 HTML。
        """
        self._ensure_parsed()
        if self._root is None:
            return []
        from lxml.cssselect import CSSSelector
        sel = CSSSelector(selector)
        matches = sel(self._root)
        return [ElementWrapper(m, self._url) for m in matches]


# ─── 浏览器响应构造 ───────────────────────────────────────────────────────────

_SHELL_HEADS = (b"<html", b"<!doctype", b"<iframe", b"<embed", b"<body")


def looks_like_rendered_shell(body: bytes) -> bool:
    """这几列字节是一份**渲染出来的文档**，不是被请求的那个文件。

    交给字节答，不交给档位答：`content-type` 说 image/png 而正文以 `<!doctype` 起头，
    这种自相矛盾只可能有一种来路 —— Chrome 给那份文件套了它自己的框。
    """
    head = (body or b"")[:256].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return head.startswith(_SHELL_HEADS)


async def _original_bytes(page: Any, resp: Any, framed: bytes) -> bytes:
    """把相框换成原件。拿不到就原样交回相框 —— 不假装成功，也不空手。

    两层各问一次，因为实测它们不总是同一个东西：图片与 JSON/XML 的原件挂在网络响应上
    （`https://httpbin.org/image/png` → 8,090 字节真 PNG），而 **PDF 的那一层被 Chrome
    换成了它自己的查看器文档** —— 实测 `rfc2616.pdf` 走 stealthy 档，content-type 说
    application/pdf，`resp.body()` 交回的却是 601 字节以 `<!doctype html>` 起头的框，
    而同一份 URL 走这个上下文自己的请求通道是 550,558 字节 `%PDF-1.1`。

    第二发只问 `resp.url` 这一个地址，且 `max_redirects=0`：CDP 那只主机围栏只看
    Document 请求，APIRequestContext 这一发它看不见，所以这一跳不许替我们换地址
    （实测：会 301 的那一发在 `max_redirects=0` 下只回 301 本身，不跟）。这个 URL
    刚刚过了导航那一头的 SSRF 与名册终检，重发它不放宽任何东西。
    """
    try:
        raw = await resp.body()
    except Exception:
        raw = b""
    if raw and not looks_like_rendered_shell(raw):
        return raw
    try:
        api = await page.context.request.get(resp.url, max_redirects=0)
        if not 200 <= api.status < 300:
            return raw or framed
        body = await api.body()
    except Exception:
        return raw or framed
    return body or raw or framed


async def response_from_browser_page(
    page: Any,
    first_response: Any,
    final_response: Optional[Any],
) -> Response:
    """从 patchright/playwright 的 page + response 对象造一份 Response。

    对标 scrapling 的 ResponseFactory.from_async_playwright_response，但不带
    Selector/解析器的开销。取出页面内容（含 Windows 上 page.content() 的重试绕法）、
    响应头、状态码等。
    """
    # 取页面内容，带 Windows 重试绕法
    # （Playwright 在 Windows 上 page.content() 有已知问题：
    #  https://github.com/microsoft/playwright/issues/16108）
    page_content = b""
    for _ in range(20):
        try:
            html_str = await page.content()
            if html_str:
                page_content = html_str.encode("utf-8")
                break
        except Exception:
            await page.wait_for_timeout(500)

    # 确定最终响应（没有最终响应时退回第一个）
    resp = final_response if final_response else first_response
    if resp is None:
        return Response(
            url=page.url if page else "",
            body=page_content,
            status=0,
            headers={},
            encoding="utf-8",
        )

    # 提取响应头
    try:
        headers = await resp.all_headers()
    except Exception:
        headers = {}

    # 从 content-type 提取编码
    ct = headers.get("content-type", "")
    encoding = _extract_encoding(ct)

    # 浏览器不渲染数据文档，它给数据文档套一个自己的相框。实测
    # https://httpbin.org/json 走 stealthy 档交回的是 616B 的 Chrome JSON viewer，
    # 连原文里的 "<em>" 都被替成了 "&lt;em&gt;" —— 那不再是这份 JSON；XML 那一族更贵
    # （428KB 的 sitemap 换成 2,335,300 个字符的 xml-viewer）。自动升级早就为同一个
    # 理由拒绝走浏览器（quality._never_escalate），被 force_fetcher/cdp 按到这一档的
    # 时候，取原件，别外交框。
    # 同一处的射程在 D-27 扩到图片与 PDF：那张判据（哪些类型 Chrome 只套框）写在
    # extractor._is_framed_document_content_type，图片那一支的实测是 403 字节的框喂给
    # OCR，报的是"认不出这张图"。D-41 把"原件挂在哪一层"这一问改成了两层 —— PDF 的那一份
    # 网络响应里也没有原件，只有 Chrome 的查看器文档。
    if page_content and _is_framed_document_content_type(ct):
        page_content = await _original_bytes(page, resp, page_content)

    # 提取状态码
    status = resp.status

    # 从上下文提取 cookie
    cookies: Dict[str, str] = {}
    try:
        cookie_list = await page.context.cookies()
        for c in cookie_list:
            cookies[c.get("name", "")] = c.get("value", "")
    except Exception:
        pass

    return Response(
        url=page.url if page else (resp.url if hasattr(resp, "url") else ""),
        body=page_content,
        status=status,
        headers=headers,
        encoding=encoding,
        cookies=cookies,
    )


def _extract_encoding(content_type: str) -> str:
    """从 content-type 头里提取字符集。"""
    if not content_type:
        return "utf-8"
    for part in content_type.split(";"):
        part = part.strip().lower()
        if part.startswith("charset="):
            return part.split("=", 1)[1].strip().strip('"').strip("'")
    return "utf-8"


# ─── 解码：声明的字符集撒谎撒得足够频繁 ─────────────────────────────────────
#
# 在一次约 60 次调用的上限测试里实测：页面声明 ISO-8859-1（Apache 的默认值），发的却是
# UTF-8，于是 "you’ve · café" 回来成 "youâ€™ve Â· cafÃ©"；一次 us-ascii 声明把同一批
# 字节变成一串 U+FFFD（"you???ve"）。页面自己的 <meta charset="utf-8"> 被忽略，因为
# 响应头无条件赢了。
#
# 不去猜字符集，而是解两遍、留更干净的那份：把 UTF-8 按单字节错误解码，留下的要么是
# 替换字符，要么是下面这些 Â/Ã 前缀；而一份真正的非 UTF-8 正文不会被换掉，因为把**它**
# 按 UTF-8 解会产生多得多的替换字符。
#
# 标记与评分住在 dhole_mcp.charset（顶部导入），与 parse.py 的本地文件那条路共用，
# 于是「这看起来像乱码」的两份定义不会各漂各的。


_HTML_META_BYTES_RE = re.compile(rb"<meta\b[^>]*>", re.IGNORECASE)
_CHARSET_ATTR_BYTES_RE = re.compile(
    rb"charset\s*=\s*[\"']?\s*([A-Za-z0-9_.:-]+)", re.IGNORECASE)


def sniff_html_charset(body: bytes) -> str:
    """页面自己在 <meta> 里声明的字符集，没有则返回 ""。"""
    if not body:
        return ""
    for tag in _HTML_META_BYTES_RE.findall(body[:16384]):
        match = _CHARSET_ATTR_BYTES_RE.search(tag)
        if match:
            return match.group(1).decode("ascii", "ignore").strip().lower()
    return ""


def _is_utf8_alias(enc: str) -> bool:
    return enc.replace("-", "").replace("_", "").lower() in ("utf8", "utf8sig")


def _decode_html_bytes(body: bytes, declared: str) -> str:
    """解码响应体；声明的字符集被字节本身否定时退回 UTF-8。

    永不抛异常。两种情况下调用方都拿到 str。
    """
    enc = (declared or "").strip() or "utf-8"
    if _is_utf8_alias(enc):
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError:
            # 字节不是 UTF-8：先问页面自己的 <meta>（R16-001）。静态站常常只在那里
            # 声明编码，HTTP 头只有一个裸 text/html。
            meta = sniff_html_charset(body)
            if meta and not _is_utf8_alias(meta):
                try:
                    return body.decode(meta, errors="replace")
                except (LookupError, UnicodeError):
                    pass
            return body.decode("utf-8", errors="replace")
    try:
        text = body.decode(enc, errors="replace")
    except (LookupError, UnicodeError):
        # 头里写着未知/胡说的字符集名。
        return body.decode("utf-8", errors="replace")
    try:
        utf8_text = body.decode("utf-8", errors="replace")
    except UnicodeError:  # pragma: no cover - 带 replace 的解码不会抛异常
        return text
    return utf8_text if _misdecode_score(utf8_text) < _misdecode_score(text) else text


# ─── HTTP 取回器（基于 primp） ─────────────────────────────────────────────────

# 点名一个主体的头。它们只去调用方点名的源，不去别处（见 _hop_headers）。
_CREDENTIAL_HEADERS = ("cookie", "authorization", "proxy-authorization")

# 对 POST 的 301/302/303 答复按「不带 body 的 GET」跟随，浏览器与 curl 就是这么做的
# （303 自己写着；301/302 同样读法，因为没人会发布一个在这里保留 body 的严格规范客户端）。
# 307/308 保留方法与 body —— 目标在拨号之前仍会重新校验，因为重定向是离开的那台服务器
# 选的，不是我们。
_METHOD_CONVERTERS = (301, 302, 303)

# 描述请求体的头；body 走了它们也走。
_BODY_HEADERS = ("content-type", "content-length")

# 超时后可以再问一遍的方法：重发这些不会让服务器停在不同的状态，所以重试是重试，不是
# 重复提交。RFC 9110 也说 PUT 与 DELETE 幂等，但那是关于端点的断言 —— 「第二次会收敛到
# 第一次」正是一个做得不好的表单端点会打破的承诺 —— 而这里没有任何东西能检查它。
# web_request 告诉调用方写只离开一次，所以这道闸问的是「这个动词能不能改变状态」，不是
# 「规范说这个动词会不会收敛」。
_RETRY_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _host_of(url: str) -> str:
    """URL 的小写、无端口主机名（没有时返回 ''）。"""
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return ""
    return host.split(":", 1)[0]


def _drop_header_ci(headers: Dict[str, str], name: str) -> None:
    """删掉一颗头，不管它当初按哪种大小写存进去。"""
    for key in [k for k in headers if k.lower() == name]:
        del headers[key]


# ─── <meta http-equiv=refresh>（G29） ─────────────────────────────────────────
#
# 写进文档里、而不是 Location 头里的重定向。现实标记比规范里的例子更乱，所以下面这个
# 解析器按实测页面出现过的四种情况放宽：属性值不加引号（``http-equiv=REFRESH``）、
# 大写 ``URL=``、单引号、分号两侧的空白。漏掉这些的抽取器会让调用方只拿到一张 300
# 字符的壳页，还得不到「正文在别处」的提示。
_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_META_ATTR_RE = re.compile(
    r"([a-z_:][-\w:]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'>`]+))",
    re.IGNORECASE,
)


def _meta_refresh_target(resp, current: str) -> tuple[str, float]:
    """从这份 HTML 正文读出 ``(绝对目标, 延迟秒数)``；没有则 ``("", 0)``。

    只认第一个 refresh 标签，浏览器大致也是这么做的：一份放了多个的页面，是在请访问者
    做一个它已经替他做过的选择。
    """
    try:
        headers = dict(resp.headers) if hasattr(resp, "headers") else {}
        ct = ""
        for k, v in headers.items():
            if k.lower() == "content-type":
                ct = v
                break
        if "html" not in ct.lower():
            return "", 0.0
        body = resp.content if hasattr(resp, "content") else b""
        if not isinstance(body, bytes) or not body:
            return "", 0.0
        # 标签住在 <head> 里；解开前 16 KB 就能找到它，不必为一次字符串匹配把一份
        # 5 MB 的文档整份过一遍解码器。
        head = _decode_html_bytes(body[:16384], (ct.split("charset=")[-1] if "charset=" in ct else "utf-8"))
        for tag in _META_TAG_RE.findall(head):
            attrs: Dict[str, str] = {}
            for m in _META_ATTR_RE.finditer(tag):
                attrs[m.group(1).lower()] = (m.group(2) or m.group(3) or m.group(4) or "").strip()
            if attrs.get("http-equiv", "").lower() != "refresh":
                continue
            content = attrs.get("content", "")
            if not content:
                continue
            delay_s = 0.0
            first, _, rest = content.partition(";")
            try:
                delay_s = float(first.strip() or 0)
            except ValueError:
                rest = content  # 没有延迟段：光写 "url=..." 不合法，但线上确实有
            key, sep, target = rest.partition("=")
            target = target.strip().strip("\"'").strip()
            if not sep or key.strip().lower() != "url" or not target:
                continue
            if target.lower().startswith(("javascript:", "data:", "about:")):
                return "", delay_s
            absolute = _urljoin(current, target)
            if absolute.split("#")[0] == current.split("#")[0]:
                return "", delay_s  # 自我刷新是重载，不是重定向
            return absolute, delay_s
        return "", 0.0
    except Exception:
        return "", 0.0


def _resp_cookies(resp) -> Dict[str, str]:
    """把 primp 响应的 cookie 归一成 ``{名字: 值}``。

    primp 交回的是一个**映射**（``{'a': '1'}``），而 16.0 之前这里的循环
    （`for c in resp.cookies`，期待一串 dict 或 cookie 对象）遍历到的是**键** —— 字符串，
    两个分支都不匹配 —— 于是每次都回 {}。这就是每一封响应都报「没有 cookie」、
    ``Set-Cookie`` 对调用方不可见的原因：下游没有任何东西看得见站点刚设了什么。
    现在映射形状先处理；序列形状保留，因为浏览器档自己会造 cookie 列表。

    只有名字与值活下来：客户端不给我们 ``Domain=`` / ``Path=`` / ``Max-Age`` 属性，
    所以 sessions.py 里的 jar 是按主机限定、带 TTL 上限的，而不是重写一遍 RFC 6265。
    """
    out: Dict[str, str] = {}
    raw = getattr(resp, "cookies", None)
    if raw is None:
        return out
    try:
        if isinstance(raw, dict):
            out.update({str(k): str(v) for k, v in raw.items() if k})
            return out
        for c in raw:
            if isinstance(c, dict):
                name = c.get("name", "")
                if name:
                    out[str(name)] = str(c.get("value", ""))
            elif hasattr(c, "name"):
                out[str(c.name)] = str(c.value)
    except Exception:
        return out
    return out


def _effective_port(parsed) -> int:
    """URL 实际拨的端口：显式写的那个，或协议的默认端口。"""
    return parsed.port or (443 if parsed.scheme == "https" else 80)


def _same_origin_strict(a: str, b: str) -> bool:
    """scheme + host + 有效端口 全等，才算「凭据可以跟着走」的同源。

    浏览器对重定向的读法（Fetch 规范）：异 scheme（https→http 降级）与异端口
    都是跨源，Authorization 一律剥掉。`_host_of` 是不带端口的，端口在这里补齐。
    """
    pa, pb = urlparse(a), urlparse(b)
    return (pa.scheme == pb.scheme
            and _host_of(a) == _host_of(b)
            and _effective_port(pa) == _effective_port(pb))


def _hop_headers(
    base: Dict[str, str],
    url: str,
    origin_host: str,
    cookies: Optional[Dict[str, str]],
    session_id: str,
    extra_credentials: Sequence[str] = (),
    origin_url: str = "",
) -> Dict[str, str]:
    """为重定向链里的**一发**请求装配头。

    比起把同一份头字典一路传下去，这里修了两件事：

    * **凭据不再搭跨源重定向的便车。** 旧代码把 ``Cookie`` 头建一次、每一跳复用，
      于是一个指向我们从未同意交谈的主机的 302，会直接把会话 cookie（以及调用方设的
      ``Authorization``）带过去 —— 那是第一台服务器选的站点，不是我们选的。浏览器与
      curl 在跨源重定向时都会剥掉这些；现在我们也剥。
    * **每一跳都按实际拨号的主机查 jar**，所以重定向进入一台我们有 cookie 的主机时
      仍然会出示它们（这正是被重定向的登录流程能工作的原因），而调用方显式给的
      ``cookies=`` 仍然绑在它点名的源上 —— 对那一台主机，显式压过 jar。

    ``extra_credentials`` 为一次调用扩充丢弃集：放在 ``X-API-Key`` 里的 API key 也是
    凭据，而内置名单不可能知道调用方选择把它叫什么。

    ``origin_url`` 把同源判定收紧到浏览器的读法：scheme **与**有效端口都相等，不只是
    主机相等。没有它，G25 之前的洞就还在 —— 指向**同一主机**的 https→http 重定向会
    保留 ``Authorization`` 并把它发上明文线。生产调用点始终传它；空默认让直接调用方
    （以及直接驱动这颗函数的测试）停在只看主机的旧口径。
    """
    hop = dict(base)
    host = _host_of(url)
    same_origin = bool(host) and host == origin_host
    if same_origin and origin_url:
        # G25 评审：同主机的 https→http 降级重定向曾带着 Authorization 走明文线。
        # 凭据跟随的同源判定用浏览器的口径——scheme 与有效端口也要相等；
        # browser 层 {80,443} 互通豁免是「同一个落点」的口径，这里不沿用。
        same_origin = _same_origin_strict(origin_url, url)
    if not same_origin:
        for name in _CREDENTIAL_HEADERS + tuple(extra_credentials):
            _drop_header_ci(hop, name.lower())
    merged = sessions.header_for(session_id, url) if session_id else {}
    if same_origin and cookies:
        merged.update({k: v for k, v in cookies.items() if v != ""})
    if merged:
        # 先删：base 里可能已经带着某种大小写的 Cookie，而同一个 dict 里两行 cookie
        # 是服务器会以不可预测方式解析的头。
        _drop_header_ci(hop, "cookie")
        hop["cookie"] = "; ".join(f"{k}={v}" for k, v in sorted(merged.items()))
    elif not same_origin:
        # 这里没有东西要发。调用方放进 `extra_headers` 的 cookie 留在同源请求上
        # （那是他自己的安排）；离开源就删掉，因为那种重定向不是可以信任的。
        _drop_header_ci(hop, "cookie")
    return hop


# browserforge 给「这颗头可以留空」写的记号是 `?n`。它不是一个值：fetch 族的取值是枚举词
# （none / same-origin / same-site / cross-site …），里面没有 `?n` 这一种拼法。实测（本地回环
# echo 逐条落档）`_stealthy_baseline()` 的字典在 600 副里 537 副写着 `sec-fetch-site: ?1`，
# 而 `HTTPSession` 连打 6 发就是 6/6 行收到 `?1` —— 我们把字典逐颗盖下去，正好盖掉了 primp
# 自己那份合法的 `none`，换成一个语法非法、又恰好是「合成头」招牌的形状。
#
# 布尔 hint 是**另一种语法**：`sec-ch-ua-mobile: ?0` / `?1` 就是 Client Hints 的正经写法
# （600 副里 531 副带着它，全部合法），一颗都不许动。所以这里的判据是「这颗头的语法里没有
# ?n 这一种拼法」，不是「值看起来怪」，也不是某个域名或某颗 UA。
_MARKER_KEYS = ("sec-fetch-site", "sec-fetch-mode", "sec-fetch-user", "sec-fetch-dest")
_EMPTY_MARKER = re.compile(r"\?\d+\Z")


def _drop_empty_markers(headers: Dict[str, str]) -> Dict[str, str]:
    """丢掉带着「可以留空」记号的 fetch 头 —— 让 TLS 预设自己那一颗枚举词成为出处。

    不补一个"更像"的值：那一颗头该说什么由发出请求的那一层决定，我们替它编一个就是第二份判决。
    """
    return {k: v for k, v in headers.items()
            if not (k in _MARKER_KEYS and _EMPTY_MARKER.match(v))}


def _brands_follow_the_ua(headers: Dict[str, str]) -> bool:
    """一副身份有没有说出**它自己**那一个大版本 —— primp 的预设不算它说的话。

    判的是形状不是运气：UA 声称 Chromium 一家（`Chrome/` 或 `Edg/`）时，`sec-ch-ua` 里必须
    有一颗与本 UA 同大版本的 brand；声称 firefox / safari 时不发 Client Hints 本来就合法，
    一颗都不许多摇。

    为什么"字典里少一颗头"要拦到交出去之前，而不是让它上线后自然缺席：实测（本地回环 echo，
    钉 chrome 家族摇 1500 副抓到 6 副缺 brand 表，把其中一副真发出去）线上收到的不是空，而是
    `sec-ch-ua: "Google Chrome";v="149"` 配上 `Chrome/114` 的 UA —— 字典缺位时那颗头由 primp
    自己的预设补上，而预设的版本同一进程里两发还各摇出 149 与 152。于是 UA 与 brand 表来自
    两台机器，正是本文件立过的那条罪（"三颗头来自三台机器"）。
    """
    m = re.search(r"(?:Chrome|Edg)/(\d+)", headers.get("user-agent", ""))
    if not m:
        return True
    return f';v="{m.group(1)}"' in headers.get("sec-ch-ua", "")


# 摇不到自洽的一副时有界重试：0.40% 是实测到的单次概率，所以八次里一次都摇不到自洽的
# 是十几次之外的运气（0.004^8），而兜底交回最后一副而不是空手 —— 不比不重试时差。
_IDENTITY_REDRAWS = 8


def _stealthy_baseline(family: str = "") -> Dict[str, str]:
    """一副浏览器身份，而且必须是**这一次要冒充的那一家**的身份。

    两个抽签各自独立就是缺陷本身。实测（同一进程连开 8 个会话，每会话发一次真请求）：
    8 次摇出 **7 副**身份，其中最后一次线上是
    `user-agent: ...iPhone; CPU iPhone OS 18_7... Version/26.3 Mobile Safari/604.1`
    配上 `sec-ch-ua: "Google Chrome";v="149"` 和 `sec-ch-ua-platform: "iOS"` —— 三颗
    头来自三台机器。成因分两层：生成器默认四个家族两种设备（实测 40 次里 safari 6 次、
    手机形状 2 次），而 primp 的 `impersonate` 是写死 "chrome" 的；于是 browserforge 交出的
    那几颗头**逐颗盖掉**了 primp 自己那一套自洽的头，只盖掉一部分 —— 没盖掉的
    `sec-ch-ua*` 还是 Chrome 预设的。另外 `impersonate_os="random"` 也不是"中性"：
    实测 16 次里 1 次抽中**手机**预设（线上就是 `"Android"` + `Mobile Safari` 的 UA），
    它换掉的是一整台机器的形状，不是一位字段。

    所以这里钉两位：`browser=[family]`（对得上 primp 那一份 TLS 预设）和
    `device=["desktop"]`（primp 的预设全是桌面的）。这两颗都不认的旧版生成器不许把
    能力一起弄丢 —— 见下面那圈回退。第三位是 `_brands_follow_the_ua`：交出去之前这一副
    得能说出自己的 brand 表，说不出就重摇（缺的那一颗会被 primp 的预设补成另一台机器）。

    browserforge 缺席时交回空 dict：调用方照旧只带 referer，与这次改动之前一致。
    """
    try:
        from browserforge.headers import HeaderGenerator
    except Exception:
        return {}
    pinned: Dict[str, Any] = {"device": ["desktop"]}
    if family:
        pinned["browser"] = [family]
    last: Dict[str, str] = {}
    for kwargs in (pinned, {}):
        for _attempt in range(_IDENTITY_REDRAWS):
            try:
                generated = HeaderGenerator(**kwargs).generate() or {}
            except Exception:
                # 这一版不认这颗参数（或摇不出这一家）：退回它一定认的那次调用。
                # 少钉一位可以，因为一颗头都发不出去是能力从路上消失了。
                break
            last = _drop_empty_markers(
                {str(k).lower(): str(v) for k, v in generated.items()})
            if _brands_follow_the_ua(last):
                return last
    return last


# browserforge 认这四个家族名（`HeaderGenerator` 的默认值就是这四个），primp 的
# `impersonate` 用的也是同一族词。对不上的（"opera" / "random" / 版本化写法之外的怪名）
# 交回空串 = 不钉家族，只钉桌面形状 —— 宁可少钉一位，也不把一个它不认识的名字塞进去。
_HEADER_FAMILIES = ("chrome", "firefox", "safari", "edge")

# UA 里读不出操作系统、但头确实说了某一家的桌面形状时，握手用这一家在桌面上最常见的
# 那个系统。这不是"猜一个最像的"：另一条路是把 OS 交给 primp 抽签，而实测它会抽中手机
# 预设 —— 那和头里的桌面 Chrome 是两台机器，错配只是交给了概率。
_OS_BY_FAMILY = {"chrome": "windows", "edge": "windows",
                 "firefox": "windows", "safari": "macos"}


def _header_family(impersonate: Any) -> str:
    """`impersonate` 里那个能被头发成的家族；读不出来交回空串（= 不钉家族）。"""
    name = str(impersonate or "").split("_")[0].strip().lower()
    return name if name in _HEADER_FAMILIES else ""


#: 只有这两家把版本钉上去。实测两趟（`r80-shape.json` / `r80-shape2.json`，只打回环 echo）：
#: chrome 与 edge 的整个接受窗口（144–153）在钉住 windows/macos/linux 之后**全部**交回桌面形状，
#: 且 UA 里的 major 与名字一致。firefox 有 `firefox_141…145` 那种"名字被接受、钉了桌面 OS 仍交出
#: 手机形状"的洞；safari 的版本名基本会被拒（生成器摇出的是 17/18）。这两族不钉：宁可留着
#: "版本各自摇"这一位**已知**的不一致，也不引入"头是桌面、握手是手机"这一位**未知**的。
_VERSION_PIN_FAMILIES = frozenset({"chrome", "edge"})

#: 能钉 OS 的那三个值。`""` 与 `"random"` 都不在此列 —— 手机形状就是从 `random` 那一支钻进来的
#: （F40 的实测：16 次里抽中 1 次手机预设）。
_DESKTOP_OS = ("windows", "macos", "linux")


def _impersonate_for(impersonate: Any, baseline: Dict[str, str], os_name: str) -> Any:
    """把 TLS 握手拉到**这份身份自己声称的那个 major** 上；读不出就原样交回。

    修的是 R02-080：`impersonate="chrome"` 不带版本，primp 每次新建客户端自选一个大版本（同一进程
    三发分别 149 / 153 / 150），而头钉在进程那份身份上 ⇒ UA 说 147、握手可能是 153 的那一套。这是
    R02-F70 的另一半：那一把拦掉的是"字典缺 brand 表时由预设说话"（两颗头互相否认），这一把要的是
    **版本**这一位也有唯一作者。

    三位一起读才钉：家族在 `_VERSION_PIN_FAMILIES` 里、major 读得出（UA 优先，`sec-ch-ua` 次之）、
    OS 读得出且是桌面的那三个值之一。缺任何一位就交回原值 —— 不猜一个更像的。
    名字被 primp 拒掉时由调用方退回（见 `_init_client`），所以这里**不**维护版本表：窗口会随
    primp 升级变，硬编的表会腐烂，而被拒静默退回等于自动维持今天的形状。
    """
    if os_name not in _DESKTOP_OS:
        return impersonate
    family = _header_family(impersonate)
    if family not in _VERSION_PIN_FAMILIES:
        return impersonate
    ua = str((baseline or {}).get("user-agent") or "")
    needle = r"Edg/(\d+)" if family == "edge" else r"Chrome/(\d+)"
    m = re.search(needle, ua)
    major = m.group(1) if m else ""
    if not major:
        sec = str((baseline or {}).get("sec-ch-ua") or "")
        found = re.findall(r'"(?:Google Chrome|Microsoft Edge)";v="(\d+)"', sec)
        major = found[0] if found else ""
    if not major:
        return impersonate
    return f"{family}_{major}"


# 一场进程一副身份，按家族分开存。存在的理由是"会话"这个词在真实使用里撑不住：
# `bulk_get` 每次调用都新建一个 `HTTPSession`（server.py 那一个 `async with`），
# 而一次 web_read 的重试、批量里的每一条、升级路上的每一档都是新会话。上一版把身份
# 定在会话级，实测下来等于每次调用重摇（8 个会话 7 副脸）。
_PROCESS_IDENTITY: Dict[str, Dict[str, str]] = {}


def _process_identity(family: str) -> Dict[str, str]:
    """这一家的身份：进程里第一次要时摇一次，之后每个会话读同一份。

    同一进程内只有一份，是因为"这台机器的浏览器"只有一个。`primp.Client` 是在 init
    那一刻按 `impersonate`/`impersonate_os` 定死握手的，头跟着那份握手走才是自洽的；
    反过来（每会话重摇头、握手不动）就是上一版的缺陷。
    """
    got = _PROCESS_IDENTITY.get(family)
    if got is None:
        got = _stealthy_baseline(family)
        _PROCESS_IDENTITY[family] = got
    return got


def reset_process_identity() -> None:
    """忘掉进程摇过的那几副身份。测试挂具用它，免得第一条用例摇出的那张脸决定
    后面每条用例看到什么头（本仓库为了一颗 ContextVar 付过一次这个代价）。"""
    _PROCESS_IDENTITY.clear()


# primp 认这三颗（实测："IOS" / "ANDROID" 会被 `Invalid impersonate_os` 拒掉），
# 而 browserforge 的 UA 里能读出的是同一族名字。对不上的（Android、Linux 上的
# Firefox 写法等）交回空串，由调用方按家族回落到桌面主力系统。
_OS_BY_UA = (("Windows NT", "windows"), ("Macintosh", "macos"),
             ("X11; Linux", "linux"), ("Android", "linux"), ("Linux", "linux"))


def _os_from_ua(ua: str) -> str:
    for needle, name in _OS_BY_UA:
        if needle in ua:
            return name
    return ""


class HTTPSession:
    """异步 HTTP 取回会话，用 primp 做 TLS 冒充。

    取代 scrapling 的 FetcherSession。提供：
    - TLS 指纹冒充（Chrome、Firefox、Safari、Edge）
    - 代理支持（http、https、socks5）
    - 带退避的重试
    - 隐形头（Google referer、真实 User-Agent）—— 一场进程一副身份，
      跨会话跨调用都读同一份，且 primp 的 OS 跟着**这份**身份选
      （见 `_process_identity` 与 `_stealthy_baseline` 的实测理由）

    用法：
        async with HTTPSession(impersonate="chrome") as session:
            response = await session.get("https://example.com")
    """

    def __init__(
        self,
        impersonate: Union[str, List[str]] = "chrome",
        proxy: Optional[str] = None,
        stealthy_headers: bool = True,
        retries: int = 1,
        retry_delay: float = 1.0,
        timeout: int = 30,
    ):
        self._impersonate = impersonate
        self._proxy = proxy
        self._stealthy_headers = stealthy_headers
        self._retries = retries
        self._retry_delay = retry_delay
        self._timeout = timeout
        self._client: Optional[primp.Client] = None
        # 会话级的那一份身份，在 `_init_client` 里定一次。空 dict = 这个会话没要
        # 隐形头，或 primp 还没起来（`_build_headers` 也允许被单独调用）。
        self._baseline: Dict[str, str] = {}

    async def __aenter__(self) -> "HTTPSession":
        await self._init_client()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def _init_client(self) -> None:
        """在工作线程里初始化 primp 客户端（导入约需 1s）。"""
        impersonate = self._impersonate
        if isinstance(impersonate, list):
            # primp 不支持 scrapling 的 curl_cffi 那种轮换池。从池里随机挑一个。
            import random
            impersonate = random.choice(impersonate)

        # 身份在**进程**这一层决定（`_process_identity` 记了为什么"会话级"撑不住），
        # 而 primp 的 TLS 档必须跟着**这份**身份选 OS：两边各抽一次签，实测约一半的请求
        # 是"Windows 的头 + 别的系统的 TLS 指纹"出站 —— 那正是检测方最容易交叉核对的一位。
        # UA 里读不出系统时按家族落到桌面的主力系统；只有 browserforge 缺席（头里根本没有
        # UA，谁也没声称自己是哪台机器）才真的回落 "random"，那才是"不许瞎猜"适用的形状。
        family = _header_family(impersonate)
        self._baseline = _process_identity(family)
        _ua = self._baseline.get("user-agent", "")
        os_name = _os_from_ua(_ua)
        if _ua and not os_name:
            os_name = _OS_BY_FAMILY.get(family, "")
        #: 第三位：握手的大版本跟着这份身份走（R02-080）。读不出 major 或读不出桌面 OS 时
        #: 交回原值，于是那一发与今天逐字节相同。
        pinned = _impersonate_for(impersonate, self._baseline, os_name)

        def _create():
            kwargs: Dict[str, Any] = {
                "impersonate_os": os_name or "random",
            }
            if self._proxy:
                kwargs["proxy"] = self._proxy
            if pinned != impersonate:
                try:
                    return primp.Client(impersonate=pinned, **kwargs)
                except Exception as exc:
                    # 只兜"这颗名字这一版 primp 不认识"：窗口会随 primp 升级移动，被拒就退回
                    # 今天那颗（零网络往返，也不需要谁去维护一张版本表）。别的构造期故障照常
                    # 冒出去 —— 把 libprimp 缺失也吞掉就又是一次"看起来成功了"。
                    if "invalid impersonate" not in str(exc).lower():
                        raise
                    logger.debug("primp 不接受 %r（%s）：按 %r 建客户端",
                                 pinned, str(exc)[:80], impersonate)
            return primp.Client(impersonate=impersonate, **kwargs)

        self._client = await asyncio.to_thread(_create)

    async def close(self) -> None:
        """关闭底层客户端。"""
        # primp.Client 没有显式 close，丢掉引用即可
        self._client = None

    @staticmethod
    def _drop_header(headers: Dict[str, str], name: str) -> None:
        """删掉 ``name`` 的每一种大小写写法（HTTP 头名不区分大小写）。"""
        for existing in [k for k in headers if k.lower() == name]:
            del headers[existing]

    def _build_headers(
        self,
        headers: Optional[Dict[str, str]] = None,
        useragent: Optional[str] = None,
    ) -> Dict[str, str]:
        """装配请求头，带上隐形默认值。"""
        final_headers: Dict[str, str] = {}
        if self._stealthy_headers:
            final_headers["referer"] = "https://www.google.com/"
            # 会话里那份**定好的**身份，不是每次重摇（`_stealthy_baseline` 记了实测）。
            for k, v in self._baseline.items():
                if k != "referer":
                    final_headers.setdefault(k, v)
        # 调用方给的头压过默认值
        if headers:
            for k, v in headers.items():
                if k.lower() == "user-agent":
                    # browserforge 已经设过 "user-agent"；调用方那颗 "User-Agent"
                    # 否则会作为第二颗头一起发出去。
                    self._drop_header(final_headers, "user-agent")
                final_headers[k] = v
        # useragent 选项压过一切，包括经 extra_headers 传进来的 user-agent。
        if useragent:
            self._drop_header(final_headers, "user-agent")
            final_headers["user-agent"] = useragent
            # 换了指纹的另一半，就得把跟着它的那几颗一起换掉：`sec-ch-ua*` 是
            # Chromium 一家才会发的（`Sec-Fetch-*` 各家都发，留着），配在一个人工
            # 指定的 Firefox/Safari UA 上，等于自己把"这些头不是同一台机器"写给
            # 检测方看。
            if "Chrome/" not in useragent:
                for name in [k for k in final_headers if k.startswith("sec-ch-ua")]:
                    self._drop_header(final_headers, name)
        return final_headers

    async def get(
        self,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        cookies: Optional[Dict[str, str]] = None,
        useragent: Optional[str] = None,
        timeout: Optional[int] = None,
        retries: Optional[int] = None,
        proxy: Optional[str] = None,
        follow_redirects: Union[bool, str] = True,
        max_redirects: int = 5,
        params: Optional[Dict[str, str]] = None,
        allow_internal: bool = False,
        session_id: str = "",
        method: str = "GET",
        body: Optional[bytes] = None,
        content_type: str = "",
        credential_headers: Sequence[str] = (),
        **kwargs: Any,
    ) -> Response:
        """经 HTTP 取回一个 URL，带 TLS 冒充。

        返回一个 Response 对象。

        ``allow_internal``: 重定向目标是否允许内网地址（默认 False，SSRF
        防护——每跳校验。仅测试/可信环境可开启）。

        ``credential_headers``: 除 Cookie/Authorization 之外还要按凭据对待的头名
        （auth 放出去的 ``X-API-Key`` 之类）。只在**跨源重定向**时被丢掉，
        同源照发——否则等于替调用方把密钥寄给了第一个服务器挑中的下一跳。

        ``session_id``: 非空时启用持久 cookie jar（G7，见 sessions 模块）——本次
        答复里的 Set-Cookie 记进 jar，下一跳/下一次调用按**同一主机**取回。跨源
        重定向不再携带 Cookie / Authorization（见 _hop_headers）。

        ``method`` / ``body`` / ``content_type``（G8）：非 GET 时走 ``client.request``。
        重定向的方法转换按浏览器与 curl 的读法来（见 _METHOD_CONVERTERS）：301/302/303
        把 POST 变成 GET 并丢掉 body，307/308 保留方法与 body —— 保留 body 跳到一个
        攻击者可选的地址，或把 body 重放到另一台主机，都是错的。
        """
        method = (method or "GET").upper()
        # 把 scrapling 风格的字符串 follow_redirects 归一成布尔。
        # Scrapling 接受 "safe"、"always"、"never"；primp 要的是布尔。
        if isinstance(follow_redirects, str):
            follow_redirects = follow_redirects.lower() != "never"

        if self._client is None:
            await self._init_client()

        client = self._client
        if proxy:
            # 这一次请求覆盖代理：新建一个客户端
            def _create_proxy_client():
                impersonate = self._impersonate
                if isinstance(impersonate, list):
                    import random
                    impersonate = random.choice(impersonate)
                return primp.Client(impersonate=impersonate, proxy=proxy)
            client = await asyncio.to_thread(_create_proxy_client)

        base_headers = self._build_headers(headers, useragent)
        if body is not None and content_type:
            base_headers["content-type"] = content_type
        origin_host = _host_of(url)

        actual_timeout = timeout or self._timeout
        actual_retries = retries if retries is not None else self._retries
        if method not in _RETRY_SAFE_METHODS:
            # 会改变服务器状态的动词不重试：上一次尝试很可能已经落库，重试就是再写一遍。
            # 超时报错让人以为「没成功」而重发，是同一件事的另一半——所以宁可一次。
            actual_retries = 0

        last_error: Optional[Exception] = None
        for attempt in range(actual_retries + 1):
            try:
                # primp 的 request() 是同步的，包进 to_thread
                def _do_get():
                    # 拼查询参数
                    req_url = url
                    if params:
                        from urllib.parse import urlencode
                        separator = "&" if "?" in req_url else "?"
                        req_url = f"{req_url}{separator}{urlencode(params)}"

                    # 手动跟随重定向：不依赖 primp 内部跟随（否则重定向目标不经
                    # validate_url 复检，构成 SSRF 绕过）。每跳校验目标 URL，
                    # 受 max_redirects 控制。primp follow_redirects=False 时
                    # 返回 3xx 原始响应，由本循环解析 Location 继续。
                    current = req_url
                    current_method = method
                    current_body = body
                    meta_from = ""      # 这条链里第一个 <meta refresh> 的来源
                    meta_delay = 0.0
                    meta_hops = 0
                    for _hop in range(max_redirects + 1):
                        hop_headers = _hop_headers(
                            base_headers, current, origin_host, cookies, session_id,
                            credential_headers, origin_url=url)
                        if current_body is None:
                            # 方法被转换掉（POST->GET）之后，描述实体的头要一起下来：
                            # 留着 content-type/content-length 就是「声明有 body 却没有
                            # body」的请求，服务端怎么处理全看运气。
                            for _h in _BODY_HEADERS:
                                _drop_header_ci(hop_headers, _h)
                        resp = client.request(
                            current_method,
                            current,
                            headers=hop_headers,
                            **({} if current_body is None else {"content": current_body}),
                            timeout=actual_timeout,
                            follow_redirects=False,
                        )
                        # 3xx 也可能带着要紧的 Set-Cookie（会重定向的登录是常见形状），
                        # 所以每一跳的 cookie 都留下，不只是最后一封的。
                        if session_id:
                            sessions.store(session_id, current, _resp_cookies(resp))
                        if not (follow_redirects and resp.status_code in (301, 302, 303, 307, 308)):
                            # G29 —— 状态行没有重定向时，文档也可能重定向。**在这里**
                            # 跟随（而不是留给调用方），才让一次 meta 跳转经过与 3xx
                            # 跳转相同的检查：目标过 validate_url、共用 max_redirects
                            # 预算、以及 _hop_headers 那条「凭据不跨源」的规矩。HEAD
                            # 除外：探针没有正文可读标签，跟随它会把调用方要求不要下载
                            # 的那一页下载下来。
                            if (follow_redirects and current_method == "GET"
                                    and resp.status_code == 200):
                                target, delay_s = _meta_refresh_target(resp, current)
                                if target:
                                    from dhole_mcp.security import SecurityError, validate_url
                                    try:
                                        validate_url(target, allow_internal=allow_internal)
                                    except SecurityError as se:
                                        logger.warning(
                                            f"meta-refresh target rejected for {current}: "
                                            f"{str(se)[:200]}"
                                        )
                                        raise
                                    meta_hops += 1
                                    if not meta_from:
                                        meta_from, meta_delay = current, delay_s
                                    current = target
                                    continue
                            return resp, current, meta_from, meta_delay, meta_hops
                        if current_method == "HEAD":
                            # HEAD 的答复不该带正文，所以重定向头**就是**有用的那部分：
                            # Location、状态码、长度。跟随它会把探针变成一次 GET，下载
                            # 调用方要求不要下载的那一页。
                            return resp, current, meta_from, meta_delay, meta_hops
                        location = ""
                        rh = resp.headers if hasattr(resp, "headers") else {}
                        for k, v in rh.items():
                            if k.lower() == "location":
                                location = v
                                break
                        if not location:
                            return resp, current, meta_from, meta_delay, meta_hops
                        from dhole_mcp.security import SecurityError, validate_url
                        try:
                            next_url = _urljoin(current, location)
                            validate_url(next_url, allow_internal=allow_internal)
                        except SecurityError as se:
                            logger.warning(
                                f"redirect target rejected for {current}: {str(se)[:200]}"
                            )
                            raise
                        if (resp.status_code in _METHOD_CONVERTERS
                                and current_method not in ("GET", "HEAD")):
                            current_method, current_body = "GET", None
                        current = next_url
                    # 预算用尽：报**最后一封**响应来自哪里，而不是我们正要拨的那一跳。
                    # 之前的写法交回待拨的目标，等于告诉调用方它抓到了一页 —— 而它从未
                    # 向任何服务器要过那一页。
                    return (resp, (str(resp.url) if hasattr(resp, "url") and resp.url
                                   else current), meta_from, meta_delay, meta_hops)

                # 一次 meta 跳转挪动地址的理由与 3xx 不同：服务器说 200，而页面说
                # 「在那边」。两个事实都跟着响应交出去，调用方才能把「站点搬家了」与
                # 「这一页只是个指路牌」分开。
                resp, final_url, meta_from, meta_delay, meta_hops = await asyncio.to_thread(_do_get)

                # 从 content-type 解析编码
                resp_headers = dict(resp.headers) if hasattr(resp, "headers") else {}
                ct = ""
                for k, v in resp_headers.items():
                    if k.lower() == "content-type":
                        ct = v
                        break
                encoding = _extract_encoding(ct)

                resp_cookies = _resp_cookies(resp)

                return Response(
                    url=final_url if final_url else (str(resp.url) if hasattr(resp, "url") else url),
                    body=resp.content if hasattr(resp, "content") else b"",
                    status=resp.status_code if hasattr(resp, "status_code") else 0,
                    headers=resp_headers,
                    encoding=encoding,
                    reason=resp.reason if hasattr(resp, "reason") else "",
                    cookies=resp_cookies,
                    meta_refresh=({"from": meta_from, "delay_s": meta_delay,
                                   "hops": meta_hops} if meta_hops else None),
                )

            except SecurityError:
                # SSRF 守卫的拒绝不可能靠再问一遍解决。重试会重新拨号、重走同一条链、
                # 得到同一个答案，所以重试唯一产出的是向刚刚被拒的主机再发一次请求。
                raise
            except Exception as e:
                last_error = e
                if attempt < actual_retries:
                    # 必须脱敏：primp/httpx 的异常文本会带上完整代理 URL
                    # （形如 http://user:pass@host），直接写日志等于把代理凭据
                    # 落进日志。项目里已有 redact_api_key，这里此前漏用。
                    logger.warning(
                        f"HTTP fetch attempt {attempt + 1} failed for {url}: "
                        f"{redact_api_key(str(e)[:200])}. "
                        f"Retrying in {self._retry_delay}s..."
                    )
                    await asyncio.sleep(self._retry_delay)
                else:
                    raise

        # 不该走到这里，但留个保险
        raise last_error or RuntimeError(f"Failed to fetch {url}")


# ─── 便捷函数 ─────────────────────────────────────────────────────────────────

async def http_get(
    url: str,
    *,
    impersonate: Union[str, List[str]] = "chrome",
    proxy: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Dict[str, str]] = None,
    useragent: Optional[str] = None,
    timeout: int = 30,
    stealthy_headers: bool = True,
    retries: int = 1,
) -> Response:
    """一次性的异步 HTTP 取回，带 TLS 冒充。

    用法：
        response = await http_get("https://example.com")
    """
    async with HTTPSession(
        impersonate=impersonate,
        proxy=proxy,
        stealthy_headers=stealthy_headers,
        retries=retries,
        timeout=timeout,
    ) as session:
        return await session.get(
            url,
            headers=headers,
            cookies=cookies,
            useragent=useragent,
            timeout=timeout,
        )


# ─── TCP 预探测 ───────────────────────────────────────────────────────────────

def proxy_preflight(proxy_url: str, timeout: float = 5.0) -> tuple[bool, str]:
    """对**代理**端点做一次快速 TCP 连接，让死代理快速失败（G21）。

    返回 ``(可达, 错误类别)``；端点没有接受连接时类别是 ``proxy_unreachable``，否则
    为 ``""``。

    为什么要有它：配置了代理时，``web_read`` 会跳过自己的 TCP 预探测（直接探目标会
    误报，因为 HTTP 档是在跟代理说话）。于是一个死代理没有任何东西可以快速失败 ——
    实测：``proxy='http://127.0.0.1:9/'`` 烧掉整个 30000ms 预算，升级到浏览器档
    （它拨的是**同一个**死代理），最后报「the 30000ms call budget ran out ... raise
    timeout for a slow host」—— 一句指向错误旋钮的错误诊断。

    对**每一种**失败模式只给一个不透明的类别，是刻意的。对任意 host:port 区分
    「拒绝」「无路由」「超时」会把这里变成端口扫描 oracle —— 也就是 ``tcp_preflight``
    对内部目标把答案压平所避免的那件事。这里代理是调用方给的，所以「你的代理没答」
    没有告诉他任何他本就不知道的事 —— 但探针也不能说得比这更细。

    连接超时算不可达：没有代理需要 5 秒才接受一条 TCP 连接，而把一个被过滤的端口
    当成「也许没事」恰恰会留下这颗探针要消除的静默 30 秒。同步函数；经
    ``asyncio.to_thread`` 调用。永不抛异常。
    """
    import socket
    try:
        parsed = urlparse(proxy_url or "")
        host = parsed.hostname
        if not host:
            return True, ""  # 不是我们能探的 URL —— 让真正的尝试去决定
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True, ""
    except Exception:
        return False, "proxy_unreachable"


def _preflight_category(error: Exception) -> str:
    """一次拨失败属于哪一类。唯一的作者：预探测的两条支路都从这里取标签。"""
    import socket
    if isinstance(error, ConnectionRefusedError):
        return "connection_refused"
    if isinstance(error, socket.gaierror):
        return "dns_unresolvable"
    if isinstance(error, (socket.timeout, TimeoutError)):
        return "timeout"
    # Windows：os error 10061 = 连接被拒，10054 = 连接被重置
    err_str = str(error)
    if "10061" in err_str or "refused" in err_str.lower():
        return "connection_refused"
    if "10054" in err_str or "reset" in err_str.lower():
        return "connection_reset"
    return "unknown"


def tcp_preflight(url: str, timeout: float = 2.0) -> tuple[bool, str]:
    """快速 TCP 连接检查，在完整的 HTTP/浏览器取回之前快速失败。

    返回 (可达: bool, 错误类别: str)。可达时错误类别为空，否则是以下之一：
    connection_refused、connection_reset、dns_unresolvable、private_target_blocked、
    timeout、unknown。

    同步函数 —— 经 asyncio.to_thread() 从工作线程调用。永不抛异常。
    """
    import socket
    try:
        from dhole_mcp.security import url_targets_internal
        # 这个"快速可达性探测"本身就是一次对内网的裸 TCP 连接，而它的分类结果会原样
        # 回报给 agent。它**不拨**内网地址，这一条没变（`test_internal_targets_
        # never_connect` 钉着）；变的是它怎么说。曾经报 `dns_failure`，而
        # 10.1.2.3 是一颗字面 IP，整个过程一次都没问过 DNS —— 那个标签给调用方的是
        # 一条假线索：他会去核域名拼写、去查 resolver，两件事都与这次失败无关。
        # 系统其他地方对同一件事说的是真话（`validate_url` 的 "URL targets
        # internal/private IP"，回执里的 `ssrf_recheck`），只有这一处在掩。掩它当初
        # 是为了不做端口扫描 oracle —— 而那个目的不靠撒谎达成：这一支对所有内网目标
        # 同形返回（开着死着的字都不差），oracle 从来就不在这句话里。
        if url_targets_internal(url):
            return False, "private_target_blocked"
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            # 没有主机名可问：这不是解析失败，是压根没有名字（`http:///x`）。
            return False, "dns_unresolvable"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        def _dial():
            sock = socket.create_connection((host, port), timeout=timeout)
            sock.close()

        try:
            _dial()
            return True, ""
        except socket.gaierror:
            # 一次解析失败**不是定论**。实测（2026-10-09，lwn.net）：预探测一次
            # gaierror → 回执 `dns_failure` + `retryable: false` + "do NOT retry"，
            # 而调用方按 `cache_ttl=0` 原样重试，1643ms 就拿到了页面。这颗牙判决的是
            # resolver 的一帧抖动，交付的却是一句"别再试了"。再拨一次，间隔 150ms：
            # 第二次成功就照常去抓，并把"这台机器的 resolver 要两次才答"说给调用方
            # （`_note_text` 落 summary —— 一次被吸收掉的故障如果毫无痕迹，就又是一次
            # 静默降级）；两次都失败才叫 `dns_unresolvable`。
            import time
            time.sleep(0.15)
            try:
                _dial()
            except socket.gaierror:
                return False, "dns_unresolvable"
            except Exception as second_error:
                # 第二次名字答了、端口没答：按第二次的实情分类，别让第一句 gaierror
                # 替它命名。
                return False, _preflight_category(second_error)
            from dhole_mcp.context import _note_text
            _note_text(
                f"DNS for {host} failed on the first probe and resolved on the second, "
                "150ms later - this machine's resolver is flaky for this name. The "
                "fetch went ahead; nothing was retried at the HTTP layer to get here")
            return True, ""
        except Exception as error:
            return False, _preflight_category(error)
    except Exception:
        return False, "unknown"
