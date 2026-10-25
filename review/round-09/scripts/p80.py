"""p80/p81 旁支：web_crawl / web_discover / doc_parse / web_request / feed_read / web_act / sys_state / sys_guide（13/13 收口）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

fdir = Path(__file__).resolve().parent.parent / "sessions" / "files"
fdir.mkdir(parents=True, exist_ok=True)
csv = fdir / "tariff.csv"
if not csv.exists():
    csv.write_text("税号,税率\n8471.30,0%\n9018.90,3%\n", encoding="utf-8")

p = PersonaSim(80, "p80-旁支")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("B1-crawl")
r = p.call("web_crawl", {"url": "http://127.0.0.1:8399", "max_pages": 3, "cache_ttl": 0})
print("   ", str(r.get("summary"))[:120])

p.pause(6)
p.set_scenario("B2-discover")
r = p.call("web_discover", {"url": "http://127.0.0.1:8399/collections"})
print("   ", str(r.get("summary"))[:120])

p.pause(6)
p.set_scenario("B3-doc_parse(本地 csv)")
r = p.call("doc_parse", {"file_path": str(csv)})
print("   ", str(r.get("summary"))[:120])

p.pause(6)
p.set_scenario("B4-feed_read")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": 2})
print("   ", str(r.get("summary"))[:120])

p.pause(6)
p.set_scenario("B5-web_request(GET)")
r = p.call("web_request", {"url": "http://127.0.0.1:8399/feed.xml", "method": "GET"})
print("   ", str(r.get("summary"))[:120])

p.pause(6)
p.set_scenario("B6-web_act(读页+确认闸回归)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/challenge/text",
                       "actions": [{"read": {"selector": "#challenge", "as": "code"}},
                                    {"fill": {"selector": "#answer", "text": "{=code}"}},
                                    {"click": {"selector": "#challenge-btn"}}],
                       "timeout": 45000})
rows = (r.get("metadata") or {}).get("actions") or []
pend = any(a.get("pending_approval") for a in rows)
print(f"   同链 read→fill ok, click 闸拦: {pend}")

p.pause(6)
p.set_scenario("B7-sys_state + sys_guide")
r = p.call("sys_state", {"show": ["cache"]})
print("    sys_state ok:", not r.get("error"))
r = p.call("sys_guide", {"topic": "call"})
print("    sys_guide ok:", not r.get("error"))
print("P80 DONE")
