"""P03 阮鸿飞3 · 导游 L1 —— 多引擎聚合账目（engines 名单/每行来源/vertical）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(3, "阮鸿飞3·导游")
p.call("cred_defaults", {"set": {"allow_private": True}})

def rows_of(r):
    return r.get("rows") or r.get("results") or []

p.set_scenario("S1-默认聚合(来源名单)")
r = p.call("web_search", {"query": "长城 旅游 攻略", "max_results": 6})
rows = rows_of(r)
srcs = {}
for x in rows:
    for e in (x.get("engines") or [str(x.get("source"))]):
        srcs[str(e)] = srcs.get(str(e), 0) + 1
print(f"   {len(rows)} 行 | 按引擎: {srcs}")
print(f"   summary: {str(r.get('summary'))[:130]}")

p.pause(6)
p.set_scenario("S2-单引擎对照(yandex)")
r = p.call("web_search", {"query": "长城 旅游 攻略", "engines": ["yandex"], "max_results": 5})
print(f"   yandex: {len(rows_of(r))} 行 | {str(r.get('summary'))[:110]}")

p.pause(6)
p.set_scenario("S3-news vertical")
r = p.call("web_search", {"query": "旅游", "sources": ["news"], "max_results": 5, "freshness": "month"})
rows = rows_of(r)
print(f"   news: {len(rows)} 行 | {str(r.get('summary'))[:110]}")

p.pause(6)
p.set_scenario("S4-单行来源不冒充(逐行查)")
r = p.call("web_search", {"query": "故宫 门票", "max_results": 4})
for x in rows_of(r)[:4]:
    print(f"   行: {str(x.get('engines') or x.get('source'))} | {str(x.get('url'))[:60]}")
print("P03 DONE")
