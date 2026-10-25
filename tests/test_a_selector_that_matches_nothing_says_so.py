"""R06-001：css_selector 编译通过但匹配 0 个元素时，要说「整页原样返回」。

现场（round-06 P01）：`.no-such-class-xyz` → 整页 269 字符 content_ok=true 出门，
summary 无一字提匹配失败——缩取参数被静默丢弃且看起来像成功。同族旁证：伪类
选择器的第一拒自己说了 "it would have been ignored and the whole page returned"
——引擎知道后果，0 匹配分支不说。修：0 匹配分支与编译失败分支同款（was NOT
applied 的诚实句），说清整页被返回。不改变回落行为（缩取是可选收窄，从不掏空
答案），只把话说出去。
"""
from dhole_mcp import context
from dhole_mcp.trafilatura_extractor import _narrow_html_to_selector


def test_zero_match_selector_emits_the_whole_page_note():
    token = context._ARG_NOTES.set([])
    try:
        out = _narrow_html_to_selector(
            "<html><body><p>only one line here</p></body></html>", ".matches-nothing")
        assert out is None, "0 matches keeps the fall-through behavior"
        notes = [n for n in (context._ARG_NOTES.get() or [])
                 if ".matches-nothing" in n]
        assert notes, "0-match selector must leave a note"
        assert "matched 0 elements" in notes[0]
        assert "whole document was returned" in notes[0]
    finally:
        context._ARG_NOTES.reset(token)


def test_matching_selector_stays_quiet():
    token = context._ARG_NOTES.set([])
    try:
        out = _narrow_html_to_selector(
            "<html><body><p class='hit'>target</p></body></html>", ".hit")
        assert out is not None and "target" in out
        notes = [n for n in (context._ARG_NOTES.get() or []) if "css_selector" in n]
        assert not notes, "a working selector adds no notes"
    finally:
        context._ARG_NOTES.reset(token)
