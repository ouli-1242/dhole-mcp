"""一个只有一两行字的 SPA 挂载页不是「成功」，而塌陷的抽取不该由调用方来补。

现场（2026-10-23，外部 agent 实测 dsh.market / dshmarketplace.dev）：

    web_read(url='https://dsh.market')          → 34 字符、content_ok=true、无下一步提示
    web_read(url='https://dshmarketplace.dev')  → 7,623 字符 / 整页可见 23,844，
                                                   error=extraction_collapsed，处方是
                                                   "自己再调一次 extraction_type='html'"

两件事的共同形状是**把该由工具做的判断交回调用方**：第一件的判据（`body_substance` 的
短页那一支）要求"每一行都是 ≤8 字的标签"才算壳，而一整页就是一个 34 字标题时
`label_share=0.0`，于是判决留空、`content_ok` 为真；第二件只把处方写进 `next_action`，
而同一份 body 换 html 那一路能多拿回 5 倍的字，且**不发第二个请求**。

本文件钉住三件事：
  * 整页几乎没字且没有一句人话 → `nav_only`（有句号的短页不在此列）；
  * 小体积壳（< 3000 字节）也要被 `_is_js_shell` 认出来 —— 3,000 那道下限只管大壳；
  * 坍缩时内部先重抽一次，接管后 `extracted_type` 说真话、并留下备注。
"""

from __future__ import annotations

from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _is_js_shell, body_substance
from dhole_mcp.server import _attach_dom_visible_chars, _translate_response_body
from dhole_mcp.fetcher import Response

TITLE_ONLY = "DSH Market · DeepSeek Harness 插件市场"
PROSE_ONE_LINER = ("This domain is for use in documentation examples without needing "
                   "permission. Avoid relying on it for testing.")


def _http(html: str, content: list[str], *, content_type: str = "text/html; charset=utf-8",
          status: int = 200) -> ResponseModel:
    """按真抓取后的信封形状搭一发（DOM 可见字符走产品里那同一把尺）。"""
    out = ResponseModel(status=status, content=content, url="https://shell.test/",
                        fetcher_used="http", content_type=content_type,
                        total_size_bytes=len(html.encode("utf-8")))
    return _attach_dom_visible_chars(out, html)


# ─── 1 · 判决：整页一两个标签字 ────────────────────────────────────────

def test_a_page_that_is_only_a_title_is_nav_only():
    """1,936 字节的标记里只有 34 个可见字、且没有一句人话 —— 这是挂载页的形状。"""
    verdict, basis = body_substance(TITLE_ONLY, len(TITLE_ONLY), page_bytes=1_936)
    assert verdict == "nav_only", (verdict, basis)


def test_a_short_answer_page_is_not_nav_only():
    """同样的字少，但标记也少（60 字节）—— 那是一行真答案，不是壳。"""
    verdict, basis = body_substance(PROSE_ONE_LINER[:12], 12, page_bytes=60)
    assert verdict != "nav_only", basis


def test_a_short_page_with_a_real_sentence_is_not_nav_only():
    """误报侧：example.com 就是 156 字一行 —— 那是答案，不是壳。"""
    verdict, _basis = body_substance(PROSE_ONE_LINER, len(PROSE_ONE_LINER), page_bytes=3_000)
    assert verdict != "nav_only"


def test_a_chinese_one_liner_is_an_answer_not_a_menu():
    """中文句末标点不在 `_body_line_shape` 那张 ASCII 表里 —— 判"有没有人话"要认它。"""
    one_line = "答案：四十二。"
    verdict, basis = body_substance(one_line, len(one_line), page_bytes=60)
    assert verdict != "nav_only", basis


def test_without_a_byte_count_the_old_label_rule_still_decides():
    """爬取的一行没有字节数：判据退回"每一行都是 ≤8 字的标签"，不许凭空升级。"""
    verdict, _basis = body_substance(TITLE_ONLY, len(TITLE_ONLY))
    assert verdict != "nav_only"


# ─── 2 · 升级：小体积的壳也要起浏览器 ──────────────────────────────────

_FILLER = "<script src='/assets/vendor-%02d.js'></script>" % 0
SPA_SHELL = ("<!DOCTYPE html><html><head><title>Market</title>"
             + "".join(f"<script src='/assets/chunk-{i:02d}.js'></script>" for i in range(30))
             + "<style>" + ("body{margin:0}" * 60) + "</style></head>"
             "<body><div id='app'></div></body></html>")


def test_a_small_spa_shell_asks_for_the_browser_tier():
    shell = _http(SPA_SHELL, [TITLE_ONLY])
    assert 500 <= shell.total_size_bytes <= 3_000, "这一发要落在小壳那一段里"
    assert shell.total_size_bytes >= 8 * shell.visible_text_chars, "脚本壳的证据：字节远多于字"
    assert _is_js_shell(shell) is True


def test_a_small_page_with_light_markup_is_not_called_a_shell():
    """字少但标记也少的短页是"短"，不是壳 —— 这条闸误判一次就是 3-5 秒白等。"""
    page = _http("<html><body><h1>Status</h1></body></html>", ["Status"])
    assert page.total_size_bytes < 8 * max(page.visible_text_chars, 1)
    assert _is_js_shell(page) is False


def test_a_small_page_that_answers_in_a_sentence_is_not_a_shell():
    page = _http(f"<html><body><p>{PROSE_ONE_LINER}</p></body></html>", [PROSE_ONE_LINER])
    assert _is_js_shell(page) is False


def test_a_json_body_is_never_a_shell():
    doc = _http('{"ok": true}', ['{"ok": true}'], content_type="application/json")
    assert _is_js_shell(doc) is False


# ─── 3 · 坍缩：内部重抽，不把处方推给调用方 ────────────────────────────

def test_a_collapsed_extract_is_retried_by_the_plain_converter(monkeypatch):
    prose = "<p>" + ("the specification says so. " * 2_000) + "</p>"
    html = f"<html><body>{prose}</body></html>"
    page = Response(url="https://collapse.test/", body=html.encode("utf-8"), status=200,
                    headers={"content-type": "text/html; charset=utf-8"})
    thin = ["the first section only"] * 40          # 明知不足，留在塌陷那一侧
    reclaimed = "reclaimed body\n" * 500

    # 让"主路只拿回一点点"成为这一发的形状：绕过它自己的两条恢复路，只测本文件这一刀。
    monkeypatch.setattr("dhole_mcp.trafilatura_extractor.extract_with_trafilatura",
                        lambda *a, **k: thin)
    monkeypatch.setattr("dhole_mcp.extractor.recover_a_lost_document",
                        lambda page, content, *a, **k: content)
    monkeypatch.setattr("dhole_mcp.extractor._collapse_boilerplate",
                        lambda content, *a, **k: content)
    # 补救交回的是**纯转换器 markdown**，不是原始标记 —— 第一版交整页 html，agent
    # 拿到的正文以 `<!DOCTYPE html>` 开头（实测人民网首页 35,596 字符标签）。
    monkeypatch.setattr("dhole_mcp.trafilatura_extractor.plain_markdown_from_html",
                        lambda h: reclaimed)

    from dhole_mcp.context import _ARG_NOTES
    notes: list[str] = []
    token = _ARG_NOTES.set(notes)
    try:
        result = _translate_response_body(page, "markdown", None, "http", 1.0)
    finally:
        _ARG_NOTES.reset(token)

    assert result.extracted_type == "markdown", (
        "纯转换器交的就是 markdown；报 html 等于把可读正文说成标记")
    assert sum(len(c) for c in result.content) == len(reclaimed)
    assert any("plain converter" in n for n in notes), (
        "换路这件事必须在回执里说得出口")


# ─── 4 · 列表页：交链接清单，不交整页标记 ──────────────────────────────

def _list_page_html(n: int = 20) -> str:
    # 标题写长一点：坍陷闸有 400 个可见字的下限（壳页够不着它），合成页要过这条线。
    items = "".join(
        f'<li><a href="/n/{i}.html">第{i}条新闻标题：这里是这一条要闻的完整标题文字</a></li>'
        for i in range(n))
    return f"<html><body><ul>{items}</ul></body></html>"


def test_a_collapsed_list_page_recovers_as_a_link_list(monkeypatch):
    """首页/索引页的"正文"是条目：渲染成 `* [标题](链接)`，而不是 35k 标签。

    实测 2026-10-23 人民网首页：整页可见 6,969 字，markdown 路只拿回一千余字，
    第一版补救交回整页 html（35,596 字符、`<!DOCTYPE html>` 打头）。这一条钉住
    形状：交出的是清单、`page_type=list`，且备注说明了这是什么。
    """
    html = _list_page_html()
    page = Response(url="https://list.test/", body=html.encode("utf-8"), status=200,
                    headers={"content-type": "text/html; charset=utf-8"})
    thin = ["导航"] * 20

    monkeypatch.setattr("dhole_mcp.trafilatura_extractor.extract_with_trafilatura",
                        lambda *a, **k: thin)
    monkeypatch.setattr("dhole_mcp.extractor.recover_a_lost_document",
                        lambda page, content, *a, **k: content)
    monkeypatch.setattr("dhole_mcp.extractor._collapse_boilerplate",
                        lambda content, *a, **k: content)

    from dhole_mcp.context import _ARG_NOTES
    notes: list[str] = []
    token = _ARG_NOTES.set(notes)
    try:
        result = _translate_response_body(page, "markdown", None, "http", 1.0)
    finally:
        _ARG_NOTES.reset(token)

    body = "\n".join(result.content)
    assert result.page_type == "list", result.page_type
    assert body.startswith("* [第0条新闻标题"), body[:80]
    assert body.count("* [") == 20, body.count("* [")
    assert "<!DOCTYPE" not in body and "<li>" not in body, "清单里不该出现标记"
    assert any("rendered as a list" in n for n in notes), notes


def test_a_recovered_list_is_not_reported_as_collapsed():
    """只有**真的渲染成清单**的那一发免检：清单按定义丢掉非链接的字。

    免检靠的是 `metadata.recovered_as='link_list'` 这颗记号，而不是 `page_type=list`：
    一个被判 list 却仍在丢正文的页面，塌陷那句话照旧要说。
    """
    from dhole_mcp.quality import extraction_collapsed

    listed = ResponseModel(
        status=200, url="https://list.test/", content=["* [a](b)"] * 20,
        fetcher_used="http", page_type="list", extracted_type="markdown",
        visible_text_chars=20_000, content_type="text/html",
        metadata={"recovered_as": "link_list"},
    )
    assert extraction_collapsed(listed) is False

    still_lost = listed.model_copy(update={"metadata": {}})
    assert extraction_collapsed(still_lost) is True, (
        "没有那颗记号时，少字仍要算塌陷")
