"""P08 谷雨 · 前端工程师 L2 —— web_act 全动作面回归（R01-006 同链取值 / R01-010 drag 全形 / checkout 确认闸）。"""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(8, "谷雨·前端")

p.set_scenario("S1-登录态全链")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "actions": [
    {"fill": {"selector": "#username", "text": "demo"}},
    {"fill": {"selector": "#password", "text": "demo"}},
    {"click": {"selector": "#login-btn", "confirm": True}},
], "timeout": 60000})
p.observe("login", r, "ok", "summary", "steps_ok")
p.pause(9)

p.set_scenario("S2-checkout 该问就问(确认闸)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/checkout", "actions": [
    {"fill": {"selector": "#qty", "text": "2"}},
    {"click": {"selector": "#place-order"}},
], "timeout": 60000})
p.observe("checkout-noconfirm", r, "ok", "summary")
p.pause(8)

p.set_scenario("S3-挑战页同链取值(变卦后回归)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/challenge/text", "actions": [
    {"read": {"selector": "#challenge", "as": "code"}},
    {"fill": {"selector": "#answer", "text": "{=code}"}},
    {"click": {"selector": "#challenge-btn", "confirm": True}},
], "timeout": 60000})
p.observe("challenge", r, "ok", "summary", "steps_ok")
p.pause(8)

p.set_scenario("S4a-drag 陌生键第一拒全形(回归)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/hoverdrag", "actions": [
    {"drag": {"from": "#card-a", "to": "#slot-b"}},
], "timeout": 60000})
p.observe("drag-first-refusal", r, "error", "summary")
print("      (回归 R01-010: 期望第一拒给全形 {selector|index, to} + unexpected keys)")
p.pause(7)

p.set_scenario("S4b-drag 正形")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/hoverdrag", "actions": [
    {"drag": {"selector": "#card-a", "to": "#slot-b"}},
], "timeout": 60000})
p.observe("drag-ok", r, "ok", "summary")
print("P08 DONE")
