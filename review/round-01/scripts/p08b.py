"""P08 追加：hoverdrag 真实结构 + 正形重试。"""
import sys, json
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT
p = PersonaSim(8, "Devin·外包")
p.seq = 8
p.set_scenario("S3b-hoverdrag重试")
r = p.call("web_read", {"url": ROOT + "/hoverdrag", "css_selector": "[id], [draggable]", "max_content_chars": 2000})
p.observe("structure", r, "content")
p.pause(8)
r = p.call("web_act", {"url": ROOT + "/hoverdrag", "timeout": 60000,
                       "actions": [
                           {"hover": {"selector": "#hover-card"}},
                           {"drag": {"selector": "#drag-knob"}},
                           {"read": {"selector": "#status", "as": "st"}},
                       ]})
p.observe("retry", r, "content_ok", "content")
for a in (r.get("metadata") or {}).get("actions") or []:
    print("   act:", json.dumps(a, ensure_ascii=False)[:220])
print("P08B DONE")
