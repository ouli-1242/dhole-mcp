"""R16-001 修复草稿（未接线）：页面只在 <meta> 声明编码时应听它。

暂停原因：fetcher.py / crawl.py / trafilatura_extractor.py 正由另一位 agent 编辑
（2026-10-26 11:38-11:50，注释翻译在途）；按用户指示不动其文件。其落地后把本文件
移回 tests/，并按 review/round-16/review.md 的修法在 fetcher.sniff_html_charset 落地。
"""

from __future__ import annotations

from dhole_mcp.fetcher import _decode_html_bytes, sniff_html_charset


def _page(charset: str, body: str) -> bytes:
    return (
        f"<!doctype html><html><head><meta charset=\"{charset}\"></head>"
        f"<body><p>{body}</p></body></html>"
    ).encode(charset)


def test_a_gbk_page_declaring_its_charset_in_meta_decodes_as_gbk():
    raw = _page("gbk", "这是一个 GBK 编码的页面，正文包含中文。")
    text = _decode_html_bytes(raw, "utf-8")
    assert "这是一个 GBK 编码的页面" in text, text
    assert "\ufffd" not in text, text


def test_a_latin1_page_declaring_its_charset_in_meta_keeps_its_accents():
    raw = _page("iso-8859-1", "Voici un texte français avec des accents : é è à ç ù.")
    text = _decode_html_bytes(raw, "utf-8")
    assert "français" in text and "é è à ç ù" in text, text
    assert "\ufffd" not in text, text


def test_utf8_bytes_win_over_a_wrong_single_byte_meta():
    raw = _page("iso-8859-1", "café · naïve · 中文")
    # 元数据说 latin-1，字节其实是 UTF-8：严格解 UTF-8 成功就该听字节
    text = _decode_html_bytes(raw, "utf-8")
    assert "café · naïve · 中文" in text, text


def test_the_sniffer_reads_both_meta_forms_and_says_unknown_when_absent():
    assert sniff_html_charset(_page("gbk", "x")) == "gbk"
    http_equiv = (
        '<meta http-equiv="Content-Type" content="text/html; charset=GB2312">'
    ).encode("ascii")
    assert sniff_html_charset(http_equiv) == "gb2312"
    assert sniff_html_charset(b"<html><body>no meta here</body></html>") == ""
