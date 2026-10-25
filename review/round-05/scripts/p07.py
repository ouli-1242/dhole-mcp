"""P07 满宠儿 · 财经自媒体 L2 —— feed 三源混坏 + since 边界。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(7, "满宠儿·自媒体")
p.set_scenario("S1-三源混坏")
p.call("cred_defaults", {"set": {"allow_private": True}})
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml",
                                  "http://127.0.0.1:8399/article",
                                  "http://127.0.0.1:8399/no-such-feed.xml"], "max_items": 3})
p.observe("feed", r, "summary")
import json
for f in (r.get("feeds") or []):
    print("   row:", f.get("source_url", "")[-28:], "| err:", str(f.get("error"))[:80])
p.pause(8)
p.set_scenario("S2-since 边界(未来日期/空串)")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "since": "2030-01-01"})
p.observe("future", r, "summary")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "since": ""})
p.observe("empty", r, "summary")
p.pause(8)
p.set_scenario("S3-交叉验证")
r = p.call("web_research", {"query": "美联储 利率 决议 2026年10月", "timeout": 60000})
p.observe("research", r, "summary")
print("P07 DONE")
