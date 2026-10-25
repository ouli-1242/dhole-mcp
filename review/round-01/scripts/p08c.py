"""P08 终：drag 正形一击。"""
import sys, json
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT
p = PersonaSim(8, "Devin·外包")
p.seq = 10
p.set_scenario("S3c-drag正形")
r = p.call("web_act", {"url": ROOT + "/hoverdrag", "timeout": 60000,
                       "actions": [
                           {"hover": {"selector": "#hover-card"}},
                           {"drag": {"selector": "#drag-knob", "to": "#drop-zone"}},
                           {"read": {"selector": "#status", "as": "st"}},
                       ]})
p.observe("final", r, "content_ok", "content")
for a in (r.get("metadata") or {}).get("actions") or []:
    print("   act:", json.dumps(a, ensure_ascii=False)[:240])
print("P08C DONE")
