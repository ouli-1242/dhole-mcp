"""P10 越千帆3b · 用户研究员 L3 猎手 —— 组合态攻击（search↔recall 交叉账/E2 采集）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(10, "越千帆3b·用户研究员")
p.call("cred_defaults", {"set": {"allow_private": True}})

def rows_of(r):
    return r.get("rows") or r.get("results") or []

p.set_scenario("S1-search→read→recall 交叉(同 URL 两路账)")
r = p.call("web_search", {"query": "fixture site 靶站", "max_results": 4})
rows = rows_of(r)
top = str((rows[0] or {}).get("url") or "") if rows else ""
print(f"   search {len(rows)} 行 | top: {top[:70]}")
if top.startswith("http"):
    p.call("web_read", {"url": top, "timeout": 30000})
    p.pause(4)
    r2 = p.call("web_recall", {"query": "靶站", "limit": 8})
    rrows = rows_of(r2)
    hit = [str(x.get("url")) for x in rrows if str(x.get("url")) == top]
    print(f"   recall {len(rrows)} 行 | top 行在 recall: {len(hit)}（交叉账目对齐）")
else:
    print("   (search 无外网行——交叉改走本地，如实记录)")

p.pause(6)
p.set_scenario("S2-E2 变体采集(recall 行的 variants 账)")
p.call("web_read", {"url": "http://127.0.0.1:8399/changing?src=a", "cache_ttl": 0})
p.call("web_read", {"url": "http://127.0.0.1:8399/changing?src=b", "cache_ttl": 0})
p.call("web_read", {"url": "http://127.0.0.1:8399/changing?src=c", "cache_ttl": 0})
p.pause(4)
r = p.call("web_recall", {"query": "changing", "limit": 8})
changing = [str(x.get("url")) for x in rows_of(r) if "/changing" in str(x.get("url"))]
print(f"   changing 行: {changing}")
print(f"   E2 第七轮样本: {len(changing)} 行并排 or 合并 variants=N")

p.pause(6)
p.set_scenario("S3-混合编排(research 后 recall 该源)")
r = p.call("web_research", {"query": "本地抓取工具配置", "max_sources": 1, "timeout": 90000})
p.pause(4)
r2 = p.call("web_recall", {"query": "本地抓取", "limit": 6})
print(f"   research 后 recall: {len(rows_of(r2))} 行 | {str(r2.get('summary'))[:100]}")

p.pause(6)
p.set_scenario("S4-recall 边界(limit=1 与 21)")
r = p.call("web_recall", {"query": "fixture", "limit": 1})
print(f"   limit=1: {len(rows_of(r))} 行")
r = p.call("web_recall", {"query": "fixture", "limit": 21})
print(f"   limit=21: {str(r.get('error'))[:140]}")
print("P10 DONE")
