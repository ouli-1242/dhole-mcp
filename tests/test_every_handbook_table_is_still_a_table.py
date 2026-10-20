r"""手册里的每一张表都得是**表**：一行几列，整张表就得几列（R02-072 的那把尺）。

现场（P4 写 R02-F57 时读到 `fields.md` 撞到的）：`consensus_skipped` 那一行与 `total_results` 那一行
粘在同一行上 —— 一行 8 颗竖线，等于把两行三列挤成一行六列。markdown 渲染后 `total_results` 在手册里
**没有自己的行**：读手册的 agent 查这颗字段时那一格不存在（而它在 schema 里明明是有的）。

这把尺以前没有：仓库里对 `fields.md` 的断言全是「某一行的内容对不对」（逐行找、逐行比），
没有一条问「这张表的形状还对不对」。所以粘行这种事只能靠人眼撞见 —— 撞到一处就补一把尺，
下次不用撞（同一维的教训本轮已经付过两次：R02-064 的门只扫文档不扫 wire、R02-066 的口径钉在散文里）。

契约：
  * 每个以 `|` 开头的连续块算一张表；表的列数 = 表头那行的列数；分隔行（`|---|`）单独看；
  * 表里**任何一行**的列数不等于表头 ⇒ 红（粘连、漏分隔符、半行被吞都会在这里红）；
  * 分隔行的列数也必须等于表头（一张表不许有 3 列表头配 2 列分隔行）；
  * 反面锁：尺必须扫**全部**手册文件（SKILL.md + references/*.md），不许只扫撞见问题那一份。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "src" / "dhole_mcp" / "skills" / "dhole-web"
FILES = [ROOT / "SKILL.md"] + sorted((ROOT / "references").glob("*.md"))


def _cells(line: str) -> int:
    return line.strip().count("|") - 1


def _is_sep(line: str) -> bool:
    body = line.strip().strip("|")
    return bool(body) and all(set(c.strip()) <= {"-", ":"} and c.strip() for c in body.split("|"))


def _tables(text: str):
    """[(表头行号, [(行号, 内容, 列数)])] —— 连续以 `|` 开头的块。"""
    out, block = [], []
    for i, ln in enumerate(text.splitlines(), 1):
        if ln.strip().startswith("|"):
            block.append((i, ln, _cells(ln)))
        elif block:
            out.append(block)
            block = []
    if block:
        out.append(block)
    return out


def test_every_handbook_file_exists():
    assert FILES and all(f.exists() for f in FILES), [str(f) for f in FILES]
    assert len(FILES) == 8, f"手册应当是 SKILL.md + 七份 reference，今天 {len(FILES)} 份"


def test_every_table_row_has_the_header_column_count():
    bad = []
    for f in FILES:
        for block in _tables(f.read_text(encoding="utf-8")):
            head_no, head_ln, cols = block[0]
            for no, ln, n in block[1:]:
                if _is_sep(ln):
                    if n != cols:
                        bad.append(f"{f.name}:{no} 分隔行 {n} 列，表头 {cols} 列")
                    continue
                if n != cols:
                    bad.append(f"{f.name}:{no} 这一行 {n} 列，表头 {cols} 列 :: {ln[:70]}")
    assert not bad, "手册里有表格行塌了：\n" + "\n".join(bad)


def test_no_glued_row_left_in_fields_reference():
    doc = (ROOT / "references" / "fields.md").read_text(encoding="utf-8")
    assert "||" not in doc, "两行被挤成一行时中间会留下空单元格：`||`"
    for name in ("consensus_skipped", "total_results"):
        rows = [ln for ln in doc.splitlines() if ln.strip().startswith(f"| `{name}` |")]
        assert len(rows) == 1, f"{name} 必须恰好一行，今天 {len(rows)}"


def test_the_ruler_actually_bends_on_a_glued_row():
    """尺自己得是有效的：喂给它一行粘连，必须报出来（否则这条测试就是在比剪子）。"""
    sample = "| a | b |\n|---|---|\n| `x` | one |\n| `y` | two || `z` | three |\n"
    blocks = _tables(sample)
    assert len(blocks) == 1
    cols = blocks[0][0][2]
    offenders = [(n, ln) for _i, ln, n in blocks[0][1:] if n != cols and not _is_sep(ln)]
    assert offenders and offenders[0][0] > cols, offenders   # 粘进来的行一定**多**列，不猜多几列
