"""web_read 的外链抽取与分类（v8）。

给一段页面 HTML，按上下文分类返回出链，让 agent 一步跟住页面的来源链，而不是
肉眼扫 markdown 链接：

  citations   - 非站点外壳的同域链接。分类靠排除法（见 _NAV_TAGS 以及
                role=navigation/menu/menubar），不靠主内容匹配 —— 后者并不存在。
                这些是页面引用的来源（论文、一手文档、相关阅读）——价值最高的一类。
  navigation  - <nav>/<header>/<footer>/<aside>/role=navigation 里的链接。
                站点外壳，值得跟的很少。
  external    - 指向页面之外其他域的链接（从上面拆出来，让 agent 一眼看到站外引用）。
  primary_source - 一条尽力而为的"这一页真正的一手来源"提示，来自 canonical/JSON-LD
    元数据，或指向已知一手主机的站内引用（arxiv.org、doi.org、biorxiv.org、github.com …）。

健壮性：一次廉价、宽容的 lxml 解析。畸形 HTML、缺段、没有 <a> 标签都返回空列表，
从不抛异常。分类是启发式的（容器上溯）—— 落在模棱两可容器里的链接归为 "citation"
（偏主内容，因为那才是 agent 想要的高价值默认值）。
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin, urlparse

from lxml import html as lxml_html

logger = logging.getLogger("dhole-mcp.links")

# 算作站点外壳的容器 -> 其中的链接归为 navigation。
_NAV_TAGS = {"nav", "header", "footer", "aside"}
# 二手文章最常引用的一手来源主机。
_PRIMARY_HOSTS = (
    "arxiv.org", "doi.org", "biorxiv.org", "medrxiv.org",
    "github.com", "gitlab.com", "opencode.dev",
    "wikipedia.org", "wikimedia.org",
    "nature.com", "science.org", "sciencedirect.com", "springer.com",
    "ieee.org", "acm.org", "plos.org",
)

_MAX_CITATIONS = 30
_MAX_NAV = 20
_MAX_EXTERNAL = 20
# 调用方 `max_links` 上限（web_read）的边界。工具描述里报的是同一组数字；
# 超出范围的在这里夹紧 —— 就在上限真正生效的那一点，文档范围便不会与实际漂开。
MAX_LINKS_FLOOR = 1
MAX_LINKS_CEILING = 100


def _norm_host(u: str) -> str:
    """主机比较用的主机名：小写，经 urlparse().hostname 去掉 userinfo/端口，
    再抹掉装饰性的开头 ``www.``。用 startswith（**不是** lstrip —— lstrip 剥的是
    字符集，'wikipedia.org' 会丢掉开头的 'w'，'web.example.com' 会变成
    'eb.example.com'）。"""
    try:
        host = (urlparse(u).hostname or "").lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _clean_text(s: str) -> str:
    return " ".join((s or "").split())


def _caps(max_links: int | None) -> tuple[int, int, int]:
    """本次调用的 (citations, navigation, external) 三档上限。

    ``None`` = 模块默认值（30/20/20）。显式 ``max_links`` 同时设定三者，并夹紧到
    文档声明的 1-100 范围。上限按列表分别计：要 5 条链接的 agent 不会因为页面恰好
    有三个类别就收到 90 条。
    """
    if max_links is None:
        return _MAX_CITATIONS, _MAX_NAV, _MAX_EXTERNAL
    try:
        cap = int(max_links)
    except (TypeError, ValueError):
        return _MAX_CITATIONS, _MAX_NAV, _MAX_EXTERNAL
    if cap <= 0:
        return _MAX_CITATIONS, _MAX_NAV, _MAX_EXTERNAL
    cap = max(MAX_LINKS_FLOOR, min(cap, MAX_LINKS_CEILING))
    return cap, cap, cap


def extract_links(html_text: str, page_url: str, metadata: dict[str, Any] | None = None,
                  max_links: int | None = None) -> dict[str, Any]:
    """分类页面的出链。

    返回 {citations, navigation, external, primary_source, total_found,
    is_truncated}。列表项是 {url, text}。``total_found`` 数的是页面上见到的每个
    不同 http(s) 链接（截断之前），``is_truncated`` 在上限至少丢掉一条时为 True ——
    agent 因此能分清"这页只链到 3 个东西"和"这页链到 300 个东西，这里是 30 个"，
    并换一片看。从不抛异常；任何错误都返回空列表与零计数。
    """
    out: dict[str, Any] = {
        "citations": [], "navigation": [], "external": [],
        "primary_source": "", "total_found": 0, "is_truncated": False,
    }
    if not html_text or not page_url:
        return out
    cap_cit, cap_nav, cap_ext = _caps(max_links)
    try:
        tree = lxml_html.fromstring(html_text)
    except Exception:
        # lxml 有时候拒绝真正损坏的标记；退化为没有链接。
        return out
    if tree is None:
        return out

    page_host = _norm_host(page_url)
    seen: set[str] = set()
    citations: list[dict[str, str]] = []
    navigation: list[dict[str, str]] = []
    external: list[dict[str, str]] = []
    content_externals: list[dict[str, str]] = []  # 主内容区的站外链接（真引用）——给 primary_source 用
    total_found = 0

    try:
        anchors = tree.xpath('//a[@href]')
    except Exception:
        anchors = []
    for a in anchors:
        try:
            href = (a.get("href") or "").strip()
        except Exception:
            continue
        if not href or href.lower().startswith(("javascript:", "mailto:", "tel:", "data:", "#")):
            continue
        try:
            absu = urljoin(page_url, href)
        except Exception:
            continue
        parsed = urlparse(absu)
        if parsed.scheme not in ("http", "https"):
            continue
        host = _norm_host(absu)
        if not host:
            continue
        key = absu.split("#")[0]
        if key in seen:
            continue
        seen.add(key)
        total_found += 1
        try:
            text = _clean_text(a.text_content() or "")
        except Exception:
            text = ""
        if len(text) > 160:
            text = text[:157] + "..."
        entry = {"url": key, "text": text}

        # 容器分类：这个锚点是否位于站点外壳里？
        try:
            in_nav = next(
                (True for anc in a.iterancestors()
                 if (anc.tag if isinstance(anc.tag, str) else "") in _NAV_TAGS
                 or (anc.get("role") or "") in ("navigation", "menu", "menubar")),
                False,
            )
        except Exception:
            in_nav = False

        is_external = host != page_host
        if is_external:
            if len(external) < cap_ext:
                external.append(entry)
            if not in_nav:
                content_externals.append(entry)  # 主内容里的站外引用
            continue
        # 同域：导航外壳 vs 主内容引用。
        if in_nav:
            if len(navigation) < cap_nav:
                navigation.append(entry)
        else:
            if len(citations) < cap_cit:
                citations.append(entry)

    out["citations"] = citations
    out["navigation"] = navigation
    out["external"] = external
    out["primary_source"] = _primary_source(page_url, metadata or {}, content_externals)
    # 透明度（G1）：上限过去是隐形的，300 条页内链接的页面和 30 条的看起来一模一样。
    out["total_found"] = total_found
    out["is_truncated"] = total_found > (len(citations) + len(navigation) + len(external))
    return out


def _primary_source(page_url: str, metadata: dict[str, Any],
                    content_externals: list[dict[str, str]]
                    ) -> str:
    """尽力而为地给出唯一一手来源 URL，没有则返回 ""。

    优先级：与页面不同主机上的 canonical/JSON-LD URL（发布者的权威位置）；否则取
    位于页面主内容区（真页内引用，非站点外壳）且指向已知一手主机
    （arxiv/doi/github/…）的第一条站外链接。同域链接永远不是一手来源（它们就是
    站点自己）。
    """
    page_host = _norm_host(page_url)
    # 1) 不同主机上的 canonical / JSON-LD @id / og:url。
    for key in ("canonical", "og:url", "url"):
        val = metadata.get(key)
        if isinstance(val, str) and val.startswith("http"):
            if _norm_host(val) and _norm_host(val) != page_host:
                return val
    # 2) 主内容里指向已知一手主机的站外引用。
    for e in content_externals:
        host = _norm_host(e["url"])
        if any(host == p or host.endswith("." + p) for p in _PRIMARY_HOSTS):
            return e["url"]
    return ""
