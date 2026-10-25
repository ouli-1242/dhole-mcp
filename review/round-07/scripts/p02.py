"""P02 屠维 · 技术作家 L3 —— behavior 篇交互章节真机回放（pin/session_id 首测）。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(2, "屠维·技术作家")
p.call("cred_defaults", {"set": {"allow_private": True}})
p.set_scenario("S1-读 behavior 篇")
r = p.call("sys_guide", {"topic": "behavior"})
import json
s = json.dumps(r, ensure_ascii=False)
print("   behavior 篇长度:", len(s), "| pin 提及:", s.count("pin"), "| session_id 提及:", s.count("session_id"))
p.pause(7)
p.set_scenario("S2-pin=true 驻留(手册示例照抄)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/challenge/text", "pin": True,
                       "session_id": "p02-pin-1", "actions": [
    {"read": {"selector": "#challenge", "as": "code"}}], "timeout": 60000})
steps = (r.get("metadata") or {}).get("actions") or []
code_reg = steps[0].get("registers") if steps else None
print("   第一步 read ok:", steps[0].get("ok") if steps else "?", "| registers:", json.dumps(code_reg, ensure_ascii=False)[:120] if code_reg else "(见回执)")
p.pause(7)
r = p.call("web_act", {"url": "http://127.0.0.1:8399/challenge/text", "pin": True,
                       "session_id": "p02-pin-1", "actions": [
    {"fill": {"selector": "#answer", "text": "{=code}"}},
    {"click": {"selector": "#challenge-btn", "confirm": True}}], "timeout": 60000})
c = r.get("content"); c = ''.join(c) if isinstance(c, list) else str(c)
print("   跨调用 pin 链:", "挑战通过" in c or "通过" in c, "|", c[:60].replace("\n", " "))
p.pause(7)
p.set_scenario("S3-session_id 会话保持")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "session_id": "p02-sess-1", "actions": [
    {"fill": {"selector": "#username", "text": "sess1"}},
    {"fill": {"selector": "#password", "text": "pw"}},
    {"click": {"selector": "#login-btn", "confirm": True}}], "timeout": 60000})
p.observe("login-with-session", r, "summary")
print("P02 DONE")
