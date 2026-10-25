"""P01 阮鸿飞2 · 手游运营 L1 —— 同链领取奖励（read as= 同链 fill+click 照手册）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(1, "阮鸿飞2·手游运营")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-读 call 篇")
r = p.call("sys_guide", {"topic": "call"})
raw = str(r.get("_raw") or "")
print(f"   call 篇长度: {len(raw)}")

p.pause(6)
p.set_scenario("S2-同链领取奖励(照手册 read as= → fill → click)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/challenge/text",
                       "actions": [{"read": {"selector": "#challenge", "as": "code"}},
                                    {"fill": {"selector": "#answer", "text": "{=code}"}},
                                    {"click": {"selector": "#challenge-btn"}}],
                       "timeout": 45000})
steps = r.get("steps") or []
print(f"   同链 ok: {r.get('error') == '' or not r.get('error')} | steps: {len(steps)}")
for s in steps:
    print("     -", str(s.get("action"))[:12], "ok=" + str(s.get("ok")), str(s.get("detail") or "")[:60])
print("   落页:", str(r.get("summary"))[:110])

p.pause(6)
p.set_scenario("S3-跨调用误用(R07-001 回归:拒绝句出路)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/challenge/text", "pin": True,
                       "actions": [{"fill": {"selector": "#answer", "text": "{=code}"}}],
                       "timeout": 45000})
err = str(r.get("error") or "")
print("   拒绝句:", err[:200])
print("   出路句在场:", all(k in err for k in ("one call", "as=", "pin=true")))
print("P01 DONE")
