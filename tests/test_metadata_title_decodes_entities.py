"""metadata.title 保留原始 HTML 实体而正文已解码 —— 同一响应两套实体口径。

症状（2026-10-06 实测，<title>轴A实验页 &amp; 对比夹具</title>）：

    metadata: {'title': '轴A实验页 &amp; 对比夹具', ...}
    content[0] 首行: '# 主标题：抓取器对比实验 & 样例'   （实体已解码）

agent 引用标题时会把 &amp; 带出去。复核附注补强：三条 title 来源
（meta/<title>/JSON-LD）都不过 unescape，不止 <title> 回退一条 —— 按同一条
"同一响应一套实体口径"修全部三处。

判据：同一 fixture 上 metadata.title ＝＝ 'a & b'（三条来源各验一条）；
既有 metadata 测试保持绿。
"""

from __future__ import annotations

from dhole_mcp.metadata import extract_metadata


def test_a_title_fallback_decodes_entities():
    meta = extract_metadata("<html><title>a &amp; b</title></html>", "https://x/")
    assert meta["title"] == "a & b"


def test_an_og_title_decodes_entities():
    html = ('<html><head>'
            '<meta property="og:title" content="Sale &amp; Clearance">'
            '</head><body></body></html>')
    meta = extract_metadata(html, "https://x/")
    assert meta["title"] == "Sale & Clearance"


def test_a_json_ld_headline_decodes_entities():
    html = ('<html><head><script type="application/ld+json">'
            '{"headline": "Q3 &amp; Beyond"}'
            '</script></head><body></body></html>')
    meta = extract_metadata(html, "https://x/")
    assert meta["title"] == "Q3 & Beyond"


def test_a_title_that_never_had_entities_is_untouched():
    meta = extract_metadata("<html><title>Plain Title</title></html>", "https://x/")
    assert meta["title"] == "Plain Title"
