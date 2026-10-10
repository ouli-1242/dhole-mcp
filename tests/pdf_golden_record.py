"""录下 PDF 提取器**今天**的输出（黄金集，P5）。

用法（改完 pdf_extractor 之后，看过 diff 再刷）::

    python tests/pdf_golden_record.py

写进 `tests/pdf_golden/<fixture>.md` 的是 `extract_pdf` 交回的 markdown 原文，
`<fixture>.json` 记的是同一次运行的结构化事实（页数、TOC、错误三态、质量分）。

为什么要有一份"逐字节"的东西：P1 要动的正是表格渲染，而"我修的是合并单元格"这句话
唯一的证人就是**没被修的那部分一个字没变**。性质断言（行格数、end_page 上界）说的是
方向，字符串钉的是现场 —— 两者都要，缺了后者，一次全表输出的漂移可以靠"性质还成立"
混过去。

刷新黄金集是一次**有意识的**决定：把 markdown 的 diff 读一遍，再把它写进提交信息。
手抄不许，凭空"修正"也不许 —— 黄金集记的是提取器做的事，不是我们希望它做的事。

夹具从哪来：
  - `tests/merged_cells.pdf` 由 `tests/merged_cells.html` 经真 Chrome 打印而来，命令写在
    `tests/test_the_pdf_golden_set_pins_what_the_extractor_does_today.py` 的抬头；
  - `tests/wrapped_heading.pdf` / `tests/figure_page.pdf` / `tests/deco_rules.pdf` /
    `tests/line_chart.pdf` 同理，各自的命令写在自己的 `.html` 抬头里（标题折行、画出来的
    图、只有装饰线的一页、由细线构成的真图 —— 后两份是同一判据的两侧对照）；
  - `tests/background_checks.pdf` / `tests/dummy.pdf` 是仓库里既有的真 PDF。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dhole_mcp.pdf_extractor import extract_pdf  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "pdf_golden"
FIXTURES = ["merged_cells.pdf", "wrapped_heading.pdf", "figure_page.pdf",
             "deco_rules.pdf", "line_chart.pdf", "background_checks.pdf",
             "dummy.pdf"]

# 记录下来的事实必须**只是**被检查过的那些：时间戳会随 pdfplumber 版本漂的东西不进 JSON。
FIELDS = ["error", "scanned", "encrypted", "pages_total", "pages_extracted",
          "quality_score", "content_ok", "title", "author"]


def record(name: str) -> tuple[int, int]:
    res = extract_pdf((HERE / name).read_bytes())
    stem = name.rsplit(".", 1)[0]
    OUT.mkdir(exist_ok=True)
    (OUT / f"{stem}.md").write_text("\n".join(res.content), encoding="utf-8")
    facts = {k: getattr(res, k) for k in FIELDS}
    facts["table_of_contents"] = list(res.table_of_contents or [])
    (OUT / f"{stem}.json").write_text(
        json.dumps(facts, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return len("\n".join(res.content)), len(list(res.table_of_contents or []))


def main() -> int:
    for name in FIXTURES:
        chars, toc = record(name)
        print(f"recorded {name}: {chars} chars of markdown, {toc} toc entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
