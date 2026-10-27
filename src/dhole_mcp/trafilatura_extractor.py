"""基于 trafilatura 的正文抽取：

Trafilatura 擅长从新闻/博客页面抽出文章主体，剥掉导航、页脚、侧栏、cookie 横幅和
其他噪声。它也能抽元数据（标题、作者、日期、url）。

抽取链（稳健兜底）：
1. 尝试请求的格式（markdown/text/article/structured）
2. 为空则用 markdown 输出再调一次 trafilatura.extract()（不同的启发式）
3. 仍为空则调 trafilatura.bare_extraction() 取元数据
4. Trafilatura 全失败时，回退 markdownify（HTML->markdown）或原始文本
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
    """解码窗口里有多少字符是任何文本编码都不会写出来的。

    数**解码后的字符**才是重点。这个问题的字节级版本（"多少字节落在 ASCII 32-126"）
    量出来的是"这页是不是 ASCII"，于是给每一页以中文、日文或韩文为主的页面都判
    "二进制" —— UTF-8 不会把那些字的任何一个字节放进可打印 ASCII 区间。在本仓库
    自己的真实文件上实测（`review/.runtime/p4/measure_binary_test.py`，4,096 字节
    窗口）：

        /longaside (text/html; charset=utf-8, 中文)   0.0006   <- 文本
        tests/page_fixtures/gnu_free_sw_definition    0.0000   <- 文本
        tests/dummy.pdf                               0.5226   <- 二进制
        tests/sample.docx                             0.5606   <- 二进制
        tests/figure_page.pdf                         0.2782   <- 二进制

    两侧差两个数量级，所以线画在 0.05。
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
    """从 Response 对象抽出原始 HTML 字符串。"""
    if hasattr(page, 'body') and page.body:
        html_bytes = page.body
        # 在这里导入，不在模块顶层：fetcher 与本模块互相引用，
        # 顶层导入会解析成循环。
        from dhole_mcp.fetcher import _decode_html_bytes
        return _decode_html_bytes(html_bytes, getattr(page, 'encoding', None) or 'utf-8')
    elif hasattr(page, 'html_content') and page.html_content:
        return page.html_content
    else:
        # 兜底：序列化 lxml 树
        root = getattr(page, '_root', None) or getattr(page, 'root', None)
        if root is not None:
            return tostring(root, encoding='unicode')
    return None


def _extract_html_title(html: str) -> str:
    """从 HTML 抽 <title> 标签内容，作为元数据的兜底。"""
    try:
        tree = html_fromstring(html)
        title_el = tree.find(".//title")
        if title_el is not None and title_el.text:
            title = title_el.text.strip()
            # 清理常见后缀，如 " - Wikipedia"、" | BBC News" 等，
            # 但只在剩下的标题仍有意义时才做
            return title
    except Exception:
        pass
    # 正则兜底
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


def _table_to_markdown_tokens(html: str) -> tuple[str, list]:
    """把每个 ``<table>`` 换成一个占位词；返回 (html, [(token, md)])。

    trafilatura 的 markdown writer 把每个单元格放在单独一行，一张 3x3 的表在文档里
    就剩九条散行 —— 被丢掉的是形状（哪个值属于哪行哪列），而同一个工具会把 DOCX
    与 XLSX 的表格渲染成真正的 GFM 表格。markdownify 能正确渲染表格，但对整篇文档
    跑它等于用导航噪声换掉 trafilatura 的样板过滤。所以：把表格取出来，让 trafilatura
    干它擅长的散文，再把每张渲染好的表放回原处。

    换不动时原样返回输入（没有 lxml / 没有 markdownify / 畸形标记 / 没有表格）——
    散行行为是兜底，不是回归。
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
            continue  # 没渲染出表格形状；别动这个节点
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
    """把每张渲染好的表放回占位符处。None = 有占位符没活过抽取，调用方必须回退，
    而不是悄悄丢掉一份手里已有副本的表。"""
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
    """尽力而为的 markdown 抽取，使用 trafilatura.extract()。

    策略：trafilatura 认出主内容（过滤导航/广告），再由 markdownify 转成保留
    [text](url) 链接的 markdown。这个混合拿到 trafilatura 的内容质量 +
    markdownify 的链接保留。

    表格走一次占位符往返（见 _table_to_markdown_tokens），因为 trafilatura 会把它们
    拍平；占位符回不来时走未取用的那条路线，而不是交回一份丢了表格的文本。
    """
    html_for_traf, tokens = _table_to_markdown_tokens(html)
    # 先试 trafilatura 原生的 markdown（快，内容过滤好）
    result = _trafilatura_extract(html_for_traf, url)
    if result and tokens:
        restored = _restore_table_markdown(result, tokens)
        if restored is not None:
            result = restored
        else:
            # 一张表会被丢掉：从原始标记重做抽取，至少让页面保住它那些散开的单元格。
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
    # trafilatura 产出了带链接的内容，直接用
    if result and "](" in result:
        return result
    # trafilatura 产出了内容但没有链接，试着对主内容区跑 markdownify
    # 保留链接引用。
    if result:
        try:
            from markdownify import markdownify as _md
            import re as _re
            # markdownify 之前剥掉 nav/header/footer/aside/script/style，
            # 避免导航链接噪声。
            _strip_re = _re.compile(
                r'<(nav|header|footer|aside|script|style|noscript)\b[^>]*>.*?</\1>',
                _re.IGNORECASE | _re.DOTALL,
            )
            cleaned = _strip_re.sub('', html)
            md_with_links = _md(cleaned, heading_style="ATX", strip=['img'])
            # 只有在 markdownify 版本带链接、且不算太臃肿时才用它
            # （最多是 trafilatura 文本长的 3 倍，避免导航噪声）。
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
    """用 bare_extraction 把文章抽成 dict。
    Trafilatura 找不到标题时回退到 HTML <title> 标签。
    抽取失败时返回 None。
    """
    result = trafilatura.bare_extraction(
        html, url=url,
        include_comments=False, include_tables=True,
    )
    if result is None:
        return None

    title = getattr(result, "title", "") or ""
    # 兜底：Trafilatura 找不到标题时试 HTML <title>
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
    """抽取带元数据的结构化文章数据。"""
    article = _trafilatura_article(html, url)
    if article is None:
        return None

    # 用 trafilatura 元数据补充
    try:
        metadata = trafilatura.metadata(html, url=url) if html else None
        if metadata:
            article["sitename"] = getattr(metadata, "sitename", "")
            # 文章自带的 categories/tags 存在时不要覆盖
            if not article["categories"]:
                article["categories"] = getattr(metadata, "categories", []) or []
            if not article["tags"]:
                article["tags"] = getattr(metadata, "tags", []) or []
    except Exception as e:
        logger.debug(f"Metadata extraction failed: {e}")

    return article


def _extract_type(html: str, url: str, extraction_type: str) -> str | None:
    """按请求的格式抽取内容，带稳健的兜底链。

    'article' 与 'structured'：先试 bare_extraction，再回退 extract() 的 markdown
    并包进预期格式。'markdown' 与 'text'：先试 extract()，再试 bare_extraction。
    """
    if extraction_type == "markdown":
        # 主路：trafilatura.extract()（markdown 最佳）
        result = _trafilatura_markdown(html, url)
        if result:
            return result
        # 兜底：bare_extraction 的文本包进标题
        article = _trafilatura_article(html, url)
        if article and article["body"]:
            title = article.get("title", "")
            return f"# {title}\n\n{article['body']}" if title else article["body"]
        return None

    elif extraction_type == "text":
        # 主路：bare_extraction 取纯文本
        article = _trafilatura_article(html, url)
        if article and article["body"]:
            return article["body"]
        # 兜底：markdown，再剥标签
        md = _trafilatura_markdown(html, url)
        return md if md else None

    elif extraction_type == "article":
        # 主路：bare_extraction 的 JSON
        article = _trafilatura_article(html, url)
        if article and article["body"]:
            return json.dumps(article, indent=2)
        # 兜底：markdown 抽取，包成 article JSON
        md = _trafilatura_markdown(html, url)
        if md:
            # 至少从 HTML <title> 标签拿一个标题
            html_title = _extract_html_title(html)
            return json.dumps({
                "title": html_title, "author": "", "date": "",
                "body": md, "description": "", "url": url,
                "categories": [], "tags": [],
            }, indent=2)
        return None

    elif extraction_type == "structured":
        # 主路：带元数据的结构化抽取
        data = _trafilatura_structured(html, url)
        if data and data.get("body"):
            return json.dumps(data, indent=2)
        # 兜底：markdown 抽取，包成 structured JSON
        md = _trafilatura_markdown(html, url)
        if md:
            # 从 bare_extraction 或 HTML <title> 取标题
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

    else:  # html 或未知：Trafilatura 不做 HTML
        return None


def plain_markdown_from_html(html: str) -> str:
    """纯转换器：对原始标记跑 markdownify，剥掉 `data:` 图片。

    Trafilatura 的主内容检测器会按"这是不是正文"删东西；当它删过头（塌陷）时，
    补救要的正是**不判断**的那条路 —— 整份标记照单转成 markdown。同一个原语有两个
    入口：读路径手里是 page 对象（`_fallback_extract`），爬取路径手里只有 html 字符串
    （`crawl._classify_and_extract` 的坍缩补救），两条路必须交同一种东西，否则同一份
    字节在两个工具里又是两种正文。

    `data:` 图片在这里剥掉：一个图片重的页面曾让 markdown 里塞进几十 KB base64，
    而 `media` 字段本来就跳过它们 —— 同一件事两处不同判是这一族的旧账。
    """
    import re as _re

    from markdownify import markdownify
    html = _re.sub(
        r'<img[^>]*\ssrc=["\']data:[^"\']*["\'][^>]*>', '', html,
        flags=_re.IGNORECASE)
    return markdownify(html) or ""


def _fallback_extract(page, extraction_type: str, css_selector: Optional[str]) -> list[str]:
    """兜底抽取：markdown/html 走 markdownify，text 类型走原始文本。

    用直接的 markdownify 与 lxml 取代 scrapling 的 Convertor._extract_content。
    不依赖 scrapling。
    """
    # 取原始 HTML
    html = getattr(page, 'content', '') or ''
    if not html and getattr(page, 'body', None):
        html = page.body.decode(
            getattr(page, 'encoding', None) or 'utf-8', errors='replace'
        )
    if not html:
        return [""]

    # CSS 选择器收窄
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
            else:
                # 与 `_narrow_html_to_selector` 的 R06-001 同一条规矩：编译通过、
                # 但这份文档里 0 匹配时回落整页是设计（缩取从不掏空答案），不说
                # 出来就不是。实测 2026-10-27：`extraction_type='html'` +
                # `css_selector='h1'` 抓 example.com（该页已无 h1），577 字节整页
                # 回来、content_ok=true、summary 里没有一个字提选择器没匹配 —— 而
                # 同一次调用换成默认 markdown 就会说。缺口只在 html/text 这条
                # 兜底路径上，因为它自己 inline 收窄、没走 _narrow_html_to_selector。
                _note_selector_matched_nothing(css_selector)
        except Exception as e:
            logger.debug(f"CSS selector '{css_selector}' failed: {e}")
            _note_selector_not_applied(css_selector, e)

    # 把扩展类型映射到能处理的形状
    if extraction_type == "html":
        return [html]
    if extraction_type == "text":
        import re
        # 剥标签取纯文本
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<noscript[^>]*>.*?</noscript>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        # 转不出字就是转不出字。这里从前交回的是 `[html]` —— 整页标记当"text"，
        # 而调用方拿到的 `content` 以 `<!DOCTYPE html>` 打头、`extracted_chars` 还
        # 因此虚高，壳闸看见"字很多"于是不升级。实测 2026-10-24：
        # `www.bilibili.com/v/popular/rank/all` 的 HTTP 档回一张 4,927 字节的灰页
        # （`window._GreyResult`），markdownify 对它产出 0 字符，这条路把 4,376 个
        # 标记字符当正文交回，`content_ok=true`、`substance=ok`，而同一 URL 走
        # stealthy 档拿到的是真实榜单（11,946 字符）。空答案才是诚实的答案：它让
        # `quality._is_js_shell` 看见"200 + 无正文"，阶梯才有机会升级。
        return [text]
    # markdown、article、structured：用 markdownify
    try:
        result = plain_markdown_from_html(html)
        logger.info(f"Falling back to markdownify extraction (type={extraction_type})")
        return [result]
    except ImportError:
        # markdownify 不在（安装不完整）：同样不交原始标记。这条路上"有内容"是假的，
        # 而假的内容比没有内容更贵 —— 调用方会把它当正文引用。缺依赖由 `dhole doctor` 报。
        logger.warning("markdownify is not installed; returning no content for %s",
                       getattr(page, "url", ""))
        return [""]


def _note_selector_matched_nothing(selector: str) -> None:
    """R06-001：选择器可编译、文档也读了，就是 0 个匹配——回落整页要说出去。

    与 `_note_selector_not_applied` 同一句式（was NOT applied 的诚实形状）：对
    调用方两件事一样——选择器没起作用。缩取是可选收窄、从不掏空答案，所以
    行为不变，变的只是这一句话在不在。
    """
    from dhole_mcp.context import _ARG_NOTES, _note_text

    head = f"css_selector '{selector}' was NOT applied"
    notes = _ARG_NOTES.get()
    if notes is not None and any(n.startswith(head) for n in notes):
        return
    _note_text(f"{head}: it matched 0 elements on this document - the whole "
               f"document was returned (narrowing never empties the answer)")


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
    """返回匹配 ``css_selector`` 的每个节点的标记，没有则 None。

    直接在原始 HTML 字符串上用 lxml + CSSSelector，因为抓取器的 Response 对象不暴露
    .css() API。旧代码以 ``hasattr(page, 'css')`` 为前提，静默得到 False，然后落进
    整页抽取 —— 这就是 css_selector 看起来只在 extraction_type='html' 下生效的原因
    （那条路径在 _fallback_extract 里单独收窄）。
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
        # R06-001：编译通过但 0 匹配——回落到整页是设计（缩取从不掏空答案），
        # 但静默不说就是「看起来成功、其实参数没生效」（round-06 P01 现场：整页
        # 269 字符 content_ok=true 出门，没有一个字提选择器没匹配）。与编译失败
        # 分支（_note_selector_not_applied）同款诚实：把后果说出去。
        _note_selector_matched_nothing(css_selector)
        return None
    # 刻意把这些片段重新包成一份文档：trafilatura 对裸片段返回空
    # （`_extract_type("<h1>X</h1>", ...) -> None`），于是调用方会回退到**整页** ——
    # 正是这个函数存在要修的那种静默空操作。
    return "<html><body>" + "\n".join(
        tostring(m, encoding="unicode") for m in matches
    ) + "</body></html>"


def extract_content_from_html(html: str, url: str = "", extraction_type: str = "markdown") -> str | None:
    """从原始 HTML 字符串抽取内容（web_crawl 用：它把页面抓成 html 一次，再
    从同一份 body 同时派生链接与 markdown）。"""
    return _extract_type(html, url, extraction_type)


def extract_html_title(html: str) -> str:
    """HTML <title> 兜底抽取器的公开包装。"""
    return _extract_html_title(html)


def extract_with_trafilatura(
    page,  # Response 对象（dhole_mcp.fetcher.Response 或兼容对象）
    extraction_type: str = "markdown",
    css_selector: Optional[str] = None,
) -> list[str]:
    """对 Response 对象使用 Trafilatura。

    多级稳健抽取链，最大化内容质量。只有所有 Trafilatura 方法都失败时才回退
    markdownify。
    """
    try:
        # 解码前先检查二进制内容
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

        # 指定了 css_selector 就先收窄 HTML（所有抽取类型，不只是 html ——
        # 见 _narrow_html_to_selector）。
        if css_selector:
            narrowed = _narrow_html_to_selector(html, css_selector)
            if narrowed:
                part = _extract_type(narrowed, page_url, extraction_type)
                if part:
                    return [part]
            # CSS 选择器没找到可用内容：改试整页
            logger.info(f"CSS selector '{css_selector}' found nothing, trying full page extraction")

        result = _extract_type(html, page_url, extraction_type)
        if result:
            return [result]

        # _extract_type 也返回 None：不管请求的类型，再用 markdown 试一次，
        # 只为拿到**一些**干净内容
        if extraction_type != "markdown":
            md = _trafilatura_markdown(html, page_url)
            if md:
                logger.info(f"Requested type '{extraction_type}' failed, returning markdown fallback")
                return [md]

        # 彻底失败：回退到 markdownify
        logger.warning(f"All Trafilatura methods failed for {page_url}, falling back to markdownify")
        return _fallback_extract(page, extraction_type, css_selector)

    except Exception as e:
        logger.warning(f"Trafilatura extraction crashed: {e}, falling back to markdownify")
        return _fallback_extract(page, extraction_type, css_selector)
