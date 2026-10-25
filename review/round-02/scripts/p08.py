"""P08 周奕 · 自动化测试工程师 · L2 刁难组 —— round-02（挑战页可发现性/drag/iframe/刀难）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(8, "周奕·测试")

p.set_scenario("S1-挑战页不给提示")
# 测试员的本能：先读页面拿码，再跨调用填 —— 靶站每导航换码，注定 403
r = p.call("web_read", {"url": "http://127.0.0.1:8399/challenge/text", "cache_ttl": 0})
token = ""
body = (r.get("content") or [""])[0]
import re
m = re.search(r"([A-Z0-9]{4,6})", body)
if m:
    token = m.group(1)
print("      读到的码:", token)
p.pause(4)
r = p.call("web_act", {"url": "http://127.0.0.1:8399/challenge/text",
                       "actions": [{"fill": {"selector": "#answer", "text": token}},
                                   {"click": {"selector": "#challenge-btn", "confirm": True}}]})
p.observe("跨调用填", r, "error", "content_ok")
p.pause(6)
# 第二试：同链 + 猜错语法（上一轮 P07 的死法——看 fill note 教不教）
r = p.call("web_act", {"url": "http://127.0.0.1:8399/challenge/text",
                       "actions": [{"read": {"selector": "#challenge", "as": "code"}},
                                   {"fill": {"selector": "#answer", "text": "{CODE}"}}]})
steps = (r.get("metadata") or {}).get("actions") or []
note = steps[1].get("note") if len(steps) > 1 else "无"
print("      fill note:", (note or "无")[:220])
p.pause(6)
# 第三试：正形
r = p.call("web_act", {"url": "http://127.0.0.1:8399/challenge/text",
                       "actions": [{"read": {"selector": "#challenge", "as": "code"}},
                                   {"fill": {"selector": "#answer", "text": "{=code}"}},
                                   {"click": {"selector": "#challenge-btn", "confirm": True}}]})
p.observe("正形", r, "error", "content_ok")
p.pause(8)

p.set_scenario("S2-hoverdrag 快照路线")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/hoverdrag", "actions": [{"snapshot": True}]})
steps = (r.get("metadata") or {}).get("actions") or []
snap = steps[0] if steps else {}
print("      snapshot keys:", list(snap.keys())[:8])
print("      snapshot sample:", str(snap.get("elements") or snap.get("snapshot") or snap)[:260])
p.pause(6)
r = p.call("web_act", {"url": "http://127.0.0.1:8399/hoverdrag",
                       "actions": [{"drag": {"from": "#a", "to": "#b"}}]})
p.observe("drag-旧形", r, "error")
p.pause(5)

p.set_scenario("S3-iframe")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/iframe", "actions": [{"read": "body"}]})
p.observe("iframe", r, "error", "content_ok", "summary")
p.pause(8)

p.set_scenario("S4-刀难三连")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/", "actions": []})
p.observe("空actions", r, "error")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/", "actions": [{"teleport": "#a"}]})
p.observe("未知键", r, "error")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/", "actions": [{"click": "#x"}], "confirm": True})
p.observe("顶层confirm", r, "error")

print("P08 DONE")
