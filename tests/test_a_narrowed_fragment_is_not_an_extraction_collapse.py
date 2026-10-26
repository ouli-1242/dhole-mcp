"""R13-001：用户点名 css_selector 收窄之后，别把「要得更少」判成「抽取器丢了字」。

现场（round-13 p21 A3 / p22 C7）：`web_read(/article, css_selector="h1")` 只取回
10 字（正是用户点名的 h1），整页可见 400 字 —— `content_ok=false`，
`error="extraction_collapsed: … the words ARE in the HTML, dhole's reader lost
them"`，next_action 还教换 extraction_type。用户要的就是这一块，抽取器一个字没丢。

同页的 `_substance_verdict` 在 R06-002 已拿到 css_selector 豁免（quality.py:890-897），
判决尺 `_detect_content_issue` 的 collapse 分支漏了同一条原则。恢复尺
（`extraction_lost_text`）不在这条豁免里：它问的是「值不值得再抽一次」，保持原样。
"""
from dhole_mcp.context import _CSS_SELECTOR
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _detect_content_issue


def _narrowed_page() -> ResponseModel:
    # 10 字是用户点名的 h1；整页可见 400 字。与 p21 A3 的线上一发同形。
    return ResponseModel(
        status=200, content=["本地抓取工具配置指南"],
        url="http://x.test/article", content_type="text/html",
        fetcher_used="http", total_extracted_chars=10,
        visible_text_chars=400, total_size_bytes=4010)


def test_selector_narrowing_is_not_called_a_collapse():
    result = _narrowed_page()
    assert _detect_content_issue(result).startswith("extraction_collapsed"), (
        "前置：整页尺确实会误判（修前现场）")
    token = _CSS_SELECTOR.set("h1")
    try:
        assert _detect_content_issue(result) == "", (
            "点名收窄的答案不许再报抽取塌陷")
    finally:
        _CSS_SELECTOR.reset(token)


def test_without_a_selector_a_real_loss_stays_collapsed():
    assert _detect_content_issue(_narrowed_page()).startswith("extraction_collapsed")
