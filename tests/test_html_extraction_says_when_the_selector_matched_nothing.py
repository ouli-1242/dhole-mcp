"""`extraction_type='html'` / `'text'` 也要说「选择器一个都没匹配上」。

现场（2026-10-27，MCP 全量覆盖时发现）：`web_read(url='https://example.com',
extraction_type='html', css_selector='h1')`。该页在 2026 版已经没有 `<h1>`，
于是 577 字节的**整页**原样回来，`content_ok=true`，`summary` 里没有一个字提
选择器没匹配。同一次调用把 `extraction_type` 换回默认 `markdown` 就会带上
R06-001 那句话 —— 说明缺口只在这条兜底路径上。

根因：`_fallback_extract` 自己 inline 收窄（`if matches:` 有命中才替换 html），
没走带注记的 `_narrow_html_to_selector`，0 匹配分支是空的。修法与 R06-001 同形：
行为不变（缩取从不掏空答案），只把后果说出来。
"""
from __future__ import annotations

import pytest

from dhole_mcp.context import _ARG_NOTES
from dhole_mcp.extractor import extract_content

HTML = ("<html><body><p>This domain is for use in documentation examples.</p>"
        "<p>Second paragraph, no heading anywhere.</p></body></html>")


class _Page:
    """足够 `_fallback_extract` 用的响应替身：.content / .body / .url。"""

    def __init__(self, html: str = HTML):
        self.content = html
        self.body = html.encode("utf-8")
        self.url = "https://example.com/"
        self.encoding = "utf-8"


@pytest.mark.parametrize("extraction_type", ["html", "text"])
def test_zero_match_selector_is_named_on_the_fallback_path(extraction_type):
    notes: list[str] = []
    token = _ARG_NOTES.set(notes)
    try:
        out = extract_content(_Page(), extraction_type=extraction_type, css_selector="h1")
    finally:
        _ARG_NOTES.reset(token)

    joined = " ".join(notes)
    assert "matched 0 elements" in joined, f"注记没写出来：{notes}"
    assert "css_selector 'h1' was NOT applied" in joined, joined
    # 缩取从不掏空答案：正文照旧在，只是现在这句话也在。
    assert any("documentation examples" in chunk for chunk in out), out


def test_a_selector_that_matches_says_nothing_extra():
    """有命中时不许冒出这句注记 —— 否则它就成了噪音，真出事时没人看。"""
    notes: list[str] = []
    token = _ARG_NOTES.set(notes)
    try:
        out = extract_content(_Page(), extraction_type="html", css_selector="p")
    finally:
        _ARG_NOTES.reset(token)

    assert not any("matched 0 elements" in n for n in notes), notes
    assert "<p>" in out[0]
