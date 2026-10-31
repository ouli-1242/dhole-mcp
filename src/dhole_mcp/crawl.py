"""web_crawl —— Dhole 面向 agent 优化的旗舰级同域深度爬取。

从起始 URL 走遍一个站点，把每一页以 agent 可用的 markdown 交回，并带上 web_read
同款的诚实信号（content_ok / summary / error / fetched_at）。目标是在 agent 可用性上
胜过免费 OSS 爬虫（Crawl4AI、Firecrawl 免费档、Jina）：逐页诚实的质量信号、按内容
自适应的抽取、以及 next_action 指引，而不是把页面原样倒回去。

旗舰设计（v6）：
  * **最佳优先优先队列**（不只是 BFS）。发现的 URL 会被打分，分值最高的一页先爬。
    打分器混合：focus 查询相关度（锚文本 + URL 路径词段）、内容可能性（抬高
    docs/guide/api/reference/article/blog，压低 login/submit/register/cart/
    admin/account）、以及偏好浅层的深度项。不给 `focus` 时它仍胜过裸 BFS：在
    内容页之前先跳过垃圾 URL（login/submit）。
  * **按页面内容自适应抽取。** 一页按自己的 HTML 分类，再用对的方式抽取：
      - article/docs  -> trafilatura 主内容（markdown）。
      - list/index    -> 结构化的 `* [title](url)` 链接清单（HN、聚合站、
                         目录页 —— trafilatura 的主内容过滤器返回空，正因为
                         这一页本身就是一堆链接）。
      - js-shell      -> 抓取那一步已经自动从 HTTP 升级到 stealthy 渲染；
                         若连渲染后的 HTML 也是空的，则 content_ok=false，
                         并给出可执行的 error（用 web_act 的截图动作 / vision）。
      - fallback      -> 清洗过的可见文本。
    这从根上修掉了"Hacker News 上 0 content_ok"与"JS SPA 超时"这一类 bug：
    列表页现在把自己的链接清单当正文交回，JS 壳会被识别并诚实上报，而不是静默为空。
  * **URL 归一化 + 去重。** 尾斜杠、默认端口、小写 host、以及跟踪类查询参数
    （utm_*, fbclid, gclid, ref, _）都在去重之前剥掉，于是 `/docs` 与 `/docs/`
    不会再被爬两次。
  * **先出地图，再读页面。** `discover_only=true` 返回 URL 地图（预取），
    `sitemap=true` 一次抓取就交回整站地图；从两者挑出的 URL 用
    `web_read(urls=[...])` 读。地图模式不抽正文，但不是免费的：展开地图意味着
    每一步抓一页，所以最多花 `max_pages` 次请求（默认 10），而 `pages_crawled`
    如实报出实际发了几次。要一次请求出地图，用 `max_pages=1`（只取起始 URL
    自己的链接）或 `sitemap=true`（整站，前提是站点有 sitemap）。
    第二阶段曾经住在这里：`crawl_urls=[...]` 抓取调用方挑出的一部分，不再重新
    发现。它在 17.0 移走了（§3 第 31 条裁决），因为调用方那张清单要先过归一化、
    同域检查、去重与上限四道，而每一道都需要自己的计数。那些 URL 从来不需要发现，
    所以 `web_read(urls=[...])` 本来就是它们的家。
  * **same_domain_only=true** 默认。外链被丢弃（不爬）。
  * **诚实的状态码。** 网络失败报 status -1（已文档化），下游逻辑才能把它与真实的
    HTTP 0 / 无响应区分开。
  * **新鲜度。** 每页带 `fetched_at`；`cache_ttl=0` 强制取新。
  * **整趟 deadline。** 一页慢不能挂住整次爬取；撞到 deadline 时交回部分结果，并置
    `truncated_by_time=true`。
  * 每页一次抓取（extraction_type='html'），复用 web_read 的反爬升级 + 抓取缓存。
    链接与 markdown 由同一份正文派生。
"""

from __future__ import annotations

import asyncio
import functools
import heapq
import json
import logging
import re
from datetime import datetime, timezone
from time import time
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlparse, urlunparse

from pydantic import Field, model_serializer

from dhole_mcp import injection
# 钳制文案的唯一作者。crawl 的 summary 不经过 _with_agent_hints（它是
# CrawlResponseModel，不是 ResponseModel），所以这里读的是那颗纯函数而不是
# ContextVar —— 同一句话，两个通道。
from dhole_mcp.context import _cache_ttl_note, _clamp_note, report_progress
# robots.txt 合规（v16.0）。robots.py 在模块层不 import dhole 的任何东西，所以这里
# 没有循环依赖；种子检查与"发现到的 URL 预判"都要用它，而只保留一个 import 点，才是
# 两边共用同一份缓存的原因。
from dhole_mcp.robots import check as check_robots, get_robots_cache
# 量的尺要只有**一把**。`visible_text_chars` / `is_extraction_collapsed` /
# `distinct_content_links` 是 web_read 那侧已经拿真实页面标定过的三把尺（比值带、
# 导航栏剥离），crawl 若自己再数一遍就会各有一套阈值 —— 同一个工具两种真相。
from dhole_mcp.envelope import (
    is_extraction_collapsed, visible_text_chars, visible_text_chars_without_chrome)
# 把一句话切成"还能核对的几块"这件事只有一位作者。crawl 曾自己写过一只
# `[A-Za-z0-9]+`（见 `_query_terms`），那条腿只认拉丁，中文/日文的 `focus=` 交出空集，
# 于是 score_link 的 focus_score 恒为 0 —— 参数成了装饰。`focus._tokens` 早就为同一件事
# 定过判据（Unicode 词段 + 无空格文字相邻二元组），这里读它，不再写第三只。
from dhole_mcp.focus import _tokens as _tokenize
from dhole_mcp.models import RefusalHandle

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


# 匹配 <a ... href="...">，同时捕获 href 与锚文本的可见文字。
_LINK_RE = re.compile(
    r'<a\b[^>]*\bhref=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
# 发现链接时跳过这些资源扩展名（它们不是页面）。
_ASSET_RE = re.compile(
    r"\.(png|jpe?g|gif|webp|svg|css|js|ico|pdf|zip|mp[34]|woff2?|gz|tar|exe|dmg)(\?|$)",
    re.IGNORECASE,
)
_SKIP_SCHEMES = ("javascript:", "mailto:", "tel:", "data:", "#")

# sitemap URL 地图的下限（见 _sitemap_map）。地图的价值是覆盖广度，所以它不像正文
# 那样跟着 max_pages 缩；只有 max_pages 超过 SITEMAP_MAP_FLOOR // 10 之后，才会把这个
# 下限顶上去。
SITEMAP_MAP_FLOOR = 1000

# 一份站点内容在地图里最多占几个位置（见 _order_map_entries）。取 3 而不是 1：
# 一个真用 /ja/ 做主语言的站，它的前三条就是 /ja/、/en/、/de/ —— 折到 1 会把它
# 自己的内容折没；折到 3 只吃掉"第 4 种语言开始"的重复。
MIRROR_VARIANTS_KEPT = 3

# 不改变页面内容的跟踪 / 统计查询参数 -> 归一化时剥掉，这样只在它们上面不同的
# 两个 URL 不会被爬两次。
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "ref_src", "source", "_ga", "mc_cid", "mc_eid",
}

# 一次爬取要服从的几个上限。具名而不是内联，因为工具描述必须写出它们，而描述一旦
# 与钳位漂开，正是本节测试存在的理由：测试读这些常量，并要求线路上出现同样的数字。
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

# 内容可能性的路径词段。抬高内容页、压低应用/后台噪声，让优先队列先爬 docs，再爬
# login/submit/cart。
_CONTENT_BOOST = ("doc", "docs", "guide", "tutorial", "api", "reference",
                  "article", "blog", "post", "learn", "manual", "help", "spec")
_JUNK_PENALTY = ("login", "signin", "sign-in", "signup", "sign-up", "register",
                 "submit", "cart", "checkout", "account", "admin", "logout",
                 "auth", "password", "settings", "preferences")


def _is_transient_error(error_str: str) -> bool:
    """错误是暂时性的（值得重试）则返回 True。timeout 与 connection reset 重试后
    可能成功；connection refused 与 DNS 失败不会。"""
    from dhole_mcp.errors import classify_network_error
    category, _ = classify_network_error(error_str)
    return category in ("timeout", "connection_reset", "unknown")


class CrawlPage(RefusalHandle):
    """爬行里的一行。戴 `RefusalHandle` 那一格把手（评审 R02-142 · 现场 r282）。

    这一行的 `error` 从来不是它自己造的：它是转手 `web_read` 那一份信封的判决句
    （`http_error_404: …` / `network_error: …`），可 `diagnosis` 这一格从前只住在信封上，
    于是同一件事在 `web_read` 里有把手、在爬行的行上没有 —— 手册教的分支办法
    （`switch on diagnosis.code`）在这一行读不到东西。行的形状由模型管，不由「每个构造点
    记得填」管，所以这里戴同一副基类，与 `FeedResult` 的行、状态门的格子同一机制。
    """
    url: str = Field(description="Page URL (final, after redirects). Normalized.")
    depth: int = Field(default=0, description="Hop depth from the start URL (0 = start).")
    status: int = Field(default=0, description="HTTP status. -1 = network error (no response / connection failed). 0 = no response yet.")
    content_ok: bool = Field(default=False, description="True = real content retrieved AND extracted. Trust content only if true. HTTP 200 alone does NOT set this.")
    fetcher_used: str = Field(default="", description="http/stealthy/cache/none")
    title: str = Field(default="", description="Page <title>")
    page_type: str = Field(default="", description="How the page was EXTRACTED - the shape of what came back, not the page's real genre: article = prose was extracted (any page whose main content reads as prose: docs, posts, but also a channel page with a blurb), list = the visible text is links and the answer is a structured link list, js_shell = script shell with no rendered words, fallback = none of the extractors found a shape. Read it before trusting `content`, not as a claim about what kind of site this is.")
    content: list[str] = Field(default=[], description="Page markdown (empty in discover_only mode). For list pages, a structured link list.")
    content_chars: int = Field(default=0, description="Chars of markdown returned for this page.")
    substance: str = Field(default="", description="What this row's body actually holds: `ok` (judged, and there are enough lines to have a shape opinion) / `nav_only` (menu-style fragments, or a page whose entire visible text is under 200 chars) / `no_words` (a packed payload) / empty = not judged. SAME AUTHOR AND SAME VALUES as web_read's `substance`: the same URL must not read as content one page at a time and as a shell inside a crawl. `nav_only`/`no_words` flip this row's `content_ok` to false.")
    substance_basis: str = Field(default="", description="The numbers that verdict was made from (line count, share of short fragment lines without sentence punctuation, mean line length, the page's own visible character count). Read it before accepting the verdict - a genuine link index stays `ok` because there the links ARE the content.")
    is_truncated: bool = Field(default=False, description="True = this page has more content; web_read it with offset=next_offset.")
    next_offset: int = Field(default=0, description="Next offset if is_truncated; 0 = no more.")
    fetched_at: str = Field(default="", description="ISO-8601 UTC when this page was fetched (may show cache age).")
    lastmod: str = Field(default="", description="<lastmod> from the site's sitemap.xml for this URL (sitemap mode only). Empty otherwise.")
    summary: str = Field(default="", description="One-line status for this page.")
    error: str = Field(default="", description="Error for this page, if content_ok is False.")
    # G25 阶段4：爬回的每页正文同样过注入启发式（见 dhole_mcp/injection.py）。
    untrusted_content: Optional[Dict[str, Any]] = Field(default=None, description="Present ONLY when this page's text matched an injection heuristic ({hits: [{kind, excerpt, count}], total} — see web_read's untrusted_content for the shapes and the discipline). Per-page marking keeps a flagged page findable inside a 50-page crawl without rereading every row.")


class CrawlResponseModel(RefusalHandle):
    """The receipt for ``web_crawl`` / ``web_discover``.

    The field descriptions are the contract for the agent. Map mode goes through
    ``_map_row_dict`` rather than a full page report, so a field missing on a map row
    does not imply it is missing on a page row.
    """

    start_url: str = Field(description="The URL crawl started from (normalized).")
    pages: list[CrawlPage] = Field(description="Crawled pages (one CrawlPage each). In discover_only, content is empty and pages hold the discovered URL map.")
    pages_crawled: int = Field(default=0, description="Pages actually fetched.")
    pages_discovered: int = Field(default=0, description="Total unique same-domain URLs found (including crawled). In map mode this is the size of the MAP, which is not the number of rows returned: when a budget or the deadline held rows back, pages_discovered is the whole map and len(pages) is the part that shipped.")
    discover_only: bool = Field(default=False, description="True = map mode (URLs only, no content).")
    truncated_by_max_pages: bool = Field(default=False, description="True = stopped early because max_pages was reached.")
    truncated_by_time: bool = Field(default=False, description="True = stopped early because the overall deadline (ms) was reached.")
    deadline_ms_applied: int = Field(default=0, description="The whole-call time budget THIS run was held to, in ms: the `deadline_ms=` you named, or this tool's own cap when you named none. It is NOT `timeout=` - that one bounds ONE HTTP attempt, and the two units were confused often enough to need the number. 0/absent = the call never reached the crawl loop (a refusal, or a run that errored before the first round).")
    budget_overrun_ms: int = Field(default=0, description="Milliseconds this run took OVER `deadline_ms_applied` while still shipping every row it had. Non-zero = the call outlived its own time budget WITHOUT being cut short: in-flight fetches are cancelled at the deadline (a short cleanup grace absorbs socket/browser teardown), so a non-zero number here is that teardown tail, not a whole extra HTTP attempt. 0/absent = the run finished inside its budget. `truncated_by_time=true` is a different event (rows held back); when both appear, the truncation sentence carries the count and this number carries the cost.")
    attempt_cap_ms: int = Field(default=0, description="Milliseconds ONE HTTP attempt was given on this run (the `timeout=` figure, or this tool's own default when none was named). It is the unit that `budget_overrun_ms` and `duration_ms` are NOT measured in: a run longer than this number did not ignore anything - it made more attempts, or a slower round. 0/absent = this path cannot name one (a map built without per-attempt caps).")

    truncated_by_total_chars: bool = Field(default=False, description="True = stopped early because the whole-run character budget (max_total_chars) was spent. The summary names how many pages were still queued and how many were fetched inside the last batch but not returned. In map mode it means the opposite of a page being dropped for being worthless: the rows were in hand and only their BYTES cost too much, so plan_id / next_resume_token carry the rest.")
    sitemap_used: bool = Field(default=False, description="True = the URL map came from the site's sitemap.xml (one fetch), not best-first BFS discovery.")
    sitemap_truncated: bool = Field(default=False, description="True = the map is SHORT OF THE SITE, not just short of your filters: it stopped at the URL cap, or the walk hit its own sitemap-index limit. pages_discovered is then a LOWER BOUND - the site's sitemap holds at least this many URLs. Absent (False) on a map that read the whole sitemap, but note that still is not a guarantee the sitemap lists every page: a sitemap is an index the site chooses to publish.")
    sitemaps: list[str] = Field(default=[], description="Sitemap.xml URLs that were fetched + parsed (sitemap mode only).")
    robots_skipped: int = Field(default=0, description="Discovered URLs dropped WITHOUT a request because their robots.txt disallows them (dhole matches its own user agent, 'dhole-mcp'). Not an error - those pages were never eligible. Pass ignore_robots=true if you have the site's permission to fetch them anyway.")
    duration_ms: float = Field(default=0, description="Duration ms.")
    error: str = Field(default="", description="Error message (crawl-level).")
    summary: str = Field(default="", description="One-line crawl status.")
    next_action: str = Field(default="", description="Obvious next call when one exists (continue crawl / fetch a page).")
    ssrf_recheck: str = Field(default="", description="Non-empty only when the post-redirect IP recheck was skipped because of this machine's resolver (a fake-ip proxy) — the SSRF posture of the WHOLE crawl, stated once. Before this field existed a crawl said nothing about it at all: the per-page receipts live on the web_read results that crawl consumes internally and never reaches the caller, so the one security posture that a 40-page run should disclose was dropped with the pages' own metadata.")
    identity_applied: str = Field(default="", description="Names only (never values) of what `cred_defaults` supplied for the pages this run fetched — one line for the whole run, gathered from each page's own receipt rather than recomputed from the store. It exists for the same reason as `ssrf_recheck`: a crawl row is a trimmed page report, so the per-page 'this was fetched with your credentials' died inside crawl. Read it before concluding the crawl saw the site the way a visitor does — a run carrying your stored auth is your logged view of that host, page after page.")
    plan_id: str = Field(default="", description="The URL list is held under this id in the ONE server process that created it (memory only, 1h TTL; it is not shared with another process, so a restart or a second server starts with an empty store and cannot resume it). web_discover sets it on every map, and on a map the id holds the WHOLE map: the rows this answer shipped first, then the rows that were never sent. A page-by-page web_crawl sets it whenever it stopped with URLs still queued (max_pages, the whole-run character budget, or the deadline) and the id then holds exactly those never-requested URLs, in the order this run would have sent them. Pass resume_token='<plan_id>:0' to web_crawl to fetch the plan without re-sending the URL list, or resume_token='<plan_id>:<rows shipped>' to web_discover for the rest of the map itself.")
    next_resume_token: str = Field(default="", description="Set when a run stopped with URLs left (page cap, char budget or deadline): pass it back as resume_token= to continue exactly where this run stopped, without re-fetching the pages it already returned. It resolves only in the server process that issued it - the plan lives in that process's memory, so a restart or another process answers 'no plan held here'. A truncated MAP carries it too, and on a map it is the cursor into the held-back rows - web_discover(resume_token=...) returns the next rows without re-walking the site. Absent when the plan is exhausted.")

    @model_serializer(mode="wrap")
    def _map_rows_carry_only_what_differs(self, handler):
        """map 模式下一行就是那个 URL，不是一份页面报告（见 _map_row_dict）。

        行的形状住在 `_map_row_dict`，因为预算必须给线路将要发射的同一件东西定价：
        `max_total_chars` 数的是序列化后的行，而形状的第二份副本会在任何一侧改动的
        那一刻漂开。

        为什么长度上限**不**放在 `wire._wire_json`（读者可能最先去那里找）：`wire.py`
        的契约是无条件压缩 —— 它把字段在文档化的默认值上剪掉，且从不截断；而一份悄悄
        变短了的响应，正是这一整批要处理的失效。序列化器无从知道"丢掉半张 URL 地图"
        对这次调用才是对的答案；crawl 知道，因为它持有那个能让被挡下的行重新可达的
        游标。所以字节上限在造行的地方施加，而 `_wire_json` 仍然只做压缩。
        """
        data = handler(self)
        if not self.discover_only:
            return data
        data["pages"] = [_map_row_dict(p, sitemap_used=self.sitemap_used)
                         for p in self.pages]
        return data


def _map_row_dict(page: CrawlPage, *, sitemap_used: bool) -> dict:
    """地图里一行要花调用方多少字节的唯一作者。

    URL 地图是唯一一种体积由站点、而不是由调用方的请求决定的输出，而工具描述推荐的
    那条流程（先用 sitemap=true 出一次地图，再读挑出来的页面）正好撞上了它：
    docs.vllm.ai 把 1,000 条序列化成 353KB JSON，而 URL 本身只有 70KB —— 5.0 倍，
    因为每一行都把 status/depth/content/content_chars/is_truncated/next_offset/
    fetched_at/error 重复成同一个默认值、把 page_type/fetcher_used/summary 重复成
    同一个常量。一次调用 ~151k token 不是 agent 能读的地图，是上下文窗口的一次事故，
    而受罚的恰恰是照文档走的调用方。

    所以在 map 模式下，字段与默认值不同才发射；page_type 每张地图都带（顶层
    discover_only 旗子已经说明它），sitemap 模式下另外三个逐行常量也一起去掉
    （sitemap_used 已经说明它们）。正文模式不动：那些行彼此不同，而这正是全部判据。

    map 模式下 page_type 是**构造上**恒定的：_classify 把它设成 "discover_only"，
    其余每处赋值都在 `if not discover_only and html:` 分支里（`_classify_and_extract`
    里的 `page_type = kind`），而 sitemap 路径报 "sitemap"，由下面的 sitemap_used
    分支覆盖。顶层 discover_only 旗子已经说明同一件事，所以这一列在"长度由站点而非
    调用方决定"的地图上重复同一个值 —— 一行 28 字符，1,000 行的地图就是 28KB，而从
    来没有哪一行靠它被区分出来。
    """
    defaults = {name: f.default for name, f in CrawlPage.model_fields.items()}
    constant = ("content_ok", "fetcher_used", "page_type", "summary") \
        if sitemap_used else ("page_type",)
    return {k: v for k, v in page.model_dump().items()
            if k not in constant and v != defaults.get(k)}


def _map_row_cost(page: CrawlPage, *, sitemap_used: bool) -> int:
    """这一行加到线路上的字符数，连逗号一起算。

    对着真正的序列化器量过，不是估的：1,000 行、URL 76 字符的地图是 116,173 个线路
    字符（URL 本身的 1.53 倍，约 32k token），而本该约束它的预算数的是 `content_md`
    —— 那在 map 模式下恒为空 —— 于是默认 60,000 字符的预算在 500 行时被透支 1.9 倍、
    1,000 行时 9.7 倍，却没有任何地方提过一句。
    """
    return len(json.dumps(_map_row_dict(page, sitemap_used=sitemap_used),
                          ensure_ascii=False, separators=(",", ":"))) + 1


def _same_root(start_url: str) -> str:
    """同域过滤用的归一化根（scheme://netloc）。"""
    p = urlparse(start_url)
    return f"{p.scheme or 'https'}://{p.netloc}"


def normalize_url(u: str) -> str:
    """为去重把 URL 规范成同一个形状：小写 host、去掉默认端口、剥掉跟踪查询参数、
    丢掉 fragment，并把非根路径的尾斜杠折掉，使 `/docs` 与 `/docs/` 比较相等。
    真正的查询参数保留（例如分页 `?page=2`）。"""
    try:
        p = urlparse(u)
    except Exception:
        return u
    scheme = (p.scheme or "https").lower()
    host = (p.netloc or "").lower()
    # 去掉默认端口。
    if host.endswith(":80") and scheme == "http":
        host = host[:-3]
    elif host.endswith(":443") and scheme == "https":
        host = host[:-4]
    path = p.path or "/"
    # 非根路径的尾斜杠折掉（"/docs/" -> "/docs"）。
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    # 丢掉跟踪查询参数；其余保留（顺序不变）。
    if p.query:
        kept = [kv for kv in p.query.split("&")
                if kv and kv.split("=", 1)[0].lower() not in _TRACKING_PARAMS]
        query = "&".join(kept)
    else:
        query = ""
    return urlunparse((scheme, host, path, p.params, query, ""))


def _link_list_markdown(html: str, base_url: str, start_url: str, max_items: int = 200) -> str:
    """把列表/索引页渲染成结构化的 markdown 链接清单。

    用在 trafilatura 的主内容过滤器返回空的时候，因为这一页**本身就是**一份链接
    目录（Hacker News、聚合站、栏目索引页）。把同域链接交回成
    `* [anchor text](url)`，agent 拿到的是这一页真正的内容（那批条目），而不是
    一个空页面。
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
    """廉价的可见文本抽取：剥掉 script/style 块与标签，折叠空白。用作最后兜底的
    fallback，也用于页面类型信号。"""
    if not html:
        return ""
    no_ss = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", no_ss)
    return re.sub(r"\s+", " ", text).strip()


def _page_signals(html: str) -> tuple[int, int, int, bool, int]:
    """页面分类用的廉价信号：(visible_text_len, script_count,
    link_count, has_framework_root, link_text_len)。"""
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
        # 与 web_read 共用同一把第二尺（R04-016a）：整页计数把 chrome 也算在内，所以
        # 一篇完整的文章压在厚重导航之下也可能显得像塌陷。
        # 保留 <aside>：它可能装着实质文本（/longaside 那发对照）。
        prose = visible_text_chars_without_chrome(html)
        if prose <= 0 or is_extraction_collapsed(len(md), prose):
            stats["extraction_collapsed"] = True
    if kind == "list":
        items = _item_links(html, url, start_url)
        stats["list_items"] = len(items)
        stats["item_set"] = frozenset(items)
        if len(items) < LIST_ITEMS_MIN:
            stats["thin_list"] = True
    # 正文形状这一维**两个入口同一位作者**（`quality.body_substance`）。存在的理由是一条
    # 实测：靶站 `/nav` 走 `web_read` 写着 `content_ok=false`（3.4 的 `nav_only`），走
    # `web_crawl` 同一份字节却写着 `content_ok=true · 509B html` —— 与 `_row_verdict`
    # docstring 为 `extraction_collapsed` 记下的那笔账是同一族，只是新的一维。
    from dhole_mcp.quality import body_substance
    verdict, basis = body_substance(md, visible)
    if verdict:
        stats["substance"] = verdict
        stats["substance_basis"] = basis


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

    长度不设第二条尺：`web_read` 对 6/10 字的真实正文都回 `content_ok=true`（实测
    `/tiny`、`/tiny5`），crawl 这边再留一条 `len(md) >= 50` 就把同一个 URL 判成两种
    真相（靶站 `/work/1`：39 字、`substance=ok`、`content_ok=false`、理由为空）。
    "空 / 菜单 / 薄列表 / 塌陷"各有自己的作者（`not md`、`substance`、`thin_list`、
    `extraction_collapsed`），不需要第五条长度闸来替它们说话。
    """
    if kind == "js_shell" or not md:
        return False
    if measured.get("extraction_collapsed") or measured.get("thin_list"):
        return False
    if measured.get("substance") in ("nav_only", "no_words"):
        # 与读取侧同判：一行全是菜单碎片时，够 50 个字符也不叫内容（3.4 那一维）。
        return False
    return True


def _measure_note(stats: dict) -> str:
    """把量到的东西写成一句话。闸翻了之后这句话**更要说**：它是被拒的那一行的理由，
    也是它唯一的解药（塌陷 → `extraction_type='html'`；薄列表 → 别把这一行当索引）。

    第一笔（0b4cb15）里这一句只在 `content_ok=true` 时说话，理由是"一行已经写着 false
    时再补一句是噪音"。那个前提在闸翻掉之后就不成立了：现在被拒的行正是**由这两个发现**
    拒的，调用方读到的必须包括为什么 —— 一个没有理由的 false 与本批要消灭的那些假 true
    是同一种罪。
    """
    if stats.get("recovered_by_plain_converter"):
        # 接管了就要说出口 —— 静默换正文形态与"看起来成功、其实没生效"是同一种罪。
        # 这一句也是调用方的量具提醒：这一行来自不判断主内容的转换器，带页面 chrome。
        _md_got = stats.get("markdown_extracted_chars")
        return (f"extraction collapsed on the markdown pass ({_md_got:,} of "
                f"{stats['visible_chars']:,} visible characters) - recovered in place "
                "from the same bytes by the plain converter (markdown, page chrome "
                "included) rather than the main-content detector")
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
    if stats.get("substance") in ("nav_only", "no_words"):
        # 翻闸之后这句更要说：它是被拒那一行的理由，也是它唯一的解药。
        return (f"no prose in this row: {stats.get('substance_basis', '')} - do not cite "
                "this row as content; web_read the URL with force_fetcher='stealthy' "
                "(which renders) or follow the links it carries")
    return ""


def _classify_and_extract(html: str, url: str, start_url: str, focus: Optional[str],
                          max_chars: int, stats: Optional[dict] = None
                          ) -> tuple[str, str, bool, bool]:
    """按内容自适应的抽取。返回 (markdown, page_type, content_ok, cut)。

    page_type 是 article / list / js_shell / fallback 之一。页面内容超过 max_chars
    且本函数只交回前 max_chars 个字符时，cut 为 True。`content_ok` 来自
    `_row_verdict` —— 没渲染出来的 js_shell、空页、页面自己的标记说明我们丢掉了的
    抽取结果，或者背后没有一组条目撑着的 "list"。

    `stats` 是这一行被那两把尺量过之后的**证据**出口（照 `focus_content(..., stats=)`
    的先例）：数字与发现都从那里交给调用方，判决本身在返回值里。
    """
    from dhole_mcp.trafilatura_extractor import (
        extract_content_from_html, plain_markdown_from_html)
    from dhole_mcp.focus import focus_content

    md = ""
    try:
        md = extract_content_from_html(html, url, "markdown") or ""
    except Exception:
        md = ""

    text_len, script_count, link_count, has_fw, link_text_len = _page_signals(html)

    # 1) 列表 / 索引页：以链接为主（可见文字大半是锚文本）。优先于 'article'，因为
    #    trafilatura 对 HN/聚合站会把链接文字当 'content' 交回来，可这一页**就是**
    #    一份列表。把同域链接渲染成结构化的 `* [title](url)` 清单，agent 才拿到这一页
    #    真正的内容（那批条目）。
    link_density = (link_text_len / text_len) if text_len else (1.0 if link_count else 0.0)

    # 与 web_read 同一刀（2026-10-23）：字在 DOM 里而 markdown 只拿回一小撮时，用**同一份
    # html** 再抽一遍 html 那一路（它保留版面，通常拿得更多）。只修读取侧就是新的不一致：
    # 同一份字节，read 说"补回来了"、crawl 这一行还说"塌陷"，两个入口各说各话正是这一族
    # 旧账的形状。列表页不参与 —— 它的 md 是渲染出来的链接清单，按定义丢掉非链接的字，
    # 拿整页可见字去比是拿错的量具（见 `_measure_row`）。
    if md and link_density < 0.5:
        _visible_all = visible_text_chars(html)
        _prose_all = visible_text_chars_without_chrome(html)
        if (is_extraction_collapsed(len(md), _visible_all)
                and (_prose_all <= 0 or is_extraction_collapsed(len(md), _prose_all))):
            # 与读取侧同一刀（2026-10-23）：坍缩时用**同一份 html** 走不判断主内容的
            # 纯转换器（`plain_markdown_from_html`），读/爬两个入口交同一种正文。
            # 这一支只看非列表页（密度 < 0.5）：列表页由下面的 list 分支渲染成清单，
            # 那是另一种形状，不该先被 markdownify 洗一遍。
            # 第一版这里交的是**原始标记**（`_reclaimed = html`），把 35k 标签当正文；
            # 也试过 `extract_content_from_html(html, url, "html")` —— 那个函数对 "html"
            # 按设计回 None，补救从未生效（实测 /longaside：读 7,326 / 爬 2,577）。
            _plain = plain_markdown_from_html(html)
            if len(_plain) > len(md) * 1.5:
                _md_before = len(md)
                md = _plain
                if stats is not None:
                    stats["recovered_by_plain_converter"] = True
                    stats["markdown_extracted_chars"] = _md_before
    if link_count >= 10 and link_density >= 0.5:
        list_md = _link_list_markdown(html, url, start_url)
        if list_md:
            md = list_md
            kind = "list"
        else:
            kind = "fallback"
    # 2) 文章 / 文档：trafilatura 找到了真正的主内容（正文，不是链接）。
    elif md and len(md) >= 200:
        if focus:
            try:
                from dhole_mcp.reranker import get_reranker
                rer = get_reranker() if _neural_for_this_call() else None
                md = focus_content(md, focus, reranker=rer)
            except Exception:
                pass
        kind = "article"
    # 3) 列表页：链接更少/更松，但 trafilatura 仍然给空。
    elif link_count >= 10 and (len(md) < 200):
        list_md = _link_list_markdown(html, url, start_url)
        if list_md:
            md = list_md
            kind = "list"
        else:
            kind = "fallback"
    # 4) JS 壳：可见文字少 + 脚本多 + 框架根节点，而 trafilatura 什么也没拿到。
    #    抓取那一步已经试过 stealthy 渲染；这里仍是壳，说明页面没渲染出来 -> 诚实失败。
    elif text_len < 400 and (script_count >= 5 or has_fw) and not md:
        kind = "js_shell"
        md = ""
    # 5) Fallback：清洗后的可见文本（有胜于无）。
    else:
        md = md or _visible_text(html)
        kind = "fallback"

    measured: dict = {}
    _measure_row(measured, html, md, kind, url, start_url)
    if stats is not None:
        stats.update(measured)
    content_ok = _row_verdict(kind, md, measured)

    # 截到每页预算。`cut` 是调用方事后无法复原的那个答案：md 一旦被切到
    # max_chars，`len(md) > max_chars` 按构造就是假的，所以拿交回的字符串去测
    # 永远检测不出它刚刚做过的那次截断。这就是为什么每个爬取页都报
    # is_truncated=false，而它自己的 summary 却写着 "truncated"。
    cut = False
    if md and len(md) > max_chars:
        md = md[:max_chars]
        cut = True
    return md, kind, content_ok, cut


# ─── 路径作用域（path_include / path_exclude） ─────────────────────────────────
#
# 重写之前量过：直接拿调用方的字符串做 `path.startswith(p)`。四种失效方式，全是静默的：
#
#   path_include=["docs"]     -> 0 条链接被保留。URL 路径以 "/" 开头，所以最自然的
#                                写法把整次爬取都挡在作用域之外，调用拿回空结果。
#   path_include=["/docs/*"]  -> 0 条链接被保留。glob 不是 startswith 字符串。
#   path_include=["/docs"]    -> 保住了 "/docs-old/legacy" 与 "/docsomething"。
#                                共享前缀不是子树。
#   path_exclude=["docs"]     -> 什么也没排除，于是调用方以为自己给一次爬取划了范围，
#                                其实没有。
#
# 现在的契约是：一个模式命名一棵路径**子树**。它匹配这一节本身以及段边界之下的一切，
# 绝不匹配只是名字开头相同的兄弟路径。
#
#   "docs"  "/docs"  "/docs/"  "/docs/*"   -> /docs 子树
#   "/"     "*"      "/*"                   -> 一切
#   "/api/*/v1"                             -> 拒绝，见下
#
# 结尾的 "/*" 被接受，因为调用方想说"这底下的一切"时就是这么写的；它被剥掉而不是
# 当成 glob 兑现，因为成文的契约是前缀作用域，不是一门模式语言。之后仍然带通配符的
# 一律**拒绝**，而不是悄悄匹配不到：一次爬取因为调用方看不见的原因交回 0 页，正是这次
# 重写要消灭的那种失效。

_WILDCARD_CHARS = ("*", "?", "[")


def normalize_path_pattern(pattern) -> str:
    """把一个 include/exclude 模式归一化成路径前缀。

    非字符串、空模式、不支持的 glob 都抛 ValueError。
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
        p = p[:-2]  # "/docs/*" 就是这棵子树，调用方就是这么写的
    p = p.rstrip("/")
    if not p:
        return "/"
    if not p.startswith("/"):
        p = "/" + p  # "docs" 与 "/docs" 是同一棵子树
    bad = [c for c in _WILDCARD_CHARS if c in p]
    if bad:
        raise ValueError(
            f"path pattern {pattern!r} contains {''.join(bad)!r}, which is not supported. "
            f"path_include/path_exclude name a path subtree (e.g. '/docs', or '/docs/*' "
            f"for everything under it); the only wildcard form accepted is a trailing '/*'."
        )
    return p


def normalize_path_patterns(patterns) -> list[str]:
    """归一化调用方的 include/exclude 列表。某一项有问题就抛 ValueError。

    裸字符串按调用方本意当成**单个**模式 —— 逐字符迭代它会让每条路径都匹配，这正是
    从前 ``path_exclude="/what/"`` 把整次爬取过滤掉的方式。
    """
    if not patterns:
        return []
    if isinstance(patterns, str):
        patterns = [patterns]
    return [normalize_path_pattern(p) for p in patterns]


def _normalize_url_path(path: str) -> str:
    """URL 路径 -> 可比较的形式：带前导斜杠、无尾斜杠。"""
    p = (path or "/").strip()
    if not p.startswith("/"):
        p = "/" + p
    p = p.rstrip("/")
    return p or "/"


def _path_in_scope(path: str, pattern: str) -> bool:
    """`path` 就是该模式的那棵子树（在段边界上）时返回 True。

    两个参数都必须已经归一化（见 _normalize_url_path / normalize_path_pattern）。
    """
    if pattern == "/":
        return True
    return path == pattern or path.startswith(pattern + "/")


def _scope_hint(pending_urls: list) -> str:
    """停早时该不该推荐 path_include，以及推荐哪一棵子树（评审 R3刀3 · R02-008 补全）。

    样板过去无脑写 `/docs`：站点没有 /docs 时那是一条修不了任何东西的建议，还顺手
    教读者去猜一颗并不存在的目录。判据取自**没抓到的那批 URL** 的第一段路径 —— 一段
    至少占 3 条、且过半，"只跑这一棵"才真把预算省下来；否则空串，调用方读到的是"抬
    预算"这一条老实建议。

    过半那一格还漏放了一种**恰好全中**：候选装着整条队列时（`n == len(pending_urls)`），
    推荐它等于推荐"保持现状"。台账现场是 `path_include=['/work']` 的一发早停，回执仍说
    "scope it with path_include=['/work'] — that subtree holds **111 of the 111** URLs
    still queued"：数字没错，行动性是零，而调用方这一发往往刚点过同一颗参数。这种句子不许
    再当动作推荐；这里只把事实说出来（整条队列都在这一段里），把"抬预算"那条真能变的留着。

    两处装配点（字符预算 / 页数与时间）从前各起各的头：一个把"scope"后面接"the run"，
    一个接"it" —— 同一句推荐两个连接词，也是同一把尺子该并的账。现在连接词跟着
    作者走，装配点只加一个逗号。

    交回的是**整条后半句**，连它自己的连接词（"or scope it with …"）一起 —— 两种话读法
    不同：推荐要接在"or scope it with"后面，而那条事实不能接（接上去就成了
    "or scope it with every URL still queued is…"，一处装配把两种话都说坏了）。作者在这里，
    装配点就不许再替它起头。
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
    if n == len(pending_urls):
        return (f"and note that every URL still queued is under {seg}, so a path "
                "subtree cannot select a subset here")
    if n < 3 or n * 2 < len(pending_urls):
        return ""
    return (f"or scope it with path_include=['{seg}'] - that subtree holds {n} of the "
            f"{len(pending_urls)} URLs still queued (a path subtree, not a string "
            "prefix)")


def _shortfall_sentence(shortfall: int, dropped_after_answering: int) -> str:
    """少了的那几颗只有一句话的作者：付过请求的不许说成没发过（评审 R02-038）。

    `shortfall` 是 `pages_discovered - pages_crawled` —— 一个把两种"少"合在一起的代数式。
    两种少的解药不同，而调用方照着这句话决定抬哪一颗预算：

      * **已经发过、答案也回来了**，只是回到循环时预算已用完，行被丢在批次里
        （`dropped_after_answering`）—— 抬预算救不回这些字节，它们已经付过钱；
      * **队列里从来没轮到的**（其余那部分）—— 抬预算才真的多发几颗。

    一版把两者合并成一句「N 颗 URL 发现了却没 fetch」，等于把第一种说成第二种：实测一次
    `deadline_ms=1000` 的爬取里，靶站在 0.80s 和 1.98s 各答过两颗被那样描述成"没发过"的
    URL（服务方自己的日志为证）。句子按两种各给一个数，没丢过任何一行时只说"never sent"，
    不许反过来把没发的吹成付过钱。
    """
    if not dropped_after_answering:
        return f"{shortfall:,} URL(s) were discovered but never sent."
    never_sent = max(shortfall - dropped_after_answering, 0)
    return (f"{dropped_after_answering} URL(s) of those had already been requested "
            f"(their answers arrived after the run stopped, so those rows were "
            f"dropped rather than returned); {never_sent} were never sent.")


def _resume_sentence(token: str, held: int, kept: int, url: str) -> str:
    """早停留下的那串没发出去的队列怎么**按回去**：坐标、它指着什么、它省下什么。

    `_shortfall_sentence` 早就数过"几条 URL 发现了却没发"，而这一发留下的出路只有
    "把帽抬大重跑" —— 重跑那一发抓回来的是**同样那几页**（靶站实测：`max_pages=3` 之后
    照旧出路跑 `max_pages=6`，交回的六行里前三行逐字就是上一发已经交回的那三行）。
    计划的那套设施今天就在（`plan_store`/`plan_token`/`plan_parse`，服务器侧 resume 那一支
    在读），缺的只是把这一串交给它。评审 R02-009。

    `kept` 是这一发已经交回的页数 —— 那句话要说得出"不重付"具体指哪一段，否则它只是
    一句形容词。地图那一停不走这句（见调用处那段注释：那颗 `plan_id` 另有作者、另有含义），
    所以这里只有正文爬取一种形状。

    `url` 必须在句里：web_crawl 的门要求 url（resume_token 只认队列），自荐的调用
    形状照抄就得过门 —— 2026-10-25 P02 照抄不带 url 的那一句撞了
    `Missing required argument(s): ['url']`（R01-003）。
    """
    return (f"Or pass web_crawl(url='{url}', resume_token='{token}'): it sends exactly "
            f"those {held} queued URL(s), without re-fetching the {kept} page(s) this "
            "run already returned.")


def path_allowed(path: str, path_include=None, path_exclude=None) -> bool:
    """BFS 链接发现与 sitemap 地图共同的唯一判定点。

    模式可以是原始的（在这里归一化）或已归一化的；坏模式抛 ValueError。只留一份实现
    正是要点：两个调用点从前各自漂开，把同一个缺陷带了两遍。
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
    stats: Optional[dict] = None,
) -> list[tuple[str, str]]:
    """从 HTML 中抽出同域的（锚 URL, 锚文本）绝对地址对。

    相对 URL 按 base_url 解析；只保留落在起始 URL netloc 上的链接；丢掉
    fragment/资源/非 http scheme；应用 path include/exclude 的子树作用域（见
    path_allowed）；并按**归一化后的** URL 去重（于是 `/docs` 与 `/docs/` 折成一条）。
    保持原序。

    ``stats``（可选，原地修改）装着两个让作用域判决可上报的计数：`links_seen`
    （过滤器被告知过的同域互异候选数）与 `links_rejected_by_scope`。一次只交回一页的
    爬取可能意味着"这一页没有链接"、"作用域把它们吃光了"、"作用域留下了它们、是别的
    东西停下了这一趟"，或"链接发现根本没跑" —— 这几种在响应里读起来一模一样，解药却
    各不相同，所以说明原因的那句话必须由计数选出，而不是看某个过滤参数恰好在不在。
    """
    root_netloc = urlparse(start_url).netloc
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    if stats is not None:
        stats.setdefault("links_seen", 0)
        stats.setdefault("links_rejected_by_scope", 0)
    if not html:
        return out
    # 每页归一化一次，而不是逐链接归一化。
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
            continue  # 外链 -> 丢弃（same_domain_only 默认）
        path = _normalize_url_path(parsed.path or "/")
        if _ASSET_RE.search(path):
            continue
        # 去重发生在作用域判定**之前**，好让计数说的是互异候选：一根导航条把同一条
        # 域外链接重复 40 次只算一次被拒的候选，不是 40 次，而"其中 40 条被作用域挡掉"
        # 是读者用不上的数字。交回集合的成员不变 —— 这些过滤器是 AND 关系，顺序既不能
        # 增加也不能减少一条 URL。
        clean = normalize_url(absu)
        if clean in seen:
            continue
        seen.add(clean)
        if stats is not None:
            stats["links_seen"] += 1
        if include and not any(_path_in_scope(path, pat) for pat in include):
            if stats is not None:
                stats["links_rejected_by_scope"] += 1
            continue
        if exclude and any(_path_in_scope(path, pat) for pat in exclude):
            if stats is not None:
                stats["links_rejected_by_scope"] += 1
            continue
        text = _TAG_RE.sub(" ", m.group(2) or "").strip()
        out.append((absu, text))  # 原始绝对 URL（用于抓取）；按 `clean` 去重
    return out


@functools.lru_cache(maxsize=32)
def _query_terms(query: str) -> frozenset[str]:
    """抓取排序用的词面依据 —— 切词由 `focus._tokens` 回答，这里只加一层缓存。

    缓存的理由是热路形状：`score_link` 是逐链接调用的（页面里每条 `<a>` 一次，站点地图
    入口每条最多三次），而一次抓取的 `focus` 是个常数。实测单次切词 8-22 µs，40 页、
    每页上百条链接不缓存就白烧几十毫秒。
    返回 frozenset：`lru_cache` 交回的是**同一只**集合，调用方可变就等于这一次抓取改写
    下一次抓取的词表。
    """
    return frozenset(_tokenize(query))


def score_link(url: str, text: str, focus: str) -> float:
    """发现到的 URL 的优先级分数。越高越先爬。

    混合三项：
      * focus 相关度（锚文本重合 * 2 + URL 路径词段重合），
      * 内容可能性（路径词段：docs/guide/api 抬高，login/submit/cart 压低），
      * 浅层偏好由调用方通过一个深度项施加。
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


# 一份站点内容在地图里的坐标：开头那一段是语言码时把它摘掉。
# 只认**开头**一段的两字母（可带地区后缀：/bn/、/zh-cn/、/pt_BR/），因为那是 i18n
# 路由的常见形状。`/api/v1/x` 的首段不是两字母，不会被误折；`/id/1234` 这种"用两字母
# 当路径名"的形状确实会被折错 —— 所以判定不是"看起来像语言码"就够了，还要同一 key 下
# 真的存在 ≥ MIRROR_VARIANTS_KEPT+1 个变体（见 _order_map_entries），单个孤立段一律不动。
_LOCALE_SEGMENT_RE = re.compile(r"^/([a-z]{2}(?:[-_][A-Za-z]{2,4})?)(?=/|$)")


def _fold_locale_path(path: str) -> tuple[str, str]:
    """(去掉开头语言段后的路径, 摘掉的那一段或 "") —— 同一份内容的两个坐标。"""
    m = _LOCALE_SEGMENT_RE.match(path or "/")
    if not m:
        return path or "/", ""
    rest = (path or "/")[len(m.group(0)):]
    return (rest or "/"), m.group(1)


def _lastmod_epoch(value: str) -> float:
    """Sitemap 的 ``<lastmod>`` 排序键；缺省/不可解析 = 0（排在已日期行之后）。

    只读取站点自己写下的日期：W3C 常见形状是 ``2026-09-27``、
    ``2026-09-27T08:00:00Z``、带时区偏移的 ISO。缺失与坏值都回到 0，而不是
    "现在" —— 一份没写日期的 sitemap 不该被当成刚更新过。
    """
    raw = (value or "").strip()
    if not raw:
        return 0.0
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        # Windows 上 timestamp() 对公元 1 年这类日期抛 OSError；坏日期与缺失同价。
        return 0.0


def _order_map_entries(entries: list, query: str = "") -> tuple[list, dict]:
    """地图里哪些行先发，以及这次重排付了什么代价。唯一作者（C4）。

    站点自己的 sitemap 是**文档序**的索引，而文档序表达的是"站点先列了什么"，不是
    "哪一页对这次的问题有用"。现场：一份 1,011 条的 sitemap，前 1,000 条是 20 篇文档
    的 50 种语言/版本副本，默认预算全花在副本上 —— 交回 634 行、覆盖 20 篇内容。
    回执读起来像"这就是这个站"。

    只做**重排**，不删行（重排可逆，删行不可逆，而"这一页其实存在"是不可收回的谎）：

    1. `score_link` 稳定排序 —— 查询词在场时它是相关度，不在场时就是那套内容先验
       （docs/api 抬、login/cart 压）。与 BFS 的 `push()` 同一把尺；地图这条路上从前
       **从不咨询它**，`score_link` 只在 `push()` 里被叫过一次。
    2. 同一页的第 MIRROR_VARIANTS_KEPT+1 个语言副本排到整张地图的最后。它们仍然是地图
       的行：`pages_discovered` 照数、游标照得到、计划里照存。

    分组要**两趟**：先按去掉语言段的路径归组，再在组内定代表。单趟"先到先得"会让
    `/bn/a` 抢下代表位而把原文 `/a` 折到后面 —— 那与本缺陷同形，不是修复。代表由
    `score_link` 判（摘掉语言段的那条路径少一个噪声 token，分更高），同分则文档序在前。
    """
    groups: dict[str, list[tuple[int, float, bool]]] = {}
    folded_of: dict[int, str] = {}
    for i, su in enumerate(entries):
        try:
            path = urlparse(su.url).path or "/"
        except Exception:
            path = su.url
        folded, locale = _fold_locale_path(path)
        folded_of[i] = folded
        # 代表的第一判据是"这条没有语言段"，也就是原文自己；`locale` 是摘下来的那段
        # 名字，直接当排序键会变成按语言字母序选代表（/ja/ 因此被 /de/ 挤掉）。
        groups.setdefault(folded, []).append(
            (i, score_link(su.url, "", query), bool(locale),
             _lastmod_epoch(getattr(su, "lastmod", ""))))
    demote: set[int] = set()
    for members in groups.values():
        if len(members) <= MIRROR_VARIANTS_KEPT:
            continue  # 不超过三个变体：一行都不许动（一个真用 /ja/ 的站就长这样）
        # 同分时新日期先当代表：同一份文档的语言副本里，最近改过的那一版更可能
        # 是站点仍在维护的原文侧；日期全缺的站回到纯文档序（epoch=0）。
        keep = sorted(members, key=lambda m: (-m[1], m[2], -m[3], m[0]))
        demote.update(i for (i, _s, _h, _lm) in keep[MIRROR_VARIANTS_KEPT:])

    front = sorted(((score_link(su.url, "", query),
                     _lastmod_epoch(getattr(su, "lastmod", "")), i, su)
                    for i, su in enumerate(entries) if i not in demote),
                   key=lambda t: (-t[0], -t[1], t[2]))
    back = sorted(((score_link(entries[i].url, "", query),
                    _lastmod_epoch(getattr(entries[i], "lastmod", "")), i, entries[i])
                   for i in demote), key=lambda t: (-t[0], -t[1], t[2]))
    shipping = [su for _sc, _lm, _i, su in front] + [su for _sc, _lm, _i, su in back]
    before = [su.url for su in entries]
    after = [su.url for _sc, _lm, _i, su in front]
    moved = sum(1 for b, a in zip(before, after) if b != a)
    return shipping, {"mirrors_demoted": len(demote),
                      "mirror_pages": len({folded_of[i] for i in demote}),
                      "moved": moved, "paths_total": len(set(folded_of.values()))}


def _error_status(resp) -> int:
    """诚实的 status：网络错误（没有 HTTP 响应）为 -1，其余为真实的 HTTP 状态码。
    让下游逻辑能把 'server returned 500' 与 'connection died' 区分开。"""
    if resp is None:
        return -1
    status = getattr(resp, "status", 0) or 0
    if status == 0 and getattr(resp, "error", ""):
        return -1
    return status


def _sitemap_passes_filters(path: str, path_include: Optional[list[str]],
                            path_exclude: Optional[list[str]]) -> bool:
    """留作 sitemap 地图的具名调用点；判定本身住在 path_allowed，好让两条作用域
    路径不会再各自漂开。"""
    return path_allowed(path, path_include, path_exclude)


#: 请求被时钟砍掉时，这一族抛的类型名。判据用**类型名**而不是消息文本（"格子不许由措辞
#: 决定"是本仓库点过的形状），也不能只认内建那颗：primp 抛的是它自己一家的 `TimeoutError`
#: （挂在 RequestError/PrimpError 下面，与 `builtins.TimeoutError` 没有继承关系），而
#: urllib/socket 抛的才是内建那颗。两家的 MRO 里都只出现这些名字。
_CLOCK_CLASS_NAMES = frozenset({
    "TimeoutError", "ConnectTimeoutError", "ReadTimeoutError",
    "ConnectionTimeout", "ConnectTimeout", "ReadTimeout",
})


def _is_the_clock(exc: BaseException) -> bool:
    """这发请求是没答，还是答了而站点本来就空 —— 两句话，别混着说。"""
    return any(cls.__name__ in _CLOCK_CLASS_NAMES for cls in type(exc).__mro__)


async def _sitemap_map(url: str, path_include: Optional[list[str]],
                       path_exclude: Optional[list[str]], *,
                       max_pages: int, deadline_t: float,
                       diag: Optional[dict] = None,
                       search: Optional[str] = None,
                       max_total_chars: int = 0) -> Optional[CrawlResponseModel]:
    """尝试经 sitemap.xml 把站点摸成一张图。成功（找到并解析了 sitemap）时返回
    CrawlResponseModel；**没有**任何 sitemap 可达时返回 None，好让调用方退回 BFS
    （'auto'）。找到了 sitemap 却没产出行的情形**不是** None —— 它交回一张明说这件事的
    空地图，因为把这种情况报成 "not found" 是另一个（且为假的）答案。同域 + 路径过滤
    在上限**之前**施加，所以 max(1000, max_pages*10) 花在调用方要的那些页上
    （见 discover_sitemap 的 `accept`）。

    `search` 加入同一组过滤器（体验修复刀3-C2）。它从前只在 BFS 路径的末尾生效，而本
    函数交回的是整份响应，于是 `sitemap=true, search="pricing"` 原样交回没过滤的地图
    且一个字不说 —— 一颗可证明什么都没做的参数，而这是 `params.py` 唯一不许可的事。
    把它推到上限之前也是它**生效**的前提：一个靠近 4,000 条 sitemap 末尾的关键词，否则
    会被上限切掉，然后被读成"这个站点上没有匹配"。

    `max_total_chars` 在这里计价出于同一理由（体验修复刀3-C3）。整轮预算从前累加的是
    `content_md`，而它对每个 map 行都是空的，于是地图成了唯一一种没有任何体积闸作用的
    输出 —— 实测默认 60,000 字符被一张 1,000 行的地图透支 9.7 倍，而
    `truncated_by_total_chars` 从头到尾没变过 true。被预算挡下的行不会被丢弃：它们进
    `held`，计划持有它们，`next_resume_token` 是指回那里的游标。

    这里每个请求跑的时钟从前是两处写死的 15s（primp + urllib 兜底）。在一个 /sitemap.xml
    要 20s 才答的站点上实测：`timeout=30000` 与 `timeout=120000` 都交回同样的 30.0s 墙钟
    （15s primp，再 15s urllib）、同样的空地图，以及一句责怪站点的话 —— 这次调用越过
    了自己的 25s deadline，同时花着调用方明确调大过的预算。现在一个请求拿到的是这次
    调用**还剩**的时间，而跑光了时间的抓取记为 `clock_cut`，报告才能说清是谁的数字决定
    了它。这里刻意没有 30s 的逐次尝试上限（`bulk_get` 那道存在的理由是把四次尝试塞进
    一次调用）；这条路径对每个 URL 每个客户端只尝试一次，约束它的是 deadline。
    """
    from dhole_mcp.sitemap import discover_sitemap

    _clock: dict = {"cut": [], "erred": []}

    def _left_s() -> float:
        """这一发还剩下多少秒 —— 时钟归调用方那句预算，不归一个写死的 15。"""
        return deadline_t - time()

    def _note(u: str, exc: BaseException) -> None:
        (_clock["cut"] if _is_the_clock(exc) else _clock["erred"]).append(u)

    def _make_http_get():
        from dhole_mcp.search_proxy import configured_proxy
        _sitemap_proxy = configured_proxy()[0]
        _cap = max(1.0, _left_s())
        try:
            import primp  # type: ignore
            client = primp.Client(proxy=_sitemap_proxy, timeout=_cap, impersonate="random",
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
                except Exception as e:
                    _note(u, e)
                    # 退到 urllib —— 但只在还有时间问的时候
            if _left_s() < 1.0:
                _clock["cut"].append(u)
                return None
            # stdlib 兜底（有些 host 拒绝 primp 的指纹，却接受 urllib）
            try:
                req = _urllib_req.Request(u, headers={"User-Agent": "Dhole-Sitemap/8.0"})
                with _urllib_req.urlopen(req, timeout=max(1.0, _left_s())) as resp:  # noqa: S310
                    body = resp.read()
                    if body:
                        return (int(resp.status), body)
            except Exception as e:
                _note(u, e)
                return None
            return None
        return _get

    t0 = time()
    root_netloc = urlparse(url).netloc
    # 地图不是爬取：它的价值是广度，所以不像正文那样跟着 max_pages 缩。
    # SITEMAP_MAP_FLOOR 就是那个下限，越过它的唯一办法是调用方按比例要得更多。
    cap = min(5000, max(SITEMAP_MAP_FLOOR, max_pages * 10))

    # `search` 在这里只兑现 URL 那一半：一条 sitemap 条目带得出来的字段就是 loc（还有
    # lastmod），**没有标题可比**。兑现一半并说出少了哪一半，是 params.py 允许的两种
    # 合法说法之一；另一种是具名拒绝，第三种"参数静默不做任何事"没有位置。
    _terms = [t for t in (search or "").lower().split()]
    # accept 对每个去重后的 URL 恰好跑一次（sitemap.py 先记 seen 再判，所以重复条目
    # 不会把这两个数撑大），于是"筛掉多少"第一次成为一个可报告的事实而不是断言。
    _counts = {"dropped_by_search": 0, "kept_by_search": 0}

    def _eligible(raw: str) -> bool:
        """同域 + 路径 + search 过滤器，对 sitemap 原始条目直接判定。

        推进到遍历内部，好让上限数的是合格 URL：过滤已经截过上限的列表，意味着在
        docs 从 sitemap 第 2000 条才开始的站点上 `path_include=["/docs/"]` 返回空 ——
        而 `_sitemap_map` 随后返回 None，调用于是静默退回 BFS，毫无迹象表明作用域
        被施加到了列表的错误一头。
        """
        try:
            parsed = urlparse(raw)
        except Exception:
            return False
        if parsed.scheme not in ("http", "https") or parsed.netloc != root_netloc:
            return False  # sitemap 可以列出别的 host；只保留同域
        if not _sitemap_passes_filters(parsed.path or "/", path_include, path_exclude):
            return False
        if _terms and not any(t in raw.lower() for t in _terms):
            _counts["dropped_by_search"] += 1
            return False
        if _terms:
            _counts["kept_by_search"] += 1
        return True

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
            diag["clock_cut"] = list(_clock["cut"])
            diag["fetch_errors"] = list(_clock["erred"])
        return None  # 没有一份可达：调用方那句"这里没有 sitemap"是真的

    # 地图里谁先走：文档序说的是"站点先列了谁"，不是"哪一页对这次有用"（C4）。
    # `_order_map_entries` 是这条判断的唯一作者（`evals/sitemap_map_baseline.py` 量的
    # 也是它），所以"前后对比"与"这次交了什么序"共用一把尺。
    map_order, _order_stats = _order_map_entries(result.urls, query=search or "")
    listed = len(result.urls)

    # 预算数的是**序列化后的行**，不是 `content_md`。map 行的 content_md 恒为空，
    # 所以这一族从前在整轮字符帽的账本上是 0：默认的 60,000 字符预算在 500 行时
    # 已被透支 1.9 倍、在 1,000 行时 9.7 倍，而 `truncated_by_total_chars` 一路是
    # False —— 一道对某类输出根本不复存在的闸，比没有闸更坏，因为它让调用方以为
    # 自己设过上限。行的长度只在这里才量得准：`_map_row_dict` 是 wire 的那一行
    # 形状的唯一作者，预算与线路共用同一把尺（见 _wire_json 为何不该管长度）。
    pages: list[CrawlPage] = []
    held: list[str] = []          # 在地图里、在这一页之外的行（留给游标）
    seen: set[str] = set()
    row_chars = 0
    stopped_by = ""               # "budget" | "deadline" | ""
    for su in map_order:
        norm = normalize_url(su.url)
        if norm in seen:
            continue
        seen.add(norm)
        if stopped_by:
            # 一旦停下，后面的条目仍然数得着：地图的大小要说得出，游标才有个指向。
            held.append(norm)
            continue
        if time() > deadline_t:
            stopped_by = "deadline"
            held.append(norm)
            continue
        row = CrawlPage(
            url=norm, depth=0, status=0, content_ok=True, fetcher_used="sitemap",
            page_type="sitemap", content=[], content_chars=0, lastmod=su.lastmod,
            summary="sitemap entry",
        )
        cost = _map_row_cost(row, sitemap_used=True)
        if max_total_chars and pages and row_chars + cost > max_total_chars:
            stopped_by = "budget"
            held.append(norm)
            continue
        row_chars += cost
        pages.append(row)
        if len(pages) >= cap:
            break

    listed = len(result.urls)
    # 地图的大小与这一页交出的行数分开数：`map_total` 是本次走到的地图（在手上、
    # 含被留下的），`ok` 是响应里的行数。两个数都出口，缺多少才是可核对的算术。
    map_total = len(pages) + len(held)
    hold_plan_id = _hold_map_tail(pages, held) if held else ""
    if not pages:
        # sitemap **确实**找到了、也解析了，只是没产出任何行。这里返回 None 会让
        # web_crawl 报 "no sitemap.xml found ... /sitemap.xml returned nothing" —— 假的，
        # 而且它把 agent 支去 BFS 找一个 dhole 已经读完 sitemap 的站点。常见原因是
        # 某个作用域过滤器匹配不到东西（见 `_eligible`），而真正有用的答案是
        # "the sitemap lists N URLs, none in the section you asked for"。
        scoped = [f"{k}={v}" for k, v in (("path_include", path_include),
                                          ("path_exclude", path_exclude)) if v]
        scope_desc = " or match ".join(scoped) if scoped else "on this host"
        if listed:
            # Eligible, but none of them became a row. 有了计数就不必再并列两个
            # 猜测：`held` 数的就是"条目在手上、时间不在了"的那些，剩下的才是
            # 归一化后的重复。合在一起说，调用方分不清该抬 deadline 还是该查站点。
            if held:
                why = (f"its {listed} URL(s) did not become rows: the deadline came "
                       f"before the first one ({len(held)} of them were in hand)")
                fix = ("Re-run with a larger budget - `timeout=` is the clock a "
                       "web_discover call runs on, `deadline_ms` the one web_crawl runs on "
                       "- or without sitemap=true to discover by links instead.")
            else:
                why = (f"its {listed} URL(s) did not become rows (a repeat after "
                       f"normalization)")
                # 这一支不怪时钟：条目全在手上、也走完了循环，只是每一条归一化之后都是
                # 前一条。叫调用方"抬预算"是把他支去拧一颗与原因无关的旋钮（同一类错在
                # C2 的 `search=` 上刚认过账 —— 出路要点名真正管事的那颗参数）。
                fix = ("Every entry the site listed normalizes to a URL already in the "
                       "map, so a larger budget changes nothing here. Run without "
                       "sitemap=true to discover by links instead.")
        elif result.filtered_out:
            # 逐因带数（C1 在同一句话上认过账）：`filtered_out` 现在同时装着"被作用域
            # 挡掉"和"被 search 挡掉"两种，把它们合成一句"都不在你要的分区里"就会把
            # 调用方支去放宽他根本不需要放宽的那个旋钮。
            _by_scope = result.filtered_out - _counts["dropped_by_search"]
            bits = []
            if _by_scope:
                bits.append(f"{_by_scope} fell outside the scope ({scope_desc})")
            if _counts["dropped_by_search"]:
                bits.append(f"{_counts['dropped_by_search']} carried none of the terms in "
                            f"search={search!r} - it compares the URL only, and a sitemap "
                            f"entry has no title to compare")
            why = (f"none of its {result.filtered_out} URL(s) became rows: "
                   + "; ".join(bits))
            fix = ("Widen path_include/path_exclude, drop search=, or use "
                   "discover_only=false / web_search when the word you have is a page's "
                   "TITLE rather than part of its URL - a sitemap cannot match on that.")
        else:
            why = "it lists no URLs"
            fix = "Run without sitemap=true to discover by links instead."
        if hold_plan_id:
            # 一个字节都没交出去，但那些 URL 是在手上的 —— 游标对空地图同样有效，
            # 丢掉它们就等于把已经付过的一次 sitemap 抓取再要一遍。指的门是
            # web_discover：`discover_only` 不在 web_crawl 的 wire 面上（17.0 §4 把它
            # 变成了工具），所以那句"传 discover_only=true"在真机上够不着。
            # 游标之后接的是**本因**那句 remedy（不再是硬编的 "larger deadline_ms"）：
            # 走到这里只可能是时钟砍的，而 web_discover 的 wire 上没有 deadline_ms 那颗
            # 旋钮 —— 对拿着 `timeout=` 的人喊抬 `deadline_ms`，就是 C2 那一族"把调用方
            # 支到一个他够不着的旋钮上"。
            fix = (f"The {len(held)} URL(s) already in hand are held in this "
                   f"process under plan {hold_plan_id}: web_discover(url='{url}', "
                   f"resume_token='{plan_token(hold_plan_id, 0)}') returns them "
                   "without re-fetching the sitemap. Otherwise "
                   + fix[0].lower() + fix[1:])
        return CrawlResponseModel(
            start_url=normalize_url(url), pages=[], pages_crawled=0,
            pages_discovered=map_total, discover_only=True, sitemap_used=True,
            truncated_by_time=stopped_by == "deadline",
            plan_id=hold_plan_id,
            next_resume_token=plan_token(hold_plan_id, 0) if hold_plan_id else "",
            sitemaps=list(result.sitemaps_used),
            duration_ms=(time() - t0) * 1000,
            summary=(f"sitemap.xml was read at {url} "
                     f"({len(result.sitemaps_used)} file(s)) but the map is empty: {why}"
                     + _held_credential_clause(url)),
            next_action=fix,
        )

    ok = len(pages)
    summary = (f"mapped {ok} URL(s) at {url} from sitemap.xml "
               f"(via {result.via}; {len(result.sitemaps_used)} sitemap file(s)) - one fetch, no BFS")
    if _order_stats["moved"]:
        # 重排要出口，否则"这一页为什么不是我 expected 的顺序"变成一个新的谜；而"覆盖
        # 了多少份不同内容"是唯一能区分"634 行=634 页"与"634 行=20 页的副本"的数。
        shipped_paths = len({_fold_locale_path(urlparse(p.url).path or "/")[0]
                             for p in pages})
        summary += (
            f" · rows ordered by relevance then the sitemap's own order "
            f"({_order_stats['moved']} of {listed} moved"
            + (f"; {_order_stats['mirrors_demoted']} locale variant(s) of "
               f"{_order_stats['mirror_pages']} page(s) placed behind the "
               f"{MIRROR_VARIANTS_KEPT} copies kept up front"
               if _order_stats["mirrors_demoted"] else "") + f") · the {ok} row(s) "
            f"shipped cover {shipped_paths} of the map's {_order_stats['paths_total']} "
            "distinct page(s)")
    if _terms:
        # 兑现了就报账：留下几条、按关键词筛掉几条、以及"这里只比 URL"。没有这句，
        # 一个空手而归的调用方会以为整站只有这几条。用的数是 `accept` 见过的条数，
        # 不是返回的行数 —— 后者被帽切过，两个数混在一句话里会把筛除说成截断。
        summary += (f" · search={search!r} kept {_counts['kept_by_search']} of "
                    f"{_counts['kept_by_search'] + _counts['dropped_by_search']} URL(s) "
                    f"in scope ({_counts['dropped_by_search']} carried none of the terms - "
                    "the URL is all a sitemap entry gives, so no title was compared)")
    next_action = (
        f"{ok} URLs mapped from the sitemap. web_read the ones you need - urls=[] "
        f"takes the whole subset in one call, no discovery and no re-crawl - or "
        f"web_crawl(url=...) from this same start to get bodies as it walks "
        f"(web_crawl returns content; a map comes only from web_discover). "
        f"Use path_include/path_exclude to scope."
    )
    if _order_stats["mirrors_demoted"] and not held:
        # 没被预算砍到的时候，被折到后面的副本也必须说得出在哪 —— 否则"这张地图覆盖
        # 了 30 页"与"我要的那一页怎么不在 30 行里"是同一个谜的两面。
        next_action += (f" The {listed} rows include "
                        f"{_order_stats['mirrors_demoted']} locale variant(s) ordered "
                        "last; they are all still in the map above, and "
                        "path_include='/<locale>/' reads one language in full.")
    if result.capped:
        # P2-9：一份 50,000 条 URL 的 sitemap 与一份恰好 `cap` 条的，在这里给出同一个
        # 数字，而 "mapped 5000 URL(s)" 读起来就是"这就是这个站"。上限永远只能报成
        # "至少" —— 到达它并不证明文档在那里结束。
        summary += (f" · map capped at {cap} URLs (the sitemap holds at least this "
                    f"many - not necessarily all of it)")
        # 这里的建议从前是 "raise max_pages"，而对一个停在默认 max_pages=10 的调用方
        # 那是死路：地图下限是 1000 条 URL，而 max_pages 要过 100 才动上限。点一颗
        # 拧不动的旋钮比什么都不点更坏，因为调用方拧了却看不到变化。
        next_action += (f" The map stops at {cap} URLs: scope with "
                        f"path_include/path_exclude to see a subtree in full, or raise "
                        f"max_pages above {SITEMAP_MAP_FLOOR // 10} to widen the cap.")
    elif result.index_truncated:
        # 另一种"比站点短"的方式，而计数看不出来：这一趟最多读 _MAX_SITEMAPS 份文件 /
        # _MAX_INDEX_DEPTH 层，所以子项更多（或更深）的 <sitemapindex> 会在上限之下
        # 结束，而 `capped` 是 False。一个把 1,200 页拆进 30 份小子 sitemap 的站点，
        # 只会图出其中 1,000 条，对其余的只字不提。
        summary += (" · the sitemap index is bigger than one walk reads, so this map "
                    "is a prefix of the site")
        # 这里没有旋钮可点，而这就是诚实的答案：max_pages 与路径过滤器都够不着那些
        # 没读的文件。说 "raise max_pages" 会是 P2-9 已经在上限那一支修掉的同一条死路。
        next_action += (" The map covers only the sitemap files this walk reads - there "
                        "is no knob to read further, so path_include scopes within what "
                        "is here rather than revealing what is not.")
    else:
        # "mapped 349 URL(s)" 加上没有别的措辞，读起来就是"这就是这个站"，而从前只有
        # 上限那一支说过相反的话。**没**到上限不是相反的证明：sitemap 是站点选择公开
        # 的一份索引，而不少站点整节整节地省掉。
        summary += (" · the sitemap's own list - a sitemap is an index the site "
                    "publishes, not proof it lists every page")
        if listed > ok:
            # 两种原因在这里都可知，所以点名而不是含糊其辞：要么是 sitemap 在两种归一化
            # 之下把同一条列了两次（`/a` 与 `/a/`），要么是这一趟跑光了 deadline。
            # 域外与被过滤的条目不可能出现在这里 —— `_eligible` 已经在上游丢掉了它们。
            summary += (f" · {listed - ok} of the sitemap's {listed} eligible entries "
                        f"did not become rows"
                        + (f" (the {stopped_by} stopped this response after {ok} "
                           f"row(s); those {len(held)} are held for the cursor)"
                           if held else " (a repeat after normalization)"))
    if held:
        # 预算或时限砍掉的行不是"站点的坏消息"，是**这一次响应的形状**。四件事必须
        # 同时出口，缺一件就是一次静默截断——本次修复的整批罪名就是这一条：
        #   ① 旗子：truncated_by_total_chars / truncated_by_time（下面 return 里置）；
        #   ② 总量：pages_discovered=map_total（地图多大），len(pages)=交回几行；
        #   ③ 游标：next_resume_token=<plan_id>:<下一个行号>，尾巴存在本进程内存里；
        #   ④ 散文：这一句里带数，用量日志一行也能读出"少了多少、为什么、怎么续"。
        summary += (
            f" · {'the char budget' if stopped_by == 'budget' else 'the deadline'} held "
            f"{len(held)} row(s) back: {ok} of {map_total} map row(s) shipped, "
            f"{row_chars:,} chars against the "
            + (f"{max_total_chars:,} you set" if stopped_by == "budget"
               else "deadline you set"))
        next_action += (
            f" {len(held)} map row(s) did not ship and are held in this server "
            f"process under plan {hold_plan_id}: web_discover(url='{url}', "
            f"resume_token='{plan_token(hold_plan_id, ok)}') returns rows "
            f"{ok + 1}-{map_total} without re-fetching the sitemap. To get them "
            f"another way: raise max_total_chars (up to "
            f"{MAX_TOTAL_CHARS_CEILING:,}, or 0 for no budget) ships more rows in one "
            "call, and path_include/search make the map smaller instead of longer. "
            "max_pages does not bound a map page - it bounds page fetches, and this "
            "page fetched none.")
    # 这一支只拿到 deadline_t（整趟帽子的**到期时刻**），开工时还剩多少就是它被约束的
    # 那段时间；一次尝试的帽子在这里不可知（不是这颗函数的参数），就不报那一笔。
    _m_applied, _m_overrun, _m_cap, _m_dur, _m_words = _time_budget_reading(
        t0, int(round((deadline_t - t0) * 1000)), 0)
    if _m_words:
        summary += _m_words
    return CrawlResponseModel(
        start_url=normalize_url(url), pages=pages, pages_crawled=0,
        pages_discovered=map_total, discover_only=True, sitemap_used=True,
        truncated_by_total_chars=stopped_by == "budget",
        truncated_by_time=stopped_by == "deadline",
        deadline_ms_applied=_m_applied,
        budget_overrun_ms=_m_overrun,
        timeout_applied=_m_cap,
        sitemap_truncated=bool(result.capped or result.index_truncated),
        sitemaps=list(result.sitemaps_used), plan_id=hold_plan_id,
        next_resume_token=plan_token(hold_plan_id, ok) if held else "",
        duration_ms=_m_dur, summary=summary, next_action=next_action,
    )


def _collect_row_identity(names: set, resp) -> None:
    """把**这一页自己的收据**并进整趟的名字集合（只报名，不重算策略）。

    为什么不从存储现算：存储里有没有一颗 auth 是"这台机器知道什么"，而这一页有没有
    带着它出去是"这一次发了什么"。前者的聚合会把从没发出去的东西声称成发过（一张
    连不上手的页、一趟只走了 sitemap 的地图模式），而 `identity_applied` 这句话的
    全部用处就在于它敢说"这几颗真的在这次的字节里"。
    """
    for one in (getattr(resp, "identity_applied", "") or "").split(","):
        if one.strip():
            names.add(one.strip())


def _no_sitemap_summary(start_norm: str, diag: dict) -> str:
    """「没找到 sitemap」这句话必须由**发生过什么**决定，不能是一句固定文案。

    2026-10-09 审计 D2：这句曾经逐字写着「robots.txt had no Sitemap directive
    and /sitemap.xml returned nothing」。在一个 robots.txt **确有**该指令、而
    ``/sitemap.xml`` **从未被请求**的私网站点上，两半都是假的 —— 读者照着去查
    robots.txt 会发现自己没写错，然后失去对整条回执的信任。这正是本仓库反复点名
    要杜绝的形状：调用看起来失败了，诊断却自信地答错。

    分支顺序就是「越具体越先说」：守卫拦下 > 本次调用的时钟砍掉 > 指令在但读不出 >
    robots 没读到 > 真的没有。

    末尾那一颗 ` · ` 是另一件事，除守卫那支之外都要挂（守卫那支根本没走到取候选的
    一步，谈不上"带凭据去要过"）：调用方可能**已经**为这台 host 存过凭据，
    而 sitemap 那一发是零凭据出去的（`_make_http_get` 用的是一个裸 primp 客户端）。
    一道要 token 才给人的 sitemap 在这里读起来就是"这站没地图"，而那正是有人存过
    凭据却看不见它起作用的样子 —— 所以说出事实，而不是让调用方去猜站点。
    """
    refused = diag.get("guard_refused") or []
    if refused:
        return (f"sitemap not read: robots.txt declares Sitemap: {refused[0]}, which "
                f"the SSRF guard refuses (a private or reserved address) - so nothing "
                f"was fetched from it")
    cut = diag.get("clock_cut") or []
    if cut:
        # 这一支从前并入「fetching or parsing it produced nothing」——那是把**调用方的
        # 预算**说不成**站点的选择**：实测一台 20s 才答的站点，在 15s 的写死时钟下回的就是
        # 这句话，而调用方明明写了 timeout=120000。时钟砍掉与站点答了空话是两件事，
        # 而下一步完全不同（抬预算 vs 换来源）。
        return (f"no sitemap could be read at {start_norm}: the request for {cut[0]} "
                f"ran out this call's time budget before the site answered - that is "
                f"the clock this call was given, not a 'no' from the site"
                + _held_credential_clause(start_norm))
    if diag.get("robots_had_sitemap"):
        base = (f"no sitemap could be read at {start_norm}: robots.txt declares a "
                f"Sitemap, but fetching or parsing it produced nothing (and "
                f"/sitemap.xml answered nothing either)")
    elif not diag.get("robots_checked"):
        base = (f"no sitemap.xml found at {start_norm}: robots.txt could not be read, "
                f"and /sitemap.xml returned nothing")
    else:
        base = (f"no sitemap.xml found at {start_norm} (robots.txt had no Sitemap "
                f"directive and /sitemap.xml returned nothing)")
    return base + _held_credential_clause(start_norm)


def _held_credential_clause(url: str) -> str:
    """存过凭据而这一发用不上时的那一句；没存过就空串（不占字数）。

    这几句"地图是空的"的话都要挂它 —— 守卫抓到过只挂一处：读通了 sitemap 而里面一个
    URL 都没有，与根本没读到一份，是同一条不一致的两张脸（一道要 token 的站点回你的
    可能是一页 HTML，parse 出来正好是"空"）。
    """
    from dhole_mcp import identity as _identity
    held = _identity.credentials_held_for(url)
    if not held:
        return ""
    return (f" · cred_defaults holds {'/'.join(held)} for this host and neither "
            "robots.txt nor the sitemap was fetched with it: a sitemap the site only "
            "serves to an authenticated caller reads here as nothing. Pages fetched "
            "by web_crawl do carry the credential (see identity_applied).")


def _no_sitemap_next_action(diag: dict) -> str:
    """与上面那句配套的出路。守卫拦下时不能说「换个站」——那不是站的问题。"""
    if diag.get("guard_refused"):
        return ("This host is private/reserved and the SSRF guard refused its sitemap. "
                "If it is your own dev site, waive it once with "
                "cred_defaults(set={\"allow_private\": true}) (loopback only) or "
                "set={\"allow_private\": [\"that.host\"]}, then call again. Otherwise "
                "omit sitemap= (or pass sitemap=false) and this same call builds the "
                "map by walking links instead.")
    if diag.get("clock_cut"):
        # 出路要点名**这把时钟归哪颗参数管**：web_discover 的 wire 上没有 deadline_ms，
        # 对它说「抬 deadline_ms」等于把调用方支到一个它够不着的旋钮上（同一个坏法在
        # C2 的 `search=` 那一族里刚认过账）。
        return ("The index request ran out this call's budget. Give it more of one: "
                "timeout= is the clock a web_discover call runs on (raise it), and "
                "deadline_ms is the one web_crawl runs on - or run with sitemap=false "
                "to walk links instead of reading the index.")
    # 「没有 sitemap」不是走错门：这扇门上省略 sitemap= 就改成游链。把调用方支到
    # web_crawl 会同时换掉成本模型（那一扇门每页抓正文），而它要的是地图。
    return ("No sitemap found. Omit sitemap= (or pass sitemap=false) and this same "
            "call builds the map by walking links instead - one request per page - "
            "or pass max_pages=1 to map just this page's own links.")


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
    """最佳优先的同域爬取。见模块 docstring。

    search: 按关键词匹配（URL 路径 + 标题）过滤已发现/已爬取的 URL。
        在大站点上配合 discover_only=True 做快速 URL 发现。
    sitemap: True = 只从站点的 sitemap.xml 出地图（一次抓取；交回完整 URL 清单 +
    lastmod，不跑 BFS、不取正文）。'auto' = 站点有 sitemap 就用，否则退回 BFS。
    False（默认）= 只跑 BFS。sitemap 这条路径把大站点的发现（几百页）压进一次调用。
    ignore_robots: 这次爬取跳过 robots.txt 合规（默认 False = 遵守）。每一页都经
    web_read 抓取，它在任何请求之前拒绝被 disallow 的 URL；这次爬取另外会把已知
    被 disallow 的发现 URL 直接丢掉（robots_skipped），而不是把预算花在一次拒绝上。
    """
    from dhole_mcp.security import validate_url, SecurityError
    from dhole_mcp.trafilatura_extractor import extract_html_title

    t0 = time()
    deadline_t = t0 + (deadline_ms / 1000.0)

    def _err(msg: str, start: str = "", hint: str = "",
             refusal: bool = False, input_refusal: bool = False) -> CrawlResponseModel:
        """一次爬行级的拒绝。`refusal=True` 说的是"dhole 自己写的整句"：
        200 字符的帽子防的是浏览器 call-log 那种噪声，不该把守卫的解释剪在句子中间
        （评审 R3刀1：discover/crawl 把 300 字符的 fake-ip 解释截在 200，读者连
        "这是守卫"都读不出来，而唯一出路那句在被剪掉的尾巴上）。

        `input_refusal=True` 说的是另一种失败：**一发都没出去**，错在调用方交来的那颗
        `url=`。这一族要同一副信封（评审 R02-097 立的前缀契约 · R02-098 真机
        `r119_url_shape_clauses.json`）：`error` 带 `invalid_request:`，`summary` 说
        "invalid request"，而不是 `crawl failed:` —— 后者把一次参数拒绝算在站点头上，
        读的人下一步就去查那个站了。
        """
        from dhole_mcp.errors import classify_network_error
        if input_refusal and not msg.startswith("invalid_request:"):
            msg = f"invalid_request: {msg}"
        if not hint:
            _, hint = classify_network_error(msg)
        if input_refusal:
            from dhole_mcp.quality import _summary_label
            _summ = f"invalid request · {_summary_label(msg)}"
        else:
            _summ = f"crawl failed: {msg[:120]}"
        return CrawlResponseModel(start_url=start or url, pages=[],
                                  error=msg if refusal else msg[:200],
                                  duration_ms=(time() - t0) * 1000,
                                  summary=_summ,
                                  next_action=hint)

    try:
        # 种子 URL 的私网放行：读的是**守卫那一步**自己并进来的存量（R02-141）。
        # 这里与 sitemap.py 逐条的 validate_url、robots.py 的复校验、每一页的抓取
        # 共用同一个答案，不需要任何一层作用域 —— 2026-10-09 审计 D2 的根因是
        # 「作用域只包了种子这一行就退出」，而把存量交给守卫读，包住的就不再是
        # 一行，是每一个走守卫的调用点（包括 `asyncio.to_thread` 那条工作线程）。
        url = validate_url(url)
    except SecurityError as e:
        from dhole_mcp.context import _TOOL
        from dhole_mcp.security import url_refusal_recovery
        msg = str(e)
        # 这一族的两半各有作者：动词按门取自 `context.nothing_went_out()`，句子本体住在
        # 守卫侧的共用文案里 —— 两处合起来的那条出路现在由 `security.url_refusal_recovery`
        # 一份装配（评审 R02-131：从前这四支住在这里，搜索门要同一件事就得再抄一遍）。
        # 门名从 `tool_scope` 拿 —— `web_discover` 就是经这条引擎路拒绝的，写死 "web_crawl"
        # 会让 discover 的读者听见别的门的名字（评审 R02-083）。
        _door = _TOOL.get() or "web_crawl"
        # 一次都没发出去（resolver / private / permanent / 形状四族全由 `url_refusal_family`
        # 认族，判决照旧全拒，出路各说各的：R02-113 / R02-097 / R02-098 / R02-117 四批真机
        # 读数在册 —— `r119_url_shape_clauses.json` 与 `r119b_boundary_clauses.json`）。
        # 信封三件齐全，前缀由 `input_refusal=True` 给。
        return _err(msg, hint=url_refusal_recovery(msg, _door),
                    refusal=True, input_refusal=True)
    except ValueError as e:
        return _err(str(e))
    start_norm = normalize_url(url)  # 去重与响应字段用的规范形式

    # robots.txt 合规（v16.0）：爬取自己的种子检查，这样一个 disallow dhole 的站点
    # 会被如实上报一次，而不是变成一次"静默收集拒绝"的爬取。逐页的强制住在 web_read
    # （每一页都过它），而这次调用之后 robots 缓存是热的，这正是下面那条"发现到的 URL
    # 预判"免费的原因。
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
    # web_read 会把它的钳位写进 summary；而一次爬取对 400 的请求静默交回 500 字符时
    # 什么都没说，于是两个工具会按被调的是哪一个，对同一件事给出不同的描述。
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
    # 裸字符串会被前缀过滤器逐字符迭代（`path.startswith(p) for p in "/what/"`），
    # 而 startswith("/") 对每条路径都成立 —— 于是一个字符串就静默把整次爬取过滤掉了。
    # 把它当成调用方本意的单前缀列表。
    #
    # 在**这里**校验，早于任何网络动作：匹配器兑现不了的模式从前会在整趟爬完之后显形为
    # "0 pages crawled"，读起来是"站点什么都没有"，而不是"你的模式写错了"。
    if isinstance(path_include, str):
        path_include = [path_include]
    if isinstance(path_exclude, str):
        path_exclude = [path_exclude]
    try:
        path_include = normalize_path_patterns(path_include)
        path_exclude = normalize_path_patterns(path_exclude)
    except ValueError as e:
        # 不是抓取失败：什么都没抓。通用的分类提示（"try a different source"）会把
        # 调用方支到一个毫无用处的地方。
        return _err(
            f"invalid path filter: {e}",
            hint=("Fix the path_include/path_exclude pattern and retry. A pattern names a "
                  "path subtree: '/docs' (or '/docs/*') keeps /docs and everything under it; "
                  "'/' or '*' keeps everything. Drop the pattern to crawl unfiltered."),
        )

    # 归一化 sitemap 旗子：True/'auto'/False。'auto' = 有 sitemap 就用，否则 BFS。
    # True = 只用 sitemap（没有就交空）。字符串比较不区分大小写。
    sm_mode = "off"
    if isinstance(sitemap, str):
        s = sitemap.strip().lower()
        sm_mode = "auto" if s == "auto" else ("on" if s in ("true", "1", "on", "yes") else "off")
    elif sitemap is True:
        sm_mode = "on"

    # ── Sitemap 模式：一次抓取就从 sitemap.xml 图出整站 ──────────────────────
    # 跑在 BFS 之前。'auto' 找到 sitemap 就用，否则落到 BFS。'on' 交回 sitemap 地图
    # （没有就交一张诚实的空地图）。
    if sm_mode in ("on", "auto"):
        # 诊断袋：只有"一份 sitemap 都没读通"时才会被填。总结与出路都由它决定，
        # 不再是一句固定文案（D2：那句固定文案在私网站点上两半都是假的）。
        _sm_diag: dict = {}
        sm_result = await _sitemap_map(url, path_include, path_exclude,
                                       max_pages=max_pages, deadline_t=deadline_t,
                                       diag=_sm_diag, search=search,
                                       max_total_chars=max_total_chars)
        if sm_result is not None:
            # 找到但为空的地图是真答案（"the sitemap lists N URLs, none in the
            # section you asked for"），但只对显式的 sitemap=true 成立。'auto' 下调用方
            # 要的是发现，而 BFS 能到达 sitemap 从未列出的页面，所以空地图落到 BFS，
            # 与从前"没有 sitemap"时完全一样。
            if sm_result.pages or sm_mode == "on":
                return sm_result  # sitemap 找到 + 已出图（auto/on 成功）
        # auto：没有 sitemap -> 落到 BFS。on：没有 sitemap -> 诚实的空。
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
    # 每页自己的身份收据并到这一趟的信封上（行是裁剪过的报告，逐页那句到不了调用方）。
    row_identity: set[str] = set()
    robots_skipped = 0
    # 链接发现的两个计数（`extract_same_domain_links` 逐页累加）。第三件事不必计数，
    # 它是"发现这一趟到底跑没跑"——`links_seen=0` 既可能是页面真的没给候选链接，也可
    # 能是我们从来没解析过任何页（`max_depth=0` 时正是如此），两句话不能共用一个 0。
    link_stats: dict[str, int] = {"links_seen": 0, "links_rejected_by_scope": 0}
    pages_scanned_for_links = 0
    truncated_maxpages = False
    truncated_time = False
    truncated_total_chars = False
    # 被帽挡下的页有两种，必须分开数：`dropped_after_answering` 是这一批**已经发过请求、
    # 答案也回来了**只是没吐出来的；队列里剩下的那些从来没付过钱。三处 break（max_pages /
    # 字符帽 / 时钟）都写这一颗 —— 合进 `discovered - crawled` 那一个数就是假话（R02-038）。
    dropped_after_answering = 0

    sem = asyncio.Semaphore(concurrency)

    # G27 — 礼貌间隔，按 host。`concurrency` 限制的是同时在飞的请求数，这与"同一台
    # 服务器被敲得多频繁"是两个问题：对一台慢 host 发 3 个并发请求就是每秒 3 个，
    # 永远如此。每台 host 有一个下次允许的时刻，于是同一 host 上的 N 条并发车道折成
    # 每个间隔一次请求，而不同 host 之间互不影响。
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
        """这台 host 要求被留多长的独处时间（0 = 它没要求，或调用方放弃了 robots）。"""
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

    # 在飞请求的收尾宽限：到点取消一个 coroutine 之后，HTTP 栈与浏览器栈把连接放回
    # 池子还要一点时间。它只吸收这段收尾，不吸收一次新的等待 —— 所以它是 `deadline_ms`
    # 的**误差上界**，不是它旁边又开的一扇预算。
    _IN_FLIGHT_GRACE_S = 0.25

    async def fetch_one(u: str) -> tuple[str, "object", str]:
        """把一页抓成 HTML。返回 (url, ResponseModel, html_str)。

        暂时性错误（timeout、connection_reset）隔 1s 自动重试一次。确定性错误
        （connection_refused、dns_failure、dns_unresolvable）不重试（再试还会失败）。

        整趟预算在这里生效（2026-10-29）：`deadline_ms` 名义上是 hard stop，但从前的
        检查只在批次边界 —— 一批里最慢的那一发把整个 `timeout=`（默认 30s）跑满才算
        完，于是 25s 的爬取实测跑出 44.9s，宿主 30s 先砍连接、什么都回不来。现在每一页
        的抓取本身被剩余预算（＋收尾宽限）包住：到点取消的是**这一页**，不是整趟。
        已经抓完的页照旧交回；被取消的这一页与从没发出去的那一页在回执里仍分开计
        （`dropped_after_answering` vs `pages_discovered - pages_crawled`）。
        """
        _left_s = deadline_t - time()
        if _left_s <= 0:
            from dhole_mcp.server import ResponseModel
            return u, ResponseModel(
                url=u, status=-1, content=[""],
                error=("deadline_ms: the whole-call time budget was already spent "
                       "when this page's turn came, so it was never requested")), ""
        try:
            return await asyncio.wait_for(
                _fetch_one_uncapped(u), timeout=_left_s + _IN_FLIGHT_GRACE_S)
        except (asyncio.TimeoutError, TimeoutError):
            from dhole_mcp.server import ResponseModel
            return u, ResponseModel(
                url=u, status=-1, content=[""],
                error=("deadline_ms: the whole-call time budget ran out while this "
                       "fetch was in flight - the request was made, and the answer "
                       "was abandoned rather than allowed to overrun the deadline")), ""

    async def _fetch_one_uncapped(u: str) -> tuple[str, "object", str]:
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
                # 暂时性错误（timeout/reset）重试一次
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
                        # 延迟 import 以打断循环依赖：
                        # crawl.py -> server.py (ResponseModel) 与
                        # server.py -> crawl.py (web_crawl)。两边都是
                        # 函数级 import，所以谁都不需要在 import 时拿到对方。
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

    # ---- 优先队列（最佳优先） ---------------------------------------------
    # 堆条目：(score, depth, seq, url, anchor_text)。`seq` 用来打破平局，于是 heapq
    # 永远不会在平局时去比较 dict/字符串。分数低 = 先弹出，所以真正的分数取负。
    heap: list[tuple[float, int, int, str, str]] = []
    seq = 0

    def push(u: str, depth: int, text: str):
        nonlocal seq
        s = score_link(u, text, focus or "")
        # 浅层偏好：优先浅页（每层一点小惩罚）。
        s -= 0.15 * depth
        heapq.heappush(heap, (-s, depth, seq, u, text))
        seq += 1

    push(url, 0, "")

    # 从堆里并发抓一批，最多 `concurrency` 条最好的 URL。
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
                # 名额在这一格用完，可这一批的请求早就发出去了 —— 那些字节是付过钱
                # 又扔掉的，不能和队列里没发的混在同一个"没 fetch 过"里。
                dropped_after_answering = len(batch) - _bi
                break
            # 整轮的字符帽在批内也要判：一批最多 `concurrency` 颗，一次 while 检查
            # 最坏会多吐出一整批。被这一格挡下的页是**已经付过请求**的，所以 summary
            # 要把"付了没返回"和"根本没付"分开说 —— 合起来只报一个"少了 N 页"，
            # 调用方就分不清是该抬预算还是该换 max_pages。
            if max_total_chars and total_chars >= max_total_chars:
                truncated_total_chars = True
                dropped_after_answering = len(batch) - _bi
                break
            if time() > deadline_t:
                truncated_time = True
                dropped_after_answering = len(batch) - _bi
                break
            u_norm = normalize_url(resp.url or u)
            # 守卫：重定向把这一页带到域外就跳过（不爬外站）。
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
                        # 抽取已经握着整页、直到每页上限；`cut` 说明还有没有更多。
                        # next_offset 是**那次抽取里的**字符偏移，所以只有拿它去
                        # web_read 这个 URL 才有意义 —— 爬取页自己没有可续的游标，
                        # 这正是 CrawlPage.next_offset 的字段描述已经写明的事。
                        is_trunc = cut
                        next_off = max_content_chars_per if cut else 0
                        content_md = md
                else:
                    # 不是 content_ok。只有"服务了、但是空页"才是渲染问题：4xx/5xx 是
                    # HTTP 的答案，status 0/-1 是根本没有答案。把那些标成 "js_shell"
                    # 等于告诉调用方站点被 JS 把关了，而 URL 只是无效。实测：arxiv
                    # 对错的分类答 400 "Invalid archive or category"，而爬取页报
                    # page_type=js_shell。
                    _status = _error_status(resp)
                    page_type = "js_shell" if 0 < _status < 400 and resp.error else "fallback"
            elif discover_only:
                # 地图模式：什么都没抽，所以 content_ok 必须保持 False。它从前被强置
                # 为 True（"URL 本身就是结果"），于是 summary 对十页只为展开链接地图
                # 而抓的页面声称 "10 content_ok" —— 一个关于并不存在的正文的断言。
                # 页面的 URL 是这次爬取的**输出**，不是这一页的正文。
                # status/fetcher_used/error 照旧填着，因为"这些 URL 里哪些是死的"
                # 是一个真答案。
                content_ok = False

            page = CrawlPage(
                url=u_norm, depth=depth, status=_error_status(resp),
                content_ok=content_ok, fetcher_used=resp.fetcher_used,
                substance=row_stats.get("substance", ""),
                substance_basis=row_stats.get("substance_basis", ""),
                title=title, page_type=page_type,
                content=[content_md] if content_md else [],
                content_chars=len(content_md), is_truncated=is_trunc,
                next_offset=next_off, fetched_at=getattr(resp, "fetched_at", ""),
                summary=row_summary, error=resp.error,
                untrusted_content=injection.scan(content_md or ""),
            )
            pages.append(page)
            _collect_row_identity(row_identity, resp)
            total_chars += len(content_md)

            # 浏览器自动升级：起始页（depth 0）以 js_shell 或 bot_challenge 失败时，
            # 把 force_fetcher 升成 "stealthy"，用于之后所有页（站点很可能需要真浏览器）。
            if (depth == 0 and not content_ok and force_fetcher is None
                    and page.page_type in ("js_shell", "fallback")
                    and ("bot_challenge" in (page.error or "") or "js_shell" in (page.error or "")
                         or page.page_type == "js_shell")):
                force_fetcher = "stealthy"
                logger.info(f"Crawl: upgrading to stealthy browser (page_type={page.page_type})")

            # 为更深一层发现链接。
            if depth < max_depth and html:
                pages_scanned_for_links += 1
                try:
                    links = extract_same_domain_links(
                        html, resp.url or u, url, path_include, path_exclude,
                        stats=link_stats)
                except Exception:
                    links = []
                for link_url, link_text in links:
                    link_norm = normalize_url(link_url)
                    if link_norm not in discovered and link_norm not in visited:
                        # 已知被 disallow 的候选在占用队列位之前就丢掉：源站的规则已经
                        # 在缓存里（上面那次种子检查放进去了），所以这里不花钱；而把一页
                        # 的 token 预算花在一次"下一页本来也会跳过"的拒绝上纯属浪费。
                        # 判定未知的仍然放行 —— web_read 才是强制点。
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
    # 堆里剩下的 URL 也算已发现（找到了但没爬）。
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
        """``pages_now`` 的统计行。

        在 `search` 过滤器之前与之后各调一次。过滤之后那次必须描述真正交回的那些页：
        把过滤前的统计当作前缀，会产出 "crawled 1 page(s); 1 content_ok"，而旁边站着
        pages_crawled=0 与 pages=[]。
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
            # 地图模式不抽正文，所以那边 "N content_ok" 会是关于并不存在的正文的断言。
            # 改说这个模式实际**花了什么**：一次 "URL map only" 调用仍然对每个展开的
            # 页面发一次请求，而抓取次数才是那个让人意外的数（默认 max_pages=10 时
            # 10 次请求）。要一次请求拿到同一张地图，见模块 docstring。
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
                + (f", {dropped_after_answering} fetched in the last batch and not "
                   "returned" if dropped_after_answering else "")
                + " - max_total_chars=0 removes the cap")
        if robots_skipped:
            bits_now.append(f"{robots_skipped} URL(s) skipped by robots.txt")
        # 四颗钳制一起报（哪一颗没动就不出现），措辞与 web_read 的 summary 同一句。
        bits_now.extend(_clamp_notes)
        if ((path_include or path_exclude) and len(pages_now) <= 1
                and not truncated_total_chars and not heap):
            # 过滤器在发现链接的过程中生效，所以一个匹配不到东西的模式会产出一趟恰好
            # 只含一页的爬取（起始 URL 从不被过滤），而任何计数都不会说过滤器跑过。
            # 实测：站点没用上的 path_include 只交回起始页、pages_discovered=1、
            # next_action 为空 —— 读起来像一次成功的爬取。
            #
            # 但这个断言只在队列**空**的时候成立。2026-10-08 反过来实测：对一个每页
            # 4,346 字符的站点用 `max_total_chars=1500`，第一页之后就停了、还有 83 条
            # URL 在队列里，而这条提示发出去时怪的是 path_include —— 怪一个这一趟自己
            # 随后就证明是对的模式（同一个作用域在第二次调用里放行了链接）。字符预算与
            # 作用域过滤器不可能同时是一次停止的原因，所以立在这里的是上面预算自己那一位。
            #
            # 同一把尺子的第二次自我否定（体验修复刀1b-C1）：上面那条把"队列空"当成
            # "过滤器吃光了链接"的充分条件，其实不是。现场是 `path_include=["/docs"]`
            # 的一次爬取，回来的 `pages_discovered: 1` 已经证明**没有任何链接被提出过**
            # ——可那句 blame 照样开火，并把解药（改模式）端给了调用方，而改模式修不了
            # 任何事。本地靶站复现同一条形状：同一个 URL 在 `max_depth=0` 下也是 1 页
            # 队列空，而那一次过滤器根本没运行（发现这一趟没跑）。
            #
            # 所以 blame 现在由**计数**选，不由"参数在不在 + 队列空不空"选：只有真被
            # 作用域挡下的候选存在时，才准说过滤器吃光了什么。匹配器**没有** bug ——
            # `/docs` 保住的是它自己的子树，两条作用域路径共用 `path_allowed`（`:659`），
            # 这一刀一行判定都不动。
            rejected = link_stats["links_rejected_by_scope"]
            seen = link_stats["links_seen"]
            scoped = (f"path_include={list(path_include or [])} / "
                      f"path_exclude={list(path_exclude or [])}")
            if rejected:
                bits_now.append(
                    f"only the start page was kept - {scoped} scoped out every one of "
                    f"the {seen} same-domain candidate link(s) the page(s) offered "
                    f"({rejected} rejected, nothing was left queued)")
            elif max_depth < 1:
                bits_now.append(
                    f"only the start page came back - {scoped} is NOT the reason: "
                    f"max_depth={max_depth} does no link discovery, so the scope never "
                    "ran on anything (raise max_depth to let it filter anything)")
            elif pages_scanned_for_links == 0:
                bits_now.append(
                    f"only the start page came back - {scoped} is NOT the reason: no "
                    "page returned HTML to parse for links, so link discovery never ran "
                    "(that row's status and error carry what actually happened)")
            elif seen == 0:
                bits_now.append(
                    f"only the start page came back - {scoped} is NOT the reason: the "
                    "HTML we parsed held 0 same-domain candidate links, so the scope "
                    "never had anything to filter (its nav is script-rendered or points "
                    "off-domain - web_read this URL with extraction_type='html' to see "
                    "the markup)")
            else:
                bits_now.append(
                    f"only the start page came back - {scoped} kept all {seen} "
                    "candidate link(s), so the scope is NOT the reason; the queue is "
                    "empty, which means those links were already known or were skipped "
                    "before they were queued (a robots.txt bit appears when that is "
                    "why) - read that row's status and error")

        if interval_used > 0:
            # 说出这个间隔存在才是要点：一次 10 页却花了 40 秒的爬取看起来就是卡住了，
            # 除非统计行说它是在故意拉开间距。也要点名是谁设的：这件事今天已没有调用方
            # 旋钮，所以站方自己的要求是唯一可能的作者。
            bits_now.append(
                f"{interval_used:g}s between requests per host "
                "(the host's own robots.txt Crawl-delay)")
        return "; ".join(bits_now)

    # 拼出简洁的 summary + next_action。
    ok = sum(1 for p in pages if p.content_ok)
    summary = _summary_for(pages)

    next_action = ""
    # 排队未发的 URL 是"能不能再砍一刀"的唯一依据：它们的第一段路径决定值不值得推荐
    # 一棵子树（见 `_scope_hint`）。
    scope_hint = _scope_hint([hu for _neg, _d, _s, hu, _t in heap])
    # 同一串 URL 从前只被**数**出来（`_shortfall_sentence` 报几条 never sent），留下来
    # 的那一份什么都没有 —— 于是"出路"只剩抬帽重跑，而重跑付的是同一批页的钱（评审
    # R02-009）。顺序按这一发本会发出的顺序排：`sorted(heap)` 就是 heappop 的序。
    #
    # 地图那一停也交队列，但交给另一位存储者（`_emit_map_tail`，就在 return 前）：
    # 正文支的计划只装一次都没发过的 URL，因为那一支的游标每前进一步都付一次请求，把
    # 已交出的行存进去等于教调用方重付；地图支的行只是清单，所以存的是"已交出的 + 尾巴"
    # —— 计划的大小就是地图的大小，`<id>:0` 与 `<id>:<N>` 因此都读得出东西（评审 R02-057：
    # 从前这一支把队列清空了事，`next_action` 报得出少了 39 行、却没有一行能续回来）。
    stop_queue = ([] if discover_only
                  else [hu for _neg, _d, _s, hu, _t in sorted(heap)])
    # 规范化：交出的行本来就是这个形状，混着两种写法的一张地图会让调用方把同一个
    # 页面数两次。
    map_tail = ([normalize_url(hu) for _neg, _d, _s, hu, _t in sorted(heap)]
                if discover_only else [])
    stop_plan_id = plan_store(stop_queue) if stop_queue else ""
    stop_cursor = plan_token(stop_plan_id, 0) if stop_plan_id else ""
    if truncated_total_chars:
        next_action = (
            f"crawl stopped at the whole-run character budget "
            f"(max_total_chars={max_total_chars:,}); "
            + _shortfall_sentence(not_fetched, dropped_after_answering)
            + " Raise max_total_chars (up to "
            f"{MAX_TOTAL_CHARS_CEILING:,}) or pass 0 for no cap, lower "
            "max_content_chars_per to fit more pages in the same budget"
            + (f", {scope_hint}" if scope_hint else "")
            + ". The pages you already have are the best-first ones - "
            "web_read the picks you actually need instead of re-crawling.")
        if stop_cursor:
            next_action += " " + _resume_sentence(stop_cursor, len(stop_queue), pages_crawled, url)
    elif truncated_maxpages or truncated_time:
        why = "time deadline" if truncated_time else "max_pages"
        if discover_only:
            # 这一支是地图早停时**真的**走到的那一支：map mode 里 "discovered > crawled"
            # 永远意味着循环撞了 max_pages，所以下面那支专用的地图文案只有经一次 off-domain
            # redirect 才到（2026-10-16 实测：A9a 那发读到的就是这里的旧句子）。
            # 出路的第一条必须点名**这扇门上就有的**那颗旋钮：web_discover 收 max_pages
            # 与时钟 timeout=（真机 C3/C5 各一发通过），不收 deadline_ms（C2 当场拒），
            # 而正文那一支保持原句 —— 那一扇门上 deadline_ms 才是它唯一的整轮时钟。
            lever = ("timeout=" if truncated_time
                     else f"max_pages= (up to {MAX_PAGES_CEILING})")
            lead = f"map stopped early ({why}); raise {lever} on this same call"
            tail = (
                ". " + _shortfall_sentence(not_fetched, dropped_after_answering)
                + " A map costs one request per page, so a deeper map is paid in "
                  "requests; for a ONE-request map pass max_pages=1 (this page's own "
                  "links) or sitemap=true (whole site, if it publishes one). For "
                  "bodies rather than a URL list, web_crawl(url=...) from the same "
                  "start is the other door."
            )
        else:
            lead = (f"crawl stopped early ({why}); re-run web_crawl with a higher "
                    f"max_pages / deadline_ms")
            tail = ". " + _shortfall_sentence(not_fetched, dropped_after_answering)
        # 连接词只在这一处装配：两支各写一遍是"同一句话两个连接词"的来路。
        next_action = lead + (f", {scope_hint}" if scope_hint else "") + tail
        if stop_cursor:
            next_action += " " + _resume_sentence(stop_cursor, len(stop_queue), pages_crawled, url)
    elif discover_only and pages_discovered > pages_crawled:
        next_action = (
            f"{pages_discovered} URLs mapped at a cost of {pages_crawled} page "
            f"fetch(es) (map mode fetches one page per expansion step to find more "
            f"links). web_read the ones you need, or web_crawl(url=...) from the "
            f"same start to fetch content (web_crawl returns bodies; a map comes "
            f"only from web_discover). For a "
            f"ONE-request map, pass max_pages=1 (start URL's links) or "
            f"sitemap=true (whole site, if it publishes one)."
        )

    # 网络失败的汇总诊断：大部分/全部页面都以网络错误失败时，给 agent 一个清楚的分类
    # 与可执行的提示，而不是把 next_action 留空。
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
            # 地图模式从不抽正文，所以那边 ok == 0 是**预期**形状，不能诊断成
            # "站点可能不可达"。
            #
            # 0 内容也有两种形状：站点真的不给正文，或者这一趟**没走到有正文的页**
            # —— 作用域把起点页之外的所有候选都挡在门外时就是后者。通用兜底句
            # "站点可能不可达/需要 JS" 在前者成立，在后者会把调用方支去修站方；
            # 2026-10-28 第二轮 H2/K4 实测：`path_include=['/work']` 筛掉 7 个
            # 候选，next_action 却一句没提作用域。这里按 summary 用的同一组计数
            # 分流，把"你筛掉了要读的页"说到出路里。
            rejected = link_stats["links_rejected_by_scope"]
            seen = link_stats["links_seen"]
            if ((path_include or path_exclude) and len(pages) <= 1
                    and not truncated_total_chars and not heap and rejected):
                scoped = (f"path_include={list(path_include or [])} / "
                          f"path_exclude={list(path_exclude or [])}")
                next_action = (
                    f"Only the start page came back, and its content did not pass "
                    f"the extraction verdict; {scoped} then rejected {rejected} of "
                    f"the {seen} same-domain candidate link(s) it offered and left "
                    "nothing queued, so the pages that might carry content were "
                    "never read. Change the scope (a pattern names a path subtree; "
                    "'/docs' does not match '/docs-old'), or web_read the start URL "
                    "with extraction_type='html' / force_fetcher='stealthy' to see "
                    "what that page actually returns.")
            else:
                next_action = ("All crawled pages returned no content. The site may be "
                              "unreachable, require JavaScript, or block automated access. "
                              "Try web_read on a specific URL for more diagnostics.")

    from dhole_mcp.security import recheck_posture_note
    _applied, _overrun, _cap, _dur_ms, _budget_words = _time_budget_reading(
        t0, deadline_ms, timeout)
    if _budget_words:
        summary += _budget_words
    result = CrawlResponseModel(
        start_url=start_norm, pages=pages, pages_crawled=pages_crawled,
        pages_discovered=pages_discovered, discover_only=discover_only,
        truncated_by_max_pages=truncated_maxpages,
        truncated_by_time=truncated_time,
        deadline_ms_applied=_applied,
        budget_overrun_ms=_overrun,
        attempt_cap_ms=_cap,
        truncated_by_total_chars=truncated_total_chars,
        # 一次读取进程事实的调用：探针已经在这次爬取里跑过了（每页的 web_read 都
        # 过那道门），这里只是把它的判词拿到调用方看得见的一层。
        ssrf_recheck=recheck_posture_note(),
        # 存储的身份在这一趟里**实际被发出去过**的那几颗（逐页收据并上来的，不是
        # 从存储现算的）：一张带着调用方 token 爬回来的 40 页站点，读起来和一张公开
        # 站点一模一样，而这两个东西不该长得一样。
        identity_applied=", ".join(sorted(row_identity)),
        duration_ms=_dur_ms,
        robots_skipped=robots_skipped,
        # 早停那队列的续跑坐标（`stop_plan_id` 只在队列非空时有值）：从前这两格在
        # 一页一页爬的那一路恒为空，只有地图那一路会填 —— 而 `next_resume_token`
        # 的字段说明早就写着"page cap, char budget or deadline"。
        plan_id=stop_plan_id,
        next_resume_token=stop_cursor,
        summary=summary, next_action=next_action,
    )

    # Search 过滤器：设了 search 时，只留 URL 或标题命中任一搜索词的页面。在大站点上
    # 配合 discover_only=True 做快速 URL 发现最好用（类似 Firecrawl 的 map + search）。
    if search and result.pages:
        _terms = search.lower().split()
        _pre_filter = len(result.pages)
        result.pages = [p for p in result.pages if any(
            t in p.url.lower() or t in (p.title or "").lower()
            for t in _terms
        )]
        result.pages_crawled = len(result.pages)
        if not result.pages:
            # 清空的那一发不许借用过滤前的停因位：「crawled 0 page(s) … stopped:
            # max_pages reached」读起来像爬满了才停，而事实是页面都爬回来了、
            # 词不在 URL/标题里（2026-10-25 P02/P05，'详情'/'卡片' 两轮白跑）。
            # 账自己说：爬了几页、0 页带词；出路交给 next_action（它本来就说对了）。
            result.summary = (f"search='{search}' matched 0 of {_pre_filter} "
                              f"crawled page(s) - the word only lives in page "
                              f"bodies, and search matches URL path + title")
        else:
            result.summary = (f"search='{search}' filtered to {len(result.pages)} URL(s); "
                              + _summary_for(result.pages))
        if not result.pages:
            result.next_action = (
                f"search='{search}' matched none of the crawled pages. It matches "
                "URL path + title only - widen it, drop it, or pass focus= to "
                "prioritize relevant links instead of filtering."
            )

    # 三条停因（帽 / 字符预算 / 时限）在这里合成一条出口：只要这一发还留着没轮过的
    # URL，地图就整份存下来（`search` 已在上面筛过，交出的那几行就是计划的前半）。
    if map_tail:
        _emit_map_tail(result, url=url, held=map_tail)

    return result


def _time_budget_reading(t0: float, deadline_ms: int, attempt_ms: int = 0) -> tuple:
    """(整趟受哪颗数字管, 越线多少 ms, 一次尝试的帽子, 这次跑了多久, 要补的那一句).

    两笔不同的账，今天两笔都是静默：

      * `deadline_ms` 是**整趟**的预算。跑得比它久通常意味着有行被砍（那一支自己有句子，
        说行数与游标），但也可以只是最后一发回来时已经过了钟点而队列正好空了 —— 那一格
        今天一个字都没有（本刀的罪名：R02-012，字符预算超额申报、时间预算超额不申报）。
      * `timeout=` 是**一次 HTTP 尝试**的帽子。整趟跑过它完全正常（一发接一发），但调用方
        按整趟去理解它 —— 于是「我点了 6000，它跑了 12018」在 S4 变成了一次直接放弃。
        这一支的句子必须把分工说清，而不是道歉。

    只报事实，不改判词：一次越线不是失败，`status` 与行的 `content_ok` 都不动。
    """
    dur_ms = (time() - t0) * 1000
    applied = int(round(deadline_ms or 0))
    overrun = int(round(dur_ms - applied)) if applied > 0 else 0
    cap = int(round(attempt_ms or 0))
    words = ""
    if overrun > 0:
        words += (
            f" · the run outlived its whole-call budget by {overrun:,}ms "
            f"(deadline_ms={applied:,} is what bounds the run, and in-flight fetches "
            "are cancelled at that point; the leftover is socket/browser teardown, "
            "not another wait)")
    if cap > 0 and dur_ms > cap:
        words += (
            f" · the {dur_ms:,.0f}ms this call took is NOT what `timeout=` was "
            f"promising: timeout={cap:,}ms is the cap on ONE HTTP attempt, while the "
            "whole run is bounded by "
            + (f"deadline_ms={applied:,}ms" if applied else "no deadline you named")
            + " - a longer run means more attempts, not a longer single one")
    return applied, max(overrun, 0), cap, dur_ms, words


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
    """Format the server-held plan cursor as ``<plan_id>:<offset>`` (the inverse of :func:`plan_parse`).
    """
    return f"{plan_id}:{offset}"


def plan_parse(token: str) -> Optional[tuple[str, int]]:
    """resume_token 形如 ``<plan_id>:<offset>``（复用 offset/next_offset 语汇）。"""
    head, _, tail = (token or "").rpartition(":")
    if not head or not tail.isdigit():
        return None
    return head, int(tail)


def _hold_map_tail(pages: list[CrawlPage], held: list[str]) -> str:
    """把一张被体积闸砍过的地图**整份**存下来，返回 plan_id。

    游标指向空无处比没有游标更坏：`next_resume_token='ab12:483'` 若后面只有 483
    行，调用方按它再问一次只会拿到"计划到头了"，而它要的那 517 行从来没有存在过。
    所以留下的尾巴要连着交出去的前半一起存 —— 计划的大小因此就是地图的大小。
    """
    return plan_store([p.url for p in pages] + list(held))


def _emit_map_tail(result, *, url: str, held: list[str]) -> None:
    """走链接的地图早停：把没轮过的 URL 连着已交出的那几行存成**整张地图**。

    sitemap 那一路早就这样收（`_hold_map_tail`），这一支以前在交给存储之前先把队列
    清空，于是同一颗 `plan_id` 在两种地图上说的是两种话：一种装着地图，一种只装着
    已经交出的那几行 —— 而调用方照着回执里那句"39 URL(s) were discovered but never
    sent"去问，读回的还是上一次的 3 行，下一句是 "Map exhausted"。计划只有 3 行、
    地图有 42 行时，那句 "exhausted" 说的是计划，不是这个站。

    下一句因此要点齐四样：存了几行（= 地图多大）、游标落在已交出的行数上、`<id>:<N>`
    读回没发过的那段、`<id>:0` 从头重读整张图，每一式都带上 `url=`（这颗门非要有 url，
    照抄不跑的建议不算出路）。正文那一支不学这个形状：它每走一步付一次请求。
    """
    shipped = [p.url for p in result.pages]
    tail = [u for u in held if u not in set(shipped)]
    if not tail:
        return
    plan_id = _hold_map_tail(result.pages, tail)
    total = len(shipped) + len(tail)
    result.plan_id = plan_id
    result.next_resume_token = plan_token(plan_id, len(shipped))
    result.summary += (f" · plan {plan_id} holds the whole map: {total} row(s), "
                       f"{len(shipped)} shipped here, {len(tail)} never sent")
    bits = (
        f" The {len(tail)} map row(s) that were never sent are held in this process "
        f"with the {len(shipped)} that shipped, under plan {plan_id} ({total} row(s) "
        f"= the whole map, 1h in memory): web_discover(url='{url}', "
        f"resume_token='{plan_token(plan_id, len(shipped))}') lists rows "
        f"{len(shipped) + 1}-{total} and spends no request.")
    if shipped:
        bits += (f" web_discover(url='{url}', resume_token='{plan_token(plan_id, 0)}')"
                 f" re-lists the map from row 1.")
    bits += (f" web_crawl(url='{url}', resume_token='{plan_token(plan_id, len(shipped))}')"
             " fetches the CONTENT of those held-back rows, and raising max_pages"
             " reaches them the slow way - one request per row.")
    result.next_action = (result.next_action or "").rstrip() + bits
