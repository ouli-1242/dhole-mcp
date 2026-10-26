"""P03 白鹭洲 · 潜水教练 L1 —— 链条③guide→read→request 写闸→会话复查（r11 必测2）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(3, "白鹭洲·潜水教练")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-sys_guide 引路")
r = p.call("sys_guide", {"topic": "start"})
print(f"   | {str(r.get('summary'))[:110]}")

p.pause(6)
p.set_scenario("S2-read 手册路线首页")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/"})
print(f"   status={r.get('status')} chars={r.get('total_extracted_chars')} | {str(r.get('summary'))[:100]}")

p.pause(6)
p.set_scenario("S3-POST /login 无 confirm（写闸）")
r = p.call("web_request", {"url": "http://127.0.0.1:8399/login", "method": "POST",
                           "body": {"username": "qianshui", "password": "pw3"},
                           "session_id": "p03a"})
err = str(r.get("error") or "")
na = str(r.get("next_action") or "")
print(f"   err: {err[:150]}")
print(f"   next_action: {na[:150]}")

p.pause(6)
p.set_scenario("S4-POST confirm 真提交")
kw = {"url": "http://127.0.0.1:8399/login", "method": "POST",
      "body": {"username": "qianshui", "password": "pw3"},
      "session_id": "p03a", "confirm": True}
r = p.call("web_request", kw)
print(f"   status={r.get('status')} | {str(r.get('summary'))[:120]}")

p.pause(6)
p.set_scenario("S5-/account 会话复查")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/account", "session_id": "p03a",
                       "actions": [{"read": {"selector": "h1"}}], "timeout": 45000})
got = ""
for a in (r.get("metadata") or {}).get("actions") or []:
    if str(a.get("action", "")).startswith("read"):
        got = str(a.get("read") or "")
print(f"   /account h1: {got[:40]!r}")
print("P03 DONE")
