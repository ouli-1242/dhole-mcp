"""P05 沈青禾 · 数据记者 L3 猎手 —— research 证据包 · search · recall · check 两发。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(5, "沈青禾·数据记者")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-research 一问一证据包")
r = p.call("web_research", {"query": "HTTP cache freshness and revalidation",
                            "max_sources": 2, "per_source_chars": 1200,
                            "timeout": 120000})
print(f"   sources={len(r.get('sources') or [])} failed={len(r.get('failed') or [])} "
      f"more_hits={len(r.get('more_hits') or [])}")

p.pause(12)
p.set_scenario("S2-search 找最新口径")
r = p.call("web_search", {"query": "data update frequency reporting", "max_results": 2})
print(f"   {str(r.get('summary'))[:200]}")

p.pause(6)
p.set_scenario("S3-recall 找回自己的页")
r = p.call("web_recall", {"query": "缓存 过期 抓取", "limit": 4})
print(f"   results={len(r.get('results') or [])}")
for x in (r.get('results') or [])[:3]:
    print("   -", str({k: x.get(k) for k in ('url', 'title') if x.get(k)})[:140])

p.pause(6)
p.set_scenario("S4-check 第一发（记基线）")
U = "http://127.0.0.1:8399/changing?cb=p05-check"
r = p.call("web_read", {"urls": [U], "extraction_type": "check"})
print(f"   {str(r.get('summary'))[:200]}")

p.pause(5)
p.set_scenario("S5-check 第二发（应 changed）")
r = p.call("web_read", {"urls": [U], "extraction_type": "check"})
print(f"   {str(r.get('summary'))[:220]}")
for x in r.get('results') or []:
    print("   -", str({k: x.get(k) for k in
          ('url', 'changed', 'status', 'note') if x.get(k) is not None})[:240])
print("P05 DONE")
