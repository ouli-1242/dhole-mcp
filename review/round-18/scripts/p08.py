"""P08 沈未 · 研究者 L2 —— 枚举错/空 query/负 offset/负 cache_ttl/超大 limit。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

p = PersonaSim(8, "沈未·文献研究者")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-search mode 枚举错")
r = p.call("web_search", {"query": "机械键盘", "mode": "deep", "max_results": 2})
print("   err:", str(r.get("error"))[:180], "| results:", len(r.get("results") or []))

p.pause(3)
p.set_scenario("S2-research 空 query 负 max_sources")
r = p.call("web_research", {"query": "   ", "max_sources": -2, "timeout": 30000})
print("   err:", str(r.get("error"))[:220])

p.pause(2)
p.set_scenario("S3-recall 空 query 负 limit")
r = p.call("web_recall", {"query": "", "limit": -5})
print("   err:", str(r.get("error"))[:220])

p.pause(2)
p.set_scenario("S4-recall 巨大 limit")
r = p.call("web_recall", {"query": "机械键盘", "limit": 100000})
print("   err:", str(r.get("error"))[:160], "| rows:", len(r.get("results") or []))

p.pause(2)
p.set_scenario("S5-search freshness 错格式")
r = p.call("web_search", {"query": "机械键盘", "freshness": "yesterday", "max_results": 2})
print("   err:", str(r.get("error"))[:200], "| results:", len(r.get("results") or []))

p.pause(3)
p.set_scenario("S6-search 负 page")
r = p.call("web_search", {"query": "机械键盘", "page": -1, "max_results": 2})
print("   err:", str(r.get("error"))[:200], "| results:", len(r.get("results") or []))

p.pause(2)
p.set_scenario("S7-自愈 recall")
r = p.call("web_recall", {"query": "机械键盘", "limit": 3})
print("   ok:", r.get("content_ok"), "rows:", len(r.get("results") or []))
