"""客户端跳转页不是内容：page_type=redirect 时 content_ok 必须为假，并给出去哪看的出路。

现场（R05-023 · 05-08 惠杉 · 国务院公报入口）：HTTP 档拿到一页只有
`location.href='./2026/issue_13006/'` 的脚本，`page_type` 已经写着 `redirect`，
`substance` 还写着 `ok`，`content_ok=true`、`next_action=""`。同一封回执里
「这是跳转」与「这本可引用」互相否决。P3 实测：`force_fetcher='stealthy'` 会跟随它
落到 `…/2026/issue_13026/` 并返回 `page_type=list` —— 所以正确的出路是明说跳转并
指向浏览器档，而不是把脚本交给调用方当正文。
"""

from __future__ import annotations

from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _agent_hints

SCRIPT = """<script>

    var num = 13;
    var href = "./2026/issue_13006/";

    if (num == '0') {
        if (href) {
            location.href='./2026/issue_13006/';
        } else {
            location.href='./2025/issue_12466/';
        }
    } else {
        location.href='./2026/issue_13026/';
    };
</script>"""


def test_a_js_redirect_shell_is_not_answered_as_content():
    r = ResponseModel(
        status=200, url="https://example.com/r", content=[SCRIPT],
        content_type="text/html", fetcher_used="http", page_type="redirect",
        visible_text_chars=4572, total_size_bytes=16000,
    )
    summary, next_action, ok = _agent_hints(r)
    assert ok is False, "跳转脚本被当成可引用的正文"
    lowered = next_action.lower()
    assert "stealthy" in lowered or "redirect" in lowered, next_action


def test_the_redirect_verdict_is_the_same_on_diagnosis_and_next_action():
    """同一封里的三只手（content_ok / diagnosis / next_action）必须都指向「跳转」。"""
    from dhole_mcp.quality import _diagnose

    r = ResponseModel(
        status=200, url="https://example.com/r", content=[SCRIPT],
        content_type="text/html", fetcher_used="http", page_type="redirect",
        visible_text_chars=4572, total_size_bytes=16000, content_ok=False,
    )
    d = _diagnose(r)
    assert d is not None
    assert d["code"] == "redirect", d
    assert "stealthy" in d["recover_with"], d
