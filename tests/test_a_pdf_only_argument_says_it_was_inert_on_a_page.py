# -*- coding: utf-8 -*-
"""R02-F65 · 只在 PDF 那条腿上的两颗参数，打在页面上时要自己说一声（R02-063 · P3）。

现场（活体，`review/round-02/sessions/P02-R5-R63-live.jsonl`，HEAD=6add95f）：

    web_read(url='http://127.0.0.1:8399/article', pages='1-2', cache_ttl=0)

拿回来的正文与**不带 `pages=` 的那一发逐字节相同**，而 `summary` 里说的是另一件事
（私网摘头那一句），`next_action` 空、`error` 空 —— `pages=` 一个字没报。`password=` 同一形状。
这与 F48 修掉的本地那一扇门同源：`_PDF_PAGES` / `_PDF_PASSWORD` 只在
`_extract_pdf_response`（`server.py:2381`）被读过，页面那一条路上没人咨询过它们。

这一刀只加一行申报，**正文一寸不改**：把那一行摘掉之后必须与不带参数时逐字节相同
（这条是全部风险的对照面）。`password` 的值永不回显。参数真起作用时（PDF 腿）不许出现这一行。
"""

import pytest

from dhole_mcp import context as CT
from dhole_mcp import server as SV
from dhole_mcp.models import ResponseModel


class _FakePage:
    """一颗最小的应答：`_translate_response` 只读这几位。"""

    def __init__(self, body: bytes, ct: str):
        self.status = 200
        self.body = body
        self.raw_body = body
        self.content_type = ct
        self.url = "http://127.0.0.1:8399/whatever"
        self.total_size = len(body)
        self.encoding = "utf-8"
        self.headers = {"content-type": ct}


def _translate(body: bytes, ct: str, *, pages=None, password=None):
    """在申报桶里跑一次翻译，交回（回执, 桶）。"""
    tok_pages = CT._PDF_PAGES.set(pages)
    tok_pw = CT._PDF_PASSWORD.set(password)
    try:
        with CT._arg_notes_scope() as notes:
            got = SV._translate_response_body(_FakePage(body, ct), "markdown", None,
                                              fetcher_used="http", duration_ms=1.0)
    finally:
        CT._PDF_PAGES.reset(tok_pages)
        CT._PDF_PASSWORD.reset(tok_pw)
    return got, list(notes)


HTML = b"<html><body><h1>Report</h1><p>body text</p></body></html>"
PDFISH = b"%PDF-1.4\ntrailer\n%%EOF\n"


# ── 格 1：惰性参数要自报 ─────────────────────────────────────────────────

class TestAnInertArgumentReportsItself:

    def test_pages_on_a_page_is_named_as_inert(self):
        _got, notes = _translate(HTML, "text/html", pages="1-2")
        joined = " ".join(notes).lower()
        assert "pages" in joined, f"参数被收下、没人读过、也没人说：{notes}"

    def test_password_on_a_page_is_named_and_never_echoed(self):
        _got, notes = _translate(HTML, "text/html", password="hunter2")
        joined = " ".join(notes).lower()
        assert "password" in joined, notes
        assert "hunter2" not in joined, "申报不许把口令的值带进回执"

    def test_both_together_are_two_names_in_one_sentence(self):
        _got, notes = _translate(HTML, "text/html", pages="3", password="s3cret")
        joined = " ".join(notes).lower()
        assert "pages" in joined and "password" in joined, notes
        assert "s3cret" not in joined, notes
        assert len([n for n in notes if "pages" in n.lower()]) == 1, notes


# ── 格 2：不许多报（三种不许出现的形状）──────────────────────────────────

class TestItDoesNotClaimWhatDidNotHappen:

    def test_no_argument_no_sentence(self):
        _got, notes = _translate(HTML, "text/html")
        assert " ".join(notes).lower().find("inert") == -1, notes
        assert not any("only apply to a pdf" in n.lower() for n in notes), notes

    def test_the_pdf_leg_stays_quiet_because_the_argument_bit(self):
        # PDF 那条腿：`_extract_pdf_response` 真的读了这两颗，所以不许说"没作用"。
        seen = {}

        def fake_extract(body, raw_ct, total_size, url, extraction_type, fetcher_used,
                         duration_ms):
            seen["pages"] = CT._PDF_PAGES.get()
            seen["password"] = CT._PDF_PASSWORD.get()
            return ResponseModel(url=url, status=200, content=["page 1"],
                                 fetcher_used="http", extracted_type="markdown")

        orig = SV._extract_pdf_response
        SV._extract_pdf_response = fake_extract
        try:
            _got, notes = _translate(PDFISH, "application/pdf", pages="1", password="pw")
        finally:
            SV._extract_pdf_response = orig
        assert seen == {"pages": "1", "password": "pw"}, seen
        assert not any("only apply to a pdf" in n.lower() for n in notes), notes


# ── 格 3：正文一寸不改（对照面）─────────────────────────────────────────

class TestTheBodyIsTheSameBytes:

    @pytest.mark.parametrize("kw", [{"pages": "1-2"}, {"password": "x"},
                                    {"pages": "1", "password": "x"}])
    def test_the_announcement_changes_no_byte_of_the_body(self, kw):
        bare, _ = _translate(HTML, "text/html")
        withp, notes = _translate(HTML, "text/html", **kw)
        assert bare.content == withp.content, (bare.content, withp.content)
        assert bare.status == withp.status and bare.error == withp.error
        assert " ".join(notes), "这一格只允许多一行申报，别的不许多"
