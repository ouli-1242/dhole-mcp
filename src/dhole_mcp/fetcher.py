"""Dhole's own HTTP fetcher and Response class.

Replaces scrapling's FetcherSession (which wraps curl_cffi) with a direct
primp-based implementation. primp provides the same TLS impersonation
(JA3/JA4 fingerprinting, HTTP/2 settings randomization) as curl_cffi but
with a cleaner API and no C dependency beyond what's already installed.

The Response class mimics scrapling's Response interface: .status, .url,
.headers, .body (bytes), .encoding, .content (decoded HTML), .css() (CSS
selector via lxml), .reason, .cookies.
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
    """Merge loopback hosts into NO_PROXY so no tier proxies them. Never raises."""
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
    except Exception:  # pragma: no cover - env mutation must never break startup
        pass


_ensure_loopback_no_proxy()


def _no_proxy_entries() -> list[str]:
    """Every NO_PROXY entry the tiers would see, both spellings merged."""
    out: list[str] = []
    for name in ("NO_PROXY", "no_proxy"):
        for part in os.environ.get(name, "").split(","):
            part = part.strip().lower().lstrip(".")
            if part and part not in out:
                out.append(part)
    return out


def system_proxy_for(url: str) -> str:
    """The proxy this machine would hand ``url`` to, or "" when it dials direct.

    Read from the same two places the tiers inherit from (`urllib.getproxies()`
    covers the environment and, on Windows, the registry's system proxy) with the
    same exemption the tiers honor (`NO_PROXY`, plus G31's unconditional loopback
    exclusion). This is *geography*, not a verdict: it answers "is there an
    intermediary between dhole and that host", which is the fact a 5xx attribution
    is allowed to rest on. Never raises.
    """
    try:
        import urllib.request as _urllib_req
        proxies = dict(_urllib_req.getproxies_environment())
        # `getproxies()` is "environment if non-empty, else registry", and
        # `no_proxy` alone counts as non-empty — it parses to a key named "no".
        # G31 keeps NO_PROXY set in this process at all times, so that path would
        # answer "no proxy" on a machine whose Windows registry says otherwise
        # (measured 2026-10-14: getproxies() == {'no': 'localhost,127.0.0.1,::1'}
        # while the registry holds http://127.0.0.1:7890). Take the real proxy from
        # the environment when one is there, otherwise from the registry.
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
    if host in _LOOPBACK_NO_PROXY_ENTRIES:
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
    """Wraps an lxml element to mimic scrapling's Selector element interface.

    Exposes ._root (the lxml node) so trafilatura_extractor's
    CSS-selector narrowing path (tostring(el._root)) keeps working.
    """

    __slots__ = ("_root", "_url")

    def __init__(self, root, url: str = ""):
        self._root = root
        self._url = url

    @property
    def url(self) -> str:
        return self._url

    def css(self, selector: str) -> List["ElementWrapper"]:
        """CSS selector query on this element's subtree."""
        from lxml.cssselect import CSSSelector
        try:
            sel = CSSSelector(selector)
            matches = sel(self._root)
            return [ElementWrapper(m, self._url) for m in matches]
        except Exception:
            return []

    def text_content(self) -> str:
        """Full text of this element AND all descendants (matches lxml's native
        .text_content()). The previous `.text or ""` returned only the element's
        own leading text node, silently dropping nested children."""
        return "".join(self._root.itertext())


class Response:
    """HTTP response with CSS selector support.

    Mimics scrapling's Response interface used throughout server.py:
        .status (int)          - HTTP status code
        .url (str)             - final URL after redirects
        .headers (dict)        - response headers
        .body (bytes)          - raw response body
        .encoding (str)        - detected encoding
        .content (str)         - decoded body (lazy)
        .css(selector) -> list - CSS selector query
        .reason (str)          - status text
        .cookies (dict)        - response cookies
    """

    __slots__ = (
        "_status", "_url", "_headers", "_body", "_encoding",
        "_reason", "_cookies", "_root", "_content_cached", "_meta_refresh",
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
        self._root: Any = None  # lazy lxml tree
        self._content_cached: Optional[str] = None

    # ── Properties matching scrapling's interface ──────────────────

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
        return self._encoding

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def cookies(self) -> Dict[str, str]:
        return self._cookies

    @property
    def meta_refresh(self) -> Optional[Dict[str, Any]]:
        """Set when a ``<meta http-equiv=refresh>`` moved us (G29): ``{from, delay_s, hops}``."""
        return self._meta_refresh

    @property
    def content(self) -> str:
        """Decoded body (lazy, cached).

        One decode policy for the whole read path: the declared charset, with a
        vote from the bytes (see _decode_html_bytes) because servers have been
        measured declaring ISO-8859-1 or us-ascii while sending UTF-8. Consumers
        that re-decode here would be a second judge of the same question, which
        is what made the schema path mojibake (BUG-1).
        """
        if self._content_cached is None:
            self._content_cached = _decode_html_bytes(self._body, self._encoding)
        return self._content_cached

    @property
    def html_content(self) -> str:
        """Alias for .content (scrapling compat)."""
        return self.content

    # ── CSS selector support ───────────────────────────────────────

    def _ensure_parsed(self):
        """Lazily parse the body into an lxml tree."""
        if self._root is not None:
            return
        if not self._body:
            from lxml import etree
            self._root = etree.fromstring(b"<html></html>")
            return
        try:
            from io import BytesIO
            from lxml import html as lxml_html
            # Use lxml.html.parse() from BytesIO for cross-platform
            # consistency. lxml.html.fromstring() returns different root
            # elements on different platforms (e.g. ubuntu CI returns the
            # first child element, not the <html> root), which breaks
            # CSSSelector (it only searches descendants, not the root).
            # doc_parse() always returns a full tree with <html> as root.
            #
            # self.content, re-encoded, with the parser pinned to that charset:
            # handing libxml2 the raw body instead lets it pick the document's
            # <meta charset> (or the host locale when there is none) over the
            # header we already honoured — which is how U+2019 came back as "â"
            # (BUG-1). lxml.html's parser specifically, not lxml.etree's: the
            # etree one builds bare _Element nodes, which have no text_content().
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
        """CSS selector query. Returns list of ElementWrapper objects.

        Each ElementWrapper has ._root (lxml element) so callers can do
        tostring(el._root, encoding='unicode') to get the HTML.
        """
        self._ensure_parsed()
        if self._root is None:
            return []
        from lxml.cssselect import CSSSelector
        sel = CSSSelector(selector)
        matches = sel(self._root)
        return [ElementWrapper(m, self._url) for m in matches]


# ─── Browser response builder ─────────────────────────────────────────────────

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
    """Build a Response from a patchright/playwright page + response objects.

    Mirrors scrapling's ResponseFactory.from_async_playwright_response but
    without the Selector/parser overhead. Gets the page content (with the
    Windows page.content() retry workaround), response headers, status, etc.
    """
    # Get page content with Windows retry workaround
    # (Playwright has a known issue with page.content() on Windows:
    #  https://github.com/microsoft/playwright/issues/16108)
    page_content = b""
    for _ in range(20):
        try:
            html_str = await page.content()
            if html_str:
                page_content = html_str.encode("utf-8")
                break
        except Exception:
            await page.wait_for_timeout(500)

    # Determine the final response (fall back to first if no final)
    resp = final_response if final_response else first_response
    if resp is None:
        return Response(
            url=page.url if page else "",
            body=page_content,
            status=0,
            headers={},
            encoding="utf-8",
        )

    # Extract headers
    try:
        headers = await resp.all_headers()
    except Exception:
        headers = {}

    # Extract encoding from content-type
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

    # Extract status
    status = resp.status

    # Extract cookies from context
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
    """Extract charset from a content-type header."""
    if not content_type:
        return "utf-8"
    for part in content_type.split(";"):
        part = part.strip().lower()
        if part.startswith("charset="):
            return part.split("=", 1)[1].strip().strip('"').strip("'")
    return "utf-8"


# ─── Decoding: the declared charset lies often enough to matter ────────────
#
# Measured on a ~60-call ceiling test: pages declared ISO-8859-1 (Apache's
# default) while sending UTF-8, so "you’ve · café" came back as
# "youâ€™ve Â· cafÃ©"; a us-ascii declaration turned the same bytes into
# U+FFFD runs ("you???ve"). The page's own <meta charset="utf-8"> was ignored
# because the header won unconditionally.
#
# Rather than guess a charset, decode twice and keep the cleaner result: a
# wrong single-byte decode of UTF-8 leaves either replacement chars or the
# Â/Ã prefixes below, and a genuine non-UTF-8 body is left alone because
# decoding *it* as UTF-8 produces far more replacement chars.
#
# The markers and the score live in dhole_mcp.charset (imported at the top),
# shared with the local-file path in parse.py so the two definitions of "this
# looks like mojibake" cannot drift apart.


def _decode_html_bytes(body: bytes, declared: str) -> str:
    """Decode a response body, falling back to UTF-8 when the declared charset
    is contradicted by the bytes themselves.

    Never raises. Callers get a str either way.
    """
    enc = (declared or "").strip() or "utf-8"
    try:
        text = body.decode(enc, errors="replace")
    except (LookupError, UnicodeError):
        # Unknown/nonsense charset name in the header.
        return body.decode("utf-8", errors="replace")
    if enc.replace("-", "").replace("_", "").lower() in ("utf8", "utf8sig"):
        return text
    try:
        utf8_text = body.decode("utf-8", errors="replace")
    except UnicodeError:  # pragma: no cover - decode with replace cannot raise
        return text
    return utf8_text if _misdecode_score(utf8_text) < _misdecode_score(text) else text


# ─── HTTP fetcher (primp-based) ────────────────────────────────────────────────

# Headers that name a principal. They travel to the origin the caller asked for
# and to nobody else (see _hop_headers).
_CREDENTIAL_HEADERS = ("cookie", "authorization", "proxy-authorization")

# A 301/302/303 answer to a POST is followed as a GET with no body, which is what
# browsers and curl do (303 says so out loud; 301/302 are read the same way since
# nobody ships a spec-following client that keeps the body here). 307/308 keep
# both method and body — and the target is still re-validated before we dial it,
# because a redirect is chosen by the server we left, not by us.
_METHOD_CONVERTERS = (301, 302, 303)

# Headers that describe a request body; they go when the body goes.
_BODY_HEADERS = ("content-type", "content-length")

# Methods a timeout may be answered by asking again: resending one of these
# cannot leave the server in a different state, so a retry is a retry rather than
# a duplicate. RFC 9110 calls PUT and DELETE idempotent too, but that is a claim
# about the endpoint — "the second time converges on the first" is exactly the
# promise a badly-built form endpoint breaks — and nothing here can check it.
# web_request tells its caller a write leaves once, so this gate asks "can this
# verb change state", not "does the spec say this verb converges".
_RETRY_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _host_of(url: str) -> str:
    """Lowercased, port-free host of a URL ('' when it has none)."""
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return ""
    return host.split(":", 1)[0]


def _drop_header_ci(headers: Dict[str, str], name: str) -> None:
    """Remove a header regardless of the case it was stored under."""
    for key in [k for k in headers if k.lower() == name]:
        del headers[key]


# ─── <meta http-equiv=refresh> (G29) ──────────────────────────────────────────
#
# A redirect written into the document instead of the Location header. Real-world
# markup is messier than the spec's example, so the parser below is tolerant in
# the four ways the measured pages were: unquoted attribute values
# (``http-equiv=REFRESH``), uppercase ``URL=``, single quotes, and whitespace
# around the semicolon. An extractor that missed those left the caller with a
# 300-character shell page and no hint that the content lived elsewhere.
_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_META_ATTR_RE = re.compile(
    r"([a-z_:][-\w:]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'>`]+))",
    re.IGNORECASE,
)


def _meta_refresh_target(resp, current: str) -> tuple[str, float]:
    """``(absolute target, delay seconds)`` from this HTML body, else ``("", 0)``.

    Only the first refresh tag counts, which is also roughly what browsers do: a
    page that ships several is asking the visitor to make a choice it has already
    made for them.
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
        # The tag lives in <head>; decoding the first 16 KB finds it without
        # pulling a 5 MB document through the decoder for a string match.
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
                rest = content  # no delay part: "url=..." alone is not valid but ships anyway
            key, sep, target = rest.partition("=")
            target = target.strip().strip("\"'").strip()
            if not sep or key.strip().lower() != "url" or not target:
                continue
            if target.lower().startswith(("javascript:", "data:", "about:")):
                return "", delay_s
            absolute = _urljoin(current, target)
            if absolute.split("#")[0] == current.split("#")[0]:
                return "", delay_s  # a self-refresh is a reload, not a redirect
            return absolute, delay_s
        return "", 0.0
    except Exception:
        return "", 0.0


def _resp_cookies(resp) -> Dict[str, str]:
    """Normalize a primp response's cookies into ``{name: value}``.

    primp hands back a MAPPING (``{'a': '1'}``), so the pre-16.0 loop here
    (`for c in resp.cookies`, expecting a list of dicts or cookie objects) iterated
    the KEYS — strings, which matched neither branch — and returned {} every time.
    That is why every response reported no cookies and why a ``Set-Cookie`` was
    invisible to callers: nothing downstream could see what the site had just set.
    The mapping shape is handled first now; the sequence shapes are kept because
    the browser tier builds its own cookie list.

    Only names and values survive this: the client gives us no ``Domain=`` /
    ``Path=`` / ``Max-Age`` attributes, which is why the jar in sessions.py is
    host-scoped and TTL-capped instead of a reimplementation of RFC 6265.
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
    """Port a URL actually dials: the explicit one, or the scheme's default."""
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
    """Build the headers for ONE request in a redirect chain.

    Two things this fixes over resending one header dict down the chain:

    * **Credentials no longer ride a cross-origin redirect.** The old code built
      the ``Cookie`` header once and reused it for every hop, so a 302 to a host
      we never agreed to talk to carried the session cookie (and an
      ``Authorization`` set by the caller) straight to it — a site chosen by the
      first server, not by us. Browsers and curl drop these on a cross-origin
      redirect; so do we now.
    * **The jar is consulted per hop**, by the host actually being dialed, so a
      redirect into a host we have cookies for still presents them (that is what
      makes a redirected login flow work) while the caller's explicit
      ``cookies=`` stay bound to the origin they named — explicit wins over the
      jar for that one host.

    ``extra_credentials`` extends the drop set for one call: an API key in
    ``X-API-Key`` is a credential too, and the built-in name list cannot know
    what the caller chose to call theirs.

    ``origin_url`` tightens the same-origin test to the browser's reading:
    equal scheme AND effective port, not just equal host. Without it the
    pre-G25 hole stands - an https→http redirect to the SAME host kept
    ``Authorization`` and sent it over plaintext. The production call site
    always passes it; the empty default keeps direct callers (and the tests
    that drive this function directly) on the host-only reading.
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
        # Drop first: the base may already carry a Cookie in either case, and two
        # cookie lines in one dict is a header the server resolves unpredictably.
        _drop_header_ci(hop, "cookie")
        hop["cookie"] = "; ".join(f"{k}={v}" for k, v in sorted(merged.items()))
    elif not same_origin:
        # Nothing to send here. A cookie the caller put in `extra_headers` stays
        # on the same-origin request (it is theirs to place); off-origin it goes,
        # because that is the case the redirect could not be trusted for.
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
    """Async HTTP fetch session using primp for TLS impersonation.

    Replaces scrapling's FetcherSession. Provides:
    - TLS fingerprint impersonation (Chrome, Firefox, Safari, Edge)
    - Proxy support (http, https, socks5)
    - Retry with backoff
    - Stealthy headers (Google referer, realistic User-Agent) —— 一场进程一副身份，
      跨会话跨调用都读同一份，且 primp 的 OS 跟着**这份**身份选
      （见 `_process_identity` 与 `_stealthy_baseline` 的实测理由）

    Usage:
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
        """Initialize the primp client in a worker thread (import is ~1s)."""
        impersonate = self._impersonate
        if isinstance(impersonate, list):
            # primp doesn't support rotation pools like scrapling's curl_cffi.
            # Pick a random one from the pool.
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
        """Close the underlying client."""
        # primp.Client doesn't have an explicit close, but we drop the reference
        self._client = None

    @staticmethod
    def _drop_header(headers: Dict[str, str], name: str) -> None:
        """Remove every spelling of ``name`` (HTTP header names are case-insensitive)."""
        for existing in [k for k in headers if k.lower() == name]:
            del headers[existing]

    def _build_headers(
        self,
        headers: Optional[Dict[str, str]] = None,
        useragent: Optional[str] = None,
    ) -> Dict[str, str]:
        """Build request headers with stealthy defaults."""
        final_headers: Dict[str, str] = {}
        if self._stealthy_headers:
            final_headers["referer"] = "https://www.google.com/"
            # 会话里那份**定好的**身份，不是每次重摇（`_stealthy_baseline` 记了实测）。
            for k, v in self._baseline.items():
                if k != "referer":
                    final_headers.setdefault(k, v)
        # User-supplied headers override defaults
        if headers:
            for k, v in headers.items():
                if k.lower() == "user-agent":
                    # browserforge already set "user-agent"; a caller's
                    # "User-Agent" would otherwise ride along as a second header.
                    self._drop_header(final_headers, "user-agent")
                final_headers[k] = v
        # The useragent option outranks everything, including a user-agent
        # passed through extra_headers.
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
        """Fetch a URL via HTTP with TLS impersonation.

        Returns a Response object.

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
        # Coerce follow_redirects from scrapling-style string to bool.
        # Scrapling accepted: "safe", "always", "never". primp expects bool.
        if isinstance(follow_redirects, str):
            follow_redirects = follow_redirects.lower() != "never"

        if self._client is None:
            await self._init_client()

        client = self._client
        if proxy:
            # Override proxy for this request: create a new client
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
                # primp's request() is synchronous, wrap in to_thread
                def _do_get():
                    # Build query params
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
                    meta_from = ""      # first <meta refresh> source in this chain
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
                        # A 3xx can carry the Set-Cookie that matters (a login that
                        # redirects is the usual shape), so every hop's cookies are
                        # kept, not just the last response's.
                        if session_id:
                            sessions.store(session_id, current, _resp_cookies(resp))
                        if not (follow_redirects and resp.status_code in (301, 302, 303, 307, 308)):
                            # G29 — the document can redirect even when the status
                            # line did not. Following it HERE (rather than in the
                            # caller) is what makes a meta hop pass the same checks
                            # as a 3xx hop: validate_url on the target, the shared
                            # max_redirects budget, and _hop_headers' rule that
                            # credentials do not cross an origin. HEAD is excluded:
                            # a probe has no body to read the tag from, and
                            # following it would download the page the caller asked
                            # not to download.
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
                            # A HEAD answer must not carry a body, so the redirect
                            # header IS the useful part: Location, status, length.
                            # Following it would turn the probe into a GET that
                            # downloads the page the caller asked not to download.
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
                    # Budget exhausted: report where the LAST response came from,
                    # not the hop we were about to dial. The previous return handed
                    # back the pending target, which told the caller it had fetched
                    # a page it never asked a server for.
                    return (resp, (str(resp.url) if hasattr(resp, "url") and resp.url
                                   else current), meta_from, meta_delay, meta_hops)

                # A meta hop moved the address for a different reason than a 3xx
                # would: the server said 200 and the page said "over there". Both
                # facts travel out to the response so the caller can tell "the site
                # moved" apart from "this page is a pointer".
                resp, final_url, meta_from, meta_delay, meta_hops = await asyncio.to_thread(_do_get)

                # Parse encoding from content-type
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
                # A refusal from the SSRF guard cannot be fixed by asking again.
                # Retrying re-dials the origin and re-walks the same chain to the
                # same answer, so the only thing a retry produces is a second
                # request to a host that was just refused.
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

        # Should not reach here, but just in case
        raise last_error or RuntimeError(f"Failed to fetch {url}")


# ─── Convenience function ─────────────────────────────────────────────────────

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
    """One-off async HTTP fetch with TLS impersonation.

    Usage:
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


# ─── TCP preflight check ────────────────────────────────────────────────────────

def proxy_preflight(proxy_url: str, timeout: float = 5.0) -> tuple[bool, str]:
    """Quick TCP connect to a PROXY endpoint, so a dead proxy fails fast (G21).

    Returns ``(reachable, error_category)``; the category is ``proxy_unreachable``
    when the endpoint did not accept a connection, else ``""``.

    Why this exists: with a proxy configured, ``web_read`` skipped its TCP
    preflight (probing the TARGET directly would misreport, since the HTTP tier
    talks to the proxy). So a dead proxy had nothing to fail fast on — measured:
    ``proxy='http://127.0.0.1:9/'`` burned the whole 30000ms budget, escalated to
    the browser tier (which dials the SAME dead proxy), and then reported
    "the 30000ms call budget ran out ... raise timeout for a slow host" — a wrong
    diagnosis pointing at the wrong knob.

    One opaque category for EVERY failure mode, deliberately. Distinguishing
    "refused" from "no route" from "timed out" for an arbitrary host:port would
    turn this into the same port-scan oracle ``tcp_preflight`` flattens its answer
    to avoid for internal targets. Here the caller supplied the proxy, so "your
    proxy did not answer" tells them nothing they did not already know - but the
    probe must not say anything finer than that.

    A connect timeout counts as unreachable: no proxy takes 5s to accept a TCP
    connection, and treating a filtered port as "maybe fine" would leave exactly
    the silent-30s case this exists to remove. Synchronous; call via
    ``asyncio.to_thread``. Never raises.
    """
    import socket
    try:
        parsed = urlparse(proxy_url or "")
        host = parsed.hostname
        if not host:
            return True, ""  # not a URL we can probe - let the real attempt decide
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
    # Windows: os error 10061 = connection refused, 10054 = reset
    err_str = str(error)
    if "10061" in err_str or "refused" in err_str.lower():
        return "connection_refused"
    if "10054" in err_str or "reset" in err_str.lower():
        return "connection_reset"
    return "unknown"


def tcp_preflight(url: str, timeout: float = 2.0) -> tuple[bool, str]:
    """Quick TCP connect check to fail fast before a full HTTP/browser fetch.

    Returns (reachable: bool, error_category: str). error_category is empty
    when reachable, otherwise one of: connection_refused, connection_reset,
    dns_unresolvable, private_target_blocked, timeout, unknown.

    Synchronous — call from a worker thread via asyncio.to_thread().
    Never raises.
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
