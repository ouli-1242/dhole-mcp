"""P08 桑无咎2 · 医疗器械注册 L1 —— 长表单逐字段照手册 + 非法值拒绝句。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(8, "桑无咎2·医疗器械注册")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-照手册读 start/call")
p.call("sys_guide", {"topic": "start"})
p.pause(6)

p.set_scenario("S2-结算长表单(非法值一栏先试)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/checkout",
                       "actions": [{"fill": {"selector": "#item", "text": "电子血压计"}},
                                    {"fill": {"selector": "#qty", "text": "abc"}}],
                       "timeout": 45000})
print("   qty=abc err:", str(r.get("error") or "")[:150] or "(页面收下?)", "| ok=", not r.get("error"))
p.pause(4)
r2 = p.call("web_act", {"url": "http://127.0.0.1:8399/checkout",
                        "actions": [{"fill": {"selector": "#item", "text": "电子血压计"}},
                                     {"fill": {"selector": "#qty", "text": "3"}},
                                     {"click": {"selector": "#place-order", "confirm": True}}],
                        "timeout": 45000})
print("   正形提交: ok=", not r2.get("error"), "| 落页:", str(r2.get("summary"))[:110])

p.pause(6)
p.set_scenario("S3-不存在的字段(第一拒教寻址)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/checkout",
                       "actions": [{"fill": {"selector": "# Qty", "text": "1"}}],
                       "timeout": 45000})
err = str(r.get("error") or "")
print("   坏 selector 第一拒:", err[:200])
print("P08 DONE")
