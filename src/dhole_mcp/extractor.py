"""Dhole's own content extraction.

Replaces scrapling's Convertor._extract_content with direct trafilatura +
markdownify + lxml usage. The extraction chain:

1. Trafilatura (primary, for markdown/text/article/structured)
2. markdownify (fallback for markdown/html types)
3. Raw text (last resort: regex tag stripping)

CSS selector narrowing uses lxml directly.
"""

from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger("dhole_mcp.extractor")


def extract_content(
    page,
    extraction_type: str = "markdown",
    css_selector: Optional[str] = None,
) -> List[str]:
    """Extract content from a Response object.

    Args:
        page: A Response object (dhole_mcp.fetcher.Response) or compatible
              object with .body, .encoding, .url, .css()
        extraction_type: 'markdown', 'html', 'text', 'article', 'structured'
        css_selector: CSS selector to narrow extraction scope

    Returns:
        List of extracted content strings (usually one element).
    """
    from dhole_mcp.trafilatura_extractor import extract_with_trafilatura, _fallback_extract

    # Trafilatura is the primary extractor for all text-like types
    result: Optional[List[str]] = None
    if extraction_type in ("markdown", "text", "article", "structured"):
        try:
            candidate = extract_with_trafilatura(page, extraction_type=extraction_type, css_selector=css_selector)
            if candidate and any(r.strip() for r in candidate):
                result = candidate
        except Exception as e:
            logger.debug(f"Trafilatura extraction failed: {e}")

    # Fallback: use markdownify for markdown/html, or raw text
    if result is None:
        result = _fallback_extract(page, extraction_type, css_selector)
    return _collapse_boilerplate(result, extraction_type)


def _collapse_boilerplate(chunks: List[str], extraction_type: str) -> List[str]:
    """页内重复样板行折叠（只对文本/正文类）。删掉的条数写进 context var，
    由 quality._with_agent_hints 报在信封上 —— 静默删内容比留着样板更糟。"""
    if extraction_type not in ("markdown", "text", "article"):
        return chunks
    from dhole_mcp.boilerplate import collapse_repeated_lines
    from dhole_mcp.context import _BOILERPLATE_LINES_COLLAPSED

    out: List[str] = []
    dropped = 0
    for chunk in chunks:
        if isinstance(chunk, str):
            new, n = collapse_repeated_lines(chunk)
            out.append(new)
            dropped += n
        else:
            out.append(chunk)
    if dropped:
        _BOILERPLATE_LINES_COLLAPSED.set(
            _BOILERPLATE_LINES_COLLAPSED.get() + dropped)
    return out


def _is_xml_content_type(content_type: str) -> bool:
    """True for data XML (sitemap / RSS / Atom / SOAP / +xml), False for HTML-ish XML.

    `application/xhtml+xml` is an HTML document wearing an XML media type: it must
    keep going through the HTML extractor, not be handed back raw.
    """
    ct = (content_type or "").split(";")[0].strip().lower()
    if not ct or ct.startswith(("application/xhtml", "image/", "text/html")):
        return False
    return ct.endswith("+xml") or ct.startswith(
        ("application/xml", "text/xml", "application/rss", "application/atom",
         "application/rdf"))
