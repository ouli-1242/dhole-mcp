"""P08 屠维5 · 电竞教练 L2 —— discover→crawl→read 地图流水线。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(8, "屠维5·电竞教练")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-discover 地图")
r = p.call("web_discover", {"url": "http://127.0.0.1:8399/collections"})
rows = r.get("results") or r.get("links") or r.get("pages") or []
urls = [str(x.get("url")) for x in rows][:3]
print(f"   地图行: {len(rows)} | 种子采样: {urls}")

p.pause(6)
p.set_scenario("S2-crawl(地图数对账)")
r = p.call("web_crawl", {"url": "http://127.0.0.1:8399", "max_pages": 4, "cache_ttl": 0})
print(f"   crawl: {str(r.get('summary'))[:140]}")

p.pause(6)
p.set_scenario("S3-read 落页(content_ok 账)")
r = p.call("web_read", {"urls": ["http://127.0.0.1:8399/collections"], "cache_ttl": 0})
rows = r.get("results") or []
for row in rows:
    print(f"   - ok={row.get('content_ok')} chars={row.get('content_chars') if 'content_chars' in row else '?'}")
print("P08 DONE")
