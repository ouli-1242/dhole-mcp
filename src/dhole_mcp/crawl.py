"""web_crawl — flagship same-domain deep crawl for Dhole, optimized for agents.

Walks a website from a start URL and returns each page as agent-usable markdown
with the same honest signals web_read produces (content_ok / summary / error /
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
                         with an actionable error (use a web_act screenshot action / vision).
      - fallback      -> cleaned visible text.
    This fixes the "0 content_ok on Hacker News" and "JS SPA timeout" class of
    bugs at the root: list pages now return their link list as content, and JS
    shells are detected and reported honestly instead of silently empty.
  * **URL normalization + dedup.** Trailing slashes, default ports, lowercase
    host, and tracking query params (utm_*, fbclid, gclid, ref, _) are stripped
    before dedup, so `/docs` and `/docs/` are no longer crawled twice.
  * **Map first, then read.** `discover_only=true` returns the URL map (prefetch)
    and `sitemap=true` returns the whole site's map in one fetch; the URLs picked
    out of either are read with `web_read(urls=[...])`. Map mode extracts no
    page content, but it is not free: expanding the map means fetching one page
    per step, so it costs up to `max_pages` requests (10 by default) and
    `pages_crawled` reports exactly how many were made. For a one-request map use
    `max_pages=1` (the start URL's own links) or `sitemap=true` (the whole site,
    if it has one).
    The second phase used to live here: `crawl_urls=[...]` fetched a chosen subset
    without re-discovering. It left in 17.0 (§3 decision 31) because a caller's
    list had to pass normalization, same-domain checking, dedup and a cap before
    it became a crawl, and each of those four cuts needed its own counter. Those
    URLs never needed discovery, so `web_read(urls=[...])` was already home.
  * **same_domain_only=true** default. External links are dropped (not crawled).
  * **Honest status.** Network failures report status -1 (documented) so
    downstream logic can distinguish them from a real HTTP 0 / no response.
  * **Freshness.** Each page carries `fetched_at`; `cache_ttl=0` forces fresh.
  * **Overall deadline.** One slow page can't hang the crawl; when the deadline
    is hit, partial results are returned with `truncated_by_time=true`.
  * One fetch per page (extraction_type='html'), reusing web_read's anti-bot
    escalation + fetch cache. Links + markdown are derived from the same body.
"""

from __future__ import annotations

import asyncio
import functools
import heapq
import logging
import re
from time import time
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlparse, urlunparse

from pydantic import BaseModel, Field, model_serializer

from dhole_mcp import injection
# 钳制文案的唯一作者。crawl 的 summary 不经过 _with_agent_hints（它是
# CrawlResponseModel，不是 ResponseModel），所以这里读的是那颗纯函数而不是
# ContextVar —— 同一句话，两个通道。
from dhole_mcp.context import _cache_ttl_note, _clamp_note, report_progress
# robots.txt compliance (v16.0). robots.py imports nothing from dhole at module
# level, so this is cycle-free; the seed check + the discovered-URL peek both
# need it, and keeping one import point is what makes the cache the shared one.
from dhole_mcp.robots import check as check_robots, get_robots_cache
# 量的尺要只有**一把**。`visible_text_chars` / `is_extraction_collapsed` /
# `distinct_content_links` 是 web_read 那侧已经拿真实页面标定过的三把尺（比值带、
# 导航栏剥离），crawl 若自己再数一遍就会各有一套阈值 —— 同一个工具两种真相。
from dhole_mcp.envelope import is_extraction_collapsed, visible_text_chars

logger = logging.getLogger("dhole-mcp.crawl")

# ─── M-1：神经 focus（按次 focus_engine='neural' 点名，17.0 起无许可门）──
# 与 server.py / focus.py 的选择语义一致：focus_engine 点名才用本地 cross-encoder
# 打分；模型缺失在工具入口显式报错（web_crawl 的闸），这里只 peek，拿不到就
# 退回 BM25，绝不在爬取中途静默下载。
#
# 原来这一节还有 M-2 那一档：`_page_relevant` 拿同一套 cross-encoder 在**整页进
# 结果之前**判一次相关性，落在跑题带的页面整页丢弃，summary 报出丢了几页。17.0 §6
# 把它删了 —— 它依赖的正是 B8 要换掉的那个打分后端，留着只会把 B8 绑在旧模型上。


def _neural_for_this_call() -> bool:
    """这一次爬取真的用语义打分吗 = 按次 ``focus_engine`` 点名 "neural"。

    17.0：``DHOLE_NEURAL_FOCUS`` 许可门已删除 —— 神经分是显式按次选择，
    没点名（""）或点名 "bm25" 都走 BM25；爬取把每页 ~12.5s 的推理乘上
    max_pages，这个代价不该在没人点名时发生。
    """
    from dhole_mcp.focus import engine_wants_neural
    return engine_wants_neural()


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

# The ceilings a crawl answers to. Named, not inline, because the tool description
# has to state them and a description that drifts from the clamp is exactly the bug
# this section's tests exist to catch: they read these constants and require the same
# numbers on the wire.
MAX_PAGES_CEILING = 40
PER_PAGE_CHARS_CEILING = 20_000
# 17.0 §3 第 31 条曾经裁决："整轮没有字符预算，因为一整趟多少字符要先把每页长度估
# 出来，而那是站点决定的数，所以没有人算得清。" 2026-10-05 的实测把这条裁决的前提
# 打掉了：算不清不等于花得起 —— `max_pages=41`（钳到 40）× 默认每页 8,000 真跑出
# **223,599 字符**（约 81k token）一次到达宿主，被截断落盘。调用方拿到的不是"一整轮
# 爬取"，是一句"结果太大，去看文件"。所以这一轮预算回来了，而它可算的部分恰恰是它
# 该存在的理由：预算是**返回给调用方的字符数**，那一个是 dhole 自己决定写多少的数。
MAX_TOTAL_CHARS_DEFAULT = 60_000
MAX_TOTAL_CHARS_CEILING = 200_000
MIN_TOTAL_CHARS = 1_000

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
    is_truncated: bool = Field(default=False, description="True = this page has more content; web_read it with offset=next_offset.")
    next_offset: int = Field(default=0, description="Next offset if is_truncated; 0 = no more.")
    fetched_at: str = Field(default="", description="ISO-8601 UTC when this page was fetched (may show cache age).")
    lastmod: str = Field(default="", description="<lastmod> from the site's sitemap.xml for this URL (sitemap mode only). Empty otherwise.")
    summary: str = Field(default="", description="One-line status for this page.")
    error: str = Field(default="", description="Error for this page, if content_ok is False.")
    # G25 阶段4：爬回的每页正文同样过注入启发式（见 dhole_mcp/injection.py）。
    untrusted_content: Optional[Dict[str, Any]] = Field(default=None, description="Present ONLY when this page's text matched an injection heuristic ({hits: [{kind, excerpt, count}], total} — see web_read's untrusted_content for the shapes and the discipline). Per-page marking keeps a flagged page findable inside a 50-page crawl without rereading every row.")


class CrawlResponseModel(BaseModel):
    start_url: str = Field(description="The URL crawl started from (normalized).")
    pages: list[CrawlPage] = Field(description="Crawled pages (one CrawlPage each). In discover_only, content is empty and pages hold the discovered URL map.")
    pages_crawled: int = Field(default=0, description="Pages actually fetched.")
    pages_discovered: int = Field(default=0, description="Total unique same-domain URLs found (including crawled).")
    discover_only: bool = Field(default=False, description="True = map mode (URLs only, no content).")
    truncated_by_max_pages: bool = Field(default=False, description="True = stopped early because max_pages was reached.")
    truncated_by_time: bool = Field(default=False, description="True = stopped early because the overall deadline (ms) was reached.")
    truncated_by_total_chars: bool = Field(default=False, description="True = stopped early because the whole-run character budget (max_total_chars) was spent. The summary names how many pages were still queued and how many were fetched inside the last batch but not returned.")
    sitemap_used: bool = Field(default=False, description="True = the URL map came from the site's sitemap.xml (one fetch), not best-first BFS discovery.")
    sitemap_truncated: bool = Field(default=False, description="True = the map is SHORT OF THE SITE, not just short of your filters: it stopped at the URL cap, or the walk hit its own sitemap-index limit. pages_discovered is then a LOWER BOUND - the site's sitemap holds at least this many URLs. Absent (False) on a map that read the whole sitemap, but note that still is not a guarantee the sitemap lists every page: a sitemap is an index the site chooses to publish.")
    sitemaps: list[str] = Field(default=[], description="Sitemap.xml URLs that were fetched + parsed (sitemap mode only).")
    robots_skipped: int = Field(default=0, description="Discovered URLs dropped WITHOUT a request because their robots.txt disallows them (dhole matches its own user agent, 'dhole-mcp'). Not an error - those pages were never eligible. Pass ignore_robots=true if you have the site's permission to fetch them anyway.")
    duration_ms: float = Field(default=0, description="Duration ms.")
    error: str = Field(default="", description="Error message (crawl-level).")
    summary: str = Field(default="", description="One-line crawl status.")
    next_action: str = Field(default="", description="Obvious next call when one exists (continue crawl / fetch a page).")
    ssrf_recheck: str = Field(default="", description="Non-empty only when the post-redirect IP recheck was skipped because of this machine's resolver (a fake-ip proxy) — the SSRF posture of the WHOLE crawl, stated once. Before this field existed a crawl said nothing about it at all: the per-page receipts live on the web_read results that crawl consumes internally and never reaches the caller, so the one security posture that a 40-page run should disclose was dropped with the pages' own metadata.")
    plan_id: str = Field(default="", description="web_discover responses only: the URL map is held server-side under this id (1h TTL). Pass resume_token='<plan_id>:0' to web_crawl to fetch the plan without re-sending the URL list.")
    next_resume_token: str = Field(default="", description="Set when a resumed plan run stopped with URLs left (page cap, char budget or deadline): pass it back as resume_token= to continue exactly where this run stopped. Absent when the plan is exhausted.")

    @model_serializer(mode="wrap")
    def _map_rows_carry_only_what_differs(self, handler):
        """In map mode a row is the URL, not a page report.

        A URL map is the one output whose size is set by the site rather than by
        what the caller asked for, and the flow the tool description recommends
        (map once with sitemap=true, then read the picks) hit it:
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


#: 一行列表至少要几条**带文字的互异条目链接**才还配叫列表。
#:
#: 这里原本还打算加第二颗下限（fallback 行的整页可见字要 ≥200，对齐 web_read 的
#: `_JS_SHELL_MIN_TEXT_CHARS`）。量过之后**删了**：靶站的正对照 `/collections?items=1`
#: 整页可见字 37 而 markdown 80 —— 那一页的内容就是三张卡片，`visible_text_chars` 那把
#: 尺天生量不到以链接与数据为正文的页面。把"字少"当成"没内容"吃掉的正是这批不能吃的
#: 那一类（与 R1 语料清扫里那两发假阳性同形：术语表被判成编码块、全小写长标识符被判成
#: base64）。`visible_chars` 仍然报出来，因为它是事实；"够不够 200"不是事实，是判断。
LIST_ITEMS_MIN = 3

#: 同一组条目链接在**一次爬取里**重复到第几行就被说成站点菜单。3：一次两行的爬取里
#: "两行同集"还可能是两个姊妹栏目页真的列了同一批东西；三行相同就不是内容了。这一句
#: 今天只上报（`site_chrome`），不翻任何一行的 `content_ok`。
CHROME_REPEAT_MIN = 3


def _item_links(html: str, base_url: str, start_url: str) -> list[str]:
    """这一页的条目链接（互异的绝对 URL，且锚文本非空）。

    走 `extract_same_domain_links` —— crawl 自己那把解析相对 href 的尺。这里刻意不借
    web_read 的 `_count_content_links`：它先剥 `<nav|header|footer|aside>`，而 books.toscrape
    的 travel 列表页把**全部 11 本书**装在 `<aside class="sidebar">` 里（实测：剥完之后
    主内容区只剩 2 条链接，而那一页恰恰是这批不能误伤的正对照）。类名与标签都靠不住，
    一页之内的"菜单 vs 列表"没有结构判据。
    """
    return [normalize_url(h) for h, t in extract_same_domain_links(html, base_url, start_url)
            if (t or "").strip()]


def _measure_row(stats: dict, html: str, md: str, kind: str, url: str,
                 start_url: str) -> None:
    """把 web_read 那侧标定过的尺量在这一行上，**不动结论位**（measure 那一刀）。

    今天 crawl 的一行只问"抽回来的 markdown 够不够 50 个字符"。这一问混了两件不同的
    事：① 字在页上而我的抽取器把它们丢了（`is_extraction_collapsed` —— web_read 用它把
    "塌陷"和"壳"分开，见 quality.py 那一处注释）；② 这一行的"列表"是真有一组条目，还是
    只有零星几条链接。各量一遍就会各有一套阈值，所以两问都用现成的尺，数字一律报出来
    （`visible_chars` / `extracted_chars` / `list_items` 是事实，阈值之下该说什么才是判断）。

    塌陷那一问只在 `kind == "article"` 这支里问：list 的 md 是渲染出来的链接清单，它
    **故意**丢掉非链接的字 —— travel 页 5,874/1,374 在渲染成列表之后比值 4.3，拿整页
    可见字去比是拿错的量具量对的东西。
    """
    visible = visible_text_chars(html)
    stats["visible_chars"] = visible
    stats["extracted_chars"] = len(md)
    if kind == "article" and is_extraction_collapsed(len(md), visible):
        stats["extraction_collapsed"] = True
    if kind == "list":
        items = _item_links(html, url, start_url)
        stats["list_items"] = len(items)
        stats["item_set"] = frozenset(items)
        if len(items) < LIST_ITEMS_MIN:
            stats["thin_list"] = True


def _chrome_rows(measured: dict) -> dict:
    """`{行 URL: 它那一组条目链接在本次爬取里出现了几行}`，只收重复到
    `CHROME_REPEAT_MIN` 行以上的组。

    纯函数，收的是 `_measure_row` 那张表：跨行的判据要等行都进来才成立，而把它单列出来
    才量得动 —— 靶站今天没有一页共享导航的站点（`_page()` 不给每页挂同一个 nav，加上去会
    挪动 E-13 扇出与 R1 语料清扫的每一格），所以这一支由这张表直接喂，端到端只证它**不
    误伤**。
    """
    counts: dict = {}
    for m in measured.values():
        key = m.get("item_set")
        if key:
            counts[key] = counts.get(key, 0) + 1
    return {url: counts[m["item_set"]]
            for url, m in measured.items()
            if m.get("item_set") and counts[m["item_set"]] >= CHROME_REPEAT_MIN}


def _row_verdict(kind: str, md: str, measured: dict) -> bool:
    """这一行算不算"给用户拿到了内容"。R2 的第二笔在这里翻闸。

    两条依据都是**实测**，不是品味：

    * `extraction_collapsed` —— 同一个 URL 走 `web_read` 回来写着
      `content_ok=false` + `error="extraction_collapsed: … holds 7,149 characters…"`，
      走 `web_crawl` 这一行却写着 `content_ok=true`（`/longaside`，同一份字节，两边都真
      跑过）。一个工具两种真相，而这一族闸在读取侧早就存在 —— 这里是**对齐**，不是新闸。
    * `thin_list` —— 渲染成列表却只有 0-2 条条目链接的行，交回去的"清单"里没有人。
      语料里最窄的那一页也有 12 条（`/nav`），travel 52 条，所以这颗下限不吃掉任何一页。

    长度那把尺**保持 50**：计划里"抬到 200 对齐 `_JS_SHELL_MIN_TEXT_CHARS`"那一档被
    `/collections?items=1` 挡在门外（见 `LIST_ITEMS_MIN` 上面那段）。这不是"一颗都不许动"，
    是这一颗的数值没有依据。
    """
    if kind == "js_shell" or not md:
        return False
    if measured.get("extraction_collapsed") or measured.get("thin_list"):
        return False
    return len(md) >= 50


def _measure_note(stats: dict) -> str:
    """把量到的东西写成一句话。闸翻了之后这句话**更要说**：它是被拒的那一行的理由，
    也是它唯一的解药（塌陷 → `extraction_type='html'`；薄列表 → 别把这一行当索引）。

    第一笔（0b4cb15）里这一句只在 `content_ok=true` 时说话，理由是"一行已经写着 false
    时再补一句是噪音"。那个前提在闸翻掉之后就不成立了：现在被拒的行正是**由这两个发现**
    拒的，调用方读到的必须包括为什么 —— 一个没有理由的 false 与本批要消灭的那些假 true
    是同一种罪。
    """
    if stats.get("extraction_collapsed"):
        visible = stats["visible_chars"]
        got = stats["extracted_chars"]
        return (f"extraction collapsed: this page's own markup holds {visible:,} "
                f"visible characters while we returned {got:,} "
                f"({got / max(visible, 1):.0%}) - the words ARE in the HTML, dhole's "
                "reader lost them. web_read this URL with extraction_type='html' "
                "rather than trusting this row")
    if stats.get("thin_list"):
        return (f"thin list: {stats['list_items']} item link(s) on a row rendered as a "
                "list - an index needs a group of entries, not one or two links")
    return ""


def _classify_and_extract(html: str, url: str, start_url: str, focus: Optional[str],
                          max_chars: int, stats: Optional[dict] = None
                          ) -> tuple[str, str, bool, bool]:
    """Content-adaptive extraction. Returns (markdown, page_type, content_ok, cut).

    page_type is one of: article / list / js_shell / fallback. cut is True when
    the page held more than max_chars and this function returned only the first
    max_chars of it. `content_ok` comes from `_row_verdict` — js_shell that
    didn't render, an empty page, an extraction the page's own markup says we
    lost, or a "list" with no group of entries behind it.

    `stats` 是这一行被那两把尺量过之后的**证据**出口（照 `focus_content(..., stats=)`
    的先例）：数字与发现都从那里交给调用方，判决本身在返回值里。
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
                from dhole_mcp.reranker import get_reranker
                rer = get_reranker() if _neural_for_this_call() else None
                md = focus_content(md, focus, reranker=rer)
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

    measured: dict = {}
    _measure_row(measured, html, md, kind, url, start_url)
    if stats is not None:
        stats.update(measured)
    content_ok = _row_verdict(kind, md, measured)

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


def _scope_hint(pending_urls: list) -> str:
    """停早时该不该推荐 path_include，以及推荐哪一棵子树（评审 R3刀3）。

    样板过去无脑写 `/docs`：站点没有 /docs 时那是一条修不了任何东西的建议，还顺手
    教读者去猜一颗并不存在的目录。判据取自**没抓到的那批 URL** 的第一段路径 —— 一段
    至少占 3 条、且过半，"只跑这一棵"才真把预算省下来；否则空串，调用方读到的是"抬
    预算"这一条老实建议。
    """
    from collections import Counter
    firsts = Counter()
    for u in pending_urls:
        seg = [s for s in (urlparse(u).path or "").split("/") if s]
        if seg:
            firsts["/" + seg[0]] += 1
    if not firsts:
        return ""
    seg, n = firsts.most_common(1)[0]
    if n < 3 or n * 2 < len(pending_urls):
        return ""
    return (f"path_include=['{seg}'] - that subtree holds {n} of the "
            f"{len(pending_urls)} URLs still queued (a path subtree, not a string "
            "prefix)")


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
                       max_pages: int, deadline_t: float,
                       diag: Optional[dict] = None) -> Optional[CrawlResponseModel]:
    """Try to map the site via sitemap.xml. Returns a CrawlResponseModel on
    success (sitemap found + parsed), or None if NO sitemap was reachable so the
    caller can fall back to BFS ('auto'). A sitemap that was found but produced no
    rows is NOT None - it returns an empty map that says so, because reporting it
    as "not found" is a different (and false) answer. Same-domain + path filters
    applied BEFORE the cap, so max(1000, max_pages*10) is spent on the pages the
    caller asked for (see discover_sitemap's `accept`)."""
    from dhole_mcp.sitemap import discover_sitemap

    def _make_http_get():
        from dhole_mcp.search_proxy import configured_proxy
        _sitemap_proxy = configured_proxy()[0]
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

    def _eligible(raw: str) -> bool:
        """Same-domain + path filters, decided on the raw sitemap entry.

        Pushed into the walk so the cap counts eligible URLs: filtering the
        already-capped list meant `path_include=["/docs/"]` on a site whose docs
        start at sitemap entry 2000 returned nothing - and `_sitemap_map` then
        returns None, so the call silently fell back to BFS with no sign that the
        scope had been applied to the wrong end of the list.
        """
        try:
            parsed = urlparse(raw)
        except Exception:
            return False
        if parsed.scheme not in ("http", "https") or parsed.netloc != root_netloc:
            return False  # sitemaps can list other hosts; keep same-domain only
        return _sitemap_passes_filters(parsed.path or "/", path_include, path_exclude)

    try:
        result = await asyncio.to_thread(
            discover_sitemap, url, http_get=_make_http_get(), max_urls=cap,
            accept=_eligible,
        )
    except Exception:
        return None
    if not result.sitemaps_used:
        # 没有一份 sitemap 读通。**为什么**读不通是调用方接下来那句总结的全部
        # 依据 —— 交出去，别让调用方去猜（2026-10-09 审计 D2：它当时猜错了，
        # 而且是朝着"断言没发生过的事"那个方向猜错的）。
        if diag is not None:
            diag["robots_checked"] = result.robots_checked
            diag["robots_had_sitemap"] = result.robots_had_sitemap
            diag["guard_refused"] = list(result.guard_refused)
            diag["via"] = result.via
        return None  # nothing reachable: the caller's "no sitemap here" answer is true

    pages: list[CrawlPage] = []
    seen: set[str] = set()
    for su in result.urls:
        if time() > deadline_t:
            break
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

    listed = len(result.urls)
    if not pages:
        # The sitemap WAS found and parsed; it just produced no rows. Returning
        # None here made web_crawl report "no sitemap.xml found ... /sitemap.xml
        # returned nothing" - false, and it sends the agent to BFS for a site
        # whose sitemap dhole has already read. A scope filter that matches
        # nothing is the usual cause (see `_eligible`), and the answer that helps
        # is "the sitemap lists N URLs, none in the section you asked for".
        scoped = [f"{k}={v}" for k, v in (("path_include", path_include),
                                          ("path_exclude", path_exclude)) if v]
        scope_desc = " or match ".join(scoped) if scoped else "on this host"
        if listed:
            # Eligible, but every one was a repeat after normalization, or the
            # deadline was already spent before the first row.
            why = (f"its {listed} URL(s) did not become rows (a repeat after "
                   f"normalization, or the deadline)")
            fix = ("Re-run with a larger deadline_ms, or without sitemap=true to "
                   "discover by links instead.")
        elif result.filtered_out:
            why = (f"none of its {result.filtered_out} URL(s) are {scope_desc}")
            fix = ("Widen or drop the scope if that is not what you meant, or run "
                   "without sitemap=true to discover by links instead.")
        else:
            why = "it lists no URLs"
            fix = "Run without sitemap=true to discover by links instead."
        return CrawlResponseModel(
            start_url=normalize_url(url), pages=[], pages_crawled=0,
            pages_discovered=0, discover_only=True, sitemap_used=True,
            sitemaps=list(result.sitemaps_used),
            duration_ms=(time() - t0) * 1000,
            summary=(f"sitemap.xml was read at {url} "
                     f"({len(result.sitemaps_used)} file(s)) but the map is empty: {why}"),
            next_action=fix,
        )

    ok = len(pages)
    summary = (f"mapped {ok} URL(s) at {url} from sitemap.xml "
               f"(via {result.via}; {len(result.sitemaps_used)} sitemap file(s)) - one fetch, no BFS")
    next_action = (
        f"{ok} URLs mapped from the sitemap. web_read the ones you need - urls=[] "
        f"takes the whole subset in one call, no discovery and no re-crawl - or "
        f"re-run web_crawl with discover_only=false to crawl content from the "
        f"start URL. Use path_include/path_exclude to scope."
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
    elif result.index_truncated:
        # The other way to be short of the site, and the counts cannot show it:
        # the walk reads at most _MAX_SITEMAPS files / _MAX_INDEX_DEPTH levels, so
        # a <sitemapindex> with more (or deeper) children ends under the cap with
        # `capped` False. A site that splits 1,200 pages across 30 small child
        # sitemaps maps 1,000 of them and says nothing about the rest.
        summary += (" · the sitemap index is bigger than one walk reads, so this map "
                    "is a prefix of the site")
        # No knob to name here, and that is the honest answer: neither max_pages
        # nor the path filters reach the unread files. Saying "raise max_pages"
        # would be the same dead end P2-9 already fixed on the cap branch.
        next_action += (" The map covers only the sitemap files this walk reads - there "
                        "is no knob to read further, so path_include scopes within what "
                        "is here rather than revealing what is not.")
    else:
        # "mapped 349 URL(s)" and no other wording reads as "this is the site", and
        # the cap branch was the only one that ever said otherwise. NOT reaching the
        # cap is not evidence of the opposite: a sitemap is an index the site
        # chooses to publish, and plenty omit whole sections.
        summary += (" · the sitemap's own list - a sitemap is an index the site "
                    "publishes, not proof it lists every page")
        if listed > ok:
            # Both causes are knowable here, so name them rather than hedging: an
            # entry the sitemap listed twice under two normalizations (`/a` and
            # `/a/`), or a walk that ran out of deadline. Off-domain and filtered
            # entries cannot appear - `_eligible` already dropped them upstream.
            summary += (f" · {listed - ok} of the sitemap's {listed} eligible entries "
                        f"did not become rows (a repeat after normalization, or the "
                        f"deadline)")
    return CrawlResponseModel(
        start_url=normalize_url(url), pages=pages, pages_crawled=0,
        pages_discovered=ok, discover_only=True, sitemap_used=True,
        sitemap_truncated=bool(result.capped or result.index_truncated),
        sitemaps=list(result.sitemaps_used),
        duration_ms=(time() - t0) * 1000, summary=summary, next_action=next_action,
    )


def _crawl_shares_the_private_door(fn):
    """把 cred_defaults 的 ``allow_private`` 铺满**整趟爬取**，不只是种子那一颗。

    2026-10-09 审计 D2（真机复现）：作用域曾经只包住种子那一行
    ``validate_url(url)`` 就退出，而 sitemap 的发现（sitemap.py 里逐条
    ``validate_url``）、robots.txt 的复校验（robots.py ``_fetch_rules``）、
    第二层链接的抓取全都发生在那之后 —— 于是同一个 ``allow_private`` 在**读**
    的门上是开的、在**爬**的门上是死的：一个 robots.txt 明写 ``Sitemap:``、
    ``/sitemap.xml`` 也确实返回 200 合法 XML 的本机 dev 站，``web_discover``
    报的是「没找到」。

    修法不是再补一个作用域（那是第二处会漂移的真相），而是让这扇门在整趟爬取里
    只有一个位置：进来时推开一次，出去时关掉。``functools.wraps`` 保住了
    ``__wrapped__``，所以 ``inspect.signature(web_crawl)`` 仍是原来那份 17 参签名
    —— 形状守卫读的是它，不是包装器。

    ContextVar 会被 ``asyncio.to_thread`` 带进工作线程（``copy_context``），
    所以 sitemap 那条线程里的 ``validate_url`` 读到的是同一个答案。
    """
    @functools.wraps(fn)
    async def _wrapped(server, url, *args, **kwargs):
        from dhole_mcp import identity as _identity
        from dhole_mcp.security import allow_private_scope
        with allow_private_scope(_identity.get("allow_private")):
            return await fn(server, url, *args, **kwargs)
    return _wrapped


def _no_sitemap_summary(start_norm: str, diag: dict) -> str:
    """「没找到 sitemap」这句话必须由**发生过什么**决定，不能是一句固定文案。

    2026-10-09 审计 D2：这句曾经逐字写着「robots.txt had no Sitemap directive
    and /sitemap.xml returned nothing」。在一个 robots.txt **确有**该指令、而
    ``/sitemap.xml`` **从未被请求**的私网站点上，两半都是假的 —— 读者照着去查
    robots.txt 会发现自己没写错，然后失去对整条回执的信任。这正是本仓库反复点名
    要杜绝的形状：调用看起来失败了，诊断却自信地答错。

    分支顺序就是「越具体越先说」：守卫拦下 > 指令在但读不出 > robots 没读到 >
    真的没有。
    """
    refused = diag.get("guard_refused") or []
    if refused:
        return (f"sitemap not read: robots.txt declares Sitemap: {refused[0]}, which "
                f"the SSRF guard refuses (a private or reserved address) - so nothing "
                f"was fetched from it")
    if diag.get("robots_had_sitemap"):
        return (f"no sitemap could be read at {start_norm}: robots.txt declares a "
                f"Sitemap, but fetching or parsing it produced nothing (and "
                f"/sitemap.xml answered nothing either)")
    if not diag.get("robots_checked"):
        return (f"no sitemap.xml found at {start_norm}: robots.txt could not be read, "
                f"and /sitemap.xml returned nothing")
    return (f"no sitemap.xml found at {start_norm} (robots.txt had no Sitemap "
            f"directive and /sitemap.xml returned nothing)")


def _no_sitemap_next_action(diag: dict) -> str:
    """与上面那句配套的出路。守卫拦下时不能说「换个站」——那不是站的问题。"""
    if diag.get("guard_refused"):
        return ("This host is private/reserved and the SSRF guard refused its sitemap. "
                "If it is your own dev site, waive it once with "
                "cred_defaults(set={\"allow_private\": true}) (loopback only) or "
                "set={\"allow_private\": [\"that.host\"]}, then call again. Otherwise "
                "run web_crawl with sitemap=false to discover by links instead.")
    return ("No sitemap found. Re-run web_crawl with sitemap=false (or omit it) "
            "to use best-first BFS discovery instead.")


@_crawl_shares_the_private_door
async def web_crawl(
    server,
    url: str,
    max_pages: int = 10,
    max_depth: int = 2,
    path_include: Optional[list[str]] = None,
    path_exclude: Optional[list[str]] = None,
    discover_only: bool = False,
    focus: Optional[str] = None,
    max_content_chars_per: int = 8000,
    max_total_chars: int = MAX_TOTAL_CHARS_DEFAULT,
    concurrency: int = 3,
    cache_ttl: int = 3600,
    force_fetcher: Optional[str] = None,
    timeout: int = 30000,
    deadline_ms: int = 25000,
    sitemap: str | bool = False,
    search: Optional[str] = None,
    ignore_robots: bool = False,
) -> CrawlResponseModel:
    """Best-first same-domain crawl. See module docstring.

    search: filter discovered/crawled URLs by keyword match (URL path + title).
        Use with discover_only=True for fast URL discovery on large sites.
    sitemap: True = map the site from its sitemap.xml only (one fetch; returns
    the full URL list + lastmod, no BFS, no content). 'auto' = use the sitemap if
    the site has one, else fall back to BFS. False (default) = BFS only. The
    sitemap path collapses big-site discovery (hundreds of pages) into one call.
    ignore_robots: skip robots.txt compliance for this crawl (default False =
    comply). Every page is fetched through web_read, which refuses a
    disallowed URL before any request; this crawl additionally drops the
    discovered URLs it already knows are disallowed (robots_skipped) instead of
    spending budget on a refusal.
    """
    from dhole_mcp.security import validate_url, SecurityError
    from dhole_mcp.trafilatura_extractor import extract_html_title

    t0 = time()
    deadline_t = t0 + (deadline_ms / 1000.0)

    def _err(msg: str, start: str = "", hint: str = "",
             refusal: bool = False) -> CrawlResponseModel:
        """一次爬行级的拒绝。`refusal=True` 说的是"dhole 自己写的整句"：
        200 字符的帽子防的是浏览器 call-log 那种噪声，不该把守卫的解释剪在句子中间
        （评审 R3刀1：discover/crawl 把 300 字符的 fake-ip 解释截在 200，读者连
        "这是守卫"都读不出来，而唯一出路那句在被剪掉的尾巴上）。
        """
        from dhole_mcp.errors import classify_network_error
        if not hint:
            _, hint = classify_network_error(msg)
        return CrawlResponseModel(start_url=start or url, pages=[],
                                  error=msg if refusal else msg[:200],
                                  duration_ms=(time() - t0) * 1000,
                                  summary=f"crawl failed: {msg[:120]}",
                                  next_action=hint)

    try:
        # 种子 URL 的私网放行：作用域在 `_crawl_shares_the_private_door` 里，
        # 已经罩住整个函数体（种子、robots、sitemap、每一页）。这里不再单独推一次
        # —— 两处作用域就是两处会漂移的真相，而 2026-10-09 审计 D2 的根因正是
        # 「作用域只包了种子这一行就退出」。这颗 validate_url 读的是同一份策略。
        url = validate_url(url)
    except SecurityError as e:
        from dhole_mcp.security import (
            PRIVATE_ADDRESS_REFUSAL_PREFIXES, PRIVATE_ADDRESS_RECOVERY,
            RESOLVER_GUARD_NEEDLE, RESOLVER_GUARD_RECOVERY)
        msg = str(e)
        if RESOLVER_GUARD_NEEDLE in msg:
            # 探测过期（进程记着 public，TUN 后来上线）—— 说清出路是重启宿主，
            # 而不是让读者去改一颗没错的参数。
            return _err(msg, hint=RESOLVER_GUARD_RECOVERY, refusal=True)
        if msg.startswith(PRIVATE_ADDRESS_REFUSAL_PREFIXES):
            # 一个包都没发的地址拒绝，兜底那句 "Fetch failed ... retry with different
            # parameters" 是条假出路：换参数不会让 127.0.0.1 变成公网。
            return _err(msg, hint=PRIVATE_ADDRESS_RECOVERY, refusal=True)
        return _err(msg, refusal=True)
    except ValueError as e:
        return _err(str(e))
    start_norm = normalize_url(url)  # canonical form for dedup + the response field

    # robots.txt compliance (v16.0): the crawl's own seed check, so a site that
    # disallows dhole is reported once, honestly, instead of as a crawl that
    # silently collected refusals. Per-page enforcement lives in web_read
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

    # 三道闸各管一件事，每一道到点都要说出口（下面那四句 clamp note + summary 里的
    # truncation=total_chars）。17.0 §3 第 31 条曾经写下"这里没有整轮字符预算，因为
    # 一整趟多少字符要先把每页长度估出来、而那是站点决定的数"；2026-10-05 的实测把
    # 前提打掉了一半：估不准是真的，但算不清不等于花得起 —— max_pages=41（钳到 40）
    # × 默认每页 8,000 真跑出 223,599 字符（约 81k token），被宿主截断落盘，调用方
    # 拿到的不是爬取结果而是一句"太大去看文件"。所以整轮预算回来了，而它数的是**返回
    # 给调用方的字符数** —— 那一个是 dhole 自己写得出的数，不需要猜站点的页有多长。
    _asked = (max_pages, max_depth, concurrency, max_content_chars_per)
    max_pages = max(1, min(int(max_pages), MAX_PAGES_CEILING))
    max_depth = max(0, min(int(max_depth), 5))
    concurrency = max(1, min(int(concurrency), 5))
    max_content_chars_per = max(500, min(int(max_content_chars_per),
                                         PER_PAGE_CHARS_CEILING))
    # 负的 cache_ttl：与 web_read / web_search 同一句话（作者在 context.py），
    # 也同一颗行为 —— 每一页都按 0 传下去，读也不读、写也不写。爬取这条路上它
    # 尤其要紧：一页一次默认 TTL 命中会让"这次爬到的正文"与"上一小时那次"混起来。
    _ttl_note = _cache_ttl_note(cache_ttl)
    if _ttl_note:
        cache_ttl = 0
    # `max_total_chars=0`（和任何负数）是"这一轮不要帽"的写法 —— 与 nested_max=0
    # 同一颗拼法，所以负数不当越界处理、也就不进上报；>0 才在 1,000..200,000 里钳。
    _asked_total = int(max_total_chars or 0)
    if _asked_total <= 0:
        max_total_chars = 0
    else:
        max_total_chars = max(MIN_TOTAL_CHARS, min(_asked_total, MAX_TOTAL_CHARS_CEILING))
    # web_read names its clamp in the summary; a crawl that silently answered
    # 500 chars for a request of 400 named nothing at all, so the two tools
    # described the same event differently depending on which one was called.
    # 那一句当初只补了 `max_content_chars_per` 一颗，另外三颗仍然静默：实测
    # max_pages=41 换来 40、max_depth=9 换来 5、concurrency=9 换来 3，信封里一个字
    # 都没有 —— 而这三颗恰恰是"这次爬了多少页为什么比我少的"唯一的答案。
    # 措辞由 context._clamp_note 一处拥有：两句同义而不同形的话，等于没有那句话。
    _clamp_notes = [note for note in (
        _clamp_note("max_pages", _asked[0], max_pages, lo=1, hi=MAX_PAGES_CEILING),
        _clamp_note("max_depth", _asked[1], max_depth, lo=0, hi=5),
        _clamp_note("concurrency", _asked[2], concurrency, lo=1, hi=5),
        _clamp_note("max_content_chars_per", _asked[3], max_content_chars_per,
                    lo=500, hi=PER_PAGE_CHARS_CEILING),
        # 负数与 0 是同一个意思（"这一轮不要帽"），不是一次越界，所以不上报。
        _clamp_note("max_total_chars", _asked_total, max_total_chars,
                    lo=MIN_TOTAL_CHARS, hi=MAX_TOTAL_CHARS_CEILING)
        if _asked_total > 0 else "",
        # 与上面四颗同一条纪律：钳位要出口。措辞的作者是 context._cache_ttl_note。
        _ttl_note,
    ) if note]
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
    if sm_mode in ("on", "auto"):
        # 诊断袋：只有"一份 sitemap 都没读通"时才会被填。总结与出路都由它决定，
        # 不再是一句固定文案（D2：那句固定文案在私网站点上两半都是假的）。
        _sm_diag: dict = {}
        sm_result = await _sitemap_map(url, path_include, path_exclude,
                                       max_pages=max_pages, deadline_t=deadline_t,
                                       diag=_sm_diag)
        if sm_result is not None:
            # A found-but-empty map is a real answer ("the sitemap lists N URLs,
            # none in the section you asked for"), but only for an explicit
            # sitemap=true. Under 'auto' the caller asked for discovery, and BFS
            # can reach pages the sitemap never listed, so an empty map falls
            # through to BFS exactly as a missing sitemap did before.
            if sm_result.pages or sm_mode == "on":
                return sm_result  # sitemap found + mapped (auto/on success)
        # auto: no sitemap -> fall through to BFS. on: no sitemap -> honest empty.
        if sm_mode == "on":
            return CrawlResponseModel(
                start_url=start_norm, pages=[], pages_crawled=0,
                pages_discovered=0, discover_only=True, sitemap_used=False,
                duration_ms=(time() - t0) * 1000,
                summary=_no_sitemap_summary(start_norm, _sm_diag),
                next_action=_no_sitemap_next_action(_sm_diag),
            )

    root = _same_root(url)
    visited: set[str] = set()
    discovered: set[str] = {start_norm}
    pages: list[CrawlPage] = []
    robots_skipped = 0
    truncated_maxpages = False
    truncated_time = False
    truncated_total_chars = False
    # 被字符帽挡下的两种页要分开数：`chars_dropped_fetched` 是这批已经发过请求、
    # 只是没返回的；队列里剩下的那些从来没付过钱。
    chars_dropped_fetched = 0

    sem = asyncio.Semaphore(concurrency)

    # G27 — politeness interval, per host. `concurrency` caps how many requests
    # are in flight, which is a different question from how often one server is
    # hit: 3 concurrent requests at a slow host is 3 per second, forever. Each
    # host gets a next-allowed slot, so N concurrent lanes at one host collapse
    # into one request per interval while different hosts stay independent.
    #
    # 17.0 §3 第 31 条：这个间隔只剩一个来源 —— 站点自己在 robots.txt 里写的 Crawl-delay。
    # 调用方的 `delay=` 删了：它不是性能参数，而是对别人服务器的礼貌问题，而这件事
    # 站方已经用 Crawl-delay 表过态。只升不降仍然成立（没有东西可以被把低了），60s
    # 的上限也还在，但那是 `robots.crawl_delay()` 对站方要求做的事，不再是调用方
    # 能推的旋钮。
    next_slot: dict[str, float] = {}
    pace_lock = asyncio.Lock()
    interval_used = 0.0

    def _interval_for(u: str) -> float:
        """How long this host asked to be left alone (0 = it did not ask, or the
        caller waived robots)."""
        if ignore_robots:
            return 0.0
        try:
            from dhole_mcp.robots import cached_crawl_delay
            return cached_crawl_delay(u) or 0.0
        except Exception:
            return 0.0

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
        after a 1s delay. Deterministic errors (connection_refused, dns_failure,
        dns_unresolvable) are not retried (they will fail again).
        """
        async with sem:
            await _pace(u)
            # 17.0: 代理池删了，页面抓取不再"每页换一个 IP"；出口由 cred_defaults
            # 存的身份统一决定 —— web_read 自己从身份里取，调用方不再传 proxy。
            try:
                resp = await server.web_read(
                    url=u, extraction_type="html", cache_ttl=cache_ttl,
                    max_content_chars=200000, force_fetcher=force_fetcher,
                    timeout=timeout,
                    ignore_robots=ignore_robots,
                )
            except Exception as e:
                # Retry once for transient errors (timeout/reset)
                if _is_transient_error(str(e)):
                    await asyncio.sleep(1.0)
                    try:
                        resp = await server.web_read(
                            url=u, extraction_type="html", cache_ttl=cache_ttl,
                            max_content_chars=200000, force_fetcher=force_fetcher,
                            timeout=timeout,
                            ignore_robots=ignore_robots,
                        )
                    except Exception as e2:
                        # Lazy import to break circular dependency:
                        # crawl.py -> server.py (ResponseModel) and
                        # server.py -> crawl.py (web_crawl). Both are
                        # function-level imports so neither module needs the
                        # other at import time.
                        from dhole_mcp.server import ResponseModel
                        resp = ResponseModel(url=u, status=-1, content=[""],
                                             fetcher_used="none", error=str(e2)[:200])
                else:
                    from dhole_mcp.server import ResponseModel
                    resp = ResponseModel(url=u, status=-1, content=[""],
                                         fetcher_used="none", error=str(e)[:200])
            # 17.0: 这里原来会按成败给代理记健康分并冷却它 —— 那是池子的机制，
            # 池子删了就没有可冷却的对象；失败本身已经写在 resp.error 里如实上报。
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

    push(url, 0, "")

    # Fetch a batch of up to `concurrency` best URLs from the heap concurrently.
    # 整轮字符帽在**发下一批之前**判，不是返回之后再砍：一版只在装配时截的预算一次
    # 网络也省不下来，只是把已经付过钱的结果藏起来。这里停下来的是"还没发的那些"。
    total_chars = 0
    # 每一行被那两把尺量到的东西，按 URL 存着。存在这里而不是塞进 CrawlPage：
    # 一行多一颗字段，在 content 模式里是每行都要序列化一遍的字节，而这张表只有
    # summary 那一句话要读它（还有 `_summary_for` 要按"实际返回的那些行"数）。
    measured: dict[str, dict] = {}
    while heap and len(pages) < max_pages and (
            not max_total_chars or total_chars < max_total_chars):
        if time() > deadline_t:
            truncated_time = True
            break
        # 一句话说清"这一轮在等什么"：一次 crawl 是几十秒到几分钟，客户端在响应
        # 回来之前除了转圈什么也看不见，超时/取消策略只能瞎猜。批次边界是这里的
        # 自然格点（一页一发没有意义，一批就是 concurrency 页）。
        await report_progress(float(len(pages)), total=float(max_pages),
                              message=(f"crawling {urlparse(url).netloc}: "
                                       f"{len(pages)} of up to {max_pages} pages done, "
                                       f"{len(heap)} queued"))
        remaining = max_pages - len(pages)
        batch: list[tuple[float, int, int, str, str]] = []
        while heap and len(batch) < min(concurrency, remaining):
            batch.append(heapq.heappop(heap))
        results = await asyncio.gather(*[fetch_one(e[3]) for e in batch])

        for _bi, ((neg_score, depth, _seq, u, _text), (ru, resp, html)) in enumerate(
                zip(batch, results)):
            if len(pages) >= max_pages:
                truncated_maxpages = True
                break
            # 整轮的字符帽在批内也要判：一批最多 `concurrency` 颗，一次 while 检查
            # 最坏会多吐出一整批。被这一格挡下的页是**已经付过请求**的，所以 summary
            # 要把"付了没返回"和"根本没付"分开说 —— 合起来只报一个"少了 N 页"，
            # 调用方就分不清是该抬预算还是该换 max_pages。
            if max_total_chars and total_chars >= max_total_chars:
                truncated_total_chars = True
                chars_dropped_fetched = len(batch) - _bi
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
            row_summary = resp.summary
            row_stats: dict = {}
            if not discover_only and html:
                if resp.content_ok:
                    try:
                        md, kind, ok, cut = _classify_and_extract(
                            html, resp.url or u, url, focus,
                            max_content_chars_per, stats=row_stats)
                    except Exception:
                        md, kind, ok, cut = "", "fallback", False, False
                        row_stats = {}
                    page_type = kind
                    content_ok = ok and bool(md)
                    if row_stats:
                        # 每一行的证据都登记：跨行的"这是菜单"判据要拿到各行的条目集合
                        # 才数得出，而整轮那句"N page(s) hold far more text"要数得出
                        # 被塌陷拒掉的行 —— 两者都不是"有话要说"才记账。
                        measured[u_norm] = row_stats
                    note = _measure_note(row_stats)
                    if note:
                        # 量到的东西要走在这一行**在场**的那句话里。一行的 summary 是
                        # crawl 行唯一的抬头（这一族没有 metadata）。
                        row_summary = f"{row_summary} · {note}" if row_summary else note
                    if md:
                        # The extraction already holds the whole page up to the
                        # per-page cap; `cut` says whether more exists. next_offset
                        # is the char offset INTO THAT extraction, so it is only
                        # meaningful with web_read on this URL - a crawl page has
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
                summary=row_summary, error=resp.error,
                untrusted_content=injection.scan(content_md or ""),
            )
            pages.append(page)
            total_chars += len(content_md)

            # Browser auto-escalation: if the start page (depth 0) failed with
            # js_shell or bot_challenge, upgrade force_fetcher to "stealthy" for
            # all subsequent pages (the site likely needs a real browser).
            if (depth == 0 and not content_ok and force_fetcher is None
                    and page.page_type in ("js_shell", "fallback")
                    and ("bot_challenge" in (page.error or "") or "js_shell" in (page.error or "")
                         or page.page_type == "js_shell")):
                force_fetcher = "stealthy"
                logger.info(f"Crawl: upgrading to stealthy browser (page_type={page.page_type})")

            # Discover links for deeper layers.
            if depth < max_depth and html:
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
                        # through - web_read is the enforcement point.
                        if not ignore_robots and get_robots_cache().cached(link_url) is False:
                            robots_skipped += 1
                            continue
                        discovered.add(link_norm)
                        push(link_url, depth + 1, link_text)  # fetch original

    pages_crawled = len(pages)
    # 收尾那一格要说**最终**数字：循环顶上那次报的是"最后一批开始前"的状态，
    # 少报一批会让人以为任务停在半路。
    await report_progress(float(pages_crawled), total=float(max_pages),
                          message=f"crawl finished: {pages_crawled} pages fetched "
                                  f"on {urlparse(url).netloc}")
    # Count any URLs still in the heap as discovered (they were found but not crawled).
    for _neg, _d, _s, hu, _t in heap:
        discovered.add(normalize_url(hu))
    pages_discovered = len(discovered)

    if pages_crawled >= max_pages and pages_discovered > pages_crawled:
        truncated_maxpages = True
    # 两道闸同时到点时，说出的是先顶到的那道：max_pages 是这颗工具文档里的"第一道
    # 预算"，字符帽是第二道。两格都立起来会让调用方以为抬哪一个都一样。
    if (max_total_chars and not truncated_maxpages and heap
            and total_chars >= max_total_chars):
        truncated_total_chars = True
    not_fetched = max(pages_discovered - pages_crawled, 0)

    # 菜单是按**整轮**才看得见的那一形：同一组条目链接逐字出现在好几行里，说明那是站点
    # 的导航件，不是这一页的内容。一页之内分不开 —— 实测两边都会挑错：books.toscrape 的
    # travel 页把全部 11 本书装在 `<aside class="sidebar">` 里（剥 aside 之后主内容区只剩
    # 2 条链接），而靶站那枚 12 链接的导航页与一个扁平 slug 列表在 URL 形状上同构
    # （`/about`、`/cart` 与 `/quote/1`、`/quote/2` 都是"一段路径、末段可变"）。所以这句
    # 只能由"跨行同集"来作证，而且它今天只上报，不翻任何一行的结论位。
    chrome_rows = _chrome_rows(measured)
    for _page in pages:
        _m = measured.get(_page.url)
        repeats = chrome_rows.get(_page.url)
        if _m is not None and repeats:
            _m["site_chrome"] = True
            note = (f"the same {_m['list_items']} item link(s) as {repeats} of these "
                    "rows - that group is site chrome, not this page's content")
            _page.summary = f"{_page.summary} · {note}" if _page.summary else note

    def _summary_for(pages_now: list) -> str:
        """Stats line for ``pages_now``.

        Called once before and once after the `search` filter. The post-filter
        call has to describe the pages actually returned: prefixing the
        pre-filter stats produced "crawled 1 page(s); 1 content_ok" sitting next
        to pages_crawled=0 and pages=[].
        """
        ok_now = sum(1 for p in pages_now if p.content_ok)
        # "N content_ok" 旁边必须站着"这几行我们知道自己没读全"。数的是**实际返回的**
        # 那些行：search 过滤器砍掉的行不再被这一句替它作证（同 _summary_for 存在的理由）。
        collapsed_now = sum(1 for p in pages_now
                            if measured.get(p.url, {}).get("extraction_collapsed"))
        chrome_now = sum(1 for p in pages_now
                         if measured.get(p.url, {}).get("site_chrome"))
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
        if collapsed_now:
            bits_now.append(
                f"{collapsed_now} page(s) hold far more text in their own markup than "
                "we returned (see those rows' summary)")
        if chrome_now:
            bits_now.append(
                f"{chrome_now} row(s) carry the same item-link group as other rows "
                "(site chrome, not per-page content)")
        if truncated_maxpages:
            bits_now.append("stopped: max_pages reached")
        if truncated_time:
            bits_now.append("stopped: time deadline reached")
        if truncated_total_chars:
            # 这一句必须自带"被砍的是哪几页"：一次 40 页的爬取只回 7 页时，调用方
            # 看到的第一件事是"怎么比我要的少"，而少的部分既可能是队列里没付过的，
            # 也可能是这一批已经付过、只是没吐出来的。两种少法要不同的解药。
            # 返回的字符数允许越过帽一格 —— 一颗页的长度只有抓回来才知道，而把
            # 已经付过钱的那页扔掉再假称"正好 8,000"是更坏的选择，所以要照实说。
            queued_now = len(heap)
            bits_now.append(
                f"truncation=total_chars: returned {len(pages_now)} page(s) / "
                f"{total_chars:,} chars against the {max_total_chars:,} you set "
                f"(a page's length is only known once it is fetched); "
                f"{queued_now:,} URL(s) still queued"
                + (f", {chars_dropped_fetched} fetched in the last batch and not "
                   "returned" if chars_dropped_fetched else "")
                + " - max_total_chars=0 removes the cap")
        if robots_skipped:
            bits_now.append(f"{robots_skipped} URL(s) skipped by robots.txt")
        # 四颗钳制一起报（哪一颗没动就不出现），措辞与 web_read 的 summary 同一句。
        bits_now.extend(_clamp_notes)
        if ((path_include or path_exclude) and len(pages_now) <= 1
                and not truncated_total_chars and not heap):
            # The filters run while links are discovered, so a pattern that matches
            # nothing produces a crawl of exactly one page (the start URL is never
            # filtered) and no count anywhere says a filter ran. Measured: a
            # path_include the site did not use returned the start page alone,
            # pages_discovered=1, empty next_action - it read as a successful crawl.
            #
            # But the claim is only true when the queue is EMPTY. Measured the other
            # way on 2026-10-08: `max_total_chars=1500` against a site whose pages
            # run 4,346 chars each stopped after page one with 83 URLs still queued,
            # and this note went out blaming path_include - about a pattern the run
            # itself then proved right (the same scope admitted links on a second
            # call). A character budget and a scope filter cannot both be the reason
            # for one stop, so the budget's own bit above is what stands here.
            bits_now.append("only the start page was kept - "
                            f"path_include={list(path_include or [])} / "
                            f"path_exclude={list(path_exclude or [])} scoped out every "
                            "discovered link (nothing was left queued)")
        if interval_used > 0:
            # Saying the interval exists is the point: a crawl that took 40
            # seconds for 10 pages looks like a hang unless the stats line says
            # it was spacing itself on purpose. And naming who set it: there is
            # no caller knob for this any more, so the host's own ask is the
            # only possible author.
            bits_now.append(
                f"{interval_used:g}s between requests per host "
                "(the host's own robots.txt Crawl-delay)")
        return "; ".join(bits_now)

    # Build a concise summary + next_action.
    ok = sum(1 for p in pages if p.content_ok)
    summary = _summary_for(pages)

    next_action = ""
    # 排队未发的 URL 是"能不能再砍一刀"的唯一依据：它们的第一段路径决定值不值得推荐
    # 一棵子树（见 `_scope_hint`）。
    scope_hint = _scope_hint([hu for _neg, _d, _s, hu, _t in heap])
    if truncated_total_chars:
        next_action = (
            f"crawl stopped at the whole-run character budget "
            f"(max_total_chars={max_total_chars:,}); {not_fetched:,} URL(s) were "
            "discovered but not fetched. Raise max_total_chars (up to "
            f"{MAX_TOTAL_CHARS_CEILING:,}) or pass 0 for no cap, lower "
            "max_content_chars_per to fit more pages in the same budget"
            + (f", or scope the run with {scope_hint}" if scope_hint else "")
            + ". The pages you already have are the best-first ones - "
            "web_read the picks you actually need instead of re-crawling.")
    elif truncated_maxpages or truncated_time:
        why = "time deadline" if truncated_time else "max_pages"
        next_action = (
            f"crawl stopped early ({why}); re-run web_crawl with a higher "
            f"max_pages / deadline_ms"
            + (f", or scope it with {scope_hint}" if scope_hint else "")
            + f". {not_fetched} URL(s) were discovered but not fetched."
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
            f"links). web_read the ones you need, or re-run web_crawl with "
            f"discover_only=false to fetch content from the start URL. For a "
            f"ONE-request map, pass max_pages=1 (start URL's links) or "
            f"sitemap=true (whole site, if it publishes one)."
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
                          "Try web_read on a specific URL for more diagnostics.")

    from dhole_mcp.security import recheck_posture_note
    result = CrawlResponseModel(
        start_url=start_norm, pages=pages, pages_crawled=pages_crawled,
        pages_discovered=pages_discovered, discover_only=discover_only,
        truncated_by_max_pages=truncated_maxpages,
        truncated_by_time=truncated_time,
        truncated_by_total_chars=truncated_total_chars,
        # 一次读取进程事实的调用：探针已经在这次爬取里跑过了（每页的 web_read 都
        # 过那道门），这里只是把它的判词拿到调用方看得见的一层。
        ssrf_recheck=recheck_posture_note(),
        duration_ms=(time() - t0) * 1000,
        robots_skipped=robots_skipped,
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


# ─── 千页确定性批量：plan 存储与续传 token（批次8 刀2，F-D-J2） ────────────────
# 阶段1：web_discover 把 URL 清单留在服务器侧并回发 plan_id；阶段2：web_crawl
# 用 resume_token=<plan_id>:<offset> 从任意断点继续——agent 不再做每轮 ~7.6KB
# 的 URL 传送带。清单只存**内存**（与凭据存储同一档信任），TTL 与容量都有帽：
# 过期的 plan 让下一次 resume 明说"重跑 web_discover"，绝不静默当成空计划。
# 明确不落 MCP Tasks：那要客户端能力协商＋客户端改行为（宿主面未兑现）；
# token 复用 offset/next_offset 语汇，宿主面兑现后可作降级层共存。
PLAN_TTL_S = 3600
PLAN_MAX = 32
_PLANS: dict[str, tuple[float, list[str]]] = {}


def plan_store(urls: list[str]) -> str:
    """留一份 URL 清单，回发短 plan_id。过期与超容的老计划先挤出。"""
    import secrets
    now_s = time()
    for pid in [p for p, (exp, _) in _PLANS.items() if exp <= now_s]:
        _PLANS.pop(pid, None)
    while len(_PLANS) >= PLAN_MAX:
        _PLANS.pop(next(iter(_PLANS)))
    pid = secrets.token_hex(6)
    _PLANS[pid] = (now_s + PLAN_TTL_S, list(urls))
    return pid


def plan_load(plan_id: str) -> Optional[list[str]]:
    """取回计划；过期/未知返回 None（调用方要给出明确的重跑指引，不能装空）。"""
    row = _PLANS.get(plan_id)
    if row is None:
        return None
    expires, urls = row
    if expires <= time():
        _PLANS.pop(plan_id, None)
        return None
    return urls


def plan_token(plan_id: str, offset: int) -> str:
    return f"{plan_id}:{offset}"


def plan_parse(token: str) -> Optional[tuple[str, int]]:
    """resume_token 形如 ``<plan_id>:<offset>``（复用 offset/next_offset 语汇）。"""
    head, _, tail = (token or "").rpartition(":")
    if not head or not tail.isdigit():
        return None
    return head, int(tail)
