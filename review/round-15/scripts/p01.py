"""P01 沈砚 · 医学院图书馆员 L2 —— 同页双 selector 交错 · 双 session 填表隔离 · guide/state。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(1, "沈砚·医学院图书馆员")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-查手册确认读法")
r = p.call("sys_guide", {"topic": "web_read"})
print(f"   topic_known={'web_read' in str(r.get('content'))} len={len(str(r.get('content')))}")

p.pause(4)
p.set_scenario("S2-同页两种 selector 交错读")
U = "http://127.0.0.1:8399/article?cb=p01sel"
a = p.call("web_read", {"url": U, "css_selector": "h1"})
p.pause(3)
b = p.call("web_read", {"url": U, "css_selector": "p"})
p.pause(3)
c = p.call("web_read", {"url": U, "css_selector": "h1"})
ta = "".join(str(x) for x in (a.get("content") or []))
tb = "".join(str(x) for x in (b.get("content") or []))
tc = "".join(str(x) for x in (c.get("content") or []))
print(f"   h1={len(ta)}字 p={len(tb)}字 h1again={len(tc)}字 同selector一致={ta == tc} 异selector不同={ta != tb}")
print(f"   cached={a.get('cached')},{b.get('cached')},{c.get('cached')}")

p.pause(4)
p.set_scenario("S3-双 session 各自填名（pin）")
TAIL = [{"read": {"selector": "input[name=username]", "property": "value"}}]
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "session_id": "p01a",
                       "pin": True, "timeout": 45000,
                       "actions": [{"fill": {"selector": "input[name=username]", "text": "沈砚"}}] + TAIL})
acts = (r.get("metadata") or {}).get("actions") or []
print(f"   A fill={acts[0].get('ok') if acts else None} read={acts[-1].get('read') if acts else None!r}")

p.pause(3)
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "session_id": "p01b",
                       "pin": True, "timeout": 45000,
                       "actions": [{"fill": {"selector": "input[name=username]", "text": "访客"}}] + TAIL})
acts = (r.get("metadata") or {}).get("actions") or []
print(f"   B fill={acts[0].get('ok') if acts else None} read={acts[-1].get('read') if acts else None!r}")

p.pause(3)
p.set_scenario("S4-回 A session 读数（应仍是沈砚）")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "session_id": "p01a",
                       "pin": True, "timeout": 45000, "actions": TAIL})
acts = (r.get("metadata") or {}).get("actions") or []
print(f"   pin_hit={'pin hit' in str(r.get('summary')).lower()} "
      f"read={acts[-1].get('read') if acts else None!r}")

p.pause(4)
p.set_scenario("S5-state 复盘")
r = p.call("sys_state", {})
print(f"   message: {str(r.get('message'))[:180]}")
print("P01 DONE")
