"""P07 闻溪 · 博物馆策展人 L2 —— 两种 path_include 地图隔离 · crawl/read · recall。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(7, "闻溪·博物馆策展人")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-地图：只要 /work")
r = p.call("web_discover", {"url": "http://127.0.0.1:8399/", "max_pages": 8,
                            "path_include": "/work"})
rows = r.get("pages") or []
print(f"   rows={len(rows)} summary={str(r.get('summary'))[:150]}")
for x in rows[:8]:
    u = x.get("url") if isinstance(x, dict) else x
    print("   -", str(u)[:110])

p.pause(4)
p.set_scenario("S2-地图：只要 /article")
r = p.call("web_discover", {"url": "http://127.0.0.1:8399/", "max_pages": 8,
                            "path_include": "/article"})
rows = r.get("pages") or []
print(f"   rows={len(rows)} summary={str(r.get('summary'))[:150]}")
for x in rows[:8]:
    u = x.get("url") if isinstance(x, dict) else x
    print("   -", str(u)[:110])

p.pause(4)
p.set_scenario("S3-crawl 前三页")
r = p.call("web_crawl", {"url": "http://127.0.0.1:8399/", "max_pages": 3})
print(f"   {str(r.get('summary'))[:200]}")
for x in (r.get("pages") or [])[:4]:
    print("   -", str({k: x.get(k) for k in ('url', 'content_ok', 'page_type') if x.get(k) is not None})[:150])

p.pause(4)
p.set_scenario("S4-地图里的详情页转 read")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/work/1?cb=p07map"})
print(f"   ok={r.get('content_ok')} chars={r.get('total_extracted_chars')} cached={r.get('cached')}")

p.pause(4)
p.set_scenario("S5-recall 展品页")
r = p.call("web_recall", {"query": "工作 规格 展品", "limit": 4})
print(f"   results={len(r.get('results') or [])}")
for x in (r.get("results") or [])[:4]:
    print("   -", str({k: x.get(k) for k in ('url', 'title') if x.get(k)})[:150])
print("P07 DONE")
