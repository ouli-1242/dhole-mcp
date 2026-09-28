"""Rule-based structured data extraction for Dhole MCP.

Extracts structured JSON from HTML using CSS selectors, metadata, tables,
and repeated element patterns. No LLM required — pure rules + lxml.

Usage:
    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "selector": "h1"},
            "price": {"type": "string", "selector": ".price"},
            "features": {"type": "array", "selector": ".feature-list li"},
            # G14: a property with its own sub-schema + a selector is one record
            # per matched container, child selectors evaluated INSIDE it. Without
            # this, `.tag` below answers with every tag on the page and the
            # grouping (which tags belong to which quote) is gone.
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

"items": {"properties": {...}} is accepted as the JSON-Schema spelling of the
same thing.
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
    """Extract structured data from HTML according to a JSON schema.

    Strategy per field:
    1. If field has 'selector': use CSS selector on the DOM ('attribute' picks an
       attribute value instead of the element's text)
    2. If field name matches metadata keys: use page metadata (OG/JSON-LD)
    3. If field type is 'array' without selector: detect repeated elements
    4. Fallback: regex search in extracted text

    Special mode: schema={"type": "auto"} or schema="auto" triggers automatic
    structure detection (tables, lists, JSON-LD, OG meta) without CSS selectors.

    Never raises — returns partial results with empty strings for failed fields.
    """
    if not schema or not isinstance(schema, dict):
        return {}

    # Auto mode: detect page structure without explicit selectors
    if schema.get("type") == "auto" or schema.get("mode") == "auto":
        return _extract_auto(html, metadata)

    properties = schema.get("properties", {})
    if not properties:
        return {}

    # Parse HTML into lxml tree
    root = _parse_html(html)
    result: Dict[str, Any] = {}

    for field_name, field_spec in properties.items():
        if not isinstance(field_spec, dict):
            field_spec = {"type": "string"}

        field_type = field_spec.get("type", "string")
        selector = field_spec.get("selector", "")
        attribute = field_spec.get("attribute") or ""

        value = None

        # G14: a field carrying a sub-schema describes RECORDS, not a text value.
        # Checked before everything else, because a nested field with a selector
        # would otherwise be read as one flattened string.
        nested = _sub_properties(field_spec)
        if nested and root is not None:
            result[field_name] = _extract_records(root, selector, field_spec, nested)
            continue

        # Strategy 1: CSS selector
        if selector and root is not None:
            value = _extract_by_selector(root, selector, field_type, attribute)

        # Strategy 2: metadata match
        if value is None and metadata:
            meta_key = _match_metadata_key(field_name, metadata)
            if meta_key:
                value = metadata[meta_key]

        # Strategy 3: JSON-LD extraction
        if value is None and root is not None:
            value = _extract_from_jsonld(root, field_name)

        # Strategy 4: regex fallback in visible text
        if value is None and root is not None:
            value = _extract_by_pattern(root, field_name, field_type)

        # Default empty value
        if value is None:
            value = [] if field_type == "array" else ""

        result[field_name] = value

    return result


# Records per repeated field. A list page can hold 5,000 items and the response
# must not become 5,000 objects; the cap is named in the tool description and the
# array length is visible in the output, so a truncated answer can be noticed
# rather than being mistaken for the whole page.
MAX_RECORDS_PER_FIELD = 200


def _sub_properties(field_spec: dict) -> Dict[str, Any]:
    """The nested property map of a field, from either spelling.

    ``{"selector": ".quote", "properties": {...}}`` is the short form;
    ``{"selector": ".quote", "items": {"properties": {...}}}`` is the JSON-Schema
    spelling a caller arrives with after writing a real schema elsewhere. Both
    mean "one record per matched container".
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
    """Elements matching ``selector`` below ``root`` (never raises)."""
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
    """Apply a sub-schema to each container the field's selector matches.

    Without this, a nested ``selector`` is evaluated against the WHOLE document,
    which is exactly the flattening the 16.0 report measured: ten quotes' tags
    came back as one 40-element array, and the grouping the caller asked for had
    quietly become the page's aggregate.
    """
    if not selector:
        # No container to iterate: the field just groups its children under a
        # name, in the same scope as its parent.
        return _extract_record(root, nested)
    nodes = _select_nodes(root, selector)
    if _is_repeating(field_spec):
        return [_extract_record(n, props) for n, props in
                ((node, nested) for node in nodes[:MAX_RECORDS_PER_FIELD])]
    return _extract_record(nodes[0], nested) if nodes else {}


def _extract_record(node, properties: Dict[str, Any]) -> Dict[str, Any]:
    """One record: every field evaluated INSIDE ``node``, recursively.

    Deliberately without the page-level fallbacks the top level has (metadata,
    JSON-LD, regex-in-text): those answer "what did this field get called on the
    page", and inside a record they would fill a missed selector with something
    from elsewhere on the page — a value that looks found but is not this row's.
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
            # No selector inside a record: the container's own text. Predictable
            # beats clever here — the caller pointed at this element.
            out[name] = _element_text(node)
        if out[name] is None:
            out[name] = [] if field_type == "array" else ""
    return out


def _parse_html(html: str):
    """Parse HTML into an lxml tree. Returns None on failure."""
    if not html:
        return None
    try:
        from lxml import html as lxml_html
        from io import BytesIO
        # encoding="utf-8" is not a default made explicit: these bytes came from
        # `html.encode("utf-8")` two lines up, so utf-8 is the only charset that
        # can be right. Without it libxml2 reads the document's own <meta
        # charset> (or, when it declares none, the machine's locale) and
        # re-decodes the body we already decoded correctly — which is how an
        # apostrophe U+2019 turned into "â" in a schema extraction (BUG-1).
        # It has to be lxml.html's parser, not lxml.etree's: the etree one builds
        # bare _Element nodes, which have no .text_content() and would empty
        # every field through the except in _element_text.
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
    """Extract text - or ``attribute`` values - using a CSS selector.

    A scalar field returns the first NON-EMPTY match. Returning elements[0]'s
    text made {"selector": "a"} look broken on any page whose first anchor wraps
    an <img> (an image-only logo has no text nodes), which read as "the schema
    parser cannot handle this page".
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
                # Attribute values are exact (a URL keeps its encoding); only
                # the surrounding whitespace is noise.
                return got.strip() if isinstance(got, str) else ""
            return _element_text(el)

        if field_type == "array":
            return [v for el in elements if (v := _value(el))]
        elif field_type == "count":
            # Count of matching elements, not their text.
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
    """Get clean text content from an lxml element."""
    try:
        text = el.text_content()
    except AttributeError:
        # Only lxml.html.HtmlElement has text_content(); a tree parsed by
        # lxml.etree's HTMLParser holds bare _Element nodes. Without this the
        # except below files that off as "this field has no text", which is how
        # a whole extraction silently goes empty.
        text = "".join(el.itertext())
    except Exception:
        return ""
    return " ".join(text.split()).strip()


def _match_metadata_key(field_name: str, metadata: Dict[str, str]) -> Optional[str]:
    """Match a schema field name to a metadata key (exact or tail-segment match)."""
    field_lower = field_name.lower().replace("_", "").replace("-", "")
    for key in metadata:
        key_lower = key.lower().replace("_", "").replace("-", "")
        # Exact match (normalized)
        if field_lower == key_lower:
            return key
        # Tail-segment match: "og:title" -> "title", "article_author" -> "author"
        key_tail = key_lower.split(":")[-1].split("_")[-1]
        if field_lower == key_tail:
            return key
    return None


def _extract_from_jsonld(root, field_name: str) -> Any:
    """Try to extract a field from JSON-LD structured data in the page."""
    try:
        scripts = root.xpath('//script[@type="application/ld+json"]')
        for script in scripts:
            text = script.text_content().strip()
            if not text:
                continue
            data = json.loads(text)
            # Handle @graph arrays
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
    """Fallback: search visible text for common patterns by field name."""
    try:
        text = root.text_content()
    except Exception:
        return None

    # Common field patterns
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


def _extract_auto(html: str, metadata: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Auto-detect page structure and extract without explicit CSS selectors.

    Detection order (first that produces data wins):
    1. JSON-LD structured data
    2. HTML tables
    3. Repeated list items (<li> in <ul>/<ol>)
    4. OpenGraph meta tags
    5. Fallback: visible text summary
    """
    root = _parse_html(html)
    result: Dict[str, Any] = {"_mode": "auto"}

    if root is None:
        return result

    # 1. JSON-LD: richest structured source
    jsonld = _extract_all_jsonld(root)
    if jsonld:
        result["json_ld"] = jsonld

    # 2. Tables: extract all tables as arrays of row-dicts
    tables = _extract_tables(root)
    if tables:
        result["tables"] = tables

    # 3. Lists: detect repeated <li> structures
    lists = _extract_lists(root)
    if lists:
        result["lists"] = lists

    # 4. OpenGraph / meta tags
    if metadata:
        og = {k: v for k, v in metadata.items()
              if k.startswith("og:") or k in ("title", "description", "author")}
        if og:
            result["metadata"] = og

    # 5. Fallback: if nothing detected, extract visible text summary
    if len(result) == 1:  # only _mode key
        try:
            text = root.text_content()
            result["text_summary"] = " ".join(text.split())[:2000]
        except Exception:
            pass

    return result


def _extract_all_jsonld(root) -> list:
    """Extract all JSON-LD blocks from the page."""
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
    """Extract HTML tables as list of row-dicts (header-keyed)."""
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
    """Extract significant <ul>/<ol> lists (>= 3 items)."""
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
