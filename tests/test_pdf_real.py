"""真 PDF 字节上的提取契约（含口令链的最后一环）。

为什么需要：
- 口令链路此前只验到"选项确实传给了 extract_pdf"（test_hardening_regressions 里那条
  用桩，证明接线、证明不了解密）；真解密行为要真字节才算数。
- 真机实测发现"缺口令"与"口令错"两类都退化成一条空消息的 `pdf_open_failed: `，
  且 encrypted=False —— 加密 PDF 看起来像文件损坏。这里把三态（缺/错/对）钉住。

fixture 来源：tests/background_checks.pdf 与 tests/dummy.pdf 是仓库里既有的真文件
（此前没有任何测试引用它们，顺手接上）；加密样本由 pypdf 在测试运行时**从真文件**生成
—— 不往仓库里塞二进制加密件，也不把口令写死在 fixture 里。pypdf 只在测试期用
（dev extra），没装就跳过。
"""

import io
from pathlib import Path

import pytest

from dhole_mcp.pdf_extractor import extract_pdf

PDF_DIR = Path(__file__).parent
TEXT_PDF = PDF_DIR / "background_checks.pdf"
SCANNED_PDF = PDF_DIR / "dummy.pdf"
MARKER = "Firearm Background Checks"   # 实测出现在正文里（见下 test_text_pdf）
PASSWORD = "correct horse battery staple"


def _encrypted(pdf_bytes: bytes, password: str = PASSWORD) -> bytes:
    pytest.importorskip("pypdf")
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(password, algorithm="AES-256")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


class TestPlaintextPdfBytes:
    """先证明 fixture 本身是可解析的真 PDF —— 否则下面每条断言都可能是空转。"""

    def test_text_pdf_is_extracted(self):
        res = extract_pdf(TEXT_PDF.read_bytes())
        assert res.error == ""
        assert res.encrypted is False
        assert res.pages_total >= 1
        assert MARKER in res.content[0]

    def test_image_only_pdf_is_reported_as_scanned(self):
        """扫描件要如实说"没有可提取文本"，而不是给一具空正文。"""
        res = extract_pdf(SCANNED_PDF.read_bytes())
        assert res.error.startswith("scanned_pdf")
        assert res.content and "no extractable text" in res.content[0]


class TestEncryptedPdf:

    def test_missing_password_is_diagnosed_not_reported_as_broken(self):
        res = extract_pdf(_encrypted(TEXT_PDF.read_bytes()), password=None)
        assert res.encrypted is True, "加密标志要立起来（调用方据此提示口令）"
        assert res.error.startswith("encrypted_pdf")
        assert "no password was supplied" in res.error
        assert "the supplied password was rejected" not in res.error

    def test_wrong_password_is_distinguishable_from_missing(self):
        """两者此前都是同一条空消息 —— agent 没法据此改行为。"""
        res = extract_pdf(_encrypted(TEXT_PDF.read_bytes()), password="wrong")
        assert res.encrypted is True
        assert "the supplied password was rejected" in res.error

    def test_correct_password_returns_the_real_text(self):
        res = extract_pdf(_encrypted(TEXT_PDF.read_bytes()), password=PASSWORD)
        assert res.error == ""
        assert MARKER in res.content[0], "解出来的必须是原正文，不是占位符"

    def test_corrupt_bytes_are_not_blamed_on_a_password(self):
        """反例守卫：真正坏掉的文件不许被说成"需要口令"。"""
        res = extract_pdf(b"%PDF-1.4\nnot a real pdf")
        assert res.encrypted is False
        assert res.error.startswith("pdf_open_failed")
        assert res.error.strip() != "pdf_open_failed:", "错误消息不许是空的"
