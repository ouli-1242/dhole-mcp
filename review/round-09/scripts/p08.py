"""P08 屠维3 · 精算师 L2 —— rerank 分级语义（high/med/low 与行内容对账）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(8, "屠维3·精算师")
p.call("cred_defaults", {"set": {"allow_private": True}})

def rows_of(r):
    return r.get("rows") or r.get("results") or []

p.set_scenario("S1-分级账目(逐行 grade)")
r = p.call("web_search", {"query": "保险 精算 偿付能力", "max_results": 6})
rows = rows_of(r)
grades = {}
for x in rows:
    g = str(x.get("relevance") or x.get("grade") or "?")
    grades[g] = grades.get(g, 0) + 1
print(f"   {len(rows)} 行 | 分级分布: {grades} | summary: {str(r.get('summary'))[:120]}")

p.pause(6)
p.set_scenario("S2-mode=neural(强rerank)")
r = p.call("web_search", {"query": "insurance solvency capital", "mode": "neural", "max_results": 5})
print(f"   neural: {len(rows_of(r))} 行 | {str(r.get('summary'))[:130]}")

p.pause(6)
p.set_scenario("S3-mode 非法值(第一拒)")
r = p.call("web_search", {"query": "x", "mode": "psychic"})
print(f"   err: {str(r.get('error'))[:180]}")

p.pause(6)
p.set_scenario("S4-max_results 边界(1 与 51)")
r1 = p.call("web_search", {"query": "python", "max_results": 1})
print(f"   max=1: {len(rows_of(r1))} 行")
r2 = p.call("web_search", {"query": "python", "max_results": 51})
print(f"   max=51: {str(r2.get('error'))[:140] or str(r2.get('summary'))[:100]}")

p.pause(6)
p.set_scenario("S5-page 翻页")
r = p.call("web_search", {"query": "python", "max_results": 3, "page": 1})
print(f"   page=1: {len(rows_of(r))} 行 | {str(r.get('summary'))[:110]}")
print("P08 DONE")
