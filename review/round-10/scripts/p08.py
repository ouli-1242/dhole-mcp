"""P08 桑无咎4 · 船长 L2 —— crawl 深预算（stopped 原因三现场）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(8, "桑无咎4·船长")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-max_pages=2(页数停)")
r = p.call("web_crawl", {"url": "http://127.0.0.1:8399", "max_pages": 2, "cache_ttl": 0})
print(f"   summary: {str(r.get('summary'))[:140]}")

p.pause(6)
p.set_scenario("S2-max_depth=0(深度停)")
r = p.call("web_crawl", {"url": "http://127.0.0.1:8399", "max_pages": 5, "max_depth": 0, "cache_ttl": 0})
print(f"   summary: {str(r.get('summary'))[:140]}")

p.pause(6)
p.set_scenario("S3-大预算(池尽停)")
r = p.call("web_crawl", {"url": "http://127.0.0.1:8399/about", "max_pages": 50, "max_depth": 1, "cache_ttl": 0})
print(f"   summary: {str(r.get('summary'))[:140]}")

p.pause(6)
p.set_scenario("S4-边界(max_pages=0)"
              )
r = p.call("web_crawl", {"url": "http://127.0.0.1:8399", "max_pages": 0})
print(f"   err: {str(r.get('error'))[:160] or str(r.get('summary'))[:100]}")
print("P08 DONE")
