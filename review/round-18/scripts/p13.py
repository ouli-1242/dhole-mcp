"""P3 探针 C —— 较少校验的动作子字段（evaluate/scroll/wait_selector/expect/dialog/tab）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

BASE = ROOT + "/frame/inner?cb=p13"
p = PersonaSim(13, "P3 探针C")
p.call("cred_defaults", {"set": {"allow_private": True}})


def act(label, actions, **kw):
    p.set_scenario(label)
    args = {"url": BASE, "timeout": 30000, "actions": actions}
    args.update(kw)
    r = p.call("web_act", args)
    acts = (r.get("metadata") or {}).get("actions") or []
    a0 = acts[0] if acts else {}
    print("   err:", str(r.get("error"))[:180],
          "| ok:", a0.get("ok"),
          "| act_err:", str(a0.get("error"))[:180])
    return r


act("C1-evaluate-expr-数字", [{"evaluate": {"expr": 123}}])
act("C2-evaluate-confirm-字符串", [{"evaluate": {"expr": "1+1", "confirm": "yes"}}])
act("C3-scroll-to-数字", [{"scroll": {"to": 123}}])
act("C4-wait_selector-count-字符串", [{"wait_selector": {"selector": "#note", "count": "x"}}])
act("C5-click-expect-数字", [{"click": {"selector": "#note", "expect": 123}}])
act("C6-dialog-answer-列表", [{"dialog": {"answer": ["x"]}}])
act("C7-tab-列表", [{"tab": ["x"]}])
act("C8-pin-字符串yes", [{"snapshot": True}], pin="yes")
act("C9-fill-attribute-数字", [{"fill": {"selector": "#note", "text": "x", "attribute": 5}}])
act("C10-select-option-空列表", [{"select": {"selector": "#note", "option": []}}])
