"""P10 越千帆2b · SDET L3 猎手 —— 组合态攻击(dialog×iframe/双 session 交替/跨 frame 寄存器/tab 边界)。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(10, "越千帆2b·SDET")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-dialog 武装但弹窗不出(frame 页无弹窗触发)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/frame",
                       "actions": [{"dialog": "accept"},
                                    {"click": {"selector": "#save", "frame": "app"}}],
                       "timeout": 60000})
dl = [s for s in (r.get("steps") or []) if "dialog" in str(s.get("action"))]
print("   err:", str(r.get("error") or "")[:80], "| dialog 账行:", str(dl[0])[:160] if dl else "无(武装未触发怎么记账?)")

p.pause(6)
p.set_scenario("S2-双 session 交替不串状态")
for sid, user in (("sA", "alice"), ("sB", "bob")):
    r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "session_id": sid,
                           "actions": [{"fill": {"selector": "#username", "text": user}},
                                        {"fill": {"selector": "#password", "text": "pw-" + user}},
                                        {"click": {"selector": "#login-btn", "confirm": True}}],
                           "timeout": 45000})
    print(f"   {sid}({user}) 登录 err:", str(r.get("error") or "")[:60])
for sid in ("sA", "sB"):
    r = p.call("web_act", {"url": "http://127.0.0.1:8399/account", "session_id": sid,
                           "actions": [{"read": {"selector": "h1"}}], "timeout": 45000})
    got = ""
    for s in (r.get("steps") or []):
        if str(s.get("action")) == "read":
            got = str(s.get("text") or s.get("detail") or "")
    print(f"   {sid} /account h1: {got[:30]!r} (两 session 都期望 欢迎回来,互不串)")

p.pause(6)
p.set_scenario("S3-跨 frame 寄存器(fill→read property→fill)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/frame",
                       "actions": [{"fill": {"selector": "#note", "text": "R10", "frame": "app"}},
                                    {"read": {"selector": "#note", "property": "value", "as": "v", "frame": "app"}},
                                    {"fill": {"selector": "#note", "text": "{=v}-ok", "frame": "app"}},
                                    {"click": {"selector": "#save", "frame": "app"}},
                                    {"read": {"selector": "#saved", "frame": "app"}}],
                       "timeout": 60000})
got = ""
for s in (r.get("steps") or []):
    if str(s.get("action")) == "read" and "saved" in str(s.get("selector") or ""):
        got = str(s.get("text") or s.get("detail") or "")
print(f"   err={str(r.get('error') or '')[:60]} | #saved={got[:40]!r} (期望 SAVED:R10-ok)")

p.pause(6)
p.set_scenario("S4-tab 边界(切不存在的页签/关最后一枚)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/about",
                       "actions": [{"tab": 3}], "timeout": 45000})
print("   tab 3 err:", str(r.get("error") or "")[:160])
r2 = p.call("web_act", {"url": "http://127.0.0.1:8399/about",
                        "actions": [{"tab": "list"}], "timeout": 45000})
print("   tab list:", str(r2.get("steps") or r2.get("summary"))[:200])
print("P10 DONE")
