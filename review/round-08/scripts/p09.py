"""P09 库尔班2 · DBA L3 猎手 —— 深水错误注入(链中失败语义/重名/错 frame/pin 边界)。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(9, "库尔班2·DBA")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-链中一 action 失败(fail-fast 语义+分账)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/dropdown",
                       "actions": [{"fill": {"selector": "#note", "text": "step1"}},
                                    {"click": {"selector": "#nope"}},
                                    {"select": {"selector": "#country", "option": "JP"}}],
                       "timeout": 45000})
steps = r.get("steps") or []
print(f"   err={str(r.get('error') or '')[:130]}")
print(f"   steps 分账: {[(str(s.get('action')), str(s.get('ok'))) for s in steps]}")
print("   语义: 失败后 select 是否被跳过(账行可见)")

p.pause(6)
p.set_scenario("S2-frame 错名")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/frame",
                       "actions": [{"fill": {"selector": "#note", "text": "x", "frame": "#nosuch"}}],
                       "timeout": 45000})
print("   错 frame err:", str(r.get("error") or "")[:200])

p.pause(6)
p.set_scenario("S3-pin 与 {=} 越界边界")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/about", "pin": True,
                       "actions": [{"read": {"selector": "h1", "as": "h"}},
                                    {"fill": {"selector": "#note", "text": "{=h}"}}],
                       "timeout": 45000})
err = str(r.get("error") or "")
print("   pin 页引用越界(无 #note 可填) err:", err[:180])

p.pause(6)
p.set_scenario("S4-空 actions/纯读链边界")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/about", "pin": True,
                       "actions": [], "timeout": 45000})
print("   空 actions: err=", str(r.get("error") or "")[:120], "| summary:", str(r.get("summary"))[:90])
print("P09 DONE")
