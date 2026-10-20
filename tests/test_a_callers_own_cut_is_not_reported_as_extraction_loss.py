"""调用方自己点名的那一刀不是抽取器的失手：`collapsed` 要量抽取器交回的那份。

现场（2026-10-15 · P02-05 邱洛（券商行业研究员 L3）· S5 晨会前砍需求 · R02-016 · P1 · 组 E）：

    web_read(url='http://127.0.0.1:8399/article#item1')                 → content_ok=true
    web_read(url='…#item1', focus='缓存')                                → content_ok=false
        substance='collapsed'
        substance_basis=「this page's own markup holds 400 visible characters
                         while the extractor returned 130」
        total_extracted_chars=130 / page_total_chars=387 / visible_text_chars=400
        focus_blocks_kept=1 of 10
        next_action=「… The page was fetched; what failed is the extraction of a readable
                     body. Untried levers on THIS url: … focus='' …」

抽取器一个字都没丢：它交回 387 字对页面自己的 400 可见字（97%）。少的 257 字是**邱洛自己
点名的 `focus='缓存'`** 取走的。旧判据把 `sum(len(c) for c in content)`（切完之后的那份）
当成"抽取器交回的量"，于是同一封响应里：`content[]` 带着他要的那句正文、`focus_blocks_kept`
记着这一刀是他要求的、判决位却说"正文提取失败"，并把 `focus=''`（他那一句的反面）列为
"没试过的杠杆"。他指着那封说：「它先把我问的那句话交给我，然后告诉我这页没有正文。」

根因（读到码）：`quality.extraction_collapsed` 与 `_is_js_shell` 的那颗 `text_len` 都读
`sum(len(c) for c in result.content)` —— 这是**切完之后**的量；比较对象 `visible_text_chars`
却是切之前的整页量。同一封信里已经带着切之前的那颗数（`page_total_chars`：模型自己的说明写着
"This page's length BEFORE focus cut it"），两把闸没有去读。

契约（本文件钉住）：
  * 抽取器交回的那份（`page_total_chars` 在场时用它）对整页可见字过闸；调用方的收窄不参与；
  * **反面锁**：真塌陷（没有 focus、抽取确实只剩一角）照旧 `collapsed`，`content_ok` 照旧为假
    —— 这一刀不许把闸拆软；
  * 壳闸那一条同一条尺：切完 60 字而抽取器本来交回 196 字（对 400 可见字是塌陷）不许被
    改判成"要起浏览器"，也不许反过来被当成成功；
  * 尺子只有一把：那两把闸读同一颗"抽取器交回多少字"的函数，不许各自再数一次。

R02-147（2026-10-19 · 活体 MCP 压测 183 例 · 靶站旁证）把同一笔账的两条**没被 R02-016 覆盖**
的路补齐：`page_total_chars` 只在 focus 真的删了字时才装配（`server._pagination_basis`），
所以另外两条切法仍退回切完的 `content[]` ——

    web_read('https://quotes.toscrape.com/js/')                        → substance=ok · true
    web_read('…', max_content_chars=600)                               → substance=collapsed · false
        「this page's own markup holds 1,502 visible characters while the extractor
          returned 448」   total_extracted_chars=1491 · 同一份缓存副本 · 8ms
    web_crawl('…', max_content_chars_per=500)  对 697,801 可见字的 uv CLI 页  → 同一句 499
    web_read(nested_selector=…, nested_fields=…) 两条作者生日 325 字       → content_ok=false
        recover_with 开出的药是「focus=''（整页不筛）」—— 用户刚点名的那两枚参数的反面

同一封响应里还并排写着两句相反的话：`summary` 说 "truncated"、`next_action` 说
"offset=473 继续翻"，而判决位说"正文提取失败"。本文件第 5、6 节钉这两条；`structured`
一支（`schema=` / `nested_*`）交回的是**投影**，与 `crawl._measure_row` 把塌陷那一关
锁在 `kind == "article"` 里是同一条理由。
"""

from __future__ import annotations

import inspect

from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import (
    _agent_hints,
    _detect_content_issue,
    _is_js_shell,
    _substance_verdict,
    extracted_chars,
    extraction_collapsed,
)

PROSE = "关于缓存保留时长：缓存默认保留 3600 秒，改 cache_ttl 可覆盖。"


def _res(content, *, visible, page_total=0, focus_blocks_kept=0,
         content_type="text/html", fetcher="http"):
    return ResponseModel(status=200, content=[content], url="https://example.com/a",
                         content_type=content_type, fetcher_used=fetcher,
                         visible_text_chars=visible, page_total_chars=page_total,
                         total_extracted_chars=len(content),
                         focus_blocks_kept=focus_blocks_kept,
                         total_size_bytes=len(content) + 5_000)


# ─── 1 · 调用方点名的那一刀不参与塌陷判据 ───

def test_a_caller_asked_cut_is_not_extraction_loss():
    """现场那一发的三个数：整页 400、抽取 387、切后 130。"""
    r = _res(PROSE, visible=400, page_total=387, focus_blocks_kept=1)
    assert extracted_chars(r) == 387
    assert extraction_collapsed(r) is False, "按用户要求切过的正文被算成抽取器失手"


def test_the_same_body_without_the_callers_cut_is_still_fine():
    r = _res(PROSE * 12, visible=400)
    assert extracted_chars(r) == len(PROSE) * 12
    assert extraction_collapsed(r) is False


# ─── 2 · 反面锁：真的塌陷照旧 ───

def test_a_page_the_extractor_actually_loses_is_still_collapsed():
    """没有 focus（`page_total_chars` 缺席）、抽取 130 字对整页 400 可见字 —— 那才是塌陷。"""
    r = _res(PROSE, visible=400)
    assert extraction_collapsed(r) is True, "闸被这一刀拆软了"


def test_a_real_collapse_under_a_larger_page_is_collapsed():
    r = _res(PROSE, visible=4_000, page_total=0)
    assert extraction_collapsed(r) is True


# ─── 3 · 壳闸读同一把尺 ───

def test_the_shell_gate_measures_the_extractors_own_output():
    """同一页切完之后只剩一角，也会因"字少"被判成要起浏览器的壳 —— 除非量的是抽取器自己交回的那份。

    现场数：整页 300 可见字（够不着塌陷那条 400 字的下限）、抽取器交回 250 字、
    用户点名的 `focus=` 把这一封切到 60 字。旧 text_len 数的是那 60 字 →
    "字少 + 没塌陷" = 判成 JS 壳 → 出路变成"起浏览器"，而同一封里正文本来就在。
    """
    kept = PROSE[:25] * 10            # 抽取器本来交回的 250 字
    r = _res(PROSE[:6], visible=300, page_total=len(kept))
    assert len(r.content[0]) < 200 and extracted_chars(r) == len(kept)
    assert _is_js_shell(r) is False, "同一页因用户点了 focus= 而换了一条出路"


def test_a_genuine_shell_is_still_a_shell():
    """真空壳：整页 6 可见字 —— 塌陷那条比例够不着，仍按壳判（要起浏览器）。"""
    r = _res("加载中", visible=6, page_total=0)
    assert _is_js_shell(r) is True, "壳闸被这一刀一起改软了"


# ─── 4 · 一把尺 ───

def test_both_gates_read_the_one_ruler():
    """两把闸都不许自己再数一次 content。"""
    for fn in (extraction_collapsed, _is_js_shell):
        src = inspect.getsource(fn)
        assert "extracted_chars(result)" in src, fn.__name__
        assert 'sum(len(c) for c in result.content)' not in src, (
            f"{fn.__name__} 里还有第二次数法")


# ─── 5 · R02-147：预算切（`max_content_chars` / `offset` / 每页帽）同样不参与 ───

def _window(content: str, *, extracted: int, visible: int, kind: str = "markdown",
            page_total: int = 0):
    """装配一封"抽取器交回 extracted 字、这一窗只装了 len(content) 字"的响应。

    `total_extracted_chars` 是 `_apply_chunking` 与 `content[]` **同一次**装配的数，
    所以它在预算这条路上天然在场（focus 那条路的 `page_total_chars` 则只在删过字时在场，
    正是 R02-016 那版尺子漏掉的另一半）。
    """
    return ResponseModel(status=200, content=[content], url="https://example.com/a",
                         content_type="text/html", fetcher_used="http",
                         extracted_type=kind, visible_text_chars=visible,
                         page_total_chars=page_total, total_extracted_chars=extracted,
                         total_size_bytes=extracted + 5_000)


def test_the_budget_window_the_caller_asked_for_is_not_extraction_loss():
    """靶站现场：整页 1,502 可见字、抽取器交回 1,491、用户点 600 字的帽 → 这一窗 448 字。"""
    r = _window(PROSE * 10, extracted=1491, visible=1502)
    assert len(r.content[0]) < 500
    assert extracted_chars(r) == 1491, "尺子还在读切完的那份"
    assert extraction_collapsed(r) is False
    assert _substance_verdict(r)[0] != "collapsed"


def test_a_crawl_rows_per_page_cap_is_not_extraction_loss():
    """uv CLI 参考页那一行：697,801 可见字、整页抽取 828,500、每页帽 500 → 行里 499 字。"""
    r = _window(PROSE * 5, extracted=828_500, visible=697_801)
    assert extraction_collapsed(r) is False, "帽切过的行被判成抽取器失手"


def test_a_real_loss_under_a_small_budget_is_still_collapsed():
    """反面锁：抽取器本来就只有 448 字（`total_extracted_chars` 与窗口同数）对 1,502 可见字。"""
    r = _window(PROSE * 10, extracted=len(PROSE) * 10, visible=1502)
    assert extraction_collapsed(r) is True, "这一刀把闸拆软了"


def test_the_collapse_sentence_quotes_the_number_the_verdict_used():
    """判决与那句散文读同一颗数：从前判决修好之后，句子还在现算 `content[]`（两个作者）。"""
    r = _window(PROSE * 10, extracted=1491, visible=1502)
    msg = _detect_content_issue(r)
    assert "extraction_collapsed" not in msg, msg
    lost = _window(PROSE * 10, extracted=len(PROSE) * 10, visible=1502)
    sentence = _detect_content_issue(lost)
    assert sentence.startswith("extraction_collapsed")
    assert f"returned {len(PROSE) * 10} " in sentence, sentence


# ─── 6 · R02-147：投影（`schema=` / `nested_*`）不参与塌陷那一问 ───

def test_a_two_layer_projection_is_not_called_an_extraction_loss():
    """现场：`nested_*` 两条作者生日共 325 字，页面自己有 1,681 可见字 —— 投影按定义丢字。"""
    r = _window('{"nested": [{"born": "March 14, 1879"}]}', extracted=325,
                visible=1681, kind="structured")
    assert extraction_collapsed(r) is False
    assert _substance_verdict(r)[0] != "collapsed", "recover_with 会开出 focus='' —— 用户那句的反面"


def test_a_schema_projection_that_matched_little_is_not_a_loss_either():
    """HN 首页第一发（裸 selector，只回来 51 字）：错在形状，不在抽取器。"""
    r = _window('{"rank": "1.", "title": "Linux 7.3 Released"}', extracted=51,
                visible=3984, kind="structured")
    assert extraction_collapsed(r) is False


def test_the_same_thin_body_as_markdown_is_still_a_loss():
    """反面锁：同一份薄正文，`extracted_type` 回到 markdown 就照旧判塌陷 —— 豁免只给投影。"""
    r = _window('{"rank": "1.", "title": "Linux 7.3 Released"}', extracted=51,
                visible=3984, kind="markdown")
    assert extraction_collapsed(r) is True, "豁免漏到了非投影那一路"


def test_a_capped_collapse_is_not_answered_with_pagination():
    """带帽 + 塌陷时，回执的主因是「抽取器丢字」，不是「翻下一页」。

    现场（R04-016b · P03/P04）：`c-api/stable` 带 `max_content_chars=1500` 回来，
    `diagnosis` 说 `extraction_type='html'`（跳过读取器），`next_action` 却说
    「page truncated … offset=950」。一封两句话互相否决；无帽时不存在（此时
    next_action 与 diagnosis 一致）。这里钉住：塌陷优先，翻页提示不许盖过它。
    """
    r = ResponseModel(status=200, content=["x" * 1500], url="https://example.com/a",
                      content_type="text/html", fetcher_used="http",
                      visible_text_chars=39327, total_extracted_chars=19066,
                      error=("extraction_collapsed: this page's own markup holds 39327 "
                             "characters of visible text while the extract returned 19066"),
                      is_truncated=True, next_offset=950, total_size_bytes=323375)
    summary, next_action, ok = _agent_hints(r)
    assert ok is False
    assert not next_action.startswith("page truncated"), next_action
    assert "extraction_type='html'" in next_action, next_action
