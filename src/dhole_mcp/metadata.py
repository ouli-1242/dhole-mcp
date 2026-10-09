"""Page metadata extraction for Dhole.

Enriches every HTML fetch response with structured metadata an agent can use to
judge relevance and cite sources: title, description, site name, type, image,
canonical URL, language, published time, and author. Pulled from OpenGraph meta
tags, JSON-LD blocks, the canonical link, and the <title> tag.

Kept dependency-free (regex + json) so it runs cheaply on every fetch. Only
populated for HTML pages; JSON / PDF / image responses get an empty dict.
"""

from __future__ import annotations

import json
import logging
import re
# 本模块的参数名 html 遮蔽 stdlib 的 html 模块——直接取 unescape 函数。
from html import unescape as _html_unescape
from typing import Any
from urllib.parse import urljoin, urlparse

logger = logging.getLogger("dhole-mcp.metadata")

# Match <meta property/name="KEY" content="VAL"> in either attribute order.
_META_RE = re.compile(
    r'<meta\b[^>]*?(?:property|name)=["\']([^"\']+)["\'][^>]*?content=["\']([^"\']*)["\']',
    re.IGNORECASE,
)
_META_RE_REV = re.compile(
    r'<meta\b[^>]*?content=["\']([^"\']*)["\'][^>]*?(?:property|name)=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_CANONICAL_RE = re.compile(
    r'<link\b[^>]*?rel=["\']canonical["\'][^>]*?href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_LANG_RE = re.compile(r'<html\b[^>]*?\blang=["\']([^"\']+)["\']', re.IGNORECASE)
_LD_RE = re.compile(
    r'<script\b[^>]*?type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
# Lazy-loaded pages often put a placeholder (or a tiny data: URI) in src and
# the real image in data-src. Match whole <img> tags so both attributes can be
# read from the same tag; src wins, data-src is the fallback.
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
# (?<!-) keeps \bsrc= from matching inside "data-src=" ('-' is a non-word char,
# so a bare \b WOULD match there).
_IMG_SRC_RE = re.compile(r'(?<!-)\bsrc=["\']([^"\']+)["\']', re.IGNORECASE)
_IMG_DATASRC_RE = re.compile(r'\bdata-src=["\']([^"\']+)["\']', re.IGNORECASE)
_ASSET_SKIP = ("data:image",)

# Map meta keys to our flat field names. First match wins per field (OpenGraph
# takes priority over Twitter/Dublin Core, etc.).
_KEY_MAP = {
    "og:title": "title", "twitter:title": "title",
    "og:description": "description", "description": "description", "twitter:description": "description",
    "og:site_name": "site_name",
    "og:type": "type",
    "og:image": "image", "twitter:image": "image",
    "og:url": "og_url",
    "article:published_time": "published_time",
    "article:modified_time": "modified_time",
    "article:author": "author", "author": "author",
}


def extract_metadata(html: str, url: str) -> dict[str, Any]:
    """Extract a flat metadata dict from an HTML string. Empty if no HTML."""
    meta: dict[str, Any] = {}
    if not html:
        return meta

    # OpenGraph / meta tags (check both attribute orders).
    for rx in (_META_RE, _META_RE_REV):
        for m in rx.finditer(html):
            if rx is _META_RE:
                key, val = m.group(1), m.group(2)
            else:  # reversed: group(1)=content, group(2)=key
                key, val = m.group(2), m.group(1)
            key = key.lower().strip()
            val = val.strip()
            if not val:
                continue
            field = _KEY_MAP.get(key)
            if field and field not in meta:
                # 与正文同一套实体口径：content 已解码，title 带着原始 &amp;
                # 出引用就是两套口径。
                meta[field] = _html_unescape(val)[:500]

    # <title> fallback.
    if "title" not in meta:
        t = _TITLE_RE.search(html)
        if t:
            title = _html_unescape(re.sub(r"\s+", " ", t.group(1)).strip())
            if title:
                meta["title"] = title[:500]

    # Canonical URL.
    c = _CANONICAL_RE.search(html)
    if c:
        try:
            meta["canonical"] = urljoin(url, c.group(1).strip())
        except Exception:
            pass

    # html lang.
    if "lang" not in meta:
        lang_match = _LANG_RE.search(html)
        if lang_match:
            meta["lang"] = lang_match.group(1).strip()

    # JSON-LD: datePublished / author / description / headline.
    for m in _LD_RE.finditer(html):
        raw = m.group(1).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        objs = data if isinstance(data, list) else [data]
        for obj in objs:
            if not isinstance(obj, dict):
                continue
            if "published_time" not in meta:
                d = obj.get("datePublished") or obj.get("dateCreated")
                if d:
                    # 40 不是 20。JSON-LD 的日期是 ISO-8601，而 `[:20]` 恰好卡在
                    # 时区偏移的中间：'2026-03-13T14:17:06+08:00' 进来、
                    # '2026-03-13T14:17:06+' 出去 —— 一个既不合法也不完整的串，被
                    # 原样端进 transcript 当引用依据（2026-10-05 实测）。日期字段
                    # 的上限只该防"有人把整个对象塞进来"，不该防它自己的格式。
                    meta["published_time"] = str(d).strip()[:40]
            if "title" not in meta:
                h = obj.get("headline") or obj.get("name")
                if h:
                    meta["title"] = _html_unescape(str(h))[:500]
            if "description" not in meta:
                d = obj.get("description")
                if d:
                    meta["description"] = str(d)[:300]
            if "author" not in meta:
                a = obj.get("author")
                if isinstance(a, dict):
                    a = a.get("name")
                elif isinstance(a, list) and a:
                    a0 = a[0]
                    a = a0.get("name") if isinstance(a0, dict) else a0
                if a:
                    meta["author"] = str(a)[:200]

    return meta


_IMG_LIST_SEP_RE = re.compile(r",\s+")


def _src_candidates(raw: str) -> list[str]:
    """把一个属性值拆成候选 URL。

    两条理由：(1) HTML 实体要还原 —— `&amp;` 不解开，交出去的 URL 带着
    `&amp;`，请求方一定拿 404；(2) 懒加载清单会把多个候选写进同一个属性
    （实测 Bing：`//th.bing.com, https://r.bing.com/th?id=...&amp;pid=2.1`），
    整串喂给 urljoin 会原样通过 `http(s)://` 前缀检查，于是 include_media
    交出的是一个含两个地址的字符串，而不是两个地址。

    分隔符要求逗号后跟空白：路径里合法的裸逗号（`/a,b.png`）不该被拆。
    """
    unescaped = _html_unescape(raw or "")
    return [p.strip() for p in _IMG_LIST_SEP_RE.split(unescaped) if p.strip()]


def extract_image_urls(html: str, url: str, max_n: int = 20) -> list[str]:
    """Extract absolute image URLs from <img> tags. Deduped, order-preserving,
    capped at max_n. Skips data: URIs. Prefers src; falls back to data-src for
    lazy-loaded pages (placeholder in src, real image in data-src). Used by
    web_read's opt-in include_media flag so a multimodal agent can pull the
    page's images."""
    if not html:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def _collect(src: str) -> bool:
        """True once max_n is reached, so the caller stops scanning."""
        for cand in _src_candidates(src):
            if cand.lower().startswith(_ASSET_SKIP):
                continue
            try:
                absu = urljoin(url, cand)
            except Exception:
                continue
            if not absu.startswith(("http://", "https://")):
                continue
            # 一个地址不该含空白、也不该有第二个 scheme：那是「两个 URL 挤在
            # 一个属性值里」的残留，交出去只会让调用方以为拿到了一张图。
            if any(c.isspace() for c in absu) or absu.count("://") != 1:
                continue
            if not urlparse(absu).netloc:
                continue
            if absu in seen:
                continue
            seen.add(absu)
            out.append(absu)
            if len(out) >= max_n:
                return True
        return False

    for tag in _IMG_TAG_RE.finditer(html):
        tag_html = tag.group(0)
        src = ""
        m = _IMG_SRC_RE.search(tag_html)
        if m:
            src = m.group(1) or ""
        if not src.strip() or src.strip().lower().startswith(_ASSET_SKIP):
            m = _IMG_DATASRC_RE.search(tag_html)
            if m:
                src = m.group(1) or ""
        if not src.strip():
            continue
        if _collect(src):
            break
    return out
