"""P08 荆无涯 · 爬虫 L3 —— css_selector 深水 + html 原始性 + truncated。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(8, "荆无涯·爬虫")
p.call("cred_defaults", {"set": {"allow_private": True}})
p.set_scenario("S1-属性选择器")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/collections?cs=1", "cache_ttl": 0,
                        "css_selector": "a[href^='/work/']", "timeout": 30000})
c = r.get("content"); c = ''.join(c) if isinstance(c, list) else str(c)
print("   attr-sel:", r.get("content_ok"), "len:", len(c), "| 头:", c[:60].replace("\n", " "))
p.pause(7)
p.set_scenario("S2-html 档原始性")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/collections?cs=2", "cache_ttl": 0,
                        "extraction_type": "html", "timeout": 30000})
c = r.get("content"); c = ''.join(c) if isinstance(c, list) else str(c)
print("   html raw:", c[:90].replace("\n", " "))
p.pause(7)
p.set_scenario("S3-伪类 ::first-line")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/collections?cs=3", "cache_ttl": 0,
                        "css_selector": ".card::first-line", "timeout": 30000})
p.observe("pseudo", r, "error", "content_ok")
print("P08 DONE")
