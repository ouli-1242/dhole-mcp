"""Trafilatura-based content extraction:

Trafilatura excels at extracting the main article content from news/blog pages,
stripping navigation, footers, sidebars, cookie banners, and other noise.
It also extracts metadata (title, author, date, url).

Extraction chain (robust fallback):
1. Try requested format (markdown/text/article/structured)
2. If empty, try trafilatura.extract() with markdown output (different heuristics)
3. If still empty, try trafilatura.bare_extraction() for metadata
4. If all Trafilatura fails, fall back to markdownify (HTML->markdown) or raw text
"""
import json
import logging
import re
from typing import Optional

import trafilatura
from lxml.etree import tostring
from lxml.html import fromstring as html_fromstring

# envelope 只有标准库依赖，不会绕回这颗模块（fetcher 会，所以那边是函数内延迟导入）。
# 塌陷的分界因此只有一份定义：抽取侧用它决定要不要换臂，判定侧用它决定"少"怎么说。
from dhole_mcp.envelope import visible_text_chars

logger = logging.getLogger("dhole_mcp.trafilatura_extractor")


def _control_char_ratio(body: bytes, declared: str = "") -> float:
    """Share of the decoded window that no text encoding would write.

    Counting *decoded characters* is the whole point. The byte-level version of
    this question ("how many bytes land in ASCII 32-126") measured "is this page
    ASCII", and so answered "binary" for every page written mostly in Chinese,
    Japanese or Korean - UTF-8 puts not one of those bytes in the printable ASCII
    range. Measured over the repo's own real files
    (`review/.runtime/p4/measure_binary_test.py`, 4,096-byte window):

        /longaside (text/html; charset=utf-8, 中文)   0.0006   <- 文本
        tests/page_fixtures/gnu_free_sw_definition    0.0000   <- 文本
        tests/dummy.pdf                               0.5226   <- 二进制
        tests/sample.docx                             0.5606   <- 二进制
        tests/figure_page.pdf                         0.2782   <- 二进制

    Two orders of magnitude between the sides, so the line sits at 0.05.
    """
    import unicodedata

    from dhole_mcp.fetcher import _decode_html_bytes

    text = _decode_html_bytes(body[:_BINARY_WINDOW_BYTES], declared or "utf-8")
    if not text:
        return 1.0
    bad = sum(1 for ch in text
              if ch == "\ufffd" or (ch not in " \t\n\r\f\v"
                                    and unicodedata.category(ch) in ("Cc", "Cf", "Cn", "Zl")))
    return bad / len(text)


#: 判据只看开头这一窗：一次判断不值把整本 PDF 解一遍。
_BINARY_WINDOW_BYTES = 4096
#: 上面那个量出来的空档里取的那条线（文本侧 <=0.001，真件侧 >=0.25）。
BINARY_CONTROL_MAX = 0.05


def _is_binary_payload(body: bytes, content_type: str = "", declared: str = "") -> bool:
    """这一份载荷是不是**不是文本**。字符集先听响应的（`declared` 是 dhole 解析过的，
    其次才是 Content-Type 里那个 `charset=`），因为「二进制」与「非 ASCII 文本」的
    区别只在解码之后才存在。
    """
    charset = (declared or "").strip()
    if not charset:
        match = re.search(r'charset\s*=\s*["\']?([\w:.-]+)', content_type or "", re.I)
        charset = match.group(1) if match else ""
    return _control_char_ratio(body, charset) > BINARY_CONTROL_MAX


def _get_html_from_page(page) -> str | None:
    """Extract raw HTML string from a Response object."""
    if hasattr(page, 'body') and page.body:
        html_bytes = page.body
        # Imported here, not at module scope: fetcher and this module reference
        # each other, and a top-level import resolves to a cycle.
        from dhole_mcp.fetcher import _decode_html_bytes
        return _decode_html_bytes(html_bytes, getattr(page, 'encoding', None) or 'utf-8')
    elif hasattr(page, 'html_content') and page.html_content:
        return page.html_content
    else:
        # Fallback: serialize the lxml tree
        root = getattr(page, '_root', None) or getattr(page, 'root', None)
        if root is not None:
            return tostring(root, encoding='unicode')
    return None


def _extract_html_title(html: str) -> str:
    """Extract the <title> tag content from HTML as a metadata fallback."""
    try:
        tree = html_fromstring(html)
        title_el = tree.find(".//title")
        if title_el is not None and title_el.text:
            title = title_el.text.strip()
            # Clean up common suffixes like " - Wikipedia", " | BBC News", etc.
            # but only if the remaining title is still meaningful
            return title
    except Exception:
        pass
    # Regex fallback
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


def _table_to_markdown_tokens(html: str) -> tuple[str, list]:
    """Swap every ``<table>`` for a placeholder word; return (html, [(token, md)]).

    trafilatura's markdown writer puts each cell on its own line, so a 3x3 table
    leaves the document as nine loose lines — the shape (which value belongs to
    which row/column) is what gets thrown away, and this same tool renders DOCX
    and XLSX tables as real GFM tables. markdownify renders the table correctly,
    but running it over the whole document would trade trafilatura's boilerplate
    filtering for nav noise. So: take the tables out, let trafilatura do what it
    is good at with the prose, and put each finished table back where it was.

    Returns the input unchanged when the swap cannot be done (no lxml / no
    markdownify / malformed markup / no tables) — the flattened-lines behaviour
    is the fallback, not a regression.
    """
    if "<table" not in html.lower():
        return html, []
    try:
        from lxml import html as _lh
        from markdownify import markdownify as _md
    except ImportError:
        return html, []
    try:
        doc = _lh.fromstring(html)
        tables = doc.xpath("//table")
    except Exception:
        return html, []
    if not tables:
        return html, []
    import secrets

    out: list = []
    for n, tbl in enumerate(tables):
        try:
            rendered = _md(_lh.tostring(tbl).decode("utf-8", "replace"),
                           heading_style="ATX", strip=["img"]).strip()
        except Exception:
            continue
        if "|" not in rendered:
            continue  # nothing table-shaped came out; leave the node alone
        token = f"DHOLE-TBL-{n}-{secrets.token_hex(3)}"
        holder = _lh.Element("p")
        holder.text = token
        parent = tbl.getparent()
        if parent is None:
            continue
        parent.insert(parent.index(tbl), holder)
        parent.remove(tbl)
        out.append((token, rendered))
    if not out:
        return html, []
    try:
        return _lh.tostring(doc, encoding="unicode"), out
    except Exception:
        return html, []


def _restore_table_markdown(text: str, tokens: list) -> str | None:
    """Put each rendered table back at its placeholder. None = a placeholder did
    not survive extraction, so the caller must fall back rather than silently
    lose a table it had a copy of."""
    if not tokens:
        return text
    import re as _re

    for token, table in tokens:
        hit = _re.search(r"[ \t]*" + _re.escape(token) + r"[ \t]*\n?", text)
        if not hit:
            return None
        text = text[:hit.start()] + table + "\n" + text[hit.end():]
    return text


def _trafilatura_extract(html: str, url: str, *, favor_recall: bool = False) -> str | None:
    """这一颗旋钮的每一次 trafilatura 调用都长这样，只有一处写法。

    三处各写一遍参数表，就是下一次"某一臂忘了 `include_links`"的来路 —— 而塌陷重试与
    默认臂之间的差别**只有** `favor_recall` 这一个旗。
    """
    return trafilatura.extract(
        html, url=url, include_comments=False, include_tables=True,
        include_links=True, output_format="markdown", favor_recall=favor_recall,
    )


def _visible_text_chars(html: str) -> int:
    """这一页自己有多少可见字符 —— 尺子坏了就当没塌陷，不许把抽取一起弄坏。"""
    try:
        return visible_text_chars(html)
    except Exception:  # noqa: BLE001
        return 0


# ─── 块覆盖：两条臂谁盖住的块多 ────────────────────────────────────────────
#: 重试带：抽回来的字数低于整页可见字的这个比值就去试宽内容选择那一趟（`favor_recall`）。
#: 0.90 是量出来的（`review/.runtime/p4/sample_ratio_band.py`，119 份页面 = 111 份存档真
#: SERP + gnu.org 定义页 + quotes.toscrape 首页 + books.toscrape travel 页 + 5 份缺陷靶页）：
#: 宽臂按块覆盖**严格占优**的页面共 12 份，它们的比值全在 0.13–0.75；比值 >= 0.90 的一份
#: 都没有（12/12）。0.50 那条塌陷线（`envelope.EXTRACT_COLLAPSE_RATIO`）**一字未动** ——
#: `quality` 拿它判 JS 壳，放宽它等于让浏览器档去背抽取的债；这里加的是一条只作用于
#: 「要不要再抽一趟」的重试带。
EXTRACT_RECALL_BAND = 0.90

#: 叶子块：读者一次读到的那一个单元。`<li>` 是一个块，装着它的 `<ul>` 不再算第二次 ——
#: 按容器算会把「12 条丢了 9 条」读成「一块没丢」，而本缺陷现场正是整块消失。
_LEAF_BLOCK_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "dt", "dd",
                    "td", "th", "blockquote", "pre", "caption")
#: 按标签剥的套壳。**明知**这一剥不干净：books.toscrape 把全部 11 本书装在
#: `<aside class="sidebar">` 里（见 crawl.py `_item_links` 记的那笔账），标签与类名在
#: 一页之内分不出「菜单」与「列表」。这把尺敢用这个剥法，是因为它只用于**同一页上两条
#: 抽取臂互相比较**：块集合与剥法两边同分，误差对消；它从不单独回答「这页丢了内容」。
_CHROME_BLOCK_TAGS = frozenset(("nav", "header", "footer", "aside", "script",
                                "style", "noscript", "form"))


def visible_leaf_blocks(html: str) -> list:
    """``[(tag, 归一化文本), …]`` —— 这一页自己写出来的非套壳叶子块。

    量不到（没有 lxml、markup 坏了、传进来的不是字符串）就交回空表：空表意味着两条臂
    打平，而打平保留默认臂 —— 判据失灵时不许换掉任何一次成功的抽取（同款 fail closed
    见 `extractor._document_text_chars`）。
    """
    try:
        doc = html_fromstring(html)
        out = []
        for el in doc.iter(*_LEAF_BLOCK_TAGS):
            if el.tag not in _LEAF_BLOCK_TAGS:
                continue
            if any(a.tag in _LEAF_BLOCK_TAGS for a in el.iter() if a is not el):
                continue  # 有嵌套块时以叶子里那一个为单元
            text = " ".join((el.text_content() or "").split())
            if not text:
                continue
            if any((a.tag in _CHROME_BLOCK_TAGS) for a in el.iterancestors()):
                continue
            out.append((el.tag, text))
        return out
    except Exception:  # noqa: BLE001
        return []


def _block_survives(text: str, answer: str) -> bool:
    """这一块的字有没有留在答复里（至少一处四字连串对得上）。

    取四处采样而不是逐词全比：markdown 会改写行首与强调，一处都对不上才是整块没了。
    """
    words = text.split()
    if len(words) < 4:
        return text in answer
    step = max(1, (len(words) - 3) // 4)
    return any(" ".join(words[i:i + 4]) in answer
               for i in range(0, len(words) - 3, step))


def omitted_body_chars(html: str, answer: str) -> int:
    """答复整块漏掉的页面自身字符数（越小越好；0 = 一块没漏）。"""
    ans = " ".join((answer or "").split())
    return sum(len(text) for _tag, text in visible_leaf_blocks(html)
               if not _block_survives(text, ans))


def _wants_a_wider_pass(extracted_chars: int, visible_chars: int) -> bool:
    """要不要再抽一趟：比值落在重试带之下。

    可见字少于塌陷下限（`EXTRACT_COLLAPSE_MIN_VISIBLE`）的页面一律不重试 —— 那是塌陷
    那条线本来就划给小页的空档，这一刀不扩大它。
    """
    from dhole_mcp.envelope import EXTRACT_COLLAPSE_MIN_VISIBLE

    if visible_chars < EXTRACT_COLLAPSE_MIN_VISIBLE:
        return False
    return extracted_chars < visible_chars * EXTRACT_RECALL_BAND


def _trafilatura_markdown(html: str, url: str = "") -> str | None:
    """Best-effort markdown extraction using trafilatura.extract().

    Strategy: trafilatura identifies main content (filtering nav/ads), then
    markdownify converts to markdown preserving [text](url) links. This hybrid
    gives trafilatura's content quality + markdownify's link preservation.

    Tables ride a placeholder round-trip (see _table_to_markdown_tokens) because
    trafilatura flattens them; when the placeholders do not come back, the
    untaken route is used instead of returning text that lost a table.
    """
    html_for_traf, tokens = _table_to_markdown_tokens(html)
    # First: try trafilatura's native markdown (fast, good content filtering)
    result = _trafilatura_extract(html_for_traf, url)
    if result and tokens:
        restored = _restore_table_markdown(result, tokens)
        if restored is not None:
            result = restored
        else:
            # A table would have been dropped: redo the extraction from the
            # original markup so the page at least keeps its flattened cells.
            result = _trafilatura_extract(html, url) or result
            tokens = []
    visible = _visible_text_chars(html)
    if _wants_a_wider_pass(len(result or ""), visible):
        # 默认的内容选择把整棵 `<h3><a>` 子树丢掉了，`include_links=True` 在这种页面上
        # 救不回来（它只在正文已被选中之后负责保链接）。换臂重试，而不是换工具：塌陷的是
        # 抽取的宽严，不是页面。
        recalled = _trafilatura_extract(html_for_traf, url, favor_recall=True)
        if recalled and tokens:
            recalled = _restore_table_markdown(recalled, tokens) or recalled
        # 选哪一趟看的是**块覆盖**，不是字数：默认臂整块漏掉页面自己的字、宽臂把它们
        # 盖回来了，才换；只是更长而一块也没多盖住的不换。这两条规则在真语料上方向
        # 相反过：存档的 `evals/corpus/serp/q041__baidu.html` 宽臂更长（745 对 518 字符）
        # 而整块漏掉的字符更多（141 对 58）—— 按字数换会把答复换成一个更长而更空的文本。
        # 空答复是这条规则自己开出来的口子：一页若没有非套壳的叶子块，两边覆盖打平，
        # 那时"有字"必须赢过"没字"。
        if recalled:
            kept_gone = omitted_body_chars(html, recalled)
            default_gone = omitted_body_chars(html, result)
            if not result or kept_gone < default_gone:
                logger.debug("content selection left out %d chars of this page's "
                             "own blocks - kept the recall pass (%d over %d chars)",
                             default_gone, len(recalled), len(result or ""))
                result = recalled
    # If trafilatura produced content WITH links, use it directly
    if result and "](" in result:
        return result
    # If trafilatura produced content but WITHOUT links, try markdownify
    # on the main content region to preserve link references.
    if result:
        try:
            from markdownify import markdownify as _md
            import re as _re
            # Strip nav/header/footer/aside/script/style before markdownify
            # to avoid navigation link noise.
            _strip_re = _re.compile(
                r'<(nav|header|footer|aside|script|style|noscript)\b[^>]*>.*?</\1>',
                _re.IGNORECASE | _re.DOTALL,
            )
            cleaned = _strip_re.sub('', html)
            md_with_links = _md(cleaned, heading_style="ATX", strip=['img'])
            # Only use markdownify version if it has links and isn't too bloated
            # (max 3x the trafilatura text length to avoid nav noise).
            #
            # 那个 3× 假设的是"trafilatura 的输出≈完整正文"。塌陷时这个前提**正好**不
            # 成立（实测 196 vs 恢复出的 2,406），所以比例闸会把唯一一份带书名的文本当
            # 灌水丢掉。因此比例闸下面垫一道绝对下限：只要这份恢复文本没有明显超过页面
            # 自己可见文本的两倍，它就还不是导航灌水。
            bloat_cap = max(len(result) * 3, visible * 2)
            if "](" in md_with_links and len(md_with_links) < bloat_cap:
                return md_with_links.strip()
        except Exception:
            pass
        return result
    return None


def _trafilatura_article(html: str, url: str = "") -> dict | None:
    """Extract article as dict using bare_extraction.
    Falls back to HTML <title> tag if Trafilatura can't find a title.
    Returns None if extraction fails.
    """
    result = trafilatura.bare_extraction(
        html, url=url,
        include_comments=False, include_tables=True,
    )
    if result is None:
        return None

    title = getattr(result, "title", "") or ""
    # Fallback: try HTML <title> if Trafilatura couldn't find one
    if not title:
        title = _extract_html_title(html)

    return {
        "title": title,
        "author": getattr(result, "author", "") or "",
        "date": getattr(result, "date", "") or "",
        "body": getattr(result, "text", "") or "",
        "description": getattr(result, "description", "") or "",
        "url": getattr(result, "url", "") or url,
        "categories": getattr(result, "categories", None) or [],
        "tags": getattr(result, "tags", None) or [],
    }


def _trafilatura_structured(html: str, url: str = "") -> dict | None:
    """Extract structured article data with metadata."""
    article = _trafilatura_article(html, url)
    if article is None:
        return None

    # Enrich with trafilatura metadata
    try:
        metadata = trafilatura.metadata(html, url=url) if html else None
        if metadata:
            article["sitename"] = getattr(metadata, "sitename", "")
            # Don't overwrite article's own categories/tags if they exist
            if not article["categories"]:
                article["categories"] = getattr(metadata, "categories", []) or []
            if not article["tags"]:
                article["tags"] = getattr(metadata, "tags", []) or []
    except Exception as e:
        logger.debug(f"Metadata extraction failed: {e}")

    return article


def _extract_type(html: str, url: str, extraction_type: str) -> str | None:
    """Extract content in the requested format with robust fallback chain.

    For 'article' and 'structured': tries bare_extraction first, then
    falls back to extract() markdown and wraps it in the expected format.
    For 'markdown' and 'text': tries extract() first, then bare_extraction.
    """
    if extraction_type == "markdown":
        # Primary: trafilatura.extract() (best for markdown)
        result = _trafilatura_markdown(html, url)
        if result:
            return result
        # Fallback: bare_extraction text wrapped in heading
        article = _trafilatura_article(html, url)
        if article and article["body"]:
            title = article.get("title", "")
            return f"# {title}\n\n{article['body']}" if title else article["body"]
        return None

    elif extraction_type == "text":
        # Primary: bare_extraction for plain text
        article = _trafilatura_article(html, url)
        if article and article["body"]:
            return article["body"]
        # Fallback: markdown then strip
        md = _trafilatura_markdown(html, url)
        return md if md else None

    elif extraction_type == "article":
        # Primary: bare_extraction as JSON
        article = _trafilatura_article(html, url)
        if article and article["body"]:
            return json.dumps(article, indent=2)
        # Fallback: markdown extraction, wrap as article JSON
        md = _trafilatura_markdown(html, url)
        if md:
            # Try to get at least a title from HTML <title> tag
            html_title = _extract_html_title(html)
            return json.dumps({
                "title": html_title, "author": "", "date": "",
                "body": md, "description": "", "url": url,
                "categories": [], "tags": [],
            }, indent=2)
        return None

    elif extraction_type == "structured":
        # Primary: structured extraction with metadata
        data = _trafilatura_structured(html, url)
        if data and data.get("body"):
            return json.dumps(data, indent=2)
        # Fallback: markdown extraction, wrap as structured JSON
        md = _trafilatura_markdown(html, url)
        if md:
            # Try to get title from bare_extraction or HTML <title>
            article = _trafilatura_article(html, url)
            title = article.get("title", "") if article else ""
            if not title:
                title = _extract_html_title(html)
            return json.dumps({
                "title": title, "author": "", "date": "",
                "body": md, "description": "", "url": url,
                "sitename": "", "categories": [], "tags": [],
            }, indent=2)
        return None

    else:  # html or unknown: Trafilatura doesn't do HTML
        return None


def _fallback_extract(page, extraction_type: str, css_selector: Optional[str]) -> list[str]:
    """Fallback extraction: markdownify for markdown/html, raw text for text type.

    Replaces scrapling's Convertor._extract_content with direct markdownify
    and lxml usage. No scrapling dependency.
    """
    # Get raw HTML
    html = getattr(page, 'content', '') or ''
    if not html and getattr(page, 'body', None):
        html = page.body.decode(
            getattr(page, 'encoding', None) or 'utf-8', errors='replace'
        )
    if not html:
        return [""]

    # CSS selector narrowing
    if css_selector:
        try:
            from lxml import html as lxml_html
            from lxml.etree import tostring
            tree = lxml_html.fromstring(html)
            from lxml.cssselect import CSSSelector
            sel = CSSSelector(css_selector)
            matches = sel(tree)
            if matches:
                html = '\n'.join(
                    tostring(m, encoding='unicode') for m in matches
                )
        except Exception as e:
            logger.debug(f"CSS selector '{css_selector}' failed: {e}")
            _note_selector_not_applied(css_selector, e)

    # Map extended types to what we can handle
    if extraction_type == "html":
        return [html]
    if extraction_type == "text":
        import re
        # Strip tags for plain text
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<noscript[^>]*>.*?</noscript>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return [text] if text else [html]
    # markdown, article, structured: use markdownify
    try:
        from markdownify import markdownify
        # data: URIs are bytes, not information - an image-heavy page came back
        # as tens of KB of base64 inside the markdown (the media field already
        # skips data: assets; same verdict here). Strip before conversion; the
        # html/text types above are the caller asking for raw bytes, untouched.
        # (`re` is a function-local import in the text branch above, so this
        # path binds its own alias instead of racing that UnboundLocalError.)
        import re as _re
        html = _re.sub(
            r'<img[^>]*\ssrc=["\']data:[^"\']*["\'][^>]*>', '', html,
            flags=_re.IGNORECASE)
        result = markdownify(html)
        logger.info(f"Falling back to markdownify extraction (type={extraction_type})")
        return [result] if result else [html]
    except ImportError:
        # markdownify not installed: return raw HTML
        return [html]


def _note_selector_not_applied(selector: str, err: Exception) -> None:
    """提取期真出事时给调用方留一句真话（同一个选择器一次调用只说一遍）。

    可编译性已经前移到校验层（`security.validate_html_css_selector`），这里剩下的
    是测不出的边角：lxml 在**这份文档**上匹配时抛的异常。静默回落到整页是评审现场
    那类"看起来成功、其实参数没生效"，措辞与 XML content-type 那条同款（was NOT
    applied），因为对调用方来说两件事一模一样：选择器没起作用。
    """
    from dhole_mcp.context import _ARG_NOTES, _note_text

    head = f"css_selector '{selector}' was NOT applied"
    notes = _ARG_NOTES.get()
    if notes is not None and any(n.startswith(head) for n in notes):
        return
    _note_text(f"{head}: the extraction engine raised while matching it "
               f"({str(err)[:120]}) - the whole document was returned")


def _narrow_html_to_selector(html: str, css_selector: str) -> Optional[str]:
    """Return the markup of every node matching ``css_selector``, else None.

    Works on the raw HTML string with lxml + CSSSelector, because the fetchers'
    Response objects do not expose a .css() API. The old code gated on
    ``hasattr(page, 'css')``, silently got False, and fell through to whole-page
    extraction — which is why css_selector appeared to work only with
    extraction_type='html' (that path narrows separately in _fallback_extract).
    """
    try:
        from lxml import html as lxml_html
        from lxml.cssselect import CSSSelector
        tree = lxml_html.fromstring(html)
        matches = CSSSelector(css_selector)(tree)
    except Exception as e:
        logger.debug(f"CSS selector '{css_selector}' failed: {e}")
        _note_selector_not_applied(css_selector, e)
        return None
    if not matches:
        return None
    # The fragments are re-wrapped as a document on purpose: trafilatura returns
    # nothing for a bare fragment (`_extract_type("<h1>X</h1>", ...) -> None`),
    # which made the caller fall back to the WHOLE page — the exact silent no-op
    # this function exists to fix.
    return "<html><body>" + "\n".join(
        tostring(m, encoding="unicode") for m in matches
    ) + "</body></html>"


def extract_content_from_html(html: str, url: str = "", extraction_type: str = "markdown") -> str | None:
    """Extract content from a raw HTML string (used by web_crawl, which fetches
    pages as html once and derives both links and markdown from the same body)."""
    return _extract_type(html, url, extraction_type)


def extract_html_title(html: str) -> str:
    """Public wrapper for the HTML <title> fallback extractor."""
    return _extract_html_title(html)


def extract_with_trafilatura(
    page,  # Response object (dhole_mcp.fetcher.Response or compatible)
    extraction_type: str = "markdown",
    css_selector: Optional[str] = None,
) -> list[str]:
    """Use Trafilatura on a Response object.

    Robust multi-stage extraction chain to maximize content quality.
    Falls back to markdownify only if ALL Trafilatura methods fail.
    """
    try:
        # Check for binary content before decoding
        raw = getattr(page, 'body', None) or getattr(page, 'content', None)
        if raw:
            raw_bytes = raw if isinstance(raw, bytes) else str(raw).encode('latin-1', errors='replace')
            if _is_binary_payload(raw_bytes,
                                  declared=getattr(page, 'encoding', '') or '',
                                  content_type=(getattr(page, 'headers', None) or {}).get(
                                      'content-type', '')):
                return [f"[Binary content detected. Cannot extract text from this URL. Content type may be PDF, image, or other non-text format. Size: {len(raw_bytes):,} bytes]"]

        html = _get_html_from_page(page)
        if html is None:
            logger.warning("Cannot extract HTML from page object, falling back to markdownify")
            return _fallback_extract(page, extraction_type, css_selector)

        page_url = page.url if hasattr(page, 'url') else ""

        # If css_selector specified, narrow the HTML first (all extraction
        # types, not just html — see _narrow_html_to_selector).
        if css_selector:
            narrowed = _narrow_html_to_selector(html, css_selector)
            if narrowed:
                part = _extract_type(narrowed, page_url, extraction_type)
                if part:
                    return [part]
            # CSS selector found nothing usable: try full page instead
            logger.info(f"CSS selector '{css_selector}' found nothing, trying full page extraction")

        result = _extract_type(html, page_url, extraction_type)
        if result:
            return [result]

        # Even _extract_type returned None: try one more time with markdown
        # regardless of requested type, just to get SOME clean content
        if extraction_type != "markdown":
            md = _trafilatura_markdown(html, page_url)
            if md:
                logger.info(f"Requested type '{extraction_type}' failed, returning markdown fallback")
                return [md]

        # Total failure: fall back to markdownify
        logger.warning(f"All Trafilatura methods failed for {page_url}, falling back to markdownify")
        return _fallback_extract(page, extraction_type, css_selector)

    except Exception as e:
        logger.warning(f"Trafilatura extraction crashed: {e}, falling back to markdownify")
        return _fallback_extract(page, extraction_type, css_selector)
