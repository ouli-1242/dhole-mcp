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
import re
from typing import List, Optional

logger = logging.getLogger("dhole_mcp.extractor")

# 主内容检测器的产出低于文档自身正文的这个比例，就不算"提取成功"，算丢了。
# 阈值是量出来的不是拍的（.scratch/measure_trafo_yield.py，20 个真页面）：唯一异常
# rfc-editor.org/rfc/rfc9110.html 在 2.20%（1.19MB 的规范文档只交出 8,357 字符，正文
# 从 6.4 节开始，1-6.3 节整段不见了），次低的正常页 pypi.org 在 46.74%，其余全部
# >= 71%。异常与正常之间有 20 倍空档，12% 落在正中间。
_MAIN_CONTENT_FLOOR = 0.12
# 小页算不出"丢了 99%"这种话 —— 447 个字符的页本来就只有那么多正文，检测器交回 340
# 个字符是提取对了。
_MIN_DOCUMENT_CHARS = 20_000
# 脚本 / 样式 / 模板里的字符不是读者会看到的正文，估文档自身文本时先连壳一起去掉。
_CHROME_AND_TAGS = re.compile(
    r"<(script|style|noscript|template)\b[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)


def _document_text_chars(page) -> int:
    """Non-whitespace characters the HTML itself holds, markup and chrome removed.

    A crude lower-bound proxy for "how much readable text is in this document", and
    it only ever gets compared against a ratio with a 20x margin on either side.

    0 means "cannot measure", which fails closed: an unmeasurable document is left
    as the detector returned it rather than swapped on a guess. `extract_content`
    takes any response-shaped object, so a `.content` that is not text (a transport
    stand-in, a raw-bytes implementation) is a calling convention that exists, not
    an impossibility to crash on.
    """
    try:
        raw = page.content
    except Exception:  # noqa: BLE001 - a body that will not decode is not a loss
        return 0
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return 0
    return len("".join(_CHROME_AND_TAGS.sub(" ", raw).split()))


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
    else:
        result = recover_a_lost_document(page, result, extraction_type, css_selector)
    return _collapse_boilerplate(result, extraction_type)


def recover_a_lost_document(page, content: List[str], extraction_type: str = "markdown",
                            css_selector: Optional[str] = None) -> List[str]:
    """A non-empty answer is not the same thing as a complete one.

    Trafilatura's main-content detector can decide most of a legitimate document IS
    boilerplate and hand back a slice; "candidate non-empty" then adopts it and the
    caller reads a success that lost the first six sections of a spec. Measured:
    8,357 characters out of a document holding 379,285 (2.20%), `content_ok` true.

    Returns the answer to build on (the plain converter's when the detector lost the
    document, otherwise `content` untouched) and announces a swap into the call's
    note bucket. Collapsing stays with the caller so neither path double-folds.

    `css_selector=` is exempt: a caller who narrowed the scope asked for less, so a
    small answer there is not a loss.
    """
    if css_selector or not content:
        return content
    answer_chars = sum(len(x) for x in content)
    body_len = len(page.body or b"")
    # 便宜的先行判据：去掉标签与空白之后的正文不会多于响应字节，所以产出已经占到
    # body 的 12% 时不可能跌破地板 —— 健康的那一路一次正则都不必付。
    if answer_chars * 100 >= body_len * 12:
        return content
    held = _document_text_chars(page)
    if held <= _MIN_DOCUMENT_CHARS or answer_chars >= held * _MAIN_CONTENT_FLOOR:
        return content
    from dhole_mcp.context import _note_text
    from dhole_mcp.trafilatura_extractor import _fallback_extract
    candidate = _fallback_extract(page, extraction_type, css_selector)
    if not candidate or sum(len(x) for x in candidate) <= answer_chars:
        return content
    _note_text(
        f"the main-content detector returned {answer_chars:,} of the {held:,} "
        f"characters this document holds ({answer_chars * 100 // held}%) - most of "
        "it read as boilerplate, so this answer comes from the plain converter; "
        "it includes page chrome the detector would have stripped")
    return candidate


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


def _is_data_document_content_type(content_type: str) -> bool:
    """JSON or XML —— "这一份是数据文档，不是一个要渲染的页面"的那一张判据表。

    两个读者共用它，因为它们是同一个事实的两面：`quality._never_escalate` 用它拒绝
    为一次浏览器渲染花钱（浏览器不渲染数据文档，它给数据文档套壳），而
    `fetcher.response_from_browser_page` 在调用方点名要走浏览器档时用它取回网络上的
    原始字节 —— 相框不该被当成正文交出去。
    """
    ct = (content_type or "").split(";")[0].strip().lower()
    return _is_xml_content_type(ct) or ct.startswith(("application/json", "text/json"))
