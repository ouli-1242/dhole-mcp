"""P10 岑晚舟 · 电网调度员 L2 —— 链条⑩request 写闸→state 复盘→recall 收尾→HEAD 体检。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(10, "岑晚舟·电网调度员")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-POST 写闸（无 confirm）")
r = p.call("web_request", {"url": "http://127.0.0.1:8399/login", "method": "POST",
                           "body": {"username": "diaodu", "password": "pw10"},
                           "session_id": "p10a"})
na = str(r.get("next_action") or "")
print(f"   pending={r.get('pending_approval')} | next_action: {na[:140]}")

p.pause(6)
p.set_scenario("S2-confirm 真提交")
r = p.call("web_request", {"url": "http://127.0.0.1:8399/login", "method": "POST",
                           "body": {"username": "diaodu", "password": "pw10"},
                           "session_id": "p10a", "confirm": True})
print(f"   status={r.get('status')} | {str(r.get('summary'))[:120]}")

p.pause(6)
p.set_scenario("S3-state 复盘")
r = p.call("sys_state", {})
print(f"   | {str(r.get('summary'))[:150]}")

p.pause(6)
p.set_scenario("S4-recall 收尾")
r = p.call("web_recall", {"query": "靶站", "limit": 6})
res = r.get("results") or []
print(f"   results={len(res)} | {str(r.get('summary'))[:100]}")

p.pause(6)
p.set_scenario("S5-HEAD /account 体检（会话内）")
r = p.call("web_request", {"url": "http://127.0.0.1:8399/account", "method": "HEAD",
                           "session_id": "p10a"})
print(f"   status={r.get('status')} | {str(r.get('summary'))[:110]}")
print("P10 DONE")
