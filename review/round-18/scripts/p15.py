"""R18 候选复现 —— press.key 非字符串被 str() 吞下，失败句指向 selector。"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

BASE = ROOT + "/frame/inner?cb=p15"
p = PersonaSim(15, "候选复现-press")
p.call("cred_defaults", {"set": {"allow_private": True}})

cases = [
    ("列表", ["Enter"]),
    ("数字", 13),
    ("字典", {"code": 13}),
]
for label, key in cases:
    p.set_scenario("press-key-" + label)
    r = p.call("web_act", {"url": BASE, "timeout": 30000,
                           "actions": [{"press": {"selector": "#note", "key": key}}]})
    acts = (r.get("metadata") or {}).get("actions") or []
    a0 = acts[0] if acts else {}
    print(f"   {label}: top_err={str(r.get('error'))[:120]!r}")
    print(f"      act_ok={a0.get('ok')} act_err={str(a0.get('error'))[:300]!r}")

p.set_scenario("press-key-合法")
r = p.call("web_act", {"url": BASE, "timeout": 30000,
                       "actions": [{"press": {"selector": "#note", "key": "Enter"}}]})
acts = (r.get("metadata") or {}).get("actions") or []
a0 = acts[0] if acts else {}
print("   合法:", a0.get("ok"), str(a0.get("error"))[:160])
