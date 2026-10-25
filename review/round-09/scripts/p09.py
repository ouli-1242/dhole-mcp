"""P09 库尔班3 · 税务师 L1 —— 坏查询第一拒自救全流程。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(9, "库尔班3·税务师")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-空查询")
r = p.call("web_search", {"query": ""})
print(f"   err: {str(r.get('error'))[:170]}")

p.pause(5)
p.set_scenario("S2-超长查询(2000 字)")
r = p.call("web_search", {"query": "税" * 2000})
print(f"   err: {str(r.get('error'))[:170] or '(收下?)'} | summary: {str(r.get('summary'))[:80]}")

p.pause(5)
p.set_scenario("S3-location 非法值")
r = p.call("web_search", {"query": "vat rate", "location": "XX"})
print(f"   err: {str(r.get('error'))[:170]}")

p.pause(5)
p.set_scenario("S4-page 越界")
r = p.call("web_search", {"query": "vat rate", "page": 99})
print(f"   err: {str(r.get('error'))[:170] or str(r.get('summary'))[:100]}")

p.pause(5)
p.set_scenario("S5-research 坏查询(空串)")
r = p.call("web_research", {"query": ""})
print(f"   err: {str(r.get('error'))[:170]}")

p.pause(5)
p.set_scenario("S6-自救重试(改正形真通)")
r = p.call("web_search", {"query": "增值税 留抵退税 政策", "max_results": 3})
rows = r.get("rows") or r.get("results") or []
print(f"   自救: {len(rows)} 行 | {str(r.get('summary'))[:110]}")
print("P09 DONE")
