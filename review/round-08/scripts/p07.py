"""P07 越千帆2 · 技术招聘 L2 —— 人在中途双挑战(text pin + captcha 图形案)。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(7, "越千帆2·技术招聘")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-text 挑战 pin=true(读→人转写→下一发填)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/challenge/text", "pin": True,
                       "actions": [{"read": {"selector": "#challenge", "as": "code"}}],
                       "timeout": 45000})
token = ""
for s in (r.get("steps") or []):
    if str(s.get("action")) == "read":
        token = str(s.get("text") or s.get("detail") or "").strip()
print(f"   第一发 err={str(r.get('error') or '')[:60]} | 读到 token={token!r} (人在中途抄下)")
p.pause(8)
r2 = p.call("web_act", {"url": "http://127.0.0.1:8399/challenge/text", "pin": True,
                        "actions": [{"fill": {"selector": "#answer", "text": token}},
                                     {"click": {"selector": "#challenge-btn"}}],
                        "timeout": 45000})
print(f"   第二发(pin 同页) err={str(r2.get('error') or '')[:60]} | 落页: {str(r2.get('summary'))[:110]}")
print("   期望: 抄写正确(pin 保页 → 同一枚 token)")

p.pause(6)
p.set_scenario("S2-captcha 图形案(pin + screenshot → 占位转写提交通路)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/captcha", "pin": True,
                       "actions": [{"screenshot": {}}],
                       "timeout": 60000})
shot = ""
for s in (r.get("steps") or []):
    if str(s.get("action")) == "screenshot":
        shot = str(s.get("image") or s.get("detail") or "")
print(f"   screenshot 账行在场: {bool(shot) or 'screenshot' in str(r)[:400]} | err={str(r.get('error') or '')[:60]}")
p.pause(8)
r2 = p.call("web_act", {"url": "http://127.0.0.1:8399/captcha", "pin": True,
                        "actions": [{"fill": {"selector": "#answer", "text": "ZZZZ"}},
                                     {"click": {"selector": "#captcha-btn"}}],
                        "timeout": 45000})
print(f"   占位转写提交: err={str(r2.get('error') or '')[:80]} | 落页: {str(r2.get('summary'))[:100]}")
print("   期望: 通路真(提交落 verify),错答 403 具名 —— 转写环节归人,脚本如实记录")
print("P07 DONE")
