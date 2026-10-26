"""Dhole MCP 的基于规则的 structured 数据抽取。

用 CSS 选择器、元数据、表格与重复元素模式从 HTML 抽结构化 JSON。不需要 LLM ——
纯规则 + lxml。

用法：
    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "selector": "h1"},
            "price": {"type": "string", "selector": ".price"},
            "features": {"type": "array", "selector": ".feature-list li"},
            # G14：带自己子 schema 又带 selector 的属性 = 每个匹配容器一条记录，
            # 子选择器在容器**内部**求值。没有它，下面的 `.tag` 会回答页面上所有
            # 标签，而分组（哪些标签属于哪段引用）就没了。
            "quotes": {
                "type": "array", "selector": ".quote",
                "properties": {
                    "text": {"selector": ".text"},
                    "author": {"selector": ".author"},
                    "tags": {"type": "array", "selector": ".tag"},
                },
            },
        }
    }
    result = extract_structured(html, schema, url="https://...")
    # {"title": "...", "price": "$29", "features": ["...", "..."],
    #  "quotes": [{"text": "...", "author": "...", "tags": ["a", "b"]}, ...]}

"items": {"properties": {...}} 被接受为同一件事的 JSON-Schema 拼法。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger("dhole_mcp.structured")


def extract_structured(
    html: str,
    schema: Dict[str, Any],
    url: str = "",
    metadata: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """按 JSON schema 从 HTML 抽结构化数据。

    每个字段的策略：
    1. 字段有 'selector'：在 DOM 上用 CSS 选择器（'attribute' 取属性值而非元素文本）
    2. 字段名匹配元数据键：用页面元数据（OG/JSON-LD）
    3. 字段类型为 'array' 且没有 selector：检测重复元素
    4. 兜底：在抽取出的文本里做正则搜索

    特殊模式：schema={"type": "auto"} 或 schema="auto" 触发自动结构检测（表格、
    列表、JSON-LD、OG meta），不用 CSS 选择器；页面有重复结构时还会附上一份
    ``_schema`` 提案（可复用的容器 + 字段选择器）—— 见 :func:`_propose_schema`。

    从不抛异常 —— 失败的字段以空值返回，结果可能不完整。
    """
    if not schema or not isinstance(schema, dict):
        return {}

    # 自动模式：不用显式选择器，直接检测页面结构
    if schema.get("type") == "auto" or schema.get("mode") == "auto":
        return _extract_auto(html, metadata)

    properties = schema.get("properties", {})
    if not properties:
        return {}

    # 把 HTML 解析成 lxml 树
    root = _parse_html(html)
    result: Dict[str, Any] = {}

    for field_name, field_spec in properties.items():
        if not isinstance(field_spec, dict):
            field_spec = {"type": "string"}

        field_type = field_spec.get("type", "string")
        selector = field_spec.get("selector", "")
        attribute = field_spec.get("attribute") or ""

        value = None

        # G14：带子 schema 的字段描述的是**记录**，不是一个文本值。先于一切检查，
        # 否则一个带 selector 的嵌套字段会被读成一个被拍平的字符串。
        nested = _sub_properties(field_spec)
        if nested and root is not None:
            result[field_name] = _extract_records(root, selector, field_spec, nested)
            continue

        # 策略 1：CSS 选择器
        if selector and root is not None:
            value = _extract_by_selector(root, selector, field_type, attribute)

        # 策略 2：元数据匹配
        if value is None and metadata:
            meta_key = _match_metadata_key(field_name, metadata)
            if meta_key:
                value = metadata[meta_key]

        # 策略 3：JSON-LD 抽取
        if value is None and root is not None:
            value = _extract_from_jsonld(root, field_name)

        # 策略 4：可见文本里的正则兜底
        if value is None and root is not None:
            value = _extract_by_pattern(root, field_name, field_type)

        # 默认空值
        if value is None:
            value = [] if field_type == "array" else ""

        result[field_name] = value

    return result


# 每个重复字段的记录数上限。一个列表页可能有 5,000 条，而响应不能变成 5,000 个
# 对象；这个帽在工具描述里点名，数组长度在输出里可见，所以被截断的答案能被注意到，
# 而不是被当成整页。
MAX_RECORDS_PER_FIELD = 200


def _sub_properties(field_spec: dict) -> Dict[str, Any]:
    """一个字段的嵌套属性表，两种拼法都认。

    ``{"selector": ".quote", "properties": {...}}`` 是短形式；
    ``{"selector": ".quote", "items": {"properties": {...}}}`` 是调用方在别处写过
    真 schema 后带来的 JSON-Schema 拼法。两者都表示"每个匹配容器一条记录"。
    """
    nested = field_spec.get("properties")
    if isinstance(nested, dict) and nested:
        return nested
    items = field_spec.get("items")
    if isinstance(items, dict):
        nested = items.get("properties")
        if isinstance(nested, dict) and nested:
            return nested
    return {}


def _select_nodes(root, selector: str) -> list:
    """``root`` 之下匹配 ``selector`` 的元素（从不抛异常）。"""
    if not selector or root is None:
        return []
    try:
        from lxml.cssselect import CSSSelector
        return list(CSSSelector(selector)(root))
    except Exception as e:
        logger.debug("selector '%s' failed: %s", selector, e)
        return []


def _is_repeating(field_spec: dict) -> bool:
    return field_spec.get("type") == "array" or isinstance(field_spec.get("items"), dict)


def _extract_records(root, selector: str, field_spec: dict,
                     nested: Dict[str, Any]) -> Any:
    """把一份子 schema 应用到字段选择器匹配出的每个容器上。

    没有它，嵌套 ``selector`` 会对着**整篇**文档求值，那正是 16.0 报告量到的拍平：
    十段引用的标签被交回成一个 40 元素的数组，调用方要的分组悄悄变成了页面聚合。
    """
    if not selector:
        # 没有可迭代的容器：字段只是把子字段归到自己名下，作用域与父级相同。
        return _extract_record(root, nested)
    nodes = _select_nodes(root, selector)
    if _is_repeating(field_spec):
        return [_extract_record(n, props) for n, props in
                ((node, nested) for node in nodes[:MAX_RECORDS_PER_FIELD])]
    return _extract_record(nodes[0], nested) if nodes else {}


def _extract_record(node, properties: Dict[str, Any]) -> Dict[str, Any]:
    """一条记录：每个字段都在 ``node`` **内部**递归求值。

    刻意不带顶层那几种页面级兜底（metadata、JSON-LD、文本正则）：那些回答的是
    "这个字段在页面上被叫作什么"，在一条记录里它们会用页面别处的东西填上一个没
    命中的选择器 —— 一个看起来找到了、却不是这一行的值。
    """
    out: Dict[str, Any] = {}
    for name, spec in properties.items():
        if not isinstance(spec, dict):
            spec = {"type": "string"}
        nested = _sub_properties(spec)
        selector = spec.get("selector", "") or ""
        field_type = spec.get("type", "string")
        if nested:
            out[name] = _extract_records(node, selector, spec, nested)
            continue
        if selector:
            out[name] = _extract_by_selector(node, selector, field_type,
                                             spec.get("attribute") or "")
        elif field_type == "count":
            out[name] = 1 if node is not None else 0
        else:
            # 记录内部没有选择器：用容器自己的文本。可预测胜过聪明 —— 调用方
            # 指的正是这个元素。
            out[name] = _element_text(node)
        if out[name] is None:
            out[name] = [] if field_type == "array" else ""
    return out


def _parse_html(html: str):
    """把 HTML 解析成 lxml 树。失败时返回 None。"""
    if not html:
        return None
    try:
        from lxml import html as lxml_html
        from io import BytesIO
        # encoding="utf-8" 不是把默认值写明确：这些字节就是上面两行
        # `html.encode("utf-8")` 的产物，所以 utf-8 是唯一可能正确的字符集。
        # 不写它，libxml2 会读文档自己的 <meta charset>（没声明时读机器 locale）
        # 把我们已正确解码的正文重新解码一遍 —— 一个撇号 U+2019 就是这样在
        # schema 抽取里变成 "â" 的（BUG-1）。
        # 必须用 lxml.html 的解析器，不能是 lxml.etree 的：etree 那个建出裸
        # _Element 节点，没有 .text_content()，会让每个字段经 _element_text 的
        # except 变成空值。
        tree = lxml_html.parse(BytesIO(html.encode("utf-8", errors="replace")),
                               parser=lxml_html.HTMLParser(encoding="utf-8"))
        return tree.getroot()
    except Exception:
        try:
            from lxml import html as lxml_html
            return lxml_html.fromstring(html)
        except Exception:
            return None


def _extract_by_selector(
    root, selector: str, field_type: str, attribute: str = ""
) -> Any:
    """用 CSS 选择器抽文本——或 ``attribute`` 的值。

    标量字段返回第一个**非空**匹配。返回 elements[0] 的文本会让
    {"selector": "a"} 在任何第一个锚点包着 <img> 的页面上显得坏掉（纯图片 logo
    没有文本节点），而那会被读成"schema 解析器处理不了这个页面"。
    """
    try:
        from lxml.cssselect import CSSSelector
        sel = CSSSelector(selector)
        elements = sel(root)
        if not elements:
            return None

        def _value(el) -> str:
            if attribute:
                got = el.get(attribute)
                # 属性值是精确的（URL 保留它的编码）；只有周围空白是噪声。
                return got.strip() if isinstance(got, str) else ""
            return _element_text(el)

        if field_type == "array":
            return [v for el in elements if (v := _value(el))]
        elif field_type == "count":
            # 匹配到的元素个数，不是它们的文本。
            return len(elements)
        for el in elements:
            v = _value(el)
            if v:
                return v
        return None
    except Exception as e:
        logger.debug("Selector '%s' failed: %s", selector, e)
        return None


def _element_text(el) -> str:
    """从 lxml 元素取干净的文本内容。"""
    try:
        text = el.text_content()
    except AttributeError:
        # 只有 lxml.html.HtmlElement 有 text_content()；由 lxml.etree 的
        # HTMLParser 解析出的树持有裸 _Element 节点。没有这一支，下面的 except
        # 会把那种情况归档成"这个字段没有文本"，一次完整抽取就这样悄悄变空。
        text = "".join(el.itertext())
    except Exception:
        return ""
    return " ".join(text.split()).strip()


def _match_metadata_key(field_name: str, metadata: Dict[str, str]) -> Optional[str]:
    """把 schema 字段名匹配到元数据键（精确或尾段匹配）。"""
    field_lower = field_name.lower().replace("_", "").replace("-", "")
    for key in metadata:
        key_lower = key.lower().replace("_", "").replace("-", "")
        # 精确匹配（归一化后）
        if field_lower == key_lower:
            return key
        # 尾段匹配："og:title" -> "title"，"article_author" -> "author"
        key_tail = key_lower.split(":")[-1].split("_")[-1]
        if field_lower == key_tail:
            return key
    return None


def _extract_from_jsonld(root, field_name: str) -> Any:
    """尝试从页面里的 JSON-LD 结构化数据抽一个字段。"""
    try:
        scripts = root.xpath('//script[@type="application/ld+json"]')
        for script in scripts:
            text = script.text_content().strip()
            if not text:
                continue
            data = json.loads(text)
            # 处理 @graph 数组
            items = data if isinstance(data, list) else data.get("@graph", [data])
            for item in items:
                if isinstance(item, dict) and field_name in item:
                    val = item[field_name]
                    if isinstance(val, list):
                        return [str(v) for v in val]
                    return str(val)
    except Exception:
        pass
    return None


def _extract_by_pattern(root, field_name: str, field_type: str) -> Any:
    """兜底：按字段名在可见文本里搜常见模式。"""
    try:
        text = root.text_content()
    except Exception:
        return None

    # 常见字段模式
    patterns = {
        "price": r'[\$\u20ac\u00a3]\s*[\d,]+\.?\d*',
        "email": r'[\w.+-]+@[\w-]+\.[\w.]+',
        "phone": r'(?:\+\d{1,3}[\s\-]?)?(?:\(\d{2,4}\)[\s\-]?)?\d{3,4}[\s\-]?\d{3,4}',
        "date": r'\d{4}[-/]\d{1,2}[-/]\d{1,2}',
        "url": r'https?://[^\s<>"\']+',
    }

    field_lower = field_name.lower()
    for key, pattern in patterns.items():
        if key in field_lower:
            match = re.search(pattern, text)
            if match:
                return match.group(0).strip()

    return None


# auto 模式提议 schema 的边界：容器要重复 >= 3 次（"两页里各一次"的巧合不算），
# 字段最多提 8 个（再多调用方也不会逐个用）。
_MIN_GROUP = 3
_MAX_PROPOSED_FIELDS = 8
# 无 class 的后代里，这些标签太泛（"div" / "span" / "p"），当字段名没有意义；
# 其余标签（h1-h6 / a / img / time …）用标签名当字段名。
_GENERIC_TAGS = frozenset({
    "div", "span", "p", "li", "ul", "ol", "section", "article", "main",
    "body", "html", "table", "tr", "td", "th", "tbody", "thead", "form",
    "nav", "header", "footer", "aside", "figure", "strong", "em", "b", "i",
})


def _css_sig(el) -> str:
    """元素的选择器签名：`tag.cls1.cls2`（最多两个 class）。"""
    tag = el.tag if isinstance(el.tag, str) else ""
    cls = [c for c in (el.get("class") or "").split() if c][:2]
    return tag + ("." + ".".join(cls) if cls else "")


def _name_token(el) -> str:
    """字段/容器名：class 的最后一个 token（snake_case），退化到标签名。

    没有 class 且标签太泛时返回 ""（这个后代不当字段）。
    """
    cls = [c for c in (el.get("class") or "").split() if c]
    if cls:
        raw = cls[-1]
    else:
        tag = el.tag if isinstance(el.tag, str) else ""
        if tag in _GENERIC_TAGS:
            return ""
        raw = tag
    return re.sub(r"[^0-9a-zA-Z]+", "_", raw).strip("_").lower()


def _propose_schema(root) -> Dict[str, Any]:
    """从重复结构里推一份可复用的抽取 schema：容器 + 字段选择器。

    确定性启发式，不猜语义：找带 class 的 `标签.class` 里重复 >= :data:`_MIN_GROUP`
    次、成员带文本的组，取重复最多的那组当**记录容器**；在它的第一个实例里，把带
    文本的后代按 `标签.class` 归类，每类给一个字段名。目的是让调用方抄一份选择器
    用于同一站点的后续页，而不是每次自己猜（"动态 schema"）。
    """
    if root is None:
        return {}
    groups: Dict[str, list] = {}
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        sig = _css_sig(el)
        if "." not in sig:          # 无 class 的重复太泛，不作容器
            continue
        groups.setdefault(sig, []).append(el)
    best_sig, best_n = "", 0
    for sig, els in groups.items():
        if len(els) > best_n and " ".join(els[0].text_content().split()):
            best_sig, best_n = sig, len(els)
    if best_n < _MIN_GROUP:
        return {}
    instance = groups[best_sig][0]
    fields: Dict[str, Any] = {}
    for el in instance.iter():
        if not isinstance(el.tag, str) or el is instance:
            continue
        if not " ".join(el.text_content().split()):
            continue
        name = _name_token(el)
        if not name or name in fields:
            continue
        fields[name] = {"selector": _css_sig(el)}
        if len(fields) >= _MAX_PROPOSED_FIELDS:
            break
    if not fields:
        return {}
    container = _name_token(instance) or "records"
    return {"type": "object", "properties": {container: {
        "type": "array", "selector": best_sig, "properties": fields}}}


def _extract_auto(html: str, metadata: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """自动检测页面结构，不用显式 CSS 选择器就抽取。

    检测顺序（第一个产出数据的胜出）：
    1. JSON-LD 结构化数据
    2. HTML 表格
    3. 重复列表项（<ul>/<ol> 中的 <li>）
    4. OpenGraph meta 标签
    5. 兜底：可见文本摘要

    页面有重复结构时还会提议一份可复用的 `_schema`（容器 + 字段选择器），让调用方
    能为同站其他页钉住它，而不是手写选择器。
    """
    root = _parse_html(html)
    result: Dict[str, Any] = {"_mode": "auto"}

    if root is None:
        return result

    # 1. JSON-LD：最丰富的结构化来源
    jsonld = _extract_all_jsonld(root)
    if jsonld:
        result["json_ld"] = jsonld

    # 2. 表格：把所有表格抽成 row-dict 数组
    tables = _extract_tables(root)
    if tables:
        result["tables"] = tables

    # 3. 列表：检测重复的 <li> 结构
    lists = _extract_lists(root)
    if lists:
        result["lists"] = lists

    # 3b. 提议 schema：页面重复结构的可复用选择器
    proposed = _propose_schema(root)
    if proposed:
        result["_schema"] = proposed

    # 4. OpenGraph / meta 标签
    if metadata:
        og = {k: v for k, v in metadata.items()
              if k.startswith("og:") or k in ("title", "description", "author")}
        if og:
            result["metadata"] = og

    # 5. 兜底：什么都没检测到时，抽可见文本摘要
    if len(result) == 1:  # 只有 _mode 一个键
        try:
            text = root.text_content()
            result["text_summary"] = " ".join(text.split())[:2000]
        except Exception:
            pass

    return result


def _extract_all_jsonld(root) -> list:
    """抽取页面里所有 JSON-LD 块。"""
    items = []
    try:
        scripts = root.xpath('//script[@type="application/ld+json"]')
        for script in scripts:
            text = script.text_content().strip()
            if not text:
                continue
            data = json.loads(text)
            if isinstance(data, list):
                items.extend(data)
            elif isinstance(data, dict):
                graph = data.get("@graph")
                if graph and isinstance(graph, list):
                    items.extend(graph)
                else:
                    items.append(data)
    except Exception:
        pass
    return items


def _extract_tables(root) -> list:
    """把 HTML 表格抽成 row-dict 列表（以表头为键）。"""
    tables = []
    try:
        for table_el in root.xpath('//table')[:5]:
            rows = table_el.xpath('.//tr')
            if len(rows) < 2:
                continue
            headers = [" ".join(c.text_content().split()).strip() for c in rows[0].xpath('.//th|.//td')]
            if not headers:
                continue
            table_data = []
            for row in rows[1:50]:
                cells = [" ".join(c.text_content().split()).strip() for c in row.xpath('.//td|.//th')]
                if cells:
                    row_dict = {}
                    for i, h in enumerate(headers):
                        row_dict[h or f"col_{i}"] = cells[i] if i < len(cells) else ""
                    table_data.append(row_dict)
            if table_data:
                tables.append(table_data)
    except Exception:
        pass
    return tables


def _extract_lists(root) -> list:
    """抽取有意义的 <ul>/<ol> 列表（>= 3 项）。"""
    lists = []
    try:
        for list_el in root.xpath('//ul|//ol')[:10]:
            items = list_el.xpath('./li')
            if len(items) < 3:
                continue
            texts = [" ".join(li.text_content().split()).strip() for li in items[:30]]
            texts = [t for t in texts if t]
            if len(texts) >= 3:
                lists.append(texts)
    except Exception:
        pass
    return lists
