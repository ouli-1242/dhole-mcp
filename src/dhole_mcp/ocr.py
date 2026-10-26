"""Dhole 的 OCR —— 只用纯 pip 依赖（无系统二进制），把纯图片 PDF 与图片页面
变成 agent 能读的文本。

两个引擎，都可用 pip 安装且自带原生库 / 模型：
  * ``pypdfium2`` —— Google PDFium 绑定（BSD-3）。把 PDF 页渲染成 PIL/numpy
    图像。不需要 poppler / 系统二进制。
  * ``rapidocr`` v3 —— ONNX OCR（PaddleOCR 的 Apache-2.0 模型）。检测 / 识别
    ONNX 模型打包在 wheel 里，支持 Python 3.13+。若装的是旧版
    ``rapidocr-onnxruntime`` v1 则回退到它。无系统二进制、无模型下载。

为什么要有它：dhole 的 ``pdf_extractor`` 对纯图片 PDF 会给出诚实的
``scanned_pdf`` 死路（"no extractable text"）。那是对的，但对 agent 没用。装上
OCR extras 后，PDF 路径会回退到渲染 + OCR，agent 才真的拿到文本。纯图片网页
（content-type ``image/*``）也走 OCR。

Agent 设计：
  * 自动 OCR：PDF 被判定为扫描件且装了 rapidocr 时，OCR 自动运行。不给 agent
    增加要学的新参数。
  * 没有 ``pages`` 指定时的页数上限：OCR 很慢（约 2-4 秒/页）。为了防止一本
    100 页的扫描 PDF 把调用挂死，agent 没指定范围时默认只 OCR 前
    ``OCR_DEFAULT_PAGES`` 页，``next_action`` 告诉 agent 怎么取剩下的
    （``pages='11-20'``）。显式的 ``pages`` 永远被精确执行。
  * 输出形状与 ``pdf_extractor`` 一致：元数据头 + ``--- Page N ---`` 标记，
    所以 OCR 过的 PDF 与文本抽取的 PDF 对 agent 读起来一样。
"""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover - 仅注解用的导入
    # pdf_extractor 在 ocr_pdf() 内部惰性导入，让本模块在没有 PDF extras 时也能
    # 导入；注解需要这个名字留在作用域里。
    from dhole_mcp.pdf_extractor import PdfResult

logger = logging.getLogger("dhole-mcp.ocr")

# 调用方没指定页范围时，OCR 最多做这么多页。防止一本巨型扫描 PDF 把调用挂上
# 几分钟。显式 `pages` 永远覆盖它。
OCR_DEFAULT_PAGES = 10

# PDFium 的渲染倍率。2.0 ≈ 144 dpi（对常见文档的 RapidOCR 够用）；提到 2.5 是
# 为了小字号更准，而慢不了多少。
_RENDER_SCALE = 2.5


def _get_pdfium():
    """惰性导入 pypdfium2（可选的 [all] 依赖）。"""
    try:
        import pypdfium2 as pdfium  # type: ignore
        return pdfium
    except ImportError as e:
        from dhole_mcp import install as _install
        raise ImportError(
            f"OCR requires pypdfium2. Run: {_install.full_install_command()}"
        ) from e


def _get_rapidocr():
    """惰性导入 RapidOCR。优先统一的 ``rapidocr`` v3 包（支持 Python 3.13+，
    模型打包在 wheel 里）；回退到旧版 ``rapidocr-onnxruntime`` v1 包（仅
    Python <3.13）。"""
    try:
        from rapidocr import RapidOCR  # type: ignore  # v3
        return RapidOCR()
    except ImportError:
        pass
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore  # v1 回退
        return RapidOCR()
    except ImportError as e:
        from dhole_mcp import install as _install
        raise ImportError(
            f"OCR requires rapidocr. Run: {_install.full_install_command()}"
        ) from e


def ocr_available() -> bool:
    """OCR 引擎 + PDFium 都能导入时为 True。廉价探测（不加载模型）。"""
    try:
        import pypdfium2  # noqa: F401
    except Exception:
        return False
    try:
        import rapidocr  # noqa: F401
        return True
    except ImportError:
        pass
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


def _ocr_ndarray(engine: Any, arr) -> str:
    """对 numpy/PIL 图像跑 RapidOCR，返回拼接后的文本行。

    兼容两种 API 形状：v3 返回带 ``.txts`` 的 RapidOCROutput；v1 返回
    ``(list[[box, text, score]], elapse)``。
    """
    out = engine(arr)
    if out is None:
        return ""
    # v3：RapidOCROutput.txts 是字符串元组。
    if hasattr(out, "txts"):
        txts = out.txts or ()
        return "\n".join(t for t in txts if t).strip()
    # v1：(list[[box, text, score]], elapse)
    if isinstance(out, tuple) and len(out) >= 1:
        res = out[0]
        if not res:
            return ""
        return "\n".join(r[1] for r in res if r and len(r) > 1 and r[1]).strip()
    return ""


def ocr_image_bytes(data: bytes) -> str:
    """OCR 一张原始图片（PNG/JPEG 等字节）。返回抽取的文本或 ''。

    用于纯图片网页（content-type image/*）。
    """
    if not data:
        return ""
    engine = _get_rapidocr()
    try:
        from PIL import Image  # type: ignore
        import numpy as np
    except ImportError as e:
        raise ImportError("OCR requires Pillow + numpy (in rapidocr-onnxruntime).") from e
    img = Image.open(io.BytesIO(data)).convert("RGB")
    return _ocr_ndarray(engine, np.array(img))


def ocr_pdf(
    body: bytes,
    pages: Optional[str] = None,
    password: Optional[str] = None,
) -> PdfResult:
    """OCR 一本扫描件/纯图片 PDF。返回 PdfResult。

    成功时：``content`` 是一个元素的 markdown 串，含页眉 + 每页
    ``--- Page N ---`` 标记，``scanned=True``，``error=""``。自动上限生效时
    （没给 ``pages`` 且页数 > ``OCR_DEFAULT_PAGES``），页眉会告诉 agent 怎么取
    下一批。失败时：``error`` 有值，``content`` 是一段给人读的解释。
    """
    from dhole_mcp.pdf_extractor import PdfResult, _parse_pages  # reuse

    if not body or not body[:5].startswith(b"%PDF"):
        return PdfResult(error="not_a_pdf: body does not start with %PDF",
                         content=["[Body is not a PDF despite content-type.]"])

    pdfium = _get_pdfium()
    try:
        import numpy as np
    except ImportError as e:
        raise ImportError("OCR requires numpy (in rapidocr-onnxruntime).") from e

    # pypdfium2 遇到错误密码会抛异常；转成 encrypted_pdf 信号。
    try:
        pdf = pdfium.PdfDocument(body, password=password or None)
    except Exception as e:
        msg = str(e).lower()
        if "password" in msg or "encrypt" in msg:
            return PdfResult(encrypted=True,
                             error="encrypted_pdf: this PDF is password-protected; "
                                   "pass a password via the 'password' option",
                             content=["[Encrypted PDF - pass a password to OCR it.]"])
        return PdfResult(error=f"pdf_open_failed: {str(e)[:200]}",
                         content=[f"[Could not open PDF for OCR: {str(e)[:200]}]"])

    try:
        total_pages = len(pdf)
        if total_pages == 0:
            return PdfResult(error="empty_pdf: no pages", content=["[PDF has no pages.]"])

        requested = _parse_pages(pages, total_pages)
        if not requested:
            return PdfResult(pages_total=total_pages,
                             error="no_pages_in_range: the requested page range is out of bounds",
                             content=[f"[No pages in range '{pages}' (PDF has {total_pages} pages).]"])

        # 自动上限：agent 没传 `pages` 时，只 OCR 前 OCR_DEFAULT_PAGES 页，
        # 避免一本巨型扫描 PDF 挂上几分钟。通过返回的范围行 + next_action 告诉
        # 它怎么取剩下的。
        capped = False
        if not (pages and pages.strip()):
            if len(requested) > OCR_DEFAULT_PAGES:
                requested = requested[:OCR_DEFAULT_PAGES]
                capped = True

        engine = _get_rapidocr()

        # 尽力取元数据（标题等）做页眉。
        meta = {}
        try:
            meta = pdf.get_metadata_dict() or {}
        except Exception:
            meta = {}
        title = str(meta.get("Title", "") or "").strip()

        rendered: list[str] = []
        for n in requested:
            try:
                page = pdf[n - 1]
                bitmap = page.render(scale=_RENDER_SCALE)
                pil = bitmap.to_pil().convert("RGB")
                text = _ocr_ndarray(engine, np.array(pil))
                if not text:
                    text = "[No text detected on this page.]"
                rendered.append(f"--- Page {n} ---\n\n{text}")
            except Exception as e:  # 单页失败不该拖垮整份文档
                logger.debug("OCR page %d failed: %s", n, e)
                rendered.append(f"--- Page {n} ---\n\n[OCR failed on this page: {str(e)[:120]}]")

        header: list[str] = []
        if title:
            header.append(f"# {title}")
        if len(requested) == total_pages:
            scope = f"{total_pages} pages"
        else:
            scope = (f"pages {requested[0]}-{requested[-1]} of {total_pages}"
                     if len(requested) > 1 else f"page {requested[0]} of {total_pages}")
        header.append(f"> OCR-extracted from scanned PDF · {scope}")
        if capped:
            header.append(
                f"> Showing the first {OCR_DEFAULT_PAGES} pages (auto-cap to keep this "
                f"call fast). Pass pages='{requested[-1] + 1}-{requested[-1] + OCR_DEFAULT_PAGES}' "
                f"for the next batch."
            )
        body_md = "\n\n".join(rendered).strip()
        full = ("\n".join(header).strip() + "\n\n" + body_md).strip()

        return PdfResult(
            content=[full],
            title=title,
            pages_total=total_pages,
            pages_extracted=requested,
            scanned=True,
            error="",
        )
    finally:
        try:
            pdf.close()
        except Exception:
            pass
