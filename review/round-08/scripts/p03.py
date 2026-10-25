"""P03 甘棠2 · 电商卖家 L1 —— session_id 跨调用购物车 + confirm 闸家产。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(3, "甘棠2·电商卖家")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-登录(session s3)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "session_id": "s3",
                       "actions": [{"fill": {"selector": "#username", "text": "gantang"}},
                                    {"fill": {"selector": "#password", "text": "shop123"}},
                                    {"click": {"selector": "#login-btn", "confirm": True}}],
                       "timeout": 45000})
print("   登录 err:", str(r.get("error") or "")[:80], "| 落页:", str(r.get("summary"))[:80])

p.pause(6)
p.set_scenario("S2-结算(同 session)+confirm 闸")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/checkout", "session_id": "s3",
                       "actions": [{"fill": {"selector": "#qty", "text": "2"}}],
                       "timeout": 45000})
print("   填单 err:", str(r.get("error") or "")[:80])
r2 = p.call("web_act", {"url": "http://127.0.0.1:8399/checkout", "session_id": "s3",
                        "actions": [{"click": {"selector": "#place-order"}}],
                        "timeout": 45000})
err = str(r2.get("error") or "")
print("   无 confirm 提交: err=", err[:160], "-> 闸:", "拒" if "confirm" in err.lower() else "?")
p.pause(4)
r3 = p.call("web_act", {"url": "http://127.0.0.1:8399/checkout", "session_id": "s3",
                        "actions": [{"click": {"selector": "#place-order", "confirm": True}}],
                        "timeout": 45000})
print("   confirm 后: ok=", not r3.get("error"), "| 落页:", str(r3.get("summary"))[:110])

p.pause(6)
p.set_scenario("S3-账户复查(跨调用会话保持)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/account", "session_id": "s3",
                       "actions": [{"read": {"selector": "h1"}}], "timeout": 45000})
got = ""
for s in (r.get("steps") or []):
    if str(s.get("action")) == "read":
        got = str(s.get("text") or s.get("detail") or "")
print(f"   /account h1: {got[:40]!r} (期望 欢迎回来=会话保持)")
print("P03 DONE")
