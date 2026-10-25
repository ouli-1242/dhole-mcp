"""p81 旁支（修形重跑）：web_crawl / web_discover / doc_parse(本地文件) / web_request / feed_read。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

fdir = Path(__file__).resolve().parent.parent / "sessions" / "files"
csv = fdir / "devices.csv"
if not csv.exists():
    csv.write_text("设备,编号\n血压计,A-100\n雾化器,B-200\n", encoding="utf-8")

p = PersonaSim(81, "p81-旁支b")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("B5-crawl")
r = p.call("web_crawl", {"url": "http://127.0.0.1:8399", "max_pages": 3, "cache_ttl": 0})
print("   ", str(r.get("summary"))[:130])

p.pause(6)
p.set_scenario("B6-discover")
r = p.call("web_discover", {"url": "http://127.0.0.1:8399/collections"})
print("   ", str(r.get("summary"))[:130])

p.pause(6)
p.set_scenario("B7-doc_parse(本地 csv)")
r = p.call("doc_parse", {"file_path": str(csv)})
print("   ", str(r.get("summary"))[:130])

p.pause(6)
p.set_scenario("B8-web_request(POST login)")
r = p.call("web_request", {"url": "http://127.0.0.1:8399/login", "method": "POST",
                           "body": {"username": "p81", "password": "x"}, "session_id": "p81req"})
print("   ", str(r.get("summary"))[:130])

p.pause(6)
p.set_scenario("B9-feed_read")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": 2})
print("   ", str(r.get("summary"))[:130])
print("P81 DONE")
