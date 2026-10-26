"""P07 慕青梧 · 殡葬策划 L1 —— 链条⑦crawl→discover 两把尺对账→断链如实。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(7, "慕青梧·殡葬策划")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-crawl 3 页")
r = p.call("web_crawl", {"url": "http://127.0.0.1:8399/", "max_pages": 3})
pages = r.get("pages") or []
print(f"   pages={len(pages)} | {str(r.get('summary'))[:110]}")
for pg in pages[:4]:
    print(f"     - {str(pg.get('url'))[:60]} status={pg.get('status')}")

p.pause(6)
p.set_scenario("S2-discover 地图对账")
r = p.call("web_discover", {"url": "http://127.0.0.1:8399/collections?items=4"})
dpages = r.get("pages") or []
drows = [pg.get("url") for pg in dpages]
works = [u for u in drows if "/work/" in str(u)]
print(f"   pages={len(dpages)} rows={len(drows)} work 行={len(works)}")

p.pause(6)
p.set_scenario("S3-read 断链（404 如实）")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/work/999"})
print(f"   status={r.get('status')} | {str(r.get('summary'))[:120]}")
print("P07 DONE")
