"""P06 卫朝雨 · 保险理赔 L2 —— checkout 确认三跳 + 404 死链 + check 增量。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(6, "卫朝雨·理赔")
p.set_scenario("S1-checkout 该问就问")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/checkout", "actions": [
    {"fill": {"selector": "#item", "text": "理赔件-001"}},
    {"fill": {"selector": "#qty", "text": "1"}},
    {"click": {"selector": "#place-order"}},
], "timeout": 60000})
md = r.get("metadata") or {}
for st in (md.get("actions") or []):
    print("   step:", st.get("action"), "ok:", st.get("ok"), str(st.get("error"))[:60])
print("   next_action:", str(r.get("next_action"))[:120])
p.pause(8)
p.set_scenario("S2-确认后真提交")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/checkout", "actions": [
    {"fill": {"selector": "#item", "text": "理赔件-001"}},
    {"fill": {"selector": "#qty", "text": "1"}},
    {"click": {"selector": "#place-order", "confirm": True}},
], "timeout": 60000})
for st in ((r.get("metadata") or {}).get("actions") or []):
    print("   step:", st.get("action"), "ok:", st.get("ok"), str(st.get("error"))[:60])
print("   page:", str(r.get("content"))[:100])
p.pause(8)
p.set_scenario("S3-核赔页已下线(404)")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/no-such-page", "timeout": 30000})
p.observe("404", r, "error", "total_extracted_chars")
p.pause(7)
p.set_scenario("S4-check 增量(/changing 两次)")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/changing", "extraction_type": "check",
                        "cache_ttl": 0, "timeout": 30000})
p.observe("check-1", r, "summary")
p.pause(5)
r = p.call("web_read", {"url": "http://127.0.0.1:8399/changing", "extraction_type": "check",
                        "cache_ttl": 0, "timeout": 30000})
p.observe("check-2", r, "summary")
print("P06 DONE")
