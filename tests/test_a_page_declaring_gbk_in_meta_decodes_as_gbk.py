"""R16-001：页面只在 <meta> 声明编码时应听它。

round-16 确认；round-17 接线。修复在 fetcher.py：HTTP 头没声明 charset 时，先严格解
UTF-8，失败则读页面自己的 <meta>；字节本身是合法 UTF-8 时仍听字节。
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
    # 元数据说 latin-1，字节其实是 UTF-8：严格解 UTF-8 成功就该听字节
    raw = (
        '<!doctype html><html><head><meta charset="iso-8859-1"></head>'
        "<body><p>café · naïve · 中文</p></body></html>"
    ).encode("utf-8")
    text = _decode_html_bytes(raw, "utf-8")
    assert "café · naïve · 中文" in text, text


def test_the_sniffer_reads_both_meta_forms_and_says_unknown_when_absent():
    assert sniff_html_charset(_page("gbk", "x")) == "gbk"
    http_equiv = (
        '<meta http-equiv="Content-Type" content="text/html; charset=GB2312">'
    ).encode("ascii")
    assert sniff_html_charset(http_equiv) == "gb2312"
    assert sniff_html_charset(b"<html><body>no meta here</body></html>") == ""
