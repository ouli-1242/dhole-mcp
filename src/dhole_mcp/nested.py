"""E-13：一次调用摊平「列表页 → 每项详情页」这两层。

要解决的问题长这样（实测报告 §5 E-13 的复现是站酷的「推荐专题」）：首页挂着 8 个
专题，每个专题里具体有哪些作品，要点进那个专题页才看得到。`schema=` 的嵌套（G14）
答不了这件事 —— 那一层嵌套解决的是**同一篇文档里的作用域**，而这里的第二层是另外
8 篇文档。所以在取回层这一侧：第一层页面选出 N 条链接，N 个第二层页面并发抓取，
每张页面按同一份字段规格抽取，结果按「一个容器一条记录」回给调用方。

三颗参数全都有硬上限，且上限是响应里能说出口的事实而不是静默的截断：`nested_max`
封顶（超出记 `truncation=nested_budget` 在 summary 里）、第二层并发走
`NESTED_CONCURRENCY` 条道、每个字段仍然吃 `MAX_RECORDS_PER_FIELD`。一个 5000 项
的列表页因此不会变成 5000 条响应。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urldefrag, urljoin, urlparse

logger = logging.getLogger("dhole_mcp.nested")

# 默认展开几项。8 不是拍脑袋：E-13 的验收场景就是「首页挂 8 个专题」，默认值要
# 让那条调用**不带 nested_max 也能一次成立**，否则参数等于没生效。
DEFAULT_NESTED_ITEMS = 8
# 上限。一次调用最多发 30 个第二层请求 —— 再往上属于 web_crawl 的事（它有自己的
# 礼貌节流与页数上限），这里硬撑会变成一个没有礼貌机制的爬虫。
MAX_NESTED_ITEMS = 30
# 并发道数，与 bulk 那条道同源的量级：站点看到的是 6 个并发而不是 30 个。
NESTED_CONCURRENCY = 6
# 每项抽几个字段。字段数是调用方写的，但每个字段都会在第 30 张页面上再跑一遍，
# 所以这里也有一个上限而不是"随便你"。
MAX_NESTED_FIELDS = 12
# 列表项的标题取多长。链接文字在列表页上常常裹着一段描述，截在这里是为了 JSON
# 可读，不是为了省字节。
MAX_LABEL_CHARS = 120


def normalize_nested_fields(fields: Any) -> Dict[str, Any]:
    """`nested_fields` → `extract_structured` 认识的 schema.properties。

    接受三种写法，都归一到同一份：

      [{"name": "作品", "selector": ".work-item a"}]        # 显式
      [{"作品": "a[href*='/works/']"}]                       # 名字当键，值是选择器
      {"作品": {"selector": "...", "type": "array"}}          # 直接给对象

    选择器的合法性不在这里判 —— `validate_css_selector` 是注入守卫，它得由
    server 那一侧统一调用（与 `schema=` 同一条路），否则第 14 颗参数的守卫就比
    第 13 颗松。这里只管形状。

    Raises ValueError（带 next_action 能直接说的那句话）而不是返回 {}：一个读不
    懂的字段规格如果静默变成"没字段"，调用方收到的是 30 个空对象，而它以为页面是空的。
    """
    if fields is None:
        return {}
    if isinstance(fields, str):
        # 与 `schema=` 同等待遇：不少客户端会把嵌套参数序列化成字符串再发。
        import json as _json
        try:
            fields = _json.loads(fields)
        except ValueError as e:
            raise ValueError(f"nested_fields is not valid JSON: {e}") from e

    properties: Dict[str, Any] = {}

    def _add(name: Any, spec: Any) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("every nested_fields entry needs a non-empty field name")
        name = name.strip()
        if isinstance(spec, str):
            spec = {"selector": spec}
        if not isinstance(spec, dict):
            raise ValueError(
                f"nested_fields entry {name!r} must be a selector string or an "
                f"object like {{'selector': ..., 'attribute': ..., 'type': 'array'}}, "
                f"got {type(spec).__name__}")
        sel = spec.get("selector")
        if not (isinstance(sel, str) and sel.strip()):
            raise ValueError(
                f"nested_fields entry {name!r} needs a non-empty 'selector' - without "
                "one there is nothing to read on the second-layer page")
        out: Dict[str, Any] = {"selector": sel.strip()}
        attr = spec.get("attribute")
        if isinstance(attr, str) and attr.strip():
            out["attribute"] = attr.strip()
        # "array" 是调用方唯一能表达「这一层是个列表」的方式：专题页里的 20 件
        # 作品如果不标 array，抽出来就是第 1 件 —— 一个看着成功的漏。
        if spec.get("type") == "array" or spec.get("multiple") is True:
            out["type"] = "array"
        properties[name] = out

    if isinstance(fields, dict):
        # {"properties": {...}} 也接受，方便把 schema 的写法直接抄过来。
        inner = fields.get("properties") if set(fields) <= {"properties"} else None
        for k, v in (inner or fields).items():
            _add(k, v)
    elif isinstance(fields, list):
        for entry in fields:
            if isinstance(entry, dict) and "selector" not in entry and "name" not in entry:
                # [{"作品": "sel", "作者": "sel2"}] —— 一项里多个字段
                for k, v in entry.items():
                    _add(k, v)
                continue
            if isinstance(entry, dict) and "name" in entry:
                _add(entry.get("name"), entry)
                continue
            raise ValueError(
                "a nested_fields list entry is either {'name': ..., 'selector': ...} "
                f"or a {{'<字段名>': '<选择器>'}} map, got {entry!r}")
    else:
        raise ValueError(
            f"nested_fields must be a list or an object, got {type(fields).__name__}")

    if len(properties) > MAX_NESTED_FIELDS:
        raise ValueError(
            f"nested_fields has {len(properties)} fields; the cap is "
            f"{MAX_NESTED_FIELDS} per call - each one runs again on every "
            "second-layer page")
    return properties


def resolve_nested_links(html: str, base_url: str, selector: str,
                         limit: int) -> Dict[str, Any]:
    """第一层页面上 `selector` 命中的元素 → 要去抓的第二层 URL 名册。

    一个命中元素变成一条链接，找 href 的顺序是：它自己 → 它里面的第一个 `a[href]`
    → 它外面最近的 `a[href]`。中间那一步不是便利，是常见形状：卡片 `.topic-card`
    本身不是 `<a>`，链接在它肚子里。只认 `<a>` 的选择器会一个都不命中，然后调用方
    收到"这页没有专题"。

    去重按最终绝对 URL（先 `urldefrag`，否则同一专题的 `#anchor` 变体各算一项）。

    三个数是三件事，分开报：`matched` 是 DOM 命中，`candidates` 是去重之后**真的能抓**
    的链接数，`expanded` 是 `limit` 允许抓的那部分。校准跑的第一次实测里，10 个容器去重
    成 8 条链接被说成了 `truncation=nested_budget` —— 少的那 2 条是同一个作者的第二次出现，
    不是被上限藏起来的。把去重报成截断，调用方就会为"剩下的 2 条"再抬一次 `nested_max`，
    而抬到多少都拿不回来。
    """
    from dhole_mcp.structured import _parse_html

    empty = {"links": [], "matched": 0, "candidates": 0, "skipped": 0}
    root = _parse_html(html or "")
    if root is None:
        return empty
    try:
        from lxml.cssselect import CSSSelector
        nodes = list(CSSSelector(selector)(root))
    except Exception as e:
        logger.debug("nested_selector '%s' failed: %s", selector, e)
        return dict(empty, error=str(e))

    candidates: List[Dict[str, str]] = []
    seen: set = set()
    skipped = 0
    for node in nodes:
        href, carrier = _href_for(node)
        if not href:
            skipped += 1
            continue
        absolute = urljoin(base_url, urldefrag(href.strip())[0])
        if not absolute.startswith(("http://", "https://")):
            skipped += 1
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        # 标题取**带 href 的那个元素**的文字，不是整张卡片的：卡片里跟着链接的
        # "20 件 · 更新于…"那一段进 label 之后，JSON 里每一项的辨识度反而下降了
        # —— label 的用途是"认出抓错了哪一项"，链接文字本身就是那一项的名字。
        candidates.append({"url": absolute, "label": _label_for(carrier)})
    return {"links": candidates[:limit], "matched": len(nodes),
            "candidates": len(candidates), "skipped": skipped}


def _href_for(node) -> tuple:
    """这个元素（或其内部/外部最近的 `<a>`）的 href，以及**带着它的那个元素**。

    返回载体而不只是字符串，是因为 label 要从它身上取：链接文字才是这一项的名字。
    """
    href = node.get("href")
    if isinstance(href, str) and href.strip():
        return href, node
    for a in node.iter("a"):
        got = a.get("href")
        if isinstance(got, str) and got.strip():
            return got, a
    parent = node.getparent()
    while parent is not None:
        if parent.tag == "a":
            got = parent.get("href")
            if isinstance(got, str) and got.strip():
                return got, parent
        parent = parent.getparent()
    return "", node


def _label_for(node) -> str:
    """这一项在列表页上被叫作什么 —— 出错时这是唯一能认出「抓错了哪一项」的信息。"""
    try:
        text = " ".join((node.text_content() or "").split())
    except Exception:
        text = ""
    if not text:
        for attr in ("title", "aria-label", "alt"):
            got = node.get(attr)
            if isinstance(got, str) and got.strip():
                text = got.strip()
                break
    return text[:MAX_LABEL_CHARS]


def nested_cap(requested: Optional[int]) -> int:
    """nested_max 的解析：None → 默认，0 → 关掉，>0 → 封顶到 MAX_NESTED_ITEMS。"""
    if requested is None:
        return DEFAULT_NESTED_ITEMS
    try:
        asked = int(str(requested).strip())
    except (TypeError, ValueError):
        return DEFAULT_NESTED_ITEMS
    if asked < 0:
        return 0
    return min(asked, MAX_NESTED_ITEMS)


# 0 命中时报几颗候选选择器。这个数字是量出来的：校准跑里两个用例各多花一整页的
# `extraction_type='html'` 探针，才问出页面上真实的类名 —— 而那页 DOM 已经在这一步
# 手上了。DOM 在手却不报，等于把"再发一次请求"当成唯一的问法。
MAX_SUGGESTIONS = 4
_SUGGEST_MIN_LINKS = 2


def suggest_link_selectors(html: str, base_url: str,
                           limit: int = MAX_SUGGESTIONS) -> List[Dict[str, Any]]:
    """这页上「成组的链接」长什么样：按类名分组数一遍，回报最大的几组。

    报三种形状，因为 `nested_selector` 三种都收，而校准跑里两个 agent 各自摸到的都是第三种：
    链接本身（`a.tag`）、**装着链接的容器**（`.quote` —— 卡片不是 `<a>`，链接在它肚子里），
    以及按 href 前缀聚出来的一组（`a[href^="/author/"]`）。只报前两种的话，类名根本不该被指望
    出现在URL上的那些站点（quotes.toscrape 的作者链接就只靠 `/author/` 这一段）还是猜不到。

    计数口径与 `resolve_nested_links` 一致：先 `urldefrag` 再绝对化再去重，所以
    这里说的 8 条就是扇出会抓的 8 条，不是 DOM 里 `a` 的个数。
    """
    from dhole_mcp.structured import _parse_html

    root = _parse_html(html or "")
    if root is None:
        return []
    groups: Dict[str, Dict[str, Any]] = {}

    def _tally(selector: str, absolute: str, label: str) -> None:
        bucket = groups.setdefault(selector, {"urls": set(), "label": ""})
        bucket["urls"].add(absolute)
        if not bucket["label"]:
            bucket["label"] = label

    for anchor in root.iter("a"):
        href = anchor.get("href")
        if not isinstance(href, str) or not href.strip():
            continue
        absolute = urljoin(base_url, urldefrag(href.strip())[0])
        if not absolute.startswith(("http://", "https://")):
            continue
        label = _label_for(anchor)
        names: List[str] = []
        own = sorted((anchor.get("class") or "").split())
        if own:
            names.append("a." + ".".join(own))
        node = anchor.getparent()
        for _ in range(3):        # 容器最多往上找三层：再远的祖先已经不像"那一项"了
            if node is None:
                break
            classes = sorted((node.get("class") or "").split())
            if classes:
                names.append("." + ".".join(classes))
            node = node.getparent()
        # href 的第一段路径：`/author/x/`、`/tag/y/`、`/p/123.html` 都算同一组。
        # 站点自己的类名帮不上忙时，链接的"长相"是唯一还有规律的那一维。
        first = urlparse(absolute).path.split("/", 2)[1:2]
        if first and first[0]:
            names.append(f'a[href^="/{first[0]}/"]')
        for selector in names:
            _tally(selector, absolute, label)
    rows = [{"selector": s, "links": len(b["urls"]), "example": b["label"]}
            for s, b in groups.items()
            if len(b["urls"]) >= _SUGGEST_MIN_LINKS]
    # 多的那组在前；一样多时挑写起来短的（`.quote` 比 `a.text-muted` 更容易是"那一项"）。
    # href 前缀那一族排在同数的前面：它是按 URL 聚的，不像类名那样可能只是样式。
    rows.sort(key=lambda r: (-r["links"], 0 if r["selector"].startswith("a[href") else 1,
                             len(r["selector"])))
    return rows[:limit]


def format_suggestions(rows: List[Dict[str, Any]]) -> str:
    """候选选择器的那一句人话，空列表回空串（没有可报的就不占字数）。"""
    if not rows:
        return ""
    parts = [f"{r['selector']} ({r['links']} links"
             + (f", e.g. \u201c{r['example']}\u201d" if r["example"] else "")
             + ")" for r in rows]
    return " | ".join(parts)
