"""Dhole 自己的正文抽取。

取代 scrapling 的 Convertor._extract_content，直接用 trafilatura + markdownify +
lxml。抽取链：

1. Trafilatura（主路，用于 markdown/text/article/structured）
2. markdownify（markdown/html 类型的兜底）
3. 原始文本（最后手段：正则剥标签）

CSS 选择器收窄直接用 lxml。
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
    """HTML 自身持有的非空白字符数，去掉标记与脚本/样式壳。

    "这份文档里有多少可读文本"的粗糙下界代理值，只用来和一条两侧都有 20 倍余量的
    比例做比较。

    0 表示"无法测量"，按失败即保守处理：测不出的文档保持检测器交回的原样，不凭猜测
    替换。`extract_content` 接受任何响应形状的对象，所以 `.content` 不是文本
    （传输层替身、原始字节实现）是一种存在的调用约定，不是该崩掉的"不可能"。
    """
    try:
        raw = page.content
    except Exception:  # noqa: BLE001 - 解不开的 body 不算丢失
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
    """从 Response 对象抽取正文。

    参数:
        page: 一个 Response 对象（dhole_mcp.fetcher.Response）或兼容对象，
              带 .body、.encoding、.url、.css()
        extraction_type: 'markdown'、'html'、'text'、'article'、'structured'
        css_selector: 用来收窄抽取范围的 CSS 选择器

    返回:
        抽取出的正文串列表（通常一个元素）。
    """
    from dhole_mcp.trafilatura_extractor import extract_with_trafilatura, _fallback_extract

    # Trafilatura 是所有文本类类型的主抽取器
    result: Optional[List[str]] = None
    if extraction_type in ("markdown", "text", "article", "structured"):
        try:
            candidate = extract_with_trafilatura(page, extraction_type=extraction_type, css_selector=css_selector)
            if candidate and any(r.strip() for r in candidate):
                result = candidate
        except Exception as e:
            logger.debug(f"Trafilatura extraction failed: {e}")

    # 兜底：markdown/html 用 markdownify，其余用原始文本
    if result is None:
        result = _fallback_extract(page, extraction_type, css_selector)
    else:
        result = recover_a_lost_document(page, result, extraction_type, css_selector)
    return _collapse_boilerplate(result, extraction_type)


def recover_a_lost_document(page, content: List[str], extraction_type: str = "markdown",
                            css_selector: Optional[str] = None) -> List[str]:
    """非空答案不等于完整答案。

    Trafilatura 的主内容检测器可能判定一份正当文档的大部分都是样板，只交回一截；
    "候选非空"于是照单全收，调用方读到的是一次成功，而一份规范的前六节都不见了。
    实测：一份持有 379,285 字符的文档只交出 8,357 字符（2.20%），`content_ok` 仍是 true。

    返回作为构建基础的答案（检测器丢了文档时用纯转换器的产出，否则原样返回
    `content`），并把这次替换记进本次调用的注记袋。折叠留给调用方，两条路都不会
    折叠两次。

    `css_selector=` 予以豁免：收窄了范围的调用方本来就要得更少，那里的小答案是
    所问非所得，不是丢失。
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
    """对数据 XML（sitemap / RSS / Atom / SOAP / +xml）为 True，对 HTML 味的 XML 为 False。

    `application/xhtml+xml` 是穿着 XML 媒体类型的 HTML 文档：必须继续走 HTML
    抽取器，不能原样交回。
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


def _is_framed_document_content_type(content_type: str) -> bool:
    """Chrome 只会给它套一层自己的查看器、不会给出原件的那一类。

    数据文档之外再加两支，因为它们在线上撞的是同一件事：
      · 图片 —— 实测（2026-10-09，`force_fetcher='stealthy'` 打
        `https://httpbin.org/image/png`）交给 OCR 的是 **403 字节的 markup**，而
        `content_type` 仍写着 `image/png`，`total_size_bytes` 跟着那 403 字节走，
        OCR 因此报 "cannot identify image file"。相框比图片还小，这一发连"读不到字"
        都算不上，是一次没发生的读取。
      · PDF —— 内建查看器把文档画进 `<embed>`，`page.content()` 里没有正文。**但它的
        原件不在网络响应那一层**（D-41 实测，2026-10-09，`rfc2616.pdf` 走 stealthy 档）：
        Chrome 给 PDF 换的是它自己的查看器文档，`resp.body()` 交回的是 601 字节
        `<!doctype html>`，而同一份 URL 走 `page.context.request` 才是 550,558 字节
        `%PDF-1.1`。所以这一类要分两层问，见 `fetcher._original_bytes`。

    SVG 故意**不在**这一类里：一个 SVG 就是它的标记，浏览器渲染出来的那棵树是它的正当
    读法（同一个分工在 `quality._is_raster_image` 里为 js_shell 判据写着 —— 那边问的
    是"这张图有没有文本层"，这里问的是"Chrome 会不会给原件"，两问不并一张表）。

    读它的只有一处：`fetcher.response_from_browser_page` 取原件那一步。
    """
    ct = (content_type or "").split(";")[0].strip().lower()
    if _is_data_document_content_type(ct):
        return True
    if ct.startswith("image/"):
        return ct != "image/svg+xml"
    return ct.startswith("application/pdf")


def is_markup_response(content_type: str) -> bool:
    """这一份响应是不是"一个页面"，而不是数据文档 / 需要另作处理的原件。

    只有两张旧表：数据文档（JSON/XML）与被 Chrome 套框的那些（图片/PDF）。它们已经
    把"这不是 markup"判完了，新建第三张表只会让两处对同一个 content_type 说两种话。
    类型缺失时算 markup —— 走到这里的调用方手里已经有一份 markup，而"没人说过它不是"
    与"有人说它是别的东西"是两件事。
    """
    return not _is_framed_document_content_type(content_type)


def served_as_page(content_type: str) -> bool:
    """有没有**正面证据**说这一份是一个网页。

    与 `is_markup_response` 是两问，不是两张表：那一颗问"抽取按不按页面走"，缺类型时
    按页面处理是安全的一侧（调用方手里本来就有一份 markup）；这一颗问"能不能断言这页
    没有话可引用"，而缺类型时没有东西可依据 —— 实测（2026-10-10，
    `test_write_gate_moves_out_of_band_when_client_elicts.py` 的回环 POST）一个只回
    2 字节 `ok` 的 API 回执不带 Content-Type，于是被当成页面，`substance` 判成
    `nav_only`、`content_ok` 翻成 false，`recover_with` 还教调用者"这页没字，换个源"。
    三个形状读数（1 行、100% 标签行、整页 2 字）与一个真正的空壳页面**完全同值**，
    分不清的时候下判就是把载荷当页面 —— 留空是这里唯一诚实的一侧。
    """
    ct = (content_type or "").split(";")[0].strip().lower()
    return ct in ("text/html", "application/xhtml+xml")
