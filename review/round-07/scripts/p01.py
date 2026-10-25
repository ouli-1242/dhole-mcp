"""P01 阮鸿飞 · 新后端 L2 —— start/call 手册逐条照做。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(1, "阮鸿飞·新后端")
p.call("cred_defaults", {"set": {"allow_private": True}})
p.set_scenario("S1-读 start 篇")
g = p.call("sys_guide", {"topic": "start"})
import json
s = json.dumps(g, ensure_ascii=False)
print("   start 篇长度:", len(s))
p.pause(6)
p.set_scenario("S2-照路线1(URL 有)")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article", "timeout": 30000})
p.observe("route1", r, "content_ok")
p.pause(6)
p.set_scenario("S3-照路线2(URL 无→search)")
r = p.call("web_search", {"query": "fixture site 靶站", "max_results": 3})
p.observe("route2", r, "summary")
p.pause(6)
p.set_scenario("S4-照路线3(整站→crawl)")
r = p.call("web_crawl", {"url": "http://127.0.0.1:8399/collections", "max_pages": 2, "cache_ttl": 0})
p.observe("route3", r, "summary")
p.pause(6)
p.set_scenario("S5-读 call 篇(placement 陷阱)")
r = p.call("sys_guide", {"topic": "call"})
p.observe("call-guide", r, "summary")
print("P01 DONE")
