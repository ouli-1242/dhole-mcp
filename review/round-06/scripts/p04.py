"""P04 商辛 · 财务 L2 —— doc_parse 真/假 PDF 对照。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
REPO = Path(__file__).resolve().parent.parent.parent.parent
p = PersonaSim(4, "商辛·财务")
p.set_scenario("S1-真 PDF")
r = p.call("doc_parse", {"file_path": str(REPO / "tests" / "dummy.pdf")})
p.observe("real-pdf", r, "content_ok", "total_extracted_chars", "summary")
p.pause(8)
p.set_scenario("S2-假 PDF")
r = p.call("doc_parse", {"file_path": str(REPO / "review" / "round-04" / "sessions" / "files" / "报价说明.pdf")})
p.observe("fake-pdf", r, "error")
p.pause(7)
p.set_scenario("S3-xlsx 小计(四现观察:每 sheet 无小计)")
r = p.call("doc_parse", {"file_path": str(REPO / "review" / "round-04" / "sessions" / "files" / "区域报表.xlsx")})
p.observe("xlsx", r, "total_extracted_chars")
print("P04 DONE")
