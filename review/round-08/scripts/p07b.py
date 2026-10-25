"""p07b 补全：screenshot 带 selector 形 + captcha 属性读 + confirm 提交（错答 403 具名）+ text 挑战 confirm 完赛。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(14, "p07b-补全")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S2b-screenshot 形修正 + attribute 读")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/captcha", "pin": True,
                       "actions": [{"screenshot": {"selector": "#captcha"}},
                                    {"read": {"selector": "#captcha", "attribute": "src"}}],
                       "timeout": 60000})
for a in (r.get("metadata") or {}).get("actions") or []:
    s = json.dumps(a, ensure_ascii=False) if 'json' in dir() else str(a)
    print("   ", str(a.get("action"))[:36], "ok=" + str(a.get("ok")), str(a.get("read") or "")[:70])

p.pause(6)
p.set_scenario("S2c-占位转写+confirm(403 具名)")
r2 = p.call("web_act", {"url": "http://127.0.0.1:8399/captcha", "pin": True,
                        "actions": [{"fill": {"selector": "#answer", "text": "ZZZZ"}},
                                     {"click": {"selector": "#captcha-btn", "confirm": True}}],
                        "timeout": 60000})
print("   err:", str(r2.get("error") or "")[:120])
print("   落页:", str(r2.get("summary"))[:130])

p.pause(6)
p.set_scenario("S1b-text 挑战 confirm 完赛")
r3 = p.call("web_act", {"url": "http://127.0.0.1:8399/challenge/text", "pin": True,
                        "actions": [{"read": {"selector": "#challenge", "as": "code"}}],
                        "timeout": 45000})
tok = ""
for a in (r3.get("metadata") or {}).get("actions") or []:
    if str(a.get("action", "")).startswith("read"):
        tok = str(a.get("read") or "").strip()
p.pause(6)
r4 = p.call("web_act", {"url": "http://127.0.0.1:8399/challenge/text", "pin": True,
                        "actions": [{"fill": {"selector": "#answer", "text": tok}},
                                     {"click": {"selector": "#challenge-btn", "confirm": True}},
                                     {"read": {"selector": "h1"}}],
                        "timeout": 60000})
for a in (r4.get("metadata") or {}).get("actions") or []:
    print("   ", str(a.get("action"))[:36], "ok=" + str(a.get("ok")), str(a.get("read") or a.get("error") or "")[:50])
print("P07B DONE")
import json
