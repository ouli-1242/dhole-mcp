"""P09 方老师 · 学术编辑 · L1 —— round-01 真实模拟。"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(9, "方老师·编辑")
p.seq = 0

p.set_scenario("S1-读稿")
r = p.call("doc_parse", {"file_path": "tests/sample.docx"})
p.observe("docx", r, "total_extracted_chars", "content")
p.pause(6)

p.set_scenario("S2-查术语")
r = p.call("web_search", {"query": "中文编辑 校对 符号 GB/T 14706", "max_results": 4})
p.rows(r, "results", 4, 120)
rows = r.get("results") or []
if rows:
    p.pause(6)
    r2 = p.call("web_read", {"url": rows[0].get("url"), "max_content_chars": 4000})
    p.observe("read", r2, "content_ok", "total_extracted_chars", "error",
              "diagnosis.code")
p.pause(30, "场景间隔")

p.set_scenario("S3-求助")
r = p.call("sys_guide", {"topic": "start"})
p.observe("guide", r, "total_extracted_chars")

p.set_scenario("S4-算了")
r = p.call("web_read", {"url": "https://www.w3.org/TR/html52/", "max_content_chars": 3000})
p.observe("spec page", r, "content_ok", "total_extracted_chars", "error",
          "diagnosis.code", "next_action")
print("P09 DONE")
