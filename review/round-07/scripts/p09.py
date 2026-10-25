"""P09 甘棠 · 客户成功 L1 —— 三类错误出路句真走通。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(9, "甘棠·客服")
p.set_scenario("S1-私网门:先撞再自救")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article", "timeout": 20000})
print("   撞门:", str(r.get("error"))[:60])
p.call("cred_defaults", {"set": {"allow_private": True}})
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article?r=1", "cache_ttl": 0, "timeout": 20000})
print("   自救:", r.get("content_ok"))
p.pause(6)
p.set_scenario("S2-写闸:confirm 路")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/checkout", "actions": [
    {"fill": {"selector": "#qty", "text": "1"}}, {"click": {"selector": "#place-order"}}], "timeout": 45000})
print("   闸:", "pending_approval" in json.dumps(r, ensure_ascii=False) if (json := __import__("json")) else "?")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/checkout", "actions": [
    {"fill": {"selector": "#qty", "text": "1"}}, {"click": {"selector": "#place-order", "confirm": True}}], "timeout": 45000})
print("   confirm 后:", r.get("ok") if "ok" in r else "ran")
print("P09 DONE")
