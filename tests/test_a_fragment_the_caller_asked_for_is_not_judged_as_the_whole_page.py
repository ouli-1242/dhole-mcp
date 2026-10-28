"""R06-002：css_selector 点名的碎片不按整页的散文尺判。

现场（round-06 P01/P08）：`.card a` 与 `a[href^='/work/']` 正好取回用户点名的
11 行卡片标题（59 字符），却 `content_ok=false`、substance=`nav_only`、summary
头一句「no prose (menu fragments only)」——散文尺量的是整页，量到的量是**用户
点名要的碎片**。原则已在册（extractor.py：narrowed 的调用方要的是更少，塌陷
回收那把尺早就豁免了 css_selector）；实质尺缺同一枚豁免。修：css_selector 在
场的调用，substance 留空判 + basis 说清尺子为何不适用。
"""
from dhole_mcp.context import _CSS_SELECTOR
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _substance_verdict


def _fragment_response():
    # 11 行短碎片、无句读——正是 round-06 P01 的 .card a 现场形状
    lines = "\n".join(f"专题 {i}" for i in range(1, 12))
    return ResponseModel(status=200, content=[lines], url="http://x.test/",
                         content_type="text/html", fetcher_used="http",
                         total_extracted_chars=len(lines), visible_text_chars=117,
                         total_size_bytes=1_182)


def test_narrowed_fragment_is_not_judged_nav_only():
    r = _fragment_response()
    assert _substance_verdict(r)[0] == "nav_only", "前置：整页尺确实会误判（修前现场）"
    token = _CSS_SELECTOR.set(".card a")
    try:
        verdict, basis = _substance_verdict(r)
        assert verdict == ""
        assert "css_selector" in basis
    finally:
        _CSS_SELECTOR.reset(token)


def test_unnarrowed_call_keeps_the_ruler():
    r = _fragment_response()
    assert _substance_verdict(r)[0] == "nav_only"
