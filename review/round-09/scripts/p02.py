"""P02 白景舟 · 图书馆员 L3 猎手 —— 过滤语义逐个真测（site/exclude/引号/engines/零结果）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(2, "白景舟·图书馆员")
p.call("cred_defaults", {"set": {"allow_private": True}})

def rows_of(r):
    return r.get("rows") or r.get("results") or []

p.set_scenario("S1-site= 限定")
r = p.call("web_search", {"query": "python tutorial", "site": "docs.python.org", "max_results": 5})
rows = rows_of(r)
bad = [u for u in (str(x.get("url")) for x in rows) if "docs.python.org" not in u]
print(f"   {len(rows)} 行 | 越域行: {len(bad)} | summary: {str(r.get('summary'))[:100]}")

p.pause(6)
p.set_scenario("S2-exclude_sites= 剔除")
r = p.call("web_search", {"query": "python tutorial", "exclude_sites": ["w3schools.com"], "max_results": 6})
rows = rows_of(r)
bad = [u for u in (str(x.get("url")) for x in rows) if "w3schools.com" in u]
print(f"   {len(rows)} 行 | 漏剔行: {len(bad)}")

p.pause(6)
p.set_scenario("S3-引号短语")
r = p.call("web_search", {"query": '"dhole mcp"', "max_results": 6})
print(f"   引号短语: {len(rows_of(r))} 行 | {str(r.get('summary'))[:110]}")

p.pause(6)
p.set_scenario("S4-engines= 收窄池")
r = p.call("web_search", {"query": "fixture site", "engines": ["yandex"], "max_results": 5})
rows = rows_of(r)
srcs = {str(x.get("engines") or x.get("source")) for x in rows}
print(f"   {len(rows)} 行 | 来源集合: {srcs} | summary: {str(r.get('summary'))[:100]}")

p.pause(6)
p.set_scenario("S5-零结果现场(不存在的站点)")
r = p.call("web_search", {"query": "quantum decoherence phonon", "site": "no-such-domain-xkcd9.example", "max_results": 5})
rows = rows_of(r)
print(f"   零结果: {len(rows)} 行 | summary: {str(r.get('summary'))[:180]}")
print("P02 DONE")
