"""smart_crawl — flagship same-domain deep crawl for Dhole, optimized for agents.

Walks a website from a start URL and returns each page as agent-usable markdown
with the same honest signals smart_fetch produces (content_ok / summary / error /
fetched_at). Designed to beat free OSS crawlers (Crawl4AI, Firecrawl free tier,
Jina) on agent-usability: honest per-page quality signals, content-adaptive
extraction, and next_action guidance instead of a dumb page dump.

Flagship design (v6):
  * **Best-first priority queue** (not just BFS). Discovered URLs are scored and
    the highest-value page is crawled next. The scorer blends: focus-query
    relevance (anchor text + URL path tokens), content-likelihood (boost
    docs/guide/api/reference/article/blog, penalize login/submit/register/cart/
    admin/account), and a shallow-depth preference. With no `focus` this still
    beats raw BFS by skipping junk URLs (login/submit) before content pages.
  * **Content-adaptive per-page extraction.** A page is classified from its HTML
    and extracted the right way:
      - article/docs  -> trafilatura main content (markdown).
      - list/index    -> a structured `* [title](url)` link list (HN, aggregators,
                         directory pages where trafilatura's main-content filter
                         returns nothing because the page IS a list of links).
      - js-shell      -> the fetch already auto-escalated HTTP->stealthy render;
                         if even the rendered HTML is empty, content_ok=false
                         with an actionable error (use screenshot / vision).
      - fallback      -> cleaned visible text.
    This fixes the "0 content_ok on Hacker News" and "JS SPA timeout" class of
    bugs at the root: list pages now return their link list as content, and JS
    shells are detected and reported honestly instead of silently empty.
  * **URL normalization + dedup.** Trailing slashes, default ports, lowercase
    host, and tracking query params (utm_*, fbclid, gclid, ref, _) are stripped
    before dedup, so `/docs` and `/docs/` are no longer crawled twice.
  * **Two-phase crawl.** `discover_only=true` returns the URL map (prefetch);
    pass `crawl_urls=[...]` to fetch a chosen subset in a second phase without
    re-discovering (selective deep crawl). Map mode extracts no page content, but
    it is not free: expanding the map means fetching one page per step, so it
    costs up to `max_pages` requests (10 by default) and `pages_crawled` reports
    exactly how many were made. For a one-request map use `max_pages=1` (the
    start URL's own links) or `sitemap=true` (the whole site, if it has one).
  * **same_domain_only=true** default. External links are dropped (not crawled).
  * **Honest status.** Network failures report status -1 (documented) so
    downstream logic can distinguish them from a real HTTP 0 / no response.
  * **Freshness.** Each page carries `fetched_at`; `cache_ttl=0` forces fresh.
  * **Overall deadline.** One slow page can't hang the crawl; when the deadline
    is hit, partial results are returned with `truncated_by_time=true`.
  * One fetch per page (extraction_type='html'), reusing smart_fetch's anti-bot
    escalation + fetch cache. Links + markdown are derived from the same body.
"""

from __future__ import annotations

import asyncio
import heapq
import logging
import re
from time import time
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse

from pydantic import BaseModel, Field, model_serializer

# robots.txt compliance (v16.0). robots.py imports nothing from dhole at module
# level, so this is cycle-free; the seed check + the discovered-URL peek both
# need it, and keeping one import point is what makes the cache the shared one.
from dhole_mcp.robots import check as check_robots, get_robots_cache

logger = logging.getLogger("dhole-mcp.crawl")

# Match <a ... href="..."> and capture the href + the anchor's visible text.
_LINK_RE = re.compile(
    r'<a\b[^>]*\bhref=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
# Skip these asset extensions when discovering links (they aren't pages).
_ASSET_RE = re.compile(
    r"\.(png|jpe?g|gif|webp|svg|css|js|ico|pdf|zip|mp[34]|woff2?|gz|tar|exe|dmg)(\?|$)",
    re.IGNORECASE,
)
_SKIP_SCHEMES = ("javascript:", "mailto:", "tel:", "data:", "#")

# Floor for a sitemap URL map (see _sitemap_map). A map's value is breadth, so it
# does not track max_pages the way content does; max_pages only widens it past
# this floor once it exceeds SITEMAP_MAP_FLOOR // 10.
SITEMAP_MAP_FLOOR = 1000

# Tracking / analytics query params that don't change page content -> stripped
# during normalization so two URLs differing only in these don't get crawled twice.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "ref_src", "source", "_ga", "mc_cid", "mc_eid",
}

# Single-call content ceiling: an explicit max_total_chars is clamped here.
# Deliberately a capability ceiling, not a default - the derived default budget
# stays max_pages * max_content_chars_per (80,000 at defaults), so callers who
# never ask see zero change. 1M chars is ~250k tokens in one response; past it
# the sane delivery unit is crawl_urls=[...] in phases, but an explicit ask is
# granted rather than forbidden.
MAX_TOTAL_CHARS = 1_000_000



# Content-likelihood path tokens. Boost content pages, penalize app/admin noise
# so the priority queue crawls docs before login/submit/cart.
_CONTENT_BOOST = ("doc", "docs", "guide", "tutorial", "api", "reference",
                  "article", "blog", "post", "learn", "manual", "help", "spec")
_JUNK_PENALTY = ("login", "signin", "sign-in", "signup", "sign-up", "register",
                 "submit", "cart", "checkout", "account", "admin", "logout",
                 "auth", "password", "settings", "preferences")


def _is_transient_error(error_str: str) -> bool:
    """True if the error is transient (worth retrying). Timeout and connection
    reset may succeed on retry; connection refused and DNS failure will not."""
    from dhole_mcp.errors import classify_network_error
    category, _ = classify_network_error(error_str)
    return category in ("timeout", "connection_reset", "unknown")


class CrawlPage(BaseModel):
    url: str = Field(description="Page URL (final, after redirects). Normalized.")
    depth: int = Field(default=0, description="Hop depth from the start URL (0 = start).")
    status: int = Field(default=0, description="HTTP status. -1 = network error (no response / connection failed). 0 = no response yet.")
    content_ok: bool = Field(default=False, description="True = real content retrieved AND extracted. Trust content only if true. HTTP 200 alone does NOT set this.")
    fetcher_used: str = Field(default="", description="http/stealthy/cache/none")
    title: str = Field(default="", description="Page <title>")
    page_type: str = Field(default="", description="How the page was extracted: article / list / js_shell / fallback / discover_only. Tells the agent what kind of content it got.")
    content: list[str] = Field(default=[], description="Page markdown (empty in discover_only mode). For list pages, a structured link list.")
    content_chars: int = Field(default=0, description="Chars of markdown returned for this page.")
    is_truncated: bool = Field(default=False, description="True = this page has more content; smart_fetch it with offset=next_offset.")
    next_offset: int = Field(default=0, description="Next offset if is_truncated; 0 = no more.")
    fetched_at: str = Field(default="", description="ISO-8601 UTC when this page was fetched (may show cache age).")
    lastmod: str = Field(default="", description="<lastmod> from the site's sitemap.xml for this URL (sitemap mode only). Empty otherwise.")
    summary: str = Field(default="", description="One-line status for this page.")
    error: str = Field(default="", description="Error for this page, if content_ok is False.")


class CrawlResponseModel(BaseModel):
    start_url: str = Field(description="The URL crawl started from (normalized).")
    pages: list[CrawlPage] = Field(description="Crawled pages (one CrawlPage each). In discover_only, content is empty and pages hold the discovered URL map.")
    pages_crawled: int = Field(default=0, description="Pages actually fetched.")
    pages_discovered: int = Field(default=0, description="Total unique same-domain URLs found (including crawled).")
    discover_only: bool = Field(default=False, description="True = map mode (URLs only, no content).")
    truncated_by_budget: bool = Field(default=False, description="True = stopped early because the total-char budget was reached.")
    truncated_by_max_pages: bool = Field(default=False, description="True = stopped early because max_pages was reached.")
    truncated_by_time: bool = Field(default=False, description="True = stopped early because the overall deadline (ms) was reached.")
    sitemap_used: bool = Field(default=False, description="True = the URL map came from the site's sitemap.xml (one fetch), not best-first BFS discovery.")
    sitemaps: list[str] = Field(default=[], description="Sitemap.xml URLs that were fetched + parsed (sitemap mode only).")
    robots_skipped: int = Field(default=0, description="Discovered URLs dropped WITHOUT a request because their robots.txt disallows them (dhole matches its own user agent, 'dhole-mcp'). Not an error - those pages were never eligible. Pass ignore_robots=true if you have the site's permission to fetch them anyway.")
    urls_supplied: int = Field(default=0, description="How many URLs the caller passed in crawl_urls (0 unless this was a crawl_urls run). Compare with pages_crawled to see whether entries were dropped.")
    urls_deduped: int = Field(default=0, description="crawl_urls entries dropped as repeats of an earlier entry AFTER normalization, so `/a` and `/a/` count as the same page. Not an error.")
    urls_dropped_off_domain: int = Field(default=0, description="crawl_urls entries dropped because their host differs from the start URL's (smart_crawl is same-domain). Not an error - use smart_fetch for those URLs. Counting scope, because the name reads broader than it is: this counts ONLY entries the CALLER passed. Links discovered while crawling a page that point off-domain are not counted here and never were candidates - the crawler keeps same-domain links by rule, so they are filtered at extraction, not dropped as an exception worth reporting.")
    urls_dropped_over_max_pages: int = Field(default=0, description="crawl_urls entries dropped because max_pages capped the list. Raise max_pages or split the list across calls to fetch them.")
    duration_ms: float = Field(default=0, description="Duration ms.")
    error: str = Field(default="", description="Error message (crawl-level).")
    summary: str = Field(default="", description="One-line crawl status.")
    next_action: str = Field(default="", description="Obvious next call when one exists (continue crawl / fetch a page).")

    @model_serializer(mode="wrap")
    def _map_rows_carry_only_what_differs(self, handler):
        """In map mode a row is the URL, not a page report.

        A URL map is the one output whose size is set by the site rather than by
        what the caller asked for, and the flow the tool description recommends
        (map once with sitemap=true, then choose with crawl_urls) hit it:
        docs.vllm.ai serialized 1000 entries to 353KB of JSON for 70KB of URLs —
        5.0x, because every row restated status/depth/content/content_chars/
        is_truncated/next_offset/fetched_at/error as the same default and
        page_type/fetcher_used/summary as the same constant. ~151k tokens in one
        call is not a map an agent can read; it is a context-window accident that
        punishes exactly the caller who followed the documented path.

        So in map mode a field is emitted only when it differs from its default,
        page_type goes in every map (the top-level discover_only flag states it),
        and in sitemap mode the other three per-row constants go too
        (sitemap_used already states them). Content mode is untouched: those
        rows differ from each other, which is the whole test.
        """
        data = handler(self)
        if not self.discover_only:
            return data
        defaults = {name: f.default for name, f in CrawlPage.model_fields.items()}
        # page_type is constant in map mode BY CONSTRUCTION: _classify sets it to
        # "discover_only" and every other assignment sits behind `if not
        # discover_only` (crawl.py:1042), while the sitemap path reports
        # "sitemap" and is covered by the sitemap_used branch below. The
        # top-level discover_only flag already states the same thing, so the
        # column repeats one value across a map whose length is set by the site
        # rather than by the caller - 28 chars a row is 28KB on a 1000-URL map,
        # and no row was ever distinguished by it.
        constant = ("content_ok", "fetcher_used", "page_type", "summary") \
            if self.sitemap_used else ("page_type",)
        rows = []
        for page in self.pages:
            rows.append({k: v for k, v in page.model_dump().items()
                         if k not in constant and v != defaults.get(k)})
        data["pages"] = rows
        return data


def _same_root(start_url: str) -> str:
    """Normalized root (scheme://netloc) for same-domain filtering."""
    p = urlparse(start_url)
    return f"{p.scheme or 'https'}://{p.netloc}"


def normalize_url(u: str) -> str:
    """Canonicalize a URL for dedup: lowercase host, drop default ports, strip
    tracking query params, drop the fragment, and collapse a trailing slash on
    non-root paths so `/docs` and `/docs/` compare equal. Preserves real query
    params (e.g. pagination `?page=2`)."""
    try:
        p = urlparse(u)
    except Exception:
        return u
    scheme = (p.scheme or "https").lower()
    host = (p.netloc or "").lower()
    # Strip default ports.
    if host.endswith(":80") and scheme == "http":
        host = host[:-3]
    elif host.endswith(":443") and scheme == "https":
        host = host[:-4]
    path = p.path or "/"
    # Collapse trailing slash on non-root paths ("/docs/" -> "/docs").
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    # Drop tracking query params; keep the rest (order preserved).
    if p.query:
        kept = [kv for kv in p.query.split("&")
                if kv and kv.split("=", 1)[0].lower() not in _TRACKING_PARAMS]
        query = "&".join(kept)
    else:
        query = ""
    return urlunparse((scheme, host, path, p.params, query, ""))


def _link_list_markdown(html: str, base_url: str, start_url: str, max_items: int = 200) -> str:
    """Render a list/index page as a structured markdown link list.

    Used when trafilatura's main-content filter returns nothing because the page
    IS a directory of links (Hacker News, aggregators, section index pages).
    Returns the same-domain links as `* [anchor text](url)` so the agent gets
    the page's actual content (the list of items) instead of an empty page.
    """
    pairs = extract_same_domain_links(html, base_url, start_url)
    if not pairs:
        return ""
    lines = []
    for href, text in pairs[:max_items]:
        text = (text or "").strip() or "(no title)"
        if len(text) > 160:
            text = text[:157] + "..."
        lines.append(f"* [{text}]({href})")
    return "\n".join(lines)


def _visible_text(html: str) -> str:
    """Cheap visible-text extraction: strip script/style blocks + tags, collapse
    whitespace. Used as a last-resort fallback and for page-type signals."""
    if not html:
        return ""
    no_ss = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", no_ss)
    return re.sub(r"\s+", " ", text).strip()


def _page_signals(html: str) -> tuple[int, int, int, bool, int]:
    """Cheap signals for page classification: (visible_text_len, script_count,
    link_count, has_framework_root, link_text_len)."""
    if not html:
        return (0, 0, 0, False, 0)
    text_len = len(_visible_text(html))
    script_count = len(re.findall(r"<script\b", html, re.IGNORECASE))
    link_text_len = 0
    link_count = 0
    for m in _LINK_RE.finditer(html):
        link_count += 1
        link_text_len += len(_TAG_RE.sub(" ", m.group(2) or "").strip())
    has_fw = bool(re.search(
        r'id="root"|id="__next"|__NUXT__|__NEXT_DATA__|data-reactroot|<div id="app"',
        html, re.IGNORECASE))
    return (text_len, script_count, link_count, has_fw, link_text_len)


def _classify_and_extract(html: str, url: str, start_url: str, focus: Optional[str],
                          max_chars: int) -> tuple[str, str, bool, bool]:
    """Content-adaptive extraction. Returns (markdown, page_type, content_ok, cut).

    page_type is one of: article / list / js_shell / fallback. content_ok is
    False only when we genuinely got nothing usable (js_shell that didn't
    render, or an empty/error page). cut is True when the page held more than
    max_chars and this function returned only the first max_chars of it.
    """
    from dhole_mcp.trafilatura_extractor import extract_content_from_html
    from dhole_mcp.focus import focus_content

    md = ""
    try:
        md = extract_content_from_html(html, url, "markdown") or ""
    except Exception:
        md = ""

    text_len, script_count, link_count, has_fw, link_text_len = _page_signals(html)

    # 1) List / index page: dominated by links (most visible text is anchor
    #    text). Takes priority over 'article' because trafilatura returns the
    #    link texts as 'content' for HN/aggregator pages, but the page IS a list.
    #    Render the same-domain links as a structured `* [title](url)` list so
    #    the agent gets the page's actual content (the items).
    link_density = (link_text_len / text_len) if text_len else (1.0 if link_count else 0.0)
    if link_count >= 10 and link_density >= 0.5:
        list_md = _link_list_markdown(html, url, start_url)
        if list_md:
            md = list_md
            kind = "list"
        else:
            kind = "fallback"
    # 2) Article / docs: trafilatura found real main content (prose, not links).
    elif md and len(md) >= 200:
        if focus:
            try:
                md = focus_content(md, focus)
            except Exception:
                pass
        kind = "article"
    # 3) List page with fewer/looser links but trafilatura still empty.
    elif link_count >= 10 and (len(md) < 200):
        list_md = _link_list_markdown(html, url, start_url)
        if list_md:
            md = list_md
            kind = "list"
        else:
            kind = "fallback"
    # 4) JS shell: little visible text + heavy scripts + framework root, and
    #    trafilatura got nothing. The fetch already tried stealthy render; if
    #    we still see a shell, the page didn't render -> honest failure.
    elif text_len < 400 and (script_count >= 5 or has_fw) and not md:
        kind = "js_shell"
        md = ""
    # 5) Fallback: cleaned visible text (better than nothing).
    else:
        md = md or _visible_text(html)
        kind = "fallback"

    content_ok = bool(md and len(md) >= 50 and kind != "js_shell")

    # Truncate to the per-page budget. `cut` is the answer the caller cannot
    # recover afterwards: once md has been sliced to max_chars, `len(md) >
    # max_chars` is false by construction, so testing the returned string can
    # never detect the truncation it just performed. That is why every crawl
    # page reported is_truncated=false while its own summary said "truncated".
    cut = False
    if md and len(md) > max_chars:
        md = md[:max_chars]
        cut = True
    return md, kind, content_ok, cut


# ─── Path scoping (path_include / path_exclude) ───────────────────────────────
#
# Measured before this was rewritten: a raw `path.startswith(p)` against the
# caller's string. Four failure modes, all of them silent:
#
#   path_include=["docs"]     -> 0 links kept. URL paths start with "/", so the
#                                most natural spelling scoped the entire crawl
#                                away and the call returned an empty result.
#   path_include=["/docs/*"]  -> 0 links kept. A glob is not a startswith string.
#   path_include=["/docs"]    -> kept "/docs-old/legacy" and "/docsomething".
#                                A shared prefix is not a subtree.
#   path_exclude=["docs"]     -> excluded nothing, so the caller believed it had
#                                scoped a crawl that it had not.
#
# The contract is now: a pattern names a path SUBTREE. It matches the section
# itself and everything beneath it at a segment boundary, never a sibling whose
# name merely starts with the same characters.
#
#   "docs"  "/docs"  "/docs/"  "/docs/*"   -> the /docs subtree
#   "/"     "*"      "/*"                   -> everything
#   "/api/*/v1"                             -> rejected, see below
#
# A trailing "/*" is accepted because it is what a caller writes when they mean
# "everything under here"; it is stripped rather than honoured as a glob, since
# the documented contract is a prefix scope and not a pattern language. Anything
# still holding a wildcard after that is REJECTED instead of quietly matching
# nothing: a crawl that returns zero pages for a reason the caller cannot see is
# the exact failure this rewrite exists to remove.

_WILDCARD_CHARS = ("*", "?", "[")


def normalize_path_pattern(pattern) -> str:
    """Normalize one include/exclude pattern to a path prefix.

    Raises ValueError on a non-string, an empty pattern, or an unsupported glob.
    """
    if not isinstance(pattern, str):
        raise ValueError(
            f"path_include/path_exclude entries must be strings, "
            f"got {type(pattern).__name__}: {pattern!r}"
        )
    p = pattern.strip()
    if not p:
        raise ValueError("path_include/path_exclude entries cannot be empty strings")
    if p in ("*", "/*"):
        return "/"
    if p.endswith("/*"):
        p = p[:-2]  # "/docs/*" is the subtree, spelled the way a caller writes it
    p = p.rstrip("/")
    if not p:
        return "/"
    if not p.startswith("/"):
        p = "/" + p  # "docs" and "/docs" are the same subtree
    bad = [c for c in _WILDCARD_CHARS if c in p]
    if bad:
        raise ValueError(
            f"path pattern {pattern!r} contains {''.join(bad)!r}, which is not supported. "
            f"path_include/path_exclude name a path subtree (e.g. '/docs', or '/docs/*' "
            f"for everything under it); the only wildcard form accepted is a trailing '/*'."
        )
    return p


def normalize_path_patterns(patterns) -> list[str]:
    """Normalize a caller's include/exclude list. Raises ValueError on a bad entry.

    A bare string is treated as the single pattern the caller meant - iterating
    it character by character made every path match, which is how
    ``path_exclude="/what/"`` used to filter the whole crawl away.
    """
    if not patterns:
        return []
    if isinstance(patterns, str):
        patterns = [patterns]
    return [normalize_path_pattern(p) for p in patterns]


def _normalize_url_path(path: str) -> str:
    """URL path -> the comparable form: leading slash, no trailing slash."""
    p = (path or "/").strip()
    if not p.startswith("/"):
        p = "/" + p
    p = p.rstrip("/")
    return p or "/"


def _path_in_scope(path: str, pattern: str) -> bool:
    """True when `path` IS the pattern's subtree, at a segment boundary.

    Both arguments must already be normalized (see _normalize_url_path /
    normalize_path_pattern).
    """
    if pattern == "/":
        return True
    return path == pattern or path.startswith(pattern + "/")


def path_allowed(path: str, path_include=None, path_exclude=None) -> bool:
    """The single decision point for BFS link discovery and the sitemap map.

    Patterns may be raw (they are normalized here) or pre-normalized; a bad
    pattern raises ValueError. Keeping one implementation is the point: the two
    call sites had drifted into carrying the same defect twice.
    """
    p = _normalize_url_path(path)
    include = normalize_path_patterns(path_include)
    exclude = normalize_path_patterns(path_exclude)
    if include and not any(_path_in_scope(p, pat) for pat in include):
        return False
    if exclude and any(_path_in_scope(p, pat) for pat in exclude):
        return False
    return True


def extract_same_domain_links(
    html: str,
    base_url: str,
    start_url: str,
    path_include: Optional[list[str]] = None,
    path_exclude: Optional[list[str]] = None,
) -> list[tuple[str, str]]:
    """Extract absolute same-domain (anchor URL, anchor text) pairs from HTML.

    Resolves relative URLs against base_url, keeps only links on the start URL's
    netloc, drops fragments/assets/non-http schemes, applies path include/exclude
    subtree scoping (see path_allowed), and dedupes by the NORMALIZED URL (so
    `/docs` and `/docs/` collapse to one). Order-preserving.
    """
    root_netloc = urlparse(start_url).netloc
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    if not html:
        return out
    # Normalized once per page rather than per link.
    include = normalize_path_patterns(path_include)
    exclude = normalize_path_patterns(path_exclude)
    for m in _LINK_RE.finditer(html):
        href = (m.group(1) or "").strip()
        if not href or href.lower().startswith(_SKIP_SCHEMES):
            continue
        try:
            absu = urljoin(base_url, href)
        except Exception:
            continue
        parsed = urlparse(absu)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc != root_netloc:
            continue  # external -> dropped (same_domain_only default)
        path = _normalize_url_path(parsed.path or "/")
        if include and not any(_path_in_scope(path, pat) for pat in include):
            continue
        if exclude and any(_path_in_scope(path, pat) for pat in exclude):
            continue
        if _ASSET_RE.search(path):
            continue
        clean = normalize_url(absu)
        if clean in seen:
            continue
        seen.add(clean)
        text = _TAG_RE.sub(" ", m.group(2) or "").strip()
        out.append((absu, text))  # original absolute URL (for fetching); dedup by `clean`
    return out


def _query_terms(query: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z0-9]+", query or "") if len(w) >= 2}


def score_link(url: str, text: str, focus: str) -> float:
    """Priority score for a discovered URL. Higher = crawl first.

    Blends:
      * focus relevance (anchor-text overlap * 2 + URL-path token overlap),
      * content-likelihood (path tokens: docs/guide/api boosted,
        login/submit/cart penalized),
      * a shallow-depth preference is applied by the caller via a depth term.
    """
    terms = _query_terms(focus)
    path_l = (urlparse(url).path or "").lower().replace("/", " ").replace("-", " ").replace("_", " ")
    text_l = (text or "").lower()
    focus_score = 0.0
    if terms:
        text_hit = sum(1 for t in terms if t in text_l) / len(terms)
        path_hit = sum(1 for t in terms if t in path_l) / len(terms)
        focus_score = text_hit * 2.0 + path_hit
    path_tokens = path_l.split()
    content_score = 0.0
    for tok in _CONTENT_BOOST:
        if tok in path_tokens:
            content_score += 0.5
    for tok in _JUNK_PENALTY:
        if tok in path_tokens:
            content_score -= 1.0
    return focus_score + content_score


def _error_status(resp) -> int:
    """Honest status: -1 for network errors (no HTTP response), else the real
    HTTP status. Lets downstream logic distinguish 'server returned 500' from
    'connection died'."""
    if resp is None:
        return -1
    status = getattr(resp, "status", 0) or 0
    if status == 0 and getattr(resp, "error", ""):
        return -1
    return status


def _sitemap_passes_filters(path: str, path_include: Optional[list[str]],
                            path_exclude: Optional[list[str]]) -> bool:
    """Kept as a named call site for the sitemap map; the decision itself lives
    in path_allowed so the two scoping paths cannot drift apart again."""
    return path_allowed(path, path_include, path_exclude)


async def _sitemap_map(url: str, path_include: Optional[list[str]],
                       path_exclude: Optional[list[str]], *,
                       max_pages: int, deadline_t: float) -> Optional[CrawlResponseModel]:
    """Try to map the site via sitemap.xml. Returns a CrawlResponseModel on
    success (sitemap found + parsed), or None if no sitemap was reachable so the
    caller can fall back to BFS ('auto'). Same-domain + path filters applied.
    Caps the returned URL map at max(1000, max_pages*10)."""
    from dhole_mcp.sitemap import discover_sitemap

    def _make_http_get():
        from dhole_mcp.search_proxy import get_next_proxy
        _sitemap_proxy = get_next_proxy()
        try:
            import primp  # type: ignore
            client = primp.Client(proxy=_sitemap_proxy, timeout=15, impersonate="random",
                                  impersonate_os="random", verify=True)
        except Exception:
            client = None
        import urllib.request as _urllib_req

        def _get(u: str):
            if client is not None:
                try:
                    r = client.get(u)
                    if r.status_code == 200 and r.content:
                        return (int(r.status_code), bytes(r.content))
                    return None
                except Exception:
                    pass  # fall back to urllib
            # stdlib fallback (some hosts reject primp fingerprints, accept urllib)
            try:
                req = _urllib_req.Request(u, headers={"User-Agent": "Dhole-Sitemap/8.0"})
                with _urllib_req.urlopen(req, timeout=15) as resp:  # noqa: S310
                    body = resp.read()
                    if body:
                        return (int(resp.status), body)
            except Exception:
                return None
            return None
        return _get

    t0 = time()
    root_netloc = urlparse(url).netloc
    # A map is not a crawl: its value is breadth, so it does not shrink with
    # max_pages the way content does. SITEMAP_MAP_FLOOR is that floor, and the
    # only way past it is a caller who asks for proportionally more.
    cap = min(5000, max(SITEMAP_MAP_FLOOR, max_pages * 10))
    try:
        result = await asyncio.to_thread(
            discover_sitemap, url, http_get=_make_http_get(), max_urls=cap,
        )
    except Exception:
        return None
    if not result.urls or not result.sitemaps_used:
        return None

    pages: list[CrawlPage] = []
    seen: set[str] = set()
    for su in result.urls:
        if time() > deadline_t:
            break
        try:
            parsed = urlparse(su.url)
        except Exception:
            continue
        if parsed.scheme not in ("http", "https") or parsed.netloc != root_netloc:
            continue  # sitemaps can list other hosts; keep same-domain only
        path = parsed.path or "/"
        if not _sitemap_passes_filters(path, path_include, path_exclude):
            continue
        norm = normalize_url(su.url)
        if norm in seen:
            continue
        seen.add(norm)
        pages.append(CrawlPage(
            url=norm, depth=0, status=0, content_ok=True, fetcher_used="sitemap",
            page_type="sitemap", content=[], content_chars=0, lastmod=su.lastmod,
            summary="sitemap entry",
        ))
        if len(pages) >= cap:
            break

    if not pages:
        return None

    ok = len(pages)
    summary = (f"mapped {ok} URL(s) at {url} from sitemap.xml "
               f"(via {result.via}; {len(result.sitemaps_used)} sitemap file(s)) - one fetch, no BFS")
    next_action = (
        f"{ok} URLs mapped from the sitemap. smart_fetch the ones you need, or re-run "
        f"smart_crawl with crawl_urls=[...] (or discover_only=false) to fetch content "
        f"for a chosen subset. Use path_include/path_exclude to scope."
    )
    if result.capped:
        # P2-9: a sitemap with 50,000 URLs and one with exactly `cap` produce the
        # same number here, and "mapped 5000 URL(s)" reads as "this is the site".
        # The cap can only ever be reported as "at least" — reaching it is not
        # evidence of where the document ends.
        summary += (f" · map capped at {cap} URLs (the sitemap holds at least this "
                    f"many - not necessarily all of it)")
        # "raise max_pages" was the advice here, and for a caller at the default
        # max_pages=10 it is a dead end: the map floor is 1000 URLs and max_pages
        # only moves the cap once it passes 100. Naming a knob that cannot turn is
        # worse than naming none, because the caller turns it and sees no change.
        next_action += (f" The map stops at {cap} URLs: scope with "
                        f"path_include/path_exclude to see a subtree in full, or raise "
                        f"max_pages above {SITEMAP_MAP_FLOOR // 10} to widen the cap.")
    return CrawlResponseModel(
        start_url=normalize_url(url), pages=pages, pages_crawled=0,
        pages_discovered=ok, discover_only=True, sitemap_used=True,
        sitemaps=list(result.sitemaps_used),
        duration_ms=(time() - t0) * 1000, summary=summary, next_action=next_action,
    )


async def smart_crawl(
    server,
    url: str,
    max_pages: int = 10,
    max_depth: int = 2,
    path_include: Optional[list[str]] = None,
    path_exclude: Optional[list[str]] = None,
    discover_only: bool = False,
    focus: Optional[str] = None,
    crawl_urls: Optional[list[str]] = None,
    max_content_chars_per: int = 8000,
    max_total_chars: Optional[int] = None,
    concurrency: int = 3,
    cache_ttl: int = 3600,
    force_fetcher: Optional[str] = None,
    timeout: int = 30000,
    deadline_ms: int = 120000,
    sitemap: str | bool = False,
    search: Optional[str] = None,
    ignore_robots: bool = False,
    delay: float = 0.0,
) -> CrawlResponseModel:
    """Best-first same-domain crawl. See module docstring.

    search: filter discovered/crawled URLs by keyword match (URL path + title).
        Use with discover_only=True for fast URL discovery on large sites.
    sitemap: True = map the site from its sitemap.xml only (one fetch; returns
    the full URL list + lastmod, no BFS, no content). 'auto' = use the sitemap if
    the site has one, else fall back to BFS. False (default) = BFS only. The
    sitemap path collapses big-site discovery (hundreds of pages) into one call.
    ignore_robots: skip robots.txt compliance for this crawl (default False =
    comply). Every page is fetched through smart_fetch, which refuses a
    disallowed URL before any request; this crawl additionally drops the
    discovered URLs it already knows are disallowed (robots_skipped) instead of
    spending budget on a refusal.
    delay: seconds to keep between requests to the SAME host (G27). concurrency
    caps how many requests are in flight, which is a different question from how
    often one server is hit — 3 concurrent requests at a slow host is 3 per
    second. A host's robots.txt ``Crawl-delay`` raises this per host when it asks
    for more; it never lowers what the caller asked for, and ignore_robots drops
    the site's ask too (a caller who opted out of compliance is not then
    rate-limited by it, silently).
    """
    from dhole_mcp.security import validate_url, SecurityError
    from dhole_mcp.trafilatura_extractor import extract_html_title

    t0 = time()
    deadline_t = t0 + (deadline_ms / 1000.0)

    def _err(msg: str, start: str = "", hint: str = "") -> CrawlResponseModel:
        from dhole_mcp.errors import classify_network_error
        if not hint:
            _, hint = classify_network_error(msg)
        return CrawlResponseModel(start_url=start or url, pages=[], error=msg[:200],
                                  duration_ms=(time() - t0) * 1000,
                                  summary=f"crawl failed: {msg[:120]}",
                                  next_action=hint)

    try:
        url = validate_url(url)
    except (SecurityError, ValueError) as e:
        return _err(str(e))
    start_norm = normalize_url(url)  # canonical form for dedup + the response field

    # robots.txt compliance (v16.0): the crawl's own seed check, so a site that
    # disallows dhole is reported once, honestly, instead of as a crawl that
    # silently collected refusals. Per-page enforcement lives in smart_fetch
    # (every page goes through it), and the robots cache is warm after this
    # call, which is what makes the discovered-URL filter below free.
    if not ignore_robots:
        try:
            _seed_verdict = await check_robots(url)
        except Exception:
            _seed_verdict = None
        if _seed_verdict is not None and not _seed_verdict.allowed:
            _robots_txt = _seed_verdict.robots_url or "robots.txt"
            return _err(
                f"robots_disallowed: {_robots_txt} disallows this URL for dhole; "
                "no page was fetched",
                start=start_norm,
                hint=("robots.txt disallows crawling this URL for dhole's user agent "
                      "('dhole-mcp'), so nothing was fetched. Switch site, or re-run "
                      "with ignore_robots=true if you have the site's permission."),
            )

    max_pages = max(1, min(int(max_pages), 100))
    max_depth = max(0, min(int(max_depth), 5))
    concurrency = max(1, min(int(concurrency), 5))
    _chars_per_asked = int(max_content_chars_per)
    max_content_chars_per = max(500, min(_chars_per_asked, 50000))
    # smart_fetch names its clamp in the summary; a crawl that silently answered
    # 500 chars for a request of 400 named nothing at all, so the two tools
    # described the same event differently depending on which one was called.
    _chars_per_clamp = ("" if max_content_chars_per == _chars_per_asked else
                        f"max_content_chars_per clamped {_chars_per_asked}->"
                        f"{max_content_chars_per} (supported range 500-50000)")
    if max_total_chars is None:
        max_total_chars = max_pages * max_content_chars_per
    max_total_chars = max(max_content_chars_per, min(int(max_total_chars), MAX_TOTAL_CHARS))
    focus = focus.strip() if isinstance(focus, str) and focus.strip() else None
    # A bare string was iterated character-by-character by the prefix filters
    # (`path.startswith(p) for p in "/what/"`), and startswith("/") holds for
    # every path - so one string silently filtered the whole crawl away. Treat
    # it as the single-prefix list the caller meant.
    #
    # Validated HERE, before any network work: a pattern the matcher cannot
    # honour used to surface as "0 pages crawled" after a full crawl, which
    # reads as "the site has nothing" rather than "your pattern was wrong".
    if isinstance(path_include, str):
        path_include = [path_include]
    if isinstance(path_exclude, str):
        path_exclude = [path_exclude]
    try:
        path_include = normalize_path_patterns(path_include)
        path_exclude = normalize_path_patterns(path_exclude)
    except ValueError as e:
        # Not a fetch failure: nothing was fetched. The generic classify hint
        # ("try a different source") would send the caller somewhere useless.
        return _err(
            f"invalid path filter: {e}",
            hint=("Fix the path_include/path_exclude pattern and retry. A pattern names a "
                  "path subtree: '/docs' (or '/docs/*') keeps /docs and everything under it; "
                  "'/' or '*' keeps everything. Drop the pattern to crawl unfiltered."),
        )
    selective = bool(crawl_urls)

    # Normalize the sitemap flag: True/'auto'/False. 'auto' = use sitemap if
    # present, else BFS. True = sitemap only (return empty if none). Strings are
    # case-insensitive.
    sm_mode = "off"
    if isinstance(sitemap, str):
        s = sitemap.strip().lower()
        sm_mode = "auto" if s == "auto" else ("on" if s in ("true", "1", "on", "yes") else "off")
    elif sitemap is True:
        sm_mode = "on"

    # ── Sitemap mode: map the site from sitemap.xml in one fetch ───────────
    # Runs before BFS. 'auto' uses the sitemap if found, else falls through to
    # BFS. 'on' returns the sitemap map (or an honest empty if none found).
    if sm_mode in ("on", "auto") and not selective:
        sm_result = await _sitemap_map(url, path_include, path_exclude,
                                       max_pages=max_pages, deadline_t=deadline_t)
        if sm_result is not None:
            return sm_result  # sitemap found + mapped (auto/on success)
        # auto: no sitemap -> fall through to BFS. on: no sitemap -> honest empty.
        if sm_mode == "on":
            return CrawlResponseModel(
                start_url=start_norm, pages=[], pages_crawled=0,
                pages_discovered=0, discover_only=True, sitemap_used=False,
                duration_ms=(time() - t0) * 1000,
                summary=f"no sitemap.xml found at {start_norm} (robots.txt had no Sitemap directive and /sitemap.xml returned nothing)",
                next_action=("No sitemap found. Re-run smart_crawl with sitemap=false (or omit it) "
                             "to use best-first BFS discovery instead."),
            )

    # Two-phase selective crawl: a caller-supplied URL subset is fetched with no
    # further discovery (max_depth=0). URLs are normalized + same-domain-checked.
    #
    # G28 — every entry that does not survive this culling is COUNTED and
    # reported. A caller who passes 40 URLs and gets 32 pages has to be able to
    # tell the difference between "the site has 32 pages" and "I dropped 8 of
    # yours", because the second answer means a page they asked for is missing
    # from the result and the first one hides that.
    selective = bool(crawl_urls)
    urls_supplied = len(crawl_urls) if selective else 0
    urls_deduped = urls_off_domain = urls_over_cap = 0
    if selective:
        root_netloc = urlparse(url).netloc
        culled: list[str] = []
        seen0: set[str] = set()
        for raw in crawl_urls:
            try:
                joined = urljoin(url, raw)
            except Exception:
                urls_off_domain += 1  # unparseable: not the start domain either way
                continue
            jnorm = normalize_url(joined)
            if urlparse(joined).netloc != root_netloc:
                urls_off_domain += 1
                continue
            if jnorm in seen0:
                urls_deduped += 1
                continue
            seen0.add(jnorm)
            culled.append(joined)  # original for fetching; dedup by jnorm
        crawl_urls = culled[:max_pages]
        urls_over_cap = len(culled) - len(crawl_urls)

    root = _same_root(url)
    visited: set[str] = set()
    discovered: set[str] = {start_norm}
    pages: list[CrawlPage] = []
    total_chars = 0
    robots_skipped = 0
    truncated_budget = False
    truncated_maxpages = False
    truncated_time = False

    sem = asyncio.Semaphore(concurrency)

    # G27 — politeness interval, per host. `concurrency` caps how many requests
    # are in flight, which is a different question from how often one server is
    # hit: 3 concurrent requests at a slow host is 3 per second, forever. Each
    # host gets a next-allowed slot, so N concurrent lanes at one host collapse
    # into one request per interval while different hosts stay independent.
    from dhole_mcp.robots import MAX_CRAWL_DELAY_S
    asked_interval = max(0.0, min(float(delay or 0.0), MAX_CRAWL_DELAY_S))
    pace_clamped = float(delay or 0.0) > MAX_CRAWL_DELAY_S
    next_slot: dict[str, float] = {}
    pace_lock = asyncio.Lock()
    interval_used = 0.0
    pace_raised_by_robots = False

    def _interval_for(u: str) -> float:
        """The caller's interval, or the host's own Crawl-delay if it asks for more."""
        nonlocal pace_raised_by_robots
        interval = asked_interval
        if not ignore_robots:
            asked = 0.0
            try:
                from dhole_mcp.robots import cached_crawl_delay
                asked = cached_crawl_delay(u) or 0.0
            except Exception:
                asked = 0.0
            if asked > interval:
                interval, pace_raised_by_robots = asked, True
        return interval

    async def _pace(u: str) -> None:
        nonlocal interval_used
        interval = _interval_for(u)
        if interval <= 0:
            return
        host = (urlparse(u).hostname or "?").lower()
        loop = asyncio.get_running_loop()
        async with pace_lock:
            earliest = max(next_slot.get(host, 0.0), loop.time())
            next_slot[host] = earliest + interval
        interval_used = max(interval_used, interval)
        wait_s = earliest - loop.time()
        if wait_s > 0:
            await asyncio.sleep(wait_s)

    async def fetch_one(u: str) -> tuple[str, "object", str]:
        """Fetch one page as HTML. Returns (url, ResponseModel, html_str).

        Transient errors (timeout, connection_reset) get one automatic retry
        after a 1s delay. Deterministic errors (connection_refused, dns_failure)
        are not retried (they will fail again).
        """
        async with sem:
            await _pace(u)
            # Rotate through proxy pool so crawl pages don't hammer one IP.
            from dhole_mcp.search_proxy import get_next_proxy, get_proxy_pool
            _crawl_proxy = get_next_proxy()
            try:
                resp = await server.smart_fetch(
                    url=u, extraction_type="html", cache_ttl=cache_ttl,
                    max_content_chars=200000, force_fetcher=force_fetcher,
                    timeout=timeout,
                    proxy=_crawl_proxy,
                    ignore_robots=ignore_robots,
                )
            except Exception as e:
                # Retry once for transient errors (timeout/reset)
                if _is_transient_error(str(e)):
                    await asyncio.sleep(1.0)
                    try:
                        resp = await server.smart_fetch(
                            url=u, extraction_type="html", cache_ttl=cache_ttl,
                            max_content_chars=200000, force_fetcher=force_fetcher,
                            timeout=timeout,
                            proxy=_crawl_proxy,
                            ignore_robots=ignore_robots,
                        )
                    except Exception as e2:
                        # Lazy import to break circular dependency:
                        # crawl.py -> server.py (ResponseModel) and
                        # server.py -> crawl.py (smart_crawl). Both are
                        # function-level imports so neither module needs the
                        # other at import time.
                        from dhole_mcp.server import ResponseModel
                        resp = ResponseModel(url=u, status=-1, content=[""],
                                             fetcher_used="none", error=str(e2)[:200])
                else:
                    from dhole_mcp.server import ResponseModel
                    resp = ResponseModel(url=u, status=-1, content=[""],
                                         fetcher_used="none", error=str(e)[:200])
            # Proxy health tracking: success marks the proxy healthy, a failed
            # fetch cools it so the next crawl page uses a different IP.
            if _crawl_proxy:
                pool = get_proxy_pool()
                if pool is not None:
                    if resp.status > 0:
                        pool.mark_success(_crawl_proxy)
                    else:
                        pool.mark_failed(_crawl_proxy)
        html = resp.content[0] if resp.content else ""
        return u, resp, html

    # ---- Priority queue (best-first) -------------------------------------
    # Heap entries: (score, depth, seq, url, anchor_text). `seq` breaks ties so
    # heapq never tries to order dicts/strings on a tie. Lower score = popped
    # first, so we negate the real score.
    heap: list[tuple[float, int, int, str, str]] = []
    seq = 0

    def push(u: str, depth: int, text: str):
        nonlocal seq
        s = score_link(u, text, focus or "")
        # Shallow-depth preference: prefer shallower pages (small penalty/depth).
        s -= 0.15 * depth
        heapq.heappush(heap, (-s, depth, seq, u, text))
        seq += 1

    if selective:
        for u in crawl_urls:
            push(u, 0, "")
        max_depth = 0  # don't expand from a selective crawl
    else:
        push(url, 0, "")

    # Fetch a batch of up to `concurrency` best URLs from the heap concurrently.
    while heap and len(pages) < max_pages and not truncated_budget:
        if time() > deadline_t:
            truncated_time = True
            break
        remaining = max_pages - len(pages)
        batch: list[tuple[float, int, int, str, str]] = []
        while heap and len(batch) < min(concurrency, remaining):
            batch.append(heapq.heappop(heap))
        results = await asyncio.gather(*[fetch_one(e[3]) for e in batch])

        for (neg_score, depth, _seq, u, _text), (ru, resp, html) in zip(batch, results):
            if len(pages) >= max_pages:
                truncated_maxpages = True
                break
            if time() > deadline_t:
                truncated_time = True
                break
            u_norm = normalize_url(resp.url or u)
            # Guard: if a redirect took us off-domain, skip it (don't crawl external).
            if urlparse(u_norm).netloc != urlparse(url).netloc:
                continue
            if u_norm in visited:
                continue
            visited.add(u_norm)

            title = ""
            try:
                title = extract_html_title(html) if html else ""
            except Exception:
                pass

            content_md = ""
            page_type = "discover_only" if discover_only else ""
            is_trunc = False
            next_off = 0
            content_ok = bool(resp.content_ok)
            if not discover_only and html:
                if resp.content_ok:
                    try:
                        md, kind, ok, cut = _classify_and_extract(
                            html, resp.url or u, url, focus, max_content_chars_per)
                    except Exception:
                        md, kind, ok, cut = "", "fallback", False, False
                    page_type = kind
                    content_ok = ok and bool(md)
                    if md:
                        # The extraction already holds the whole page up to the
                        # per-page cap; `cut` says whether more exists. next_offset
                        # is the char offset INTO THAT extraction, so it is only
                        # meaningful with smart_fetch on this URL - a crawl page has
                        # no resumable cursor of its own, which is what the field
                        # description on CrawlPage.next_offset already says.
                        is_trunc = cut
                        next_off = max_content_chars_per if cut else 0
                        content_md = md
                else:
                    # Not content_ok. Only an empty-but-served page is a rendering
                    # problem: a 4xx/5xx is the HTTP answer and a status of 0/-1 is
                    # no answer at all. Labeling those "js_shell" tells the caller
                    # the site is JS-gated when the URL is simply invalid. Measured:
                    # arxiv answered 400 "Invalid archive or category" for a bad
                    # category and the crawl page reported page_type=js_shell.
                    _status = _error_status(resp)
                    page_type = "js_shell" if 0 < _status < 400 and resp.error else "fallback"
            elif discover_only:
                # Map mode: nothing was extracted, so content_ok must stay False.
                # It used to be forced True ("the URL itself is the result"), which
                # made the summary claim "10 content_ok" about ten pages that were
                # fetched only to expand the link map - a claim about content that
                # does not exist. The page's URL is the crawl's OUTPUT; it is not
                # this page's content. status/fetcher_used/error stay populated
                # because "which of these URLs are dead" is a real answer.
                content_ok = False

            page = CrawlPage(
                url=u_norm, depth=depth, status=_error_status(resp),
                content_ok=content_ok, fetcher_used=resp.fetcher_used,
                title=title, page_type=page_type,
                content=[content_md] if content_md else [],
                content_chars=len(content_md), is_truncated=is_trunc,
                next_offset=next_off, fetched_at=getattr(resp, "fetched_at", ""),
                summary=resp.summary, error=resp.error,
            )
            pages.append(page)
            total_chars += page.content_chars
            if total_chars >= max_total_chars and not discover_only:
                truncated_budget = True

            # Browser auto-escalation: if the start page (depth 0) failed with
            # js_shell or bot_challenge, upgrade force_fetcher to "stealthy" for
            # all subsequent pages (the site likely needs a real browser).
            if (depth == 0 and not content_ok and force_fetcher is None
                    and page.page_type in ("js_shell", "fallback")
                    and ("bot_challenge" in (page.error or "") or "js_shell" in (page.error or "")
                         or page.page_type == "js_shell")):
                force_fetcher = "stealthy"
                logger.info(f"Crawl: upgrading to stealthy browser (page_type={page.page_type})")

            # Discover links for deeper layers (skip in selective mode).
            if not selective and depth < max_depth and html:
                try:
                    links = extract_same_domain_links(
                        html, resp.url or u, url, path_include, path_exclude)
                except Exception:
                    links = []
                for link_url, link_text in links:
                    link_norm = normalize_url(link_url)
                    if link_norm not in discovered and link_norm not in visited:
                        # Known-disallowed candidates are dropped before they
                        # take a queue slot: the origin's rules are already in
                        # the cache (the seed check above put them there), so
                        # this costs nothing, and spending a page of the token
                        # budget on a refusal the next page would have skipped
                        # anyway is pure waste. Unknown verdicts still go
                        # through - smart_fetch is the enforcement point.
                        if not ignore_robots and get_robots_cache().cached(link_url) is False:
                            robots_skipped += 1
                            continue
                        discovered.add(link_norm)
                        push(link_url, depth + 1, link_text)  # fetch original
            if truncated_budget:
                break

    pages_crawled = len(pages)
    # In selective mode, "discovered" is just the chosen subset.
    if selective:
        pages_discovered = len(crawl_urls)
    else:
        # Count any URLs still in the heap as discovered (they were found but not crawled).
        for _neg, _d, _s, hu, _t in heap:
            discovered.add(normalize_url(hu))
        pages_discovered = len(discovered)

    if pages_crawled >= max_pages and pages_discovered > pages_crawled:
        truncated_maxpages = True
    # A crawl_urls list longer than max_pages hits the same ceiling from the
    # other direction: the cap trimmed the caller's own list, so the crawl ran
    # exactly max_pages deep and stopped with entries still on the table.
    if urls_over_cap:
        truncated_maxpages = True

    # What the caller asked for that they did not get, for the stats line. In
    # selective mode the list was capped BEFORE the loop, so
    # pages_discovered - pages_crawled is 0 there while `urls_over_cap` counts
    # the entries the cap actually removed.
    not_fetched = max(pages_discovered - pages_crawled, urls_over_cap)

    # G28 — the culling tally, in one sentence, so the caller can tell "the site
    # has N pages" apart from "I dropped M of the N you named".
    _drop_bits = []
    if urls_deduped:
        _drop_bits.append(f"{urls_deduped} already in the list")
    if urls_off_domain:
        _drop_bits.append(f"{urls_off_domain} off-domain")
    if urls_over_cap:
        _drop_bits.append(f"{urls_over_cap} over max_pages={max_pages}")
    urls_dropped_note = ""
    if selective and _drop_bits:
        urls_dropped_note = (f"crawl_urls: {urls_supplied} supplied, {len(crawl_urls)} "
                             f"crawled ({', '.join(_drop_bits)} dropped)")

    def _summary_for(pages_now: list) -> str:
        """Stats line for ``pages_now``.

        Called once before and once after the `search` filter. The post-filter
        call has to describe the pages actually returned: prefixing the
        pre-filter stats produced "crawled 1 page(s); 1 content_ok" sitting next
        to pages_crawled=0 and pages=[].
        """
        ok_now = sum(1 for p in pages_now if p.content_ok)
        by_type_now: dict[str, int] = {}
        for p in pages_now:
            if p.page_type:
                by_type_now[p.page_type] = by_type_now.get(p.page_type, 0) + 1
        type_bits_now = ", ".join(f"{k}:{v}" for k, v in sorted(by_type_now.items()) if k != "discover_only") or ""
        bits_now = [f"crawled {len(pages_now)} page(s) at {root} (depth <= {max_depth})",
                    f"{ok_now} content_ok"]
        if type_bits_now:
            bits_now.append(type_bits_now)
        if discover_only:
            # Map mode extracts nothing, so "N content_ok" would be a claim about
            # content that does not exist. Say what the mode actually COST instead:
            # a "URL map only" call still performs one request per expanded page,
            # and the fetch count is the number that surprises people (10 requests
            # at the default max_pages=10). See the module docstring for how to get
            # the same map in one request.
            bits_now = [f"mapped {pages_discovered} URL(s) at {root} (depth <= {max_depth}) "
                        f"using {len(pages_now)} page fetch(es); no content extracted"]
            if type_bits_now:
                bits_now.append(type_bits_now)
        if truncated_budget:
            bits_now.append("stopped: token budget reached")
        if truncated_maxpages:
            bits_now.append("stopped: max_pages reached")
        if truncated_time:
            bits_now.append("stopped: time deadline reached")
        if robots_skipped:
            bits_now.append(f"{robots_skipped} URL(s) skipped by robots.txt")
        if urls_dropped_note:
            bits_now.append(urls_dropped_note)
        if _chars_per_clamp:
            bits_now.append(_chars_per_clamp)
        if (path_include or path_exclude) and len(pages_now) <= 1 and not selective:
            # The filters are applied while links are discovered, so a pattern that
            # matches nothing produces a crawl of exactly one page (the start URL is
            # never filtered) and no count anywhere says a filter ran. Measured: a
            # path_include the site did not use returned the start page alone,
            # pages_discovered=1, empty next_action - it read as a successful crawl.
            bits_now.append("only the start page was kept - path_include/path_exclude "
                            "may have scoped out every discovered link")
        if interval_used > 0:
            # Saying the interval exists is the point: a crawl that took 40
            # seconds for 10 pages looks like a hang unless the stats line says
            # it was spacing itself on purpose.
            bits_now.append(
                f"{interval_used:g}s between requests per host"
                + (" (raised by a host's robots Crawl-delay)" if pace_raised_by_robots else "")
                + (" (delay above the 60s ceiling was clamped)" if pace_clamped else ""))
        return "; ".join(bits_now)

    # Build a concise summary + next_action.
    ok = sum(1 for p in pages if p.content_ok)
    summary = _summary_for(pages)

    next_action = ""
    if truncated_budget or truncated_maxpages or truncated_time:
        why = ("time deadline" if truncated_time else
               "token budget" if truncated_budget else "max_pages")
        next_action = (
            f"crawl stopped early ({why}); re-run smart_crawl with a higher "
            f"max_pages / max_total_chars / deadline_ms, or scope it with "
            f"path_include=['/docs'] (a path subtree, not a string prefix). "
            f"{not_fetched} URL(s) were discovered but not fetched."
        )
        if urls_over_cap:
            next_action += (
                f" {urls_over_cap} of the crawl_urls you supplied were not fetched "
                f"because max_pages={max_pages} caps the LIST, not just a crawl: "
                f"split them across calls or raise max_pages."
            )
        if discover_only:
            # This is the branch map mode actually lands in whenever the map is
            # incomplete: "discovered > crawled" in map mode always means the loop
            # hit max_pages, so the truncation branch above wins and the dedicated
            # map-mode branch below is only reachable via an off-domain redirect.
            # Put the cost disclosure where it will actually be read.
            next_action += (
                " Map mode costs one request per page, so raising max_pages buys a "
                "deeper map with more requests; for a ONE-request map use max_pages=1 "
                "(start URL's links) or sitemap=true (whole site)."
            )
    elif discover_only and pages_discovered > pages_crawled:
        next_action = (
            f"{pages_discovered} URLs mapped at a cost of {pages_crawled} page "
            f"fetch(es) (map mode fetches one page per expansion step to find more "
            f"links). smart_fetch the ones you need, or re-run smart_crawl with "
            f"discover_only=false (or crawl_urls=[...]) to fetch content for a chosen "
            f"subset. For a ONE-request map, pass max_pages=1 (start URL's links) or "
            f"sitemap=true (whole site, if it publishes one)."
        )
    elif not next_action and selective and not pages and (urls_deduped or urls_off_domain):
        # An empty result from a list the caller supplied is the case where
        # silence hurts most: they cannot tell an empty site from a rejected
        # list, and the two need opposite fixes.
        next_action = (
            f"nothing to crawl: of the {urls_supplied} crawl_urls supplied, "
            f"{urls_deduped} were repeats of an earlier entry (after normalization) "
            f"and {urls_off_domain} were on a different host than the start URL. "
            f"smart_crawl only fetches the start URL's host - smart_fetch the "
            f"off-domain URLs, or start the crawl from their own site."
        )

    # Network failure aggregate diagnosis: when most/all pages failed with
    # network errors, give the agent a clear classification + actionable hint
    # instead of leaving next_action empty.
    if not next_action and pages:
        network_failures = sum(1 for p in pages if p.status == -1 or p.status == 0)
        if network_failures > 0 and network_failures >= len(pages) * 0.5:
            sample_errors = [p.error for p in pages if p.error][:3]
            from dhole_mcp.errors import classify_network_error
            category, hint = classify_network_error(" ".join(sample_errors))
            next_action = (
                f"{network_failures}/{len(pages)} pages failed with network errors "
                f"({category}). {hint} "
                f"Sample: {sample_errors[0][:100] if sample_errors else 'unknown'}"
            )
        elif ok == 0 and pages_crawled > 0 and not discover_only:
            # Map mode never extracts content, so ok == 0 is the EXPECTED shape
            # there and must not be diagnosed as "the site may be unreachable".
            next_action = ("All crawled pages returned no content. The site may be "
                          "unreachable, require JavaScript, or block automated access. "
                          "Try smart_fetch on a specific URL for more diagnostics.")

    result = CrawlResponseModel(
        start_url=start_norm, pages=pages, pages_crawled=pages_crawled,
        pages_discovered=pages_discovered, discover_only=discover_only,
        truncated_by_budget=truncated_budget, truncated_by_max_pages=truncated_maxpages,
        truncated_by_time=truncated_time, duration_ms=(time() - t0) * 1000,
        robots_skipped=robots_skipped,
        urls_supplied=urls_supplied, urls_deduped=urls_deduped,
        urls_dropped_off_domain=urls_off_domain,
        urls_dropped_over_max_pages=urls_over_cap,
        summary=summary, next_action=next_action,
    )

    # Search filter: when search is set, only keep pages whose URL or title
    # matches any of the search terms. Best with discover_only=True for fast
    # URL discovery on large sites (like Firecrawl's map + search).
    if search and result.pages:
        _terms = search.lower().split()
        result.pages = [p for p in result.pages if any(
            t in p.url.lower() or t in (p.title or "").lower()
            for t in _terms
        )]
        result.pages_crawled = len(result.pages)
        result.summary = (f"search='{search}' filtered to {len(result.pages)} URL(s); "
                          + _summary_for(result.pages))
        if not result.pages:
            result.next_action = (
                f"search='{search}' matched none of the crawled pages. It matches "
                "URL path + title only - widen it, drop it, or pass focus= to "
                "prioritize relevant links instead of filtering."
            )

    return result
