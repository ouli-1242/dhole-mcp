"""`extraction_collapsed` 的出路只有一个作者，而它只推荐真能换一条管路的那几枚。

症状（15 场景真人体测，场景 3；见 `review/2026-10-10-用户体验模拟报告.md`）：一处静态文本
站触发 collapsed，dhole 把 `extraction_type='html'` 与 `'text'` **并列**劝了三遍 —— `error`
散文、`next_action`、`diagnosis.recover_with` 各一遍。照 `'text'` 试的那一发回来 527/1,998
字、同一条 collapsed、同样那两句建议。一句建议把人送回他刚刚失败的那一步，而那一发确实
花了 1.5 秒和一次往返。

道理不在"哪枚取值抽得更狠"，在**抽取管路只有两条**：markdown / text / article 都过 dhole
的读取器（塌陷正是它出的事），只有 `html` 与 `schema=` 绕开它。所以在前三枚之间换一枚不是
"换档"，是把同一件坏掉的东西再量一次。`force_fetcher='stealthy'` 同理 —— 浏览器把同一份
静态标记交给同一个读取器（批次 2 收过那一半，见
`test_advice_never_recommends_the_tier_that_just_failed.py` §4）。本文件把两支并成一条判据：
**建议点名的 extraction_type 取值必须属于"换管路"那一族**。

三个落点必须同一句：`_detect_content_issue`（散文）、`_agent_hints`（`next_action`）、
`_DIAGNOSIS_PREFIXES`（`diagnosis.recover_with` 的表）。手写三遍就有三份措辞，而三份措辞
迟早有一份还留着 `'text'` —— 判据因此写成"三处都**等于**那颗常量"（表那一处用 `is`），
不是"三处意思差不多"。

一条长度判据混在这里不是跑题：`server._inline_hole_reason` 给爬取行的 reason 有
`HOLE_REASON_CAP` 的帽，而那一行是那条通道上唯一带着这句话的地方（`server.py` 里那颗数字
的注释把"出路不许被剪掉"写成它存在的原因）。整句量得进帽，由 §4 量。
"""

from __future__ import annotations

import inspect
import re

from dhole_mcp import guide, quality
from dhole_mcp.envelope import page_type_from_error
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import (_agent_hints, _COLLAPSE_RECOVERY, _detect_content_issue,
                               _diagnose, _DIAGNOSIS_PREFIXES, classify_error_text)
from dhole_mcp.server import _clip_reason, HOLE_REASON_CAP

URL = "https://example.com/travel"

#: 过 dhole 读取器的那几枚 —— 塌陷页换它们不是出路。
READER_VALUES = frozenset({"markdown", "text", "article"})
#: 绕开读取器的那两条路。
SKIP_THE_READER = frozenset({"html"}) | {"schema="}


def _collapsed(**updates) -> ResponseModel:
    """现场形状：静态列表页，DOM 有 1,374 个可见字符而抽取只回 176。

    `visible_text_chars`（3.3 起上线的公开字段）是 HTML 还在手边时量好的（见 `_attach_dom_visible_chars`），
    判据只读它，所以这里直接摆数 —— 与
    `test_a_list_page_is_not_called_a_js_shell.py` §2 用的是同一个形状。

    `error` 与 `content_ok` 按**产地**的写法补齐（`check_content_quality` 把
    `_detect_content_issue` 的判决抄进 `error`，server 再把 `content_ok` 翻成 False）：
    本文件量的是三个落点的话术，而它们读的都是装配完的那一层 —— 留 `error` 空着的话，
    `_agent_hints` 会走 page_type 那一支（实测先撞上过一次：塌陷的一张 list 页被劝去
    `web_crawl`，正是同一条管路）。
    """
    base = dict(status=200, content=["£45.17 In stock " * 11], url=URL,
                fetcher_used="http", content_type="text/html",
                total_size_bytes=48_000, page_type="list")
    base.update(updates)
    result = ResponseModel(**base)
    result.visible_text_chars = 1_374
    issue = _detect_content_issue(result)
    result.error = issue
    result.content_ok = not issue
    return result


def _prose(result: ResponseModel) -> str:
    return _detect_content_issue(result)


def _hint(result: ResponseModel) -> str:
    return _agent_hints(result)[1]


def _recover(result: ResponseModel) -> str:
    return (_diagnose(result) or {}).get("recover_with", "")


# ─── 1 · 一个作者，三个落点 ─────────────────────────────────────

def test_the_three_landing_points_quote_the_one_sentence():
    """散文、`next_action`、`recover_with` 三处都带着那颗常量**原文**。

    反向：把任意一处改回手写的一句同义话，这一条红 —— 它比的是同一串字符，不是口径。
    """
    r = _collapsed()
    prose = _prose(r)
    assert prose.startswith("extraction_collapsed:"), prose
    for where, text in (("error", prose), ("next_action", _hint(r)),
                        ("diagnosis.recover_with", _recover(r))):
        assert _COLLAPSE_RECOVERY in text, (where, text)


def test_the_table_holds_the_constant_itself_not_a_copy_of_it():
    """`_DIAGNOSIS_PREFIXES` 里那一格必须是同一个对象，不是第二次打字。

    `is` 是有意的：两张不同的字符串可以长得一模一样并各自演化，而 `row_diagnosis`
    （feed 的行、批量的行）读的正是这张表 —— 那里是这句话的第四个落点。
    """
    entry = [t for t in _DIAGNOSIS_PREFIXES if t[0] == "extraction_collapsed"]
    assert len(entry) == 1, entry
    assert entry[0][3] is _COLLAPSE_RECOVERY

def test_the_row_channel_says_the_same_thing_as_the_envelope():
    """没有 ResponseModel 的那条通道（`classify_error_text`）也读同一句。"""
    code, retryable, recover = classify_error_text(_prose(_collapsed()))
    assert code == "extraction_collapsed", code
    assert retryable is False, "换 extraction_type 是一次新调用，不是同一份的重发"
    assert recover is _COLLAPSE_RECOVERY


def test_nobody_retyped_the_advice_next_to_the_constant():
    """静态守卫：`quality.py` 里点名"不过读取器"那几枚取值的句子必须一处都没有。

    `schema_no_match` 那支（"fetch with extraction_type='html' to inspect the markup"）
    不在射程：它说的是另一件事（选择器没中，要看标记），而它推荐的正是绕开读取器的那枚。
    判据只认带引号的 `'markdown'` / `'text'` / `'article'` —— 常量讲道理时用无引号的
    markdown / text / article，一旦有人把它抄回带引号的形状，那就是把失败的那一步重新
    包装成出路（场景 3 的账）。
    """
    src = inspect.getsource(quality)
    quoted = [line.strip() for line in src.splitlines()
              if re.search(r"extraction_type='(?:markdown|text|article)'", line)]
    assert not quoted, f"有人手抄了一句建议，还点了共用读取器的取值：{quoted}"
    assert src.count("_COLLAPSE_RECOVERY") >= 5, (
        f"常量现在有 {src.count('_COLLAPSE_RECOVERY')} 处引用（定义之外该有 5 处："
        "散文 / next_action / 表 / 两处注释指向）；少了就是有一处改回了手写")


# ─── 2 · 点名的取值必须换一条管路 ───────────────────────────────

def test_every_extraction_type_the_advice_names_skips_the_reader():
    names = set(re.findall(r"extraction_type='([a-z]+)'", _COLLAPSE_RECOVERY))
    assert names, "建议里没有点名任何 extraction_type 取值"
    assert names <= SKIP_THE_READER, (
        f"塌陷页被劝去换 {sorted(names - SKIP_THE_READER)} —— 那一枚过的是刚刚丢掉正文的"
        "同一个读取器")


def test_the_other_door_is_named_too():
    """只劝 `html` 会缺一半：`schema=` 同样不经过读取器，而它是有名字地取字段的那条。"""
    assert "schema=" in _COLLAPSE_RECOVERY


def test_the_sentence_says_why_the_three_values_are_not_a_change_of_door():
    """光说"用 html"不够：调用方手里那份默认就是 markdown，他要知道换一枚不算换档。

    实测的那一发是照着 `'text'` 那句去做的 —— 所以这一句必须点名 markdown/text/article
    并且说明它们**共用**同一个刚刚失败的读取器。
    """
    for value in sorted(READER_VALUES):
        assert value in _COLLAPSE_RECOVERY, value
    assert "same reader" in _COLLAPSE_RECOVERY or "re-run the reader" in _COLLAPSE_RECOVERY
    assert "browser" in _COLLAPSE_RECOVERY, "起浏览器是同一族的第二条回路，也要说否"
    assert "stealthy" not in _COLLAPSE_RECOVERY and "escalat" not in _COLLAPSE_RECOVERY


def test_the_quoted_form_of_the_failed_value_is_offered_nowhere():
    """措辞的分工：带引号的是"把这颗传进来"（建议），不带引号的是提到有一枚这样的取值。

    本文件的常量用无引号的 markdown / text / article 讲道理；一旦有人把它抄回带引号的
    `'text'`，那就是把刚刚失败的那一步重新包装成出路 —— 场景 3 的账。
    """
    r = _collapsed()
    for where, text in (("error", _prose(r)), ("next_action", _hint(r)),
                        ("recover_with", _recover(r))):
        assert "'text'" not in text, (where, text)
        assert "'markdown'" not in text and "'article'" not in text, (where, text)


# ─── 3 · 反面守卫：新话术不许把别的判决一起改掉 ─────────────────

def test_a_real_shell_still_hears_about_the_browser():
    """塌陷那一支改了措辞，壳那一支一个字不许跟着变弱。"""
    shell = ResponseModel(status=200, content=["<div id=root></div>"], url=URL,
                          fetcher_used="http", content_type="text/html",
                          total_size_bytes=48_000, page_type="js_shell",
                          error="js_shell_detected: 48,000-byte body with 120 chars")
    hint = _hint(shell)
    assert "stealthy" in hint, hint
    assert "extraction_collapsed" not in hint, hint


def test_the_collapse_is_still_not_a_js_shell():
    """两支的分工没被合并：塌陷不产出"要渲染"的判决，也不盖掉结构化 page_type。"""
    issue = _prose(_collapsed())
    assert "js_shell" not in issue, issue
    assert page_type_from_error(issue) == "", issue


def test_a_page_that_was_read_whole_gets_no_advice_at_all():
    """没塌就不许说话：1,374 个可见字符而抽回 1,640 —— 闸在另一边也要关着。"""
    fine = _collapsed(content=["£45.17 In stock " * 120])
    assert _prose(fine) == "", _prose(fine)
    assert _recover(fine) == ""


# ─── 4 · 帽与手册 ──────────────────────────────────────────────

def test_the_whole_sentence_fits_the_crawl_rows_reason_cap():
    """爬取行是这句话唯一的落点，所以它不许被 `HOLE_REASON_CAP` 剪掉出路。

    两边都要量：塌陷句本身（最宽的位数），和 `_clip_reason` 的原样返回 —— 后者是"这一行
    确实没被剪"的收据，而不是"帽大概够"。
    """
    worst = _collapsed(content=["x"])
    worst.visible_text_chars = 999_999
    prose = _prose(worst)
    assert len(prose) <= HOLE_REASON_CAP, (len(prose), HOLE_REASON_CAP, prose)
    assert _clip_reason(prose) == prose
    assert _clip_reason(prose).endswith(_COLLAPSE_RECOVERY)


def test_the_manual_names_the_same_two_doors_and_the_same_measured_case():
    """三个落点上了话，解释就必须住在只读手册的 agent 也看得见的卷里。

    量的是那一条**实测**（527/1,998）和"哪两枚是门"—— 一句改写过的建议如果没有留下
    证据，读手册的人就只知道结论，下一次照样并列劝两枚。
    """
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))
    assert "extraction_collapsed" in corpus, "手册里没有这一族的词条"
    for needle in ("This is the door", "not doors", "1,998 characters"):
        assert needle in corpus, f"{needle!r} 不在语料里"
    # 旧文案把两枚并列成 `(or 'text')`，原样存在过 —— 手册与代码各说一套正是这一族反复
    # 付过的账（`test_wire_moves_land_in_the_manual.py`）。这一条不许那句话回来。
    assert "(or 'text')" not in corpus
