"""P07 陆沉舟 · 隧道测量员 L2 —— 链⑦search→crawl→discover 对账→read + 中环第一拒 2 发。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(7, "陆沉舟·隧道测量员")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-search 开题")
r = p.call("web_search", {"query": "playwright automation", "max_results": 3})
results = r.get("results") or []
print(f"   results={len(results)} | first={str((results[0] or {}).get('url'))[:60] if results else '-'}")

p.pause(6)
p.set_scenario("S2-crawl 2 页")
r = p.call("web_crawl", {"url": "http://127.0.0.1:8399/", "max_pages": 2})
pages = r.get("pages") or []
print(f"   pages={len(pages)} | {str(r.get('summary'))[:110]}")

p.pause(6)
p.set_scenario("S3-中环拒：crawl max_pages=0（clamp 落 summary）")
r = p.call("web_crawl", {"url": "http://127.0.0.1:8399/", "max_pages": 0})
print(f"   | {str(r.get('summary'))[:160]}")

p.pause(6)
p.set_scenario("S4-中环拒：search max_results=0（r12 家产）")
r = p.call("web_search", {"query": "dhole", "max_results": 0})
print(f"   | {str(r.get('summary'))[:160]}")

p.pause(6)
p.set_scenario("S5-discover 对账")
r = p.call("web_discover", {"url": "http://127.0.0.1:8399/collections?items=3"})
pages = r.get("pages") or []
works = [pg.get("url") for pg in pages if "/work/" in str(pg.get("url"))]
print(f"   pages={len(pages)} work 行={len(works)}（对 items=3）")

p.pause(6)
p.set_scenario("S6-read 落点")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/work/1"})
print(f"   status={r.get('status')} chars={r.get('total_extracted_chars')}")
print("P07 DONE")
