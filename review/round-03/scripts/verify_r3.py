"""P5 复验：真重启后 page_type 语义现场 + 抽取值不变。"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(20, "verify-r3")
p.call("cred_defaults", {"set": {"allow_private": True}})
r = p.call("web_crawl", {"url": "http://127.0.0.1:8399/collections", "max_pages": 2, "cache_ttl": 0})
rows = r.get("pages") or []
print("rows:", [(x.get("url","")[-20:], x.get("page_type")) for x in rows])
r2 = p.call("web_read", {"url": "http://127.0.0.1:8399/article?r3v=1", "timeout": 30000})
print("read content_ok:", r2.get("content_ok"))
print("VERIFY DONE")
