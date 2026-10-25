"""P09 迟桉 · 自由翻译 L2 —— doc_parse 多 sheet/GBK/假 PDF + 外网 401 杠杆回归(R02-003)。"""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

HERE = Path(__file__).resolve().parent.parent
FILES = HERE / "sessions" / "files"

p = PersonaSim(9, "迟桉·翻译")

p.set_scenario("S1-三区报表(多 sheet + 合并单元格)")
r = p.call("doc_parse", {"file_path": str(FILES / "区域报表.xlsx")})
p.observe("xlsx", r, "summary", "total_extracted_chars")
p.pause(8)

p.set_scenario("S2-GBK 老表格")
r = p.call("doc_parse", {"file_path": "tests/gbk_sample.csv"})
p.observe("gbk", r, "summary", "total_extracted_chars")
p.pause(7)

p.set_scenario("S3-假 PDF(改扩展名的文本)")
r = p.call("doc_parse", {"file_path": str(FILES / "报价说明.pdf")})
p.observe("fake-pdf", r, "error", "summary")
print("      (回归: not_a_pdf 具名)")
p.pause(8)

p.set_scenario("S4-原文站要登录(外网 401)")
r = p.call("web_read", {"url": "https://wsj.com/articles/tech-companies-data-privacy-11634996753",
                        "timeout": 30000})
p.observe("paywall", r, "status", "summary")
d = r.get("diagnosis") or {}
print("      (回归 R02-003: recover_with =", str(d.get("recover_with"))[:110])
print("P09 DONE")
