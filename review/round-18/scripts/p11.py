"""P3 探针 A —— 动作子字段与数组元素类型错，找未捕获异常（B 类）而非 UX。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

BASE = ROOT + "/frame/inner?cb=p11"
p = PersonaSim(11, "P3 探针A")
p.call("cred_defaults", {"set": {"allow_private": True}})


def act(label, actions):
    p.set_scenario(label)
    r = p.call("web_act", {"url": BASE, "timeout": 30000, "actions": actions})
    acts = (r.get("metadata") or {}).get("actions") or []
    a0 = acts[0] if acts else {}
    print("   err:", str(r.get("error"))[:200],
          "| ok:", a0.get("ok"),
          "| act_err:", str(a0.get("error"))[:180])
    return r


act("A1-click-at-字符串", [{"click": {"at": "10,20"}}])
act("A2-drag-数字端点", [{"drag": {"selector": 1, "to": 2}}])
act("A3-screenshot-save_to-数字", [{"screenshot": {"save_to": 123}}])
act("A4-read-selector-数字", [{"read": {"selector": 5}}])
act("A5-snapshot-字符串", [{"snapshot": "yes"}])
act("A6-wait-毫秒字符串", [{"wait": {"ms": "abc"}}])
act("A7-upload-files-字符串", [{"upload": {"selector": "#doc", "files": "a.txt"}}])
act("A8-select-option-数字", [{"select": {"selector": "#country", "option": 5}}])
act("A9-tab-open-数字", [{"tab": {"open": 1}}])
act("A10-press-key-对象", [{"press": {"selector": "#note", "key": ["Enter"]}}])
