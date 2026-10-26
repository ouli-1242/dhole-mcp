"""web_crawl 的 Sitemap.xml 发现与解析（v8）。

抓大站的天才一步：不做盲目的最佳优先 BFS（发现下一层要每页抓一次），而是一次
调用抓回站点的 sitemap.xml，拿到完整 URL 列表 + <lastmod> 日期。大多数真实站点
都提供 sitemap（并在 robots.txt 里声明）。这把一次 500 页的发现爬取压缩成一次
抓取。

公开面：
  discover_sitemap(start_url, *, http_get, max_urls, accept) -> SitemapResult
    找站点的 sitemap：先看 robots.txt 的 `Sitemap:` 指令，再看约定的
    /sitemap.xml + /sitemap_index.xml 路径。抓取 + 解析（递归进入 <sitemapindex>
    子项，有上限），返回一份扁平、去重的 {url, lastmod} 列表加响应来源。`accept`
    是可选谓词，在 max_urls 计数**之前**应用，所以按路径收窄的调用方拿到的是它
    要的那批页面里的 `max_urls`，而不是站点列出的前 `max_urls` 页（见下面的说明）。

  parse_sitemap(xml_bytes) -> (urls, sub_sitemaps)
    底层解析器：sitemap.xml 要么是 <urlset>（叶子：<url><loc>），要么是
    <sitemapindex>（嵌套：<sitemap><loc>）。返回 (叶子 URL, 子索引)。

稳健性：XML 解析不依赖命名空间（lxml local-name），容忍 gzip（服务器有时会压缩
sitemap），限制总 URL 数与递归深度，且从不抛——抓取/解析失败交回空结果，让 crawl
退回 BFS。

传输层是注入的（`http_get`），所以本模块不硬依赖某个 HTTP 客户端，能用假抓取器
做单测。`http_get(url) -> (status, bytes)`，失败时 None。
"""

from __future__ import annotations

import gzip
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse

from lxml import etree

logger = logging.getLogger("dhole-mcp.sitemap")

# 传输函数：url -> (status:int, body:bytes) | None
HttpGet = Callable[[str], Optional[tuple[int, bytes]]]

_MAX_URLS_DEFAULT = 5000      # 单次 discover_sitemap 调用的上限
_MAX_INDEX_DEPTH = 3          # <sitemapindex> -> 子 <sitemapindex> -> ...
_MAX_SITEMAPS = 25            # 不递归进没有边界的索引森林


@dataclass
class SitemapURL:
    """One leaf URL declared by a sitemap.

    ``lastmod`` is the value exactly as the document carried it; when the sitemap omits it
    the string stays empty instead of being filled with a guessed date.
    """

    url: str
    lastmod: str = ""


@dataclass
class SitemapResult:
    """The result of one completed sitemap discovery.

    Three counters keep the reasons a map came back short separate: ``capped`` hit
    ``max_urls``, ``index_truncated`` hit the sitemap index's depth/file limits, and
    ``filtered_out`` counts rows the caller's ``accept`` predicate rejected. The first two
    mean the site has more; the third means the filter was too narrow.
    """

    urls: list[SitemapURL] = field(default_factory=list)
    sitemaps_used: list[str] = field(default_factory=list)   # 真正解析成功的 sitemap URL
    capped: bool = False  # URL 列表停在 max_urls 上（P2-9）
    # 遍历的**另一个**停止条件，也是任何计数都显示不出来的那个：遍历最多读
    # _MAX_SITEMAPS 个文件 / _MAX_INDEX_DEPTH 层，所以一个子项更多（或嵌套更深）的
    # <sitemapindex> 结束时 URL 列表仍**短于** max_urls——`capped` 保持 False，调用方
    # 拿到站点的一个前缀，却没有任何标记说明它是前缀。与 `capped` 分开，因为解法不同：
    # URL 上限跟着 max_pages 走，这两个上限不跟任何东西走。
    index_truncated: bool = False
    # sitemap 列出但被 `accept` 拒掉的 URL。sitemap 自身大小 = len(urls) +
    # filtered_out，这让调用方能区分"站点在这个区没有页面"与"我的过滤器写错了"——
    # 没有它，一次全被拒的遍历与一份空 sitemap 从外面看一模一样。
    filtered_out: int = 0
    via: str = ""                                             # "robots" | "conventional" | ""
    robots_checked: bool = False
    # robots.txt 的**诊断**：它到底有没有 Sitemap: 指令，以及有几条候选被 SSRF
    # 守卫拦下。加这两颗是因为 web_crawl 在 sitemap 抓不到时会写一句总结，而那句
    # 总结曾经逐字断言"robots.txt 没有 Sitemap 指令、且 /sitemap.xml 返回空"——
    # 在一个 robots.txt 确有指令、/sitemap.xml 从未被请求的私网站点上，两半都是
    # 假的（2026-10-09 审计 D2，真机复现）。报告要能说真话，先得有人把事实记下来；
    # 而"守卫拦了什么"过去是彻底静默的（`continue` 一句注释都没有），地图短了一截
    # 却没有任何一处能解释为什么。
    robots_had_sitemap: bool = False
    guard_refused: list[str] = field(default_factory=list)


def _maybe_gunzip(body: bytes) -> bytes:
    """sitemap 有时即使没请求也是 gzip（Sitemap .xml.gz 路径，或忽略
    Accept-Encoding 的服务器）。按魔数识别，不看扩展名。"""
    if body[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(body)
        except Exception:
            return body
    return body


def parse_sitemap(xml_bytes: bytes) -> tuple[list[SitemapURL], list[str]]:
    """解析一份 sitemap.xml 文档。

    返回 (叶子 URL, 子 sitemap URL)。<urlset> 只交叶子；<sitemapindex> 只交子
    sitemap。不依赖命名空间：按 local-name 匹配，所以常见的
    xmlns="http://www.sitemaps.org/schemas/.../sitemap"（以及任何命名空间变体 /
    无命名空间）都能解析。
    """
    if not xml_bytes:
        return [], []
    try:
        body = _maybe_gunzip(xml_bytes)
        # recover=True，让截断/畸形的 sitemap 仍能交出它能交的部分。
        root = etree.fromstring(body, parser=etree.XMLParser(recover=True, huge_tree=True))
    except Exception:
        return [], []
    if root is None:
        return [], []

    def lname(el) -> str:
        t = el.tag
        return t.split("}", 1)[1] if isinstance(t, str) and "}" in t else (t or "")

    root_name = lname(root)
    urls: list[SitemapURL] = []
    children: list[str] = []

    if root_name == "urlset":
        for url_el in root:
            if lname(url_el) != "url":
                continue
            loc = lastmod = ""
            for child in url_el:
                n = lname(child)
                if n == "loc" and child.text:
                    loc = child.text.strip()
                elif n == "lastmod" and child.text:
                    lastmod = child.text.strip()
            if loc:
                urls.append(SitemapURL(url=loc, lastmod=lastmod))
    elif root_name == "sitemapindex":
        for sm_el in root:
            if lname(sm_el) != "sitemap":
                continue
            for child in sm_el:
                if lname(child) == "loc" and child.text:
                    loc = child.text.strip()
                    if loc:
                        children.append(loc)
                    break
    return urls, children


def _fetch(http_get: HttpGet, url: str) -> Optional[bytes]:
    try:
        res = http_get(url)
    except Exception:
        return None
    if not res:
        return None
    status, body = res
    if status != 200 or not body:
        return None
    return body


def _robots_sitemaps(start_url: str, http_get: HttpGet,
                     *, report: Optional[dict] = None) -> tuple[list[str], bool]:
    """抓 /robots.txt，返回它的 Sitemap: 指令（绝对 URL）。

    SSRF 防护：robots.txt 的 ``Sitemap:`` 指令可能指向内网（攻击者站点
    声明 ``Sitemap: http://169.254.169.254/...``），抓取前逐条校验。

    ``report`` 是可选的事实收集袋：填 ``had_directive``（robots.txt 里**有没有**
    Sitemap 指令）与 ``refused``（被守卫拦下的候选，按出现顺序）。返回值形状保持
    2 元组 —— 现有调用点与守卫都按 2 元组解包（tests/test_audit_fixes.py），
    诊断走旁路加，不动那个已被钉住的接口。
    """
    p = urlparse(start_url)
    robots_url = f"{p.scheme or 'https'}://{p.netloc}/robots.txt"
    body = _fetch(http_get, robots_url)
    if body is None:
        return [], False
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:
        return [], True
    out: list[str] = []
    refused: list[str] = []
    had_directive = False
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("sitemap:"):
            val = line.split(":", 1)[1].strip()
            if val:
                had_directive = True
                candidate = urljoin(robots_url, val)
                # SSRF：拒绝指向内网/保留地址的 sitemap URL。
                # 被拦下不是"没找到"：记进 refused，让调用方说得出是哪条、为什么。
                try:
                    from dhole_mcp.security import validate_url
                    validate_url(candidate)
                except Exception:
                    refused.append(candidate)
                    continue
                out.append(candidate)
    if report is not None:
        report["had_directive"] = had_directive
        report["refused"] = refused
    # 去重，保持顺序
    seen: set[str] = set()
    uniq = [u for u in out if not (u in seen or seen.add(u))]
    return uniq, True


def _conventional_sitemaps(start_url: str) -> list[str]:
    p = urlparse(start_url)
    base = f"{p.scheme or 'https'}://{p.netloc}"
    return [base + "/sitemap.xml", base + "/sitemap_index.xml"]


def discover_sitemap(start_url: str, *, http_get: HttpGet,
                     max_urls: int = _MAX_URLS_DEFAULT,
                     accept: Optional[Callable[[str], bool]] = None) -> SitemapResult:
    """发现并抓取站点的 sitemap，返回一份扁平的 URL 列表。

    先试 robots.txt 的 `Sitemap:` 指令；一条都解析不出时，退回约定的
    /sitemap.xml + /sitemap_index.xml 路径。递归进入 <sitemapindex> 子项（受
    _MAX_INDEX_DEPTH + _MAX_SITEMAPS 限制）。总 URL 数上限为 max_urls。从不抛；
    任何失败都返回空结果，让调用方退回 BFS。

    `accept`（可选）决定哪些 URL 合格，它在 max_urls 计数**之前**跑。这个顺序
    正是参数存在的全部意义：上限必须花在调用方要的页面上。如果反过来在上限之后
    应用（web_crawl 从前就是这么在返回列表上做的），要一个栏目的调用方会拿到
    站点碰巧列出的前 max_urls 条里筛出的那个栏目——一个可能深达数千条的子树的
    空地图或残缺地图，而且没有任何错误说明。

    谓词在某条 URL 上抛异常时只丢那一条，不终止整趟遍历。本模块的合同是从不抛，
    另一个选择是更糟的失败：一份 5,000 条的 sitemap 里有一条解析不了，就会把整张
    地图带走，让 web_crawl 退回 BFS 且什么都不说。
    """
    result = SitemapResult()
    if not start_url:
        return result

    candidates: list[str] = []
    robots_report: dict = {}
    robots_smaps, robots_checked = _robots_sitemaps(start_url, http_get,
                                                    report=robots_report)
    result.robots_checked = robots_checked
    result.robots_had_sitemap = bool(robots_report.get("had_directive"))
    result.guard_refused.extend(robots_report.get("refused") or [])
    if robots_smaps:
        candidates.extend(robots_smaps)
        result.via = "robots"
    else:
        candidates.extend(_conventional_sitemaps(start_url))
        result.via = "conventional"

    seen_urls: set[str] = set()
    flat: list[SitemapURL] = []
    visited_sitemaps: set[str] = set()

    def _drain(sitemap_url: str, depth: int, *, from_index: bool = False) -> None:
        # 下面两道守卫都只在还有东西可读时才会到达，所以在这里置标志是准确的
        # "遍历被截断"，而不是 `capped` 必须带的"至少"退让：一个正好有
        # _MAX_SITEMAPS 个子项的索引永远不会第 _MAX_SITEMAPS+1 次调用 _drain，
        # 标志保持 False，对它是正确答案。
        #
        # `from_index` 把顶层循环排除在外：那个循环也会走**回退**候选
        # （/sitemap.xml 之后的 /sitemap_index.xml），而拒绝一个已被第一个候选
        # 覆盖过的回退，说明不了地图是否短于站点——那是对同一份文档的第二次机会，
        # 不是一份没读的文档。
        if depth > _MAX_INDEX_DEPTH or len(visited_sitemaps) >= _MAX_SITEMAPS:
            if from_index:
                result.index_truncated = True
            return
        if sitemap_url in visited_sitemaps:
            return
        visited_sitemaps.add(sitemap_url)
        # SSRF：sitemapindex 子 URL 可能指向内网，抓取前校验。
        # 拦下要记账：这里过去是一句 `return`，于是"index 里 30 个子 sitemap、
        # 其中 12 个指向私网"与"index 只有 18 个子 sitemap"在报告里长得一模一样。
        try:
            from dhole_mcp.security import validate_url
            validate_url(sitemap_url)
        except Exception:
            result.guard_refused.append(sitemap_url)
            return
        body = _fetch(http_get, sitemap_url)
        if body is None:
            return
        result.sitemaps_used.append(sitemap_url)  # 来源：每一份真正抓取并解析的 sitemap
        urls, children = parse_sitemap(body)
        for su in urls:
            u = su.url.split("#")[0]
            if not u or u in seen_urls:
                continue
            # 先标 seen 再过谓词，这样同一条被拒的 URL 即使出现在多份 sitemap 里
            # 也只计一次——否则 `filtered_out` 会多计，它报出的数字（"sitemap 列了
            # 80 条 URL"）就是错的。
            seen_urls.add(u)
            if accept is not None:
                try:
                    eligible = bool(accept(u))
                except Exception:
                    eligible = False
                if not eligible:
                    result.filtered_out += 1
                    continue
            flat.append(su)
            if len(flat) >= max_urls:
                return
        for child in children:
            if len(flat) >= max_urls:
                return
            _drain(child, depth + 1, from_index=True)

    for c in candidates:
        if len(flat) >= max_urls:
            break
        _drain(c, 0)

    result.urls = flat
    # 到达上限不能证明 sitemap 到此为止——一个正好有 max_urls 条目的站点，与一个
    # 有十倍条目的站点看起来一模一样。所以这个标志只说"停在上限上"，调用方的措辞
    # 必须停在"至少"。
    result.capped = bool(max_urls) and len(flat) >= max_urls
    return result
