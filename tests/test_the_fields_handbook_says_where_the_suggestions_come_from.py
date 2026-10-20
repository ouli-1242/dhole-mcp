r"""手册里 `related_queries` 那一格要交代这些词是**从哪儿来的**（R02-036）。

现场（P02-10 邬桐 · S1 第三发正常英文检索 · R02-036 · P3 · 组 F）：

    web_search(query='community self-organization urban studies open access monograph')
      → related_queries = ['community definition', 'cluster analysis', 'definition meaning',
                           'english dictionary', 'som cluster']
    而 `references/fields.md` 那一格只写：`Related-search suggestions | Useful for a rephrase`

她的工作是写检索策略，这颗字段对她是有用的增量；坏在**没有出处口径**：客户端会把它读成
「引擎建议的更宽词」或「该主题的相关概念」，而这里两个词其实是**离题结果**带进来的
（同发结果第 2 条是 scirp 那篇用 self-organizing maps 的论文，与她的主题无关）。

读码与量（动文档之前先确认缺的是哪一层）：
  * **wire 上早就有这句**：`search.py` 的字段说明写着 "Follow-up queries worth searching next,
    mined extractively from the result titles+snippets (no LLM)" —— `git show 3a9c295` 里就在，
    不是本轮加的。所以「从查询推的还是从结果推的」在 schema 层已经有答案；
  * **缺的是手册层**：agent 实际读的是 `references/fields.md`（`sys_guide` 交回的那份），
    那一格只有两个词。这是 B-16 那一族（字段只活在 description 里、手册没覆盖）；
  * **不加新字段**：矿里那个 df 本轮量过（`review/.runtime/p4/f58_measure.txt`，14 条交回：
    df=2 的 12 条、df=3 的 2 条）——分布太窄，把「几家用过」做成一颗数只是把同一句话写两遍。
    「凭什么推荐这个词」由散文说清判据（两家以上 + 只隔空白 + 不是引擎建议），比一颗恒等于 2 的数有用。

契约（本文件钉住）：那一格必须说得出四件事，且**不许**与 schema 打架 ——
  1. 词是从**这一轮自己的**标题与摘要里摘出来的，没有模型参与，不是引擎给的建议词；
  2. 门槛是「两个只隔空白的相邻词」+「至少两家独立域名用过它」；
  3. 空着的时候是哪一种空由 `fetch_hint` 说（那一格自己不说假话也不静默）；
  4. 用法那一栏不许再把「换词重搜」写成无条件出路 —— 行里没有词边界时换词不改这一族的形状。
反面锁：schema 里那句 "mined extractively" 一字不动（改文档不是改判据）；这一格仍是表里的
一行三列（`|` 数不对就是把两行挤成一行）；句子不许带版本号 / ISO 日期（`fields.md` 在
`test_the_corpus_carries_no_maintainer_history.py` 的扫描名单里）。
"""

from __future__ import annotations

import re
from pathlib import Path

FIELDS = (Path(__file__).resolve().parent.parent / "src" / "dhole_mcp" / "skills"
          / "dhole-web" / "references" / "fields.md")


def _row() -> str:
    doc = FIELDS.read_text(encoding="utf-8")
    rows = [ln for ln in doc.splitlines() if ln.startswith("| `related_queries` |")]
    assert len(rows) == 1, "手册里这颗字段必须恰好一行"
    return rows[0]


def test_the_cell_names_where_the_words_come_from():
    row = _row()
    low = row.lower()
    assert "titles" in low and "snippets" in low, row[:200]
    assert "this round" in low, row                      # 是**这一轮**的行，不是全局话题表
    assert "no model" in low or "extractive" in low, row  # 不是模型想的
    assert "not engine" in low or "not from the engine" in low, row


def test_the_cell_states_both_thresholds():
    row = _row().lower()
    assert "two words" in row and "only space" in row, row      # R02-F54 的相邻定义
    assert re.search(r"two (or more|independent)|2\+ ", row) or "two independent" in row, row
    assert "domain" in row, row                                  # 独立来源按域名数


def test_the_cell_points_the_empty_case_at_fetch_hint():
    row = _row().lower()
    assert "fetch_hint" in row, row
    assert "empty" in row or "nothing" in row, row


def test_the_use_column_is_not_an_unconditional_rephrase_advice():
    """换词重搜只在「行里有词可配」时是出路；手册不许再把它写成无条件建议。"""
    tail = _row().rsplit("|", 2)[-2].strip()
    assert "rephrase" in tail.lower() and "unless" in tail.lower(), tail
    assert "no word boundaries" in tail.lower() or "rows have no" in tail.lower(), tail


def test_the_cell_is_still_one_row_of_three_columns():
    assert _row().count("|") == 4, _row()[:200]


def test_the_schema_wording_is_untouched():
    src = (Path(__file__).resolve().parent.parent / "src" / "dhole_mcp" / "search.py"
           ).read_text(encoding="utf-8")
    assert "mined extractively from the result titles+snippets (no LLM)" in src, \
        "这一刀改的是手册，不是判据也不是 schema"
