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
import re
from typing import Any, Dict, List, Optional, Sequence, Union
from urllib.parse import urljoin, urlparse

import primp

from dhole_mcp.charset import misdecode_score as _misdecode_score
from dhole_mcp import sessions
from dhole_mcp.security import SecurityError, redact_api_key

logger = logging.getLogger("dhole_mcp.fetcher")


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
            # parse() always returns a full tree with <html> as root.
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

# Methods whose retry is safe: resending the same request cannot leave the server
# in a different state, so a timeout is a retry rather than a possible duplicate.
# POST/PATCH are excluded — the previous attempt may well have landed.
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})


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


def _hop_headers(
    base: Dict[str, str],
    url: str,
    origin_host: str,
    cookies: Optional[Dict[str, str]],
    session_id: str,
    extra_credentials: Sequence[str] = (),
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
    """
    hop = dict(base)
    host = _host_of(url)
    same_origin = bool(host) and host == origin_host
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



class HTTPSession:
    """Async HTTP fetch session using primp for TLS impersonation.

    Replaces scrapling's FetcherSession. Provides:
    - TLS fingerprint impersonation (Chrome, Firefox, Safari, Edge)
    - Proxy support (http, https, socks5)
    - Retry with backoff
    - Stealthy headers (Google referer, realistic User-Agent)

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

        def _create():
            kwargs: Dict[str, Any] = {
                "impersonate": impersonate,
                "impersonate_os": "random",
            }
            if self._proxy:
                kwargs["proxy"] = self._proxy
            return primp.Client(**kwargs)

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
            try:
                from browserforge.headers import HeaderGenerator
                hg = HeaderGenerator()
                generated = hg.generate()
                # Merge browserforge headers (lowercase keys)
                for k, v in generated.items():
                    if k.lower() not in ("referer",):
                        final_headers.setdefault(k.lower(), v)
            except Exception:
                pass
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
        （options.auth 放出去的 ``X-API-Key`` 之类）。只在**跨源重定向**时被丢掉，
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
        if method not in _IDEMPOTENT_METHODS:
            # 非幂等方法不重试：上一次尝试很可能已经落库，重试就是再写一遍。
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
                            credential_headers)
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

    Why this exists: with a proxy configured, ``smart_fetch`` skipped its TCP
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


def tcp_preflight(url: str, timeout: float = 2.0) -> tuple[bool, str]:
    """Quick TCP connect check to fail fast before a full HTTP/browser fetch.

    Returns (reachable: bool, error_category: str). error_category is empty
    when reachable, otherwise one of: connection_refused, dns_failure, timeout.

    Synchronous — call from a worker thread via asyncio.to_thread().
    Never raises.
    """
    import socket
    try:
        from dhole_mcp.security import url_targets_internal
        # 这个"快速可达性探测"本身就是一次对内网的裸 TCP 连接，而且它的分类结果会
        # 原样回报给 agent（connection_refused vs dns_failure = 端口开/关的信号），
        # 等于一个内网端口扫描 oracle。先判一次，命中内网时返回与"真的不可达"**同形**
        # 的类别，不给指纹留下可区分的东西。
        if url_targets_internal(url):
            return False, "dns_failure"
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            return False, "dns_failure"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True, ""
    except ConnectionRefusedError:
        return False, "connection_refused"
    except socket.gaierror:
        return False, "dns_failure"
    except (socket.timeout, TimeoutError):
        return False, "timeout"
    except OSError as e:
        # Windows: os error 10061 = connection refused, 10054 = reset
        err_str = str(e)
        if "10061" in err_str or "refused" in err_str.lower():
            return False, "connection_refused"
        if "10054" in err_str or "reset" in err_str.lower():
            return False, "connection_reset"
        return False, "unknown"
    except Exception:
        return False, "unknown"
