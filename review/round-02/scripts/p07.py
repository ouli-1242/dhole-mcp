"""P07 邱原 · 三甲医院主治医师 · L2 —— round-02 真实模拟（research 证据包）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(7, "邱原·医师")

p.set_scenario("S1-research 4 源")
r = p.call("web_research", {"query": "semaglutide cardiovascular outcomes trial SUSTAIN-6",
                            "max_sources": 4, "timeout": 120000})
p.observe("research", r, "summary")
txt = str(r)[:400]
print("      pack head:", txt.replace(chr(10), ' ')[:380])
p.pause(20)

p.set_scenario("S2-低可信源挑刺")
r = p.call("web_search", {"query": "GLP-1 receptor agonist cardiovascular benefit meta-analysis",
                          "max_results": 6})
p.observe("search", r, "summary")
p.rows(r, "results", 6, 130)
p.pause(12)

p.set_scenario("S3-PDF 指定页交叉")
r = p.call("web_read", {"url": "https://www.nejm.org/doi/pdf/10.1056/NEJMoa1607628",
                        "pages": "1-2", "timeout": 90000})
p.observe("pdf", r, "content_ok", "summary", "page_type", "total_extracted_chars")
p.pause(12)

p.set_scenario("S4-freshness 二连")
r = p.call("web_search", {"query": "semaglutide review", "max_results": 4, "freshness": "year"})
p.observe("year", r, "summary", "date_filter")
p.rows(r, "results", 4, 110)

print("P07 DONE")
