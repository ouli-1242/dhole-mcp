"""PDF 黄金集：把提取器**今天**的输出钉住，P1/P2/P3 动的每一格都要看得见（P5）。

为什么建在改动之前：这批要动的是表格渲染、标题层级和 TOC 的页码上界，而
`tests/test_pdf_real.py` 里 6 条用例**没有一条**断言过表格、TOC、标题或图。改完之后
"看起来对了"是这句被点名的失败在本仓库的又一次出场 —— 唯一能让"我只修了合并单元格"
这句话成立的证人，是**其余部分逐字节没动**。

两个证人各有各的活，缺一个都不算：

- **字符串**（`tests/pdf_golden/<fixture>.md`）：现场。它说"今天这台 pdfplumber 交回
  的就是这段字节"。一次全表输出的漂移不许靠"性质还成立"混过去。
- **性质**（下面 `test_every_*` 那几条）：方向。字符串钉死会把 pdfplumber 的版本号也
  钉进仓库，所以真正要长期守的是"不存在非空单元格数与表头不符的行""TOC 的 end_page
  不许越过这份文档的页数"这类不依赖版本的断言。

刷新黄金集 = `python tests/pdf_golden_record.py`，而它是一次**有意识的**决定：把
markdown 的 diff 读完再刷，并把 delta 写进提交信息（与 `tests/mcp_snapshot.py` 的
census 同一规矩）。手抄、或为了让用例绿而改黄金集，都是把证人换成被告。

`tests/merged_cells.pdf` 的来路（CONTRIBUTING: never hand-write a fixture —— PDF 是
二进制，手写的 PDF 只会画出"我以为表格长成的样子"，所以它是一份**打印结果**，源文件是
入库的 `tests/merged_cells.html`）：

    "C:/Program Files/Google/Chrome/Application/chrome.exe" \
      --headless=new --disable-gpu --no-pdf-header-footer \
      --print-to-pdf="D:/tools/dhole-mcp/tests/merged_cells.pdf" \
      "file:///D:/tools/dhole-mcp/tests/merged_cells.html"

命令原样记在这里，也在 `tests/merged_cells.html` 的注释里；重跑它要产出同一个文件，
否则那份 HTML 就不是作者而是装饰品。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from dhole_mcp.pdf_extractor import extract_pdf

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "pdf_golden"
FIXTURES = ["merged_cells.pdf", "background_checks.pdf", "dummy.pdf"]

_TABLE_SEP = re.compile(r"^\|(?: :?---:? \|)+$", re.MULTILINE)


@pytest.fixture(scope="module")
def extracted():
    return {name: extract_pdf((HERE / name).read_bytes()) for name in FIXTURES}


def _markdown(res) -> str:
    return "\n".join(res.content)


def _tables(markdown: str) -> list[list[list[str]]]:
    """从 markdown 里读出每张表：表头行 + 跳过分隔行 + 后续数据行，直到非 `|` 行。

    解析写在测试里是刻意的：黄金集要能独立复核（读 JSON/文本就能重言同一件事），而
    "提取器自己数出的表"不能作为自己输出的证人。
    """
    out = []
    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        if (i + 1 < len(lines) and lines[i].startswith("|")
                and _TABLE_SEP.match(lines[i + 1].strip())):
            rows = [[c.strip() for c in lines[i].strip().strip("|").split("|")]]
            j = i + 2
            while j < len(lines) and lines[j].startswith("|"):
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            out.append(rows)
            i = j
        else:
            i += 1
    return out


class TestTheGoldenFilesAreThePoint:

    @pytest.mark.parametrize("name", FIXTURES)
    def test_the_markdown_is_recorded_byte_for_byte(self, extracted, name):
        stem = name.rsplit(".", 1)[0]
        want = (GOLDEN / f"{stem}.md").read_text(encoding="utf-8")
        got = _markdown(extracted[name])
        assert got == want, (
            f"{name} 的输出与黄金集不一致。先读 diff 再决定：是提取器变了（那就是这次"
            "要看的 delta，跑 python tests/pdf_golden_record.py 重录并把 delta 写进提交"
            "信息），还是这一改动本来就不该动它。")

    @pytest.mark.parametrize("name", FIXTURES)
    def test_the_recorded_facts_match(self, extracted, name):
        stem = name.rsplit(".", 1)[0]
        facts = json.loads((GOLDEN / f"{stem}.json").read_text(encoding="utf-8"))
        res = extracted[name]
        for key, value in facts.items():
            got = (list(res.table_of_contents) if key == "table_of_contents"
                   else list(res.pages_extracted) if key == "pages_extracted"
                   else getattr(res, key))
            assert got == value, f"{name}.{key}: 记录的是 {value!r}，现在量到 {got!r}"


class TestTheStructureProperties:
    """性质断言：不依赖 pdfplumber 版本，是这批修完之后长期要守的那一半。"""

    @pytest.mark.parametrize("name", FIXTURES)
    def test_every_row_has_as_many_cells_as_its_header(self, extracted, name):
        """一张 markdown 表里不许出现"格子数与表头不符"的行。

        今天它是靠 `_table_to_markdown` 补 `""` 才成立的 —— 补出来的空格正是 P1 要
        处理的谎：跨列合并的续格被写成"这一格没有数据"。所以这条守的是**结构完整性**
        （不塌列），而下面那条命名的才是**语义**损失。
        """
        for idx, table in enumerate(_tables(_markdown(extracted[name])), 1):
            header = table[0]
            bad = [(n + 1, len(r)) for n, r in enumerate(table) if len(r) != len(header)]
            assert not bad, f"{name} 第 {idx} 张表行格数与表头({len(header)})不符: {bad}"

    @pytest.mark.parametrize("name", FIXTURES)
    def test_no_table_is_all_phantom_cells(self, extracted, name):
        """一张表至少要有一行是真有内容的，否则"提取到表格"这句话是空的。"""
        for idx, table in enumerate(_tables(_markdown(extracted[name])), 1):
            assert any(any(c for c in row) for row in table[1:] or table), \
                f"{name} 第 {idx} 张表除了表头全是空格"

    def test_the_heading_tree_matches_the_document(self, extracted):
        """四行标题就是四条：1 个 level-1 + 3 个 level-2，且都在第 1 页。

        P2 要动的就是这一条的**反面**（标题被按换行腰斩、续行因结尾带 `.` 被降级），
        所以今天先把正确的形状录下来。
        """
        toc = extracted["merged_cells.pdf"].table_of_contents
        assert [e["level"] for e in toc] == [1, 2, 2, 2], toc
        assert all(e["page"] == 1 for e in toc)
        assert "Merged cell fixtures" not in json.dumps(toc), \
            "文档元数据的标题不该混进正文 TOC"

    @pytest.mark.parametrize("name", FIXTURES)
    def test_every_toc_range_stays_inside_the_document(self, extracted, name):
        """P3 的靶子：`end_page` 越过 `pages_total` 的区间会命中任何 `pages=` 请求。

        书签路径 `_extract_toc` 返回时不钳（标题路径钳），所以这一条今天要**允许**
        它成立 —— 单页夹具恰好钳得住，P3 用多页夹具来撞它。
        """
        res = extracted[name]
        for entry in (res.table_of_contents or []):
            assert 1 <= entry["page"] <= res.pages_total, entry
            assert entry["end_page"] <= res.pages_total, entry
            assert entry["end_page"] >= entry["page"], entry


class TestWhatTodayGetsWrong:
    """把今天的**错**写在断言里，P1 落地时这两条要一起翻，翻不全就是没修完。"""

    def test_a_merged_subtotal_is_written_as_an_empty_cell_today(self, extracted):
        """跨列的 "North subtotal" 今天被补成 `| North subtotal |  | 545 |`。

        读的人无从区分"这一格属于左边那一格"与"这一格真的没数据"—— 一次静默的语义
        丢失，而它出现在一张自称成功的表格里（`content_ok: true`）。P1 之后这一行
        不该再以网格形式出现（非矩形表降级为对齐文本/列表），这条断言随之改写。
        """
        md = _markdown(extracted["merged_cells.pdf"])
        assert "| North subtotal |  | 545 |" in md, md

    def test_a_two_row_header_collapses_into_the_data_rows_today(self, extracted):
        """表二有两行表头（`Revenue by quarter` 跨两列），markdown 只容得下一行。

        后果是第二行表头（Third quarter / Fourth quarter / Year）被当成**数据行**发出
        —— 现场还原里那一页 "Fourth quarter 掉进数据行" 就是这么来的。P1 之后这张表
        要产出一个诚实的降级渲染，而不是这张自信的错网格。
        """
        tables = _tables(_markdown(extracted["merged_cells.pdf"]))
        spanned = [t for t in tables
                   if any(r[1:3] == ["Third quarter", "Fourth quarter"] for r in t)]
        assert spanned, "表二今天是被当网格发的 —— 这条记录着 P1 要改的现场"
        header, sub_header = spanned[0][0], spanned[0][1]
        assert "Revenue by quarter" in header and "Revenue by quarter" not in sub_header

    def test_the_wide_real_world_table_is_fifty_percent_phantom_today(self, extracted):
        """`background_checks.pdf`（仓库里既有的真 PDF）是同一件事的真实尺寸证人。

        表头 25 颗格子里绝大多数是补出来的空位，而每一行数据都被摊进那 25 列。
        记这个数不是为了好看：P1 若只把玩具表修好而让真文件更糟，这个数会动。
        """
        tables = _tables(_markdown(extracted["background_checks.pdf"]))
        assert tables, "这份真 PDF 里确实有表"
        widest = max(tables, key=lambda t: len(t[0]))
        phantom = sum(1 for c in widest[0] if not c)
        assert phantom >= len(widest[0]) // 2, (
            f"表头 {len(widest[0])} 格里只有 {phantom} 格是空的，与记录不符")


class TestTheFixtureItselfIsReproducible:

    def test_the_html_source_is_checked_in_next_to_the_pdf(self):
        src = HERE / "merged_cells.html"
        assert src.exists(), "PDF 的源必须入库，否则这份夹具没人能重造"
        text = src.read_text(encoding="utf-8")
        for table_id in ("rectangular", "colspan", "rowspan"):
            assert f'id="{table_id}"' in text, f"少了 {table_id} 那张表"
        assert "--print-to-pdf" in text, "生成命令要写在源文件里，不只是写在这里"

    def test_the_scanned_fixture_still_says_it_has_no_text(self, extracted):
        """dummy.pdf 是"没有可提取文本"的正对照：黄金集不许把它录成一具空正文。"""
        res = extracted["dummy.pdf"]
        assert res.scanned is True and res.error.startswith("scanned_pdf")
        assert "no extractable text" in _markdown(res)
