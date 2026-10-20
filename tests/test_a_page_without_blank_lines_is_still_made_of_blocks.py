# -*- coding: utf-8 -*-
"""没有空行的长文档也是块拼起来的；切得更细之后，块的数目要一起交出来。

一份 81,000 字、段落之间只有**单换行**的在线文档，在只认空行的切法下是**一整块**。
那一格里 focus 什么都做不了，而 `focus_content` 为此留着一句道歉：
"this page is one block, so there is nothing to filter"。那句话是诚实的 —— 它的存在
本身才是缺陷：读者付了整页的 token，换来的解释是"这一页没法切"。

判据的形状：

1. 界不止空行一种 —— ATX 标题、代码围栏、表格、水平线、列表串的开始都是界；一块还有
   `MAX_BLOCK_CHARS` 的上限，超长的一处在句界断开。
2. 切得更细**不许少一个字**：原文写下的每一行都落在恰好一块里、顺序不变；断到的那一处
   `"".join(parts)` 与原来那块逐字相等（断点落在标点**之后**，标点不是被吃掉的）。
3. 结构性正文不按长度处置：围栏内永不切（包括围栏里那些长得像标题、列表、表格的行），
   表格永不断 —— 掉了两句的正文还能读，掉了三行的表读起来是假数据。
4. 切细之后「不许静默丢内容」这笔税要当场结（3.6）：`focus_blocks_total` /
   `focus_blocks_kept` 把块数写在响应上。正文头部那句 "showing 3 of 17 blocks" 住在
   正文里，从第 2 片起就被分页切走；两颗数与它同源（同一份 stats，不重算）。
"""

from __future__ import annotations

import re

import pytest

from dhole_mcp import wire
from dhole_mcp.context import _FOCUS
from dhole_mcp.focus import (
    MAX_BLOCK_CHARS,
    _CLAUSE_BREAKS,
    _SENTENCE_BREAKS,
    _cap_block,
    _is_code,
    _is_table,
    _split_blocks,
    focus_content,
)
from dhole_mcp.models import ResponseModel
from dhole_mcp.server import _apply_chunking

ANSWER = "会话 cookie 的存活时间由 DHOLE_COOKIE_TTL_S 决定，默认 3000 秒。"
FILLER = "第 {i} 段讲的是别的事：版面、字体回退，以及一些与本题无关的说明。"
BOUNDARIES = _SENTENCE_BREAKS + _CLAUSE_BREAKS


def _dense_doc(paras: int = 24) -> str:
    """一节一段、节与节之间只有**单换行**的一页 —— 旧切法在这里只得到一块。"""
    mid = paras // 2
    body = [f"## 第 {i} 节\n" + (ANSWER if i == mid else FILLER.format(i=i))
            for i in range(paras)]
    return "\n".join(body)


def _chunked(text: str, *, focus, max_chars: int = 4_000,
             offset: int = 0) -> ResponseModel:
    result = ResponseModel(url="https://e.test/dense", status=200,
                           content=[text], extracted_type="markdown")
    token = _FOCUS.set(focus)
    try:
        return _apply_chunking(result.model_copy(), max_chars=max_chars, offset=offset)
    finally:
        _FOCUS.reset(token)


def _lines(blocks: list[str]) -> list[str]:
    return [ln for b in blocks for ln in b.splitlines() if ln.strip()]


# 八种形状。`CAPPED` 里的那一种会被长度上限在一行**中间**断开（它整行只有一个界都找
# 不到），所以行级不变量对它不成立 —— 对它成立的是字符级的那一条。
CAPPED = {"one_line_with_no_boundary"}
SHAPES = {
    "sections_single_newlines": _dense_doc(),
    "prose_single_newlines": "\n".join(FILLER.format(i=i) for i in range(30)),
    "headings_only": "\n".join(f"# 第 {i} 节" for i in range(8)),
    "list_run": "\n".join(["步骤如下：", "- 安装", "- 重启", "- 验证",
                           "", "下一步讲配置。", "先设 DHOLE_HOME，再起宿主。"]),
    "table": ("| 列 | 值 | 备注 |\n| --- | --- | --- |\n"
              + "\n".join(f"| 第{i}行 | {i} | 备注内容 |" for i in range(40))),
    "fence": ("```python\n"
              + "\n".join(f"x_{i} = {i}  # 注释" for i in range(40))
              + "\n```\n后面还有一句。"),
    "rule": "上一节收尾。\n---\n下一节开始，讲分页。",
    "one_line_with_no_boundary": "字" * (MAX_BLOCK_CHARS * 3 + 17),
}


# ─── 1 · 无空行的一页也切得动 ────────────────────────────────────

def test_a_page_without_blank_lines_is_not_one_block():
    doc = _dense_doc()
    assert "\n\n" not in doc, "夹具里有空行就不是这一条要的形状了"
    assert len(_split_blocks(doc)) > 1, (
        "只认空行的话这里就是一整块 —— 3.5 要修的正是这一格")


def test_focus_cuts_a_dense_page_instead_of_apologising():
    doc = _dense_doc()
    stats: dict = {}
    out = focus_content(doc, "cookie 存活时间", stats=stats)
    assert not stats.get("ignored"), f"这一页现在切得动，道歉不该还在：{stats}"
    assert ANSWER in out, "省 token 不许以丢掉答案为代价"
    assert len(out) < len(doc) / 2, (
        f"切完还留下 {len(out)}/{len(doc)} 字：那一页在打分器眼里仍是一块")


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_no_written_character_disappears_from_the_finer_cut(name):
    """切得更细的代价不能是"悄悄少了几行"：把块拼回去，一个字都不差。"""
    text = SHAPES[name]
    joined = "".join(_split_blocks(text)).replace("\n", "")
    assert joined == text.replace("\n", "")


@pytest.mark.parametrize("name", sorted(set(SHAPES) - CAPPED))
def test_every_written_line_survives_the_finer_cut(name):
    """不被长度上限打断的那些形状里：原文每一行按原顺序落在恰好一块里。"""
    text = SHAPES[name]
    assert _lines(_split_blocks(text)) == [ln for ln in text.splitlines() if ln.strip()]


# ─── 2 · 上限与断点 ──────────────────────────────────────────────

def test_the_cap_returns_its_block_character_for_character():
    block = "第一句话说明一个配置项。" * 1_200
    assert len(block) > 5 * MAX_BLOCK_CHARS
    parts = _cap_block(block)
    assert len(parts) > 1, "超长的一块没有断开，上限就只是文档里的一个数"
    assert "".join(parts) == block, (
        "断点若落在标点之前，`。` 就被吃掉了 —— 那是丢内容，不是切块")


def test_a_cut_part_ends_on_a_boundary_not_mid_sentence():
    parts = _cap_block("段落 0 讲 alpha, beta; gamma - delta。下一句说明配置项。" * 400)
    assert len(parts) > 2, "没断成几块，这一条就没有对照"
    for part in parts[:-1]:
        assert len(part) <= MAX_BLOCK_CHARS
        assert part.endswith(BOUNDARIES), f"断点不在界上：…{part[-12:]!r}"


def test_a_run_with_no_boundary_anywhere_still_respects_the_cap():
    """一个界都找不到时，宁可硬切也不留一块 100k：BM25 的平均文档长度会被一个巨块拉平。"""
    whole = "字" * (MAX_BLOCK_CHARS * 2 + 5)
    parts = _cap_block(whole)
    assert [len(p) for p in parts] == [MAX_BLOCK_CHARS, MAX_BLOCK_CHARS, 5]
    assert "".join(parts) == whole


def test_no_prose_block_outlives_the_cap():
    text = "\n".join(FILLER.format(i=i) for i in range(400))
    blocks = _split_blocks(text)
    assert len(text) > 5 * MAX_BLOCK_CHARS, "夹具不够长，这一条就没有对照"
    assert len(blocks) > 1, "整页一块：上限没有起作用"
    for block in blocks:
        assert len(block) <= MAX_BLOCK_CHARS


# ─── 3 · 结构性正文不按长度处置 ─────────────────────────────────

def test_a_code_fence_is_never_cut_from_its_middle():
    body = "\n".join(f"    line_{i} = {i}  # 注释" for i in range(700))
    blocks = _split_blocks(f"```python\n{body}\n```\n")
    assert len(blocks) == 1, f"围栏被长度断了：{len(blocks)} 块"
    assert len(blocks[0]) > MAX_BLOCK_CHARS, "夹具短到不需要断，这一条就没有内容"
    assert _is_code(blocks[0])


def test_a_heading_inside_a_fence_stays_inside_the_fence():
    text = "```md\n# 这行像标题\n- 这行像列表\n| a | b |\n```\n围栏外的一句。"
    blocks = _split_blocks(text)
    assert _is_code(blocks[0]), "围栏里的形状被当成了形状，代码块就被拆开了"


def test_a_wide_table_is_never_cut_into_a_fake_one():
    rows = "\n".join(f"| 第{i}行 | 值{i} | 备注内容内容内容 |" for i in range(400))
    blocks = _split_blocks(f"| 列 | 值 | 备注 |\n| --- | --- | --- |\n{rows}")
    assert len(blocks) == 1, f"表被断成 {len(blocks)} 块"
    assert len(blocks[0]) > MAX_BLOCK_CHARS
    assert _is_table(blocks[0])


def test_a_table_is_its_own_block_not_a_paragraph_appendage():
    blocks = _split_blocks("先看这张表：\n| 列 | 值 |\n| --- | --- |\n| a | 1 |")
    assert blocks[-1].splitlines()[0] == "| 列 | 值 |", (
        "表头没有另起一块，`_is_table` 就读不到这张表")


# ─── 4 · 五种界各归一位 ─────────────────────────────────────────

def test_a_heading_keeps_the_paragraph_under_it():
    blocks = _split_blocks("# 安装\n先运行 pip install dhole-mcp，再重启宿主。")
    assert len(blocks) == 1
    assert blocks[0].startswith("# 安装"), "标题与它引出的正文是一块，这才是「这一块讲什么」"


def test_a_run_of_list_items_is_one_block():
    run = next(b for b in _split_blocks(SHAPES["list_run"]) if b.startswith("- 安装"))
    assert run.splitlines() == ["- 安装", "- 重启", "- 验证"], (
        "连续的条目讲的是一件事，把它们拆开就是三条都不完整")


def test_a_horizontal_rule_starts_a_block():
    blocks = _split_blocks(SHAPES["rule"])
    assert blocks[1].splitlines()[0] == "---", "水平线是界，不是上一段的续行"


def test_a_long_document_without_blank_lines_becomes_rankable():
    """81k 字那一份的真实形状：分节标题 + 单换行正文。旧切法一块，新切法几十块。"""
    text = "\n".join(f"## 第 {i} 节\n" + FILLER.format(i=i) * 3 for i in range(60))
    blocks = _split_blocks(text)
    assert len(text) > 5_000
    assert len(blocks) >= 20, f"只有 {len(blocks)} 块：这一页在打分器眼里还是一整块"
    assert _lines(blocks) == [ln for ln in text.splitlines() if ln.strip()]


# ─── 5 · 块账（3.6）────────────────────────────────────────────

def test_the_ledger_matches_the_count_written_in_the_body():
    stats: dict = {}
    out = focus_content(_dense_doc(), "cookie 存活时间", stats=stats)
    said = re.search(r"showing (\d+) of (\d+) blocks", out)
    assert said, out[:160]
    assert (int(said.group(1)), int(said.group(2))) == (
        stats["blocks_kept"], stats["blocks_total"]), (
        "头部说的数与账本不是同一份 —— 那就是两个作者各数一遍")


def test_best_mode_counts_the_block_it_kept():
    stats: dict = {}
    out = focus_content(_dense_doc(), "cookie 存活时间", mode="best", stats=stats)
    assert int(re.search(r"top of (\d+) by", out).group(1)) == stats["blocks_total"]
    body = out.split("\n\n", 1)[1]
    assert len([b for b in body.split("\n\n") if b.strip()]) == stats["blocks_kept"], (
        "账上的块数与实际交回的块数不符")


def test_a_no_op_focus_still_reports_a_ledger():
    """整页原样留下=一块都没丢，不是"没数" —— 这一格空白会被读成过滤器坏了。"""
    stats: dict = {}
    one = "整页只有一块正文，没有空行，也没有能当界用的形状。"
    assert focus_content(one, "正文", stats=stats) == one
    assert "one block" in stats["ignored"], stats
    assert (stats["blocks_total"], stats["blocks_kept"]) == (1, 1)


def test_the_ledger_is_on_the_wire_only_when_focus_ran():
    doc = _dense_doc()
    dumped = wire._wire_dump(_chunked(doc, focus=None))
    assert "focus_blocks_total" not in dumped
    assert "focus_blocks_kept" not in dumped, "没跑 focus 的一发在付块账的字节"

    loud = _chunked(doc, focus="cookie 存活时间")
    assert loud.focus_blocks_total > 1
    assert 0 < loud.focus_blocks_kept < loud.focus_blocks_total


def test_a_continuation_page_still_carries_the_ledger():
    """头部那句住在第 0 片里，翻页之后就没了 —— 数不能跟着没。"""
    doc = _dense_doc(40)
    stats: dict = {}
    focus_content(doc, "版面 字体", stats=stats)
    out = _chunked(doc, focus="版面 字体", max_chars=60, offset=60)
    assert "[Focus:" not in out.content[0], "夹具没把头部切走，这一条就没有对照"
    assert out.focus_blocks_total == stats["blocks_total"]
    assert out.focus_blocks_kept == stats["blocks_kept"], (
        "信封上的块数与打分器数出来的不一致 —— 又是两个作者各数一遍")


def test_the_no_more_content_branch_carries_the_ledger_too():
    first = _chunked(_dense_doc(), focus="cookie 存活时间", max_chars=10, offset=0)
    out = _chunked(_dense_doc(), focus="cookie 存活时间", max_chars=10, offset=10 ** 6)
    assert out.content == ["[No more content.]"], out.content
    assert (out.focus_blocks_total, out.focus_blocks_kept) == (
        first.focus_blocks_total, first.focus_blocks_kept), (
        "同一页、同一个 focus 词的两发交出两个块数 —— 那本书就不是同一本了")
