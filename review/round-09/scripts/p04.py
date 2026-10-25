"""P04 越千帆3 · 舆情分析师 L3 猎手 —— 时效性/cache/E2 变体家族真实回访。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(4, "越千帆3·舆情分析师")
p.call("cred_defaults", {"set": {"allow_private": True}})

def rows_of(r):
    return r.get("rows") or r.get("results") or []

p.set_scenario("S1-sort=newest 与 freshness")
r = p.call("web_search", {"query": "AI policy", "sort": "newest", "freshness": "week", "max_results": 5})
print(f"   newest+week: {len(rows_of(r))} 行 | {str(r.get('summary'))[:130]}")

p.pause(6)
p.set_scenario("S2-freshness 非法值(第一拒)")
r = p.call("web_search", {"query": "AI policy", "freshness": "fortnight"})
print(f"   err: {str(r.get('error'))[:200]}")

p.pause(6)
p.set_scenario("S3-cache 两态(default vs ttl=0)")
r1 = p.call("web_search", {"query": "舆情 cache 探针 q7", "max_results": 3})
r2 = p.call("web_search", {"query": "舆情 cache 探针 q7", "max_results": 3, "cache_ttl": 0})
s1, s2 = str(r1.get("summary")), str(r2.get("summary"))
print(f"   首发缓存账: {'cached' in s1.lower() or 'cache' in s1.lower()} | {s1[:100]}")
print(f"   ttl=0: {s2[:100]}")

p.pause(6)
p.set_scenario("S4-E2 变体家族回访(同页两查询串 → recall)")
p.call("web_read", {"url": "http://127.0.0.1:8399/article?r=1", "cache_ttl": 0})
p.call("web_read", {"url": "http://127.0.0.1:8399/article?r=2", "cache_ttl": 0})
p.pause(4)
r = p.call("web_recall", {"query": "本地抓取", "limit": 8})
rows = rows_of(r)
arts = [str(x.get("url")) for x in rows if "/article" in str(x.get("url"))]
print(f"   recall {len(rows)} 行 | /article 变体行: {arts}")
print(f"   E2 形状: {len(arts)} 行并排（合并=1 行带 variants=N 则家产保持）")

p.pause(6)
p.set_scenario("S5-after/before 语义")
r = p.call("web_search", {"query": "AI policy", "after": "2026-10-01", "max_results": 4})
print(f"   after: {len(rows_of(r))} 行 | {str(r.get('summary'))[:130]}")
print("P04 DONE")
