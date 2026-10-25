"""P06 屠维2 · DevRel L2 —— 6+ action 长链、双寄存器不串名、顺序错误点名。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(6, "屠维2·DevRel")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-长链双寄存器")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/challenge/text",
                       "actions": [{"read": {"selector": "#challenge", "as": "code"}},
                                    {"read": {"selector": "h1", "as": "title"}},
                                    {"fill": {"selector": "#answer", "text": "{=code}"}},
                                    {"click": {"selector": "#challenge-btn"}},
                                    {"read": {"selector": "h1"}}],
                       "timeout": 60000})
steps = r.get("steps") or []
print(f"   长链 err={str(r.get('error') or '')[:60]} | steps={len(steps)}")
for s in steps:
    if str(s.get("action")) == "read":
        print("     read:", str(s.get("text") or s.get("detail") or "")[:60])
regs = r.get("registers") or r.get("metadata", {}).get("registers")
print("   registers 账:", str(regs)[:120])

p.pause(6)
p.set_scenario("S2-顺序错(fill 在 read 前,同链)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/challenge/text",
                       "actions": [{"fill": {"selector": "#answer", "text": "{=late}"}},
                                    {"read": {"selector": "#challenge", "as": "late"}}],
                       "timeout": 45000})
err = str(r.get("error") or "")
print("   顺序错 err:", err[:220])
print("   点名在册(R07-F1 句):", ("this one call" in err and "as=late" in err))

p.pause(6)
p.set_scenario("S3-重名寄存器(同名 as= 两次)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/challenge/text",
                       "actions": [{"read": {"selector": "#challenge", "as": "dup"}},
                                    {"read": {"selector": "h1", "as": "dup"}},
                                    {"fill": {"selector": "#answer", "text": "{=dup}"}}],
                       "timeout": 45000})
err = str(r.get("error") or "")
print("   重名 err:", err[:160] or "(收下,后者覆盖?)", "| ok=", not r.get("error"))
if not r.get("error"):
    for s in (r.get("steps") or []):
        if str(s.get("action")) == "fill":
            print("     fill 实填:", str(s.get("text") or s.get("detail") or "")[:60])
print("P06 DONE")
