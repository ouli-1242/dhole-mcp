"""p07c 补正：pin=true + session_id 同会话（人在中途 text 挑战正形）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(15, "p07c-补正")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1c-pin+session 第一发(读 token)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/challenge/text", "pin": True,
                       "session_id": "s7",
                       "actions": [{"read": {"selector": "#challenge", "as": "code"}}],
                       "timeout": 45000})
tok = ""
for a in (r.get("metadata") or {}).get("actions") or []:
    if str(a.get("action", "")).startswith("read"):
        tok = str(a.get("read") or "").strip()
print("   token:", tok)
p.pause(6)
p.set_scenario("S1c-第二发(同 session + pin → 填+confirm)")
r2 = p.call("web_act", {"url": "http://127.0.0.1:8399/challenge/text", "pin": True,
                        "session_id": "s7",
                        "actions": [{"fill": {"selector": "#answer", "text": tok}},
                                     {"click": {"selector": "#challenge-btn", "confirm": True}},
                                     {"read": {"selector": "h1"}}],
                        "timeout": 60000})
for a in (r2.get("metadata") or {}).get("actions") or []:
    print("   ", str(a.get("action"))[:36], "ok=" + str(a.get("ok")), str(a.get("read") or a.get("error") or "")[:60])
print("P07C DONE")
