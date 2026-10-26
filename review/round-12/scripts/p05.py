"""P05 常镜秋 · 评估师 L2 —— 链条⑤act pin 挂页→回读复核→request 会话接力（r08 家产）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(5, "常镜秋·评估师")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-act snapshot（pin 挂页）")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "action": "snapshot",
                       "session_id": "p05a", "pin": True, "timeout": 45000})
rows = (r.get("metadata") or {}).get("actions") or []
print(f"   actions={len(rows)} | {str(r.get('summary'))[:100]}")

p.pause(6)
p.set_scenario("S2-填表 + 寄存器铸值（同页 pin）")
r = p.call("web_act", {"session_id": "p05a", "pin": True, "timeout": 45000,
                       "actions": [
                           {"fill": {"selector": "input[name=username]", "text": "gupiaoren"}},
                           {"read": {"selector": "input[name=username]", "as": "who"}},
                       ]})
rows = (r.get("metadata") or {}).get("actions") or []
regs = (r.get("metadata") or {}).get("registers")
for a in rows:
    if a.get("read_as"):
        print(f"   read_as={a.get('read_as')} value={str(a.get('read'))[:40]!r}")
print(f"   registers: {str(regs)[:120]}")

p.pause(6)
p.set_scenario("S3-跨调用回读（页是否还活着）")
r = p.call("web_act", {"session_id": "p05a", "pin": True, "timeout": 45000,
                       "actions": [{"read": {"selector": "input[name=username]"}}]})
rows = (r.get("metadata") or {}).get("actions") or []
val = next((str(a.get("read")) for a in rows if a.get("read")), "")
print(f"   回读值: {val[:40]!r}（期待 gupiaoren=页保持）")

p.pause(6)
p.set_scenario("S4-request POST 接力（confirm）")
r = p.call("web_request", {"url": "http://127.0.0.1:8399/login", "method": "POST",
                           "body": {"username": "gupiaoren", "password": "pw5"},
                           "session_id": "p05a", "confirm": True})
print(f"   status={r.get('status')} | {str(r.get('summary'))[:120]}")

p.pause(6)
p.set_scenario("S5-/account 复核")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/account", "session_id": "p05a",
                       "actions": [{"read": {"selector": "h1"}}], "timeout": 45000})
got = ""
for a in (r.get("metadata") or {}).get("actions") or []:
    if str(a.get("action", "")).startswith("read"):
        got = str(a.get("read") or "")
print(f"   /account h1: {got[:40]!r}")
print("P05 DONE")
