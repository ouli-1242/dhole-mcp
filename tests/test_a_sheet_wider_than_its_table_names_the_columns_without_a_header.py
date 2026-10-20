r"""工作表那一格交回的"表"其实是整张 used range：没表头的那几列要由回执自己说出来。

现场（P02-02 邝野 S6 xlsx · R02-024 · 画像 repro：openpyxl 造 A1:D7 一张 4 列表 + F2「合计」G2 138）：

    | 专业 | 计划人数 | 选课人数 | 班级数 |  |  |  |
    | --- | --- | --- | --- | --- | --- | --- |
    | 新闻学 | 42 | 7 | 两个班 |  | 合计 | 138 |

`合计 138` 住在 F2/G2 —— 与第一行数据毫无关系，可它现在坐在「新闻学」那一行的第 6、7 格里，
读表的人只能猜。原账 root_cause 写的是 unknown（待读码），复验后读到的是**两颗不同的东西**：

  * 宽度来自 `col_count = len(header)`，而 openpyxl 的 `iter_rows` 把每一行都补齐到整张表的
    used-range 宽度（F2/G2 那两个孤儿把宽度撑到 7）—— 所以 7 不是"这张表有 7 列"，是"这张纸有 7 列宽"；
  * 今天**没有任何一句话**说明过这件事（`says_anything=False`：五种形状全跑过一遍，回执里
    一个相关词都没有）。

先说什么**不能**改（这是本刀的量出来的边界，不是保守）：
  * 不能删列、不能并表。实测两种形状证明"空列"有两种意思：
    (c) 画像式 —— 空列 E 右边还站着孤儿列 F/G，删掉 E 就把「班级数」与「合计」贴在一起，
        正是读者投诉的那个误读**更严重的版本**；
    (e) 两块独立小表并排（A:C 与 E:F，中间靠 D 那一列空的分开）—— 那根空列是**唯一的分隔**，
        删了就把两张表合成一张。
    所以本刀一个单元格都不动：交付的表格字节与今天完全一致，走掉的只是那份沉默。
  * 也不能反过来把孤儿"搬"到表外 —— 搬就是重新解释数据（它可能本来就是这张表缺了表头的一列，
    形状 (d) 就是那种：第 3 列没有标题，但**每一行都有值**）。

契约（本文件钉住）：
  * 有几列没有表头 ⇒ 说出**几列、哪些列（Excel 的列名）、它们各自在哪些行有值**
    （行号用纸上的行号，与 F2/G2 那种说法同一套坐标）；
  * 一句话里必须同时给出「这张纸几列宽」与「其中几列有表头」两个数 —— 只有前一个数的话
    读者仍然不知道 7 是表的列数还是纸的列数；
  * **没有孤儿列时一个字不说**（干净 4 列表不产生这句话；每列都有表头的表也不产生）；
  * 一个单元格都不许移动或删除：五张表每一个非空单元格仍逐字出现在输出里（反面锁）；
  * 已有的两条注记（未缓存公式、还有多少行没显示）不许退化 —— 新句子接进同一处注记，
    与 `_parse_xlsx` 里"看起来像成功"那三条失败同一个通道；
  * 手册要看得见这句话（只读手册的 agent 也要知道 markdown 表的列数是**纸**的几何）。
"""

from __future__ import annotations

import re

import pytest
from openpyxl import Workbook

from dhole_mcp import parse

HEADER = ["专业", "计划人数", "选课人数", "班级数"]
ROWS = [
    ["新闻学", 42, 7, "两个班"],
    ["广播编导", 35, 6, "一个班"],
    ["广告学", 40, 8, "两个班"],
]


@pytest.fixture()
def sheet(tmp_path):
    def _build(cells) -> str:
        """cells(rows) 里第一行是表头行；列用 None 表示"这一格是空的"。"""
        p = tmp_path / "s.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.title = "S"
        for row in cells:
            ws.append([c for c in row])
        wb.save(p)
        return parse._parse_xlsx(str(p))

    return _build


def _rows(md: str) -> list[list[str]]:
    out = []
    for line in md.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c or "---") for c in cells):
            continue
        out.append(cells)
    return out


def _note(md: str) -> str:
    """`... (…)` 那一尾 —— 这张表自己的话住在正文里，注记住在这一尾。"""
    m = re.search(r"\.\.\. \((.*)\)", md, re.S)
    return m.group(1) if m else ""


# ─── 1 · 画像那一发：三列没有表头，就说三列 ────────────────────

def test_the_persona_sheet_says_which_columns_have_no_header():
    md = _persona()
    note = _note(md)
    assert "no header" in note, (note, md)
    assert "7" in note and "4" in note, note


def test_the_note_names_the_letters_and_where_the_values_are():
    md = _persona()
    note = _note(md)
    for letter in ("E", "F", "G"):
        assert letter in note, (letter, note)
    assert "row 2" in note, note


def test_the_grid_is_not_touched():
    """一个单元格都不移动、不删除：孤儿仍在 F2/G2 那一行。"""
    md = _persona()
    row = _rows(md)[1]
    assert row[:4] == ["新闻学", "42", "7", "两个班"], row
    assert row[5] == "合计" and row[6] == "138", row
    assert len(row) == 7, row


# ─── 2 · 沉默的那几格 ─────────────────────────────────────────

def test_a_clean_table_says_nothing(sheet):
    md = sheet([HEADER, *ROWS])
    assert "no header" not in _note(md), _note(md)


def test_a_table_where_every_column_has_a_title_says_nothing(sheet):
    md = sheet([["甲", "乙", "丙", "丁"], ["1", "2", "3", "4"]])
    assert "no header" not in _note(md), _note(md)


# ─── 3 · 三种"没表头"各有不同的话 ─────────────────────────────

def test_a_column_with_no_header_but_data_in_every_row_is_not_called_empty(sheet):
    """形状 (d)：第 3 列没标题，但每行都有值 —— 这是"这张表缺了个标题"，不是纸边的孤儿。"""
    md = sheet([["专业", "计划人数", "", "班级数"],
                ["新闻学", 42, 7, "两个班"],
                ["广告学", 40, 8, "一个班"]])
    note = _note(md)
    assert "C" in note and "every row" in note.lower(), note


def test_a_spacer_column_that_carries_nothing_says_it_carries_nothing(sheet):
    """形状 (c)/(e)：整列全空的那一根要说"什么都没有"，因为它是唯一的分隔。"""
    md = sheet([["专业", "", "计划人数", "选课人数"],
                ["新闻学", "", 42, 7], ["广告学", "", 40, 8]])
    note = _note(md)
    assert "B" in note, note
    assert "no value" in note or "empty" in note, note


def test_the_two_side_by_side_tables_lose_no_column(sheet):
    """形状 (e)：A:C 与 E:F 两块并排 —— 删掉中间那根空列就把两张表合成一张。"""
    md = sheet([["专业", "计划", "选课", "", "季度", "收入"],
                ["新闻学", 42, 7, "", "Q1", 900],
                ["广告学", 40, 8, "", "Q2", 1200]])
    assert len(_rows(md)[0]) == 6, _rows(md)
    assert "季度" in md and "收入" in md, md
    assert "no header" in _note(md), _note(md)


# ─── 4 · 不退化的邻居 ─────────────────────────────────────────

def test_the_existing_notes_still_travel_together(sheet, monkeypatch):
    """未缓存公式那一句与本句同处一个注记表；两句话同时在，谁也不许挤掉谁。"""
    from dhole_mcp import parse as P

    # E 列（索引 4）没有表头，且第一行数据那格是一条没有缓存值的公式
    monkeypatch.setattr(P, "_xlsx_uncached_formulas",
                        lambda fp: {("S", 1, 4): "=SUM(A2:D2)"})
    md = sheet([["专业", "计划人数", "选课人数", "班级数", "", ""],
                ["新闻学", 42, 7, "两个班", None, None]])
    note = _note(md)
    assert "no cached value" in note, note
    assert "no header cell" in note, note
    # 这一发只有一行数据，E 的那半句是"每一行都有值"（不是"只在第 2 行"）——
    # 行号那一格由画像那一发钉住，这里钉的是两句话共处一处。
    assert "E" in note and "F" in note, note


def test_every_non_empty_cell_survives_across_all_shapes(sheet):
    for cells in ([HEADER, *ROWS],
                  [["专业", "", "计划人数", "选课人数"], ["新闻学", "", 42, 7]],
                  [["专业", "计划", "选课", "", "季度", "收入"],
                   ["新闻学", 42, 7, "", "Q1", 900]],
                  [["专业", "计划人数", "", "班级数"], ["新闻学", 42, 7, "两个班"]]):
        md = sheet(cells)
        for row in cells:
            for value in row:
                if value not in (None, ""):
                    assert str(value) in md, (cells, value, md[:400])


# ─── 5 · 手册 ─────────────────────────────────────────────────

def test_the_manual_says_the_grid_is_the_sheets_geometry():
    from dhole_mcp import guide
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))
    assert "no header cell" in corpus or "carry no header" in corpus, (
        "xlsx 那格的形状没进手册：读者只能从 markdown 表自己猜 7 是纸的宽度还是表的列数")


def _persona(_ignored=None) -> str:
    """画像那张表的**字节**：A1:D7 四列表 + F2「合计」/ G2 138。"""
    import tempfile
    from pathlib import Path

    p = Path(tempfile.mkdtemp()) / "选课分布.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "选课分布"
    ws.append(HEADER)
    for r in ROWS:
        ws.append(r)
    ws["F2"] = "合计"
    ws["G2"] = 138
    wb.save(p)
    return parse._parse_xlsx(str(p))
