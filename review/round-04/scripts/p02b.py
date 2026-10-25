"""P02b: 求助(sys_guide)后重试 upload + iframe(frame 语法)。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
HERE = Path(__file__).resolve().parent.parent
p = PersonaSim(22, "顾晚舟·求助后")

p.set_scenario("S1b-upload 重试(修好路径)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/upload", "actions": [
    {"upload": {"selector": "#doc", "files": [str(HERE / "sessions" / "files" / "报价说明.pdf")]}},
    {"click": {"selector": "#send"}},
], "timeout": 60000})
for st in ((r.get("metadata") or {}).get("actions") or []):
    print("   step:", st.get("action"), "ok:", st.get("ok"), str(st.get("error"))[:80])
p.pause(7)

p.set_scenario("S3b-iframe(先查说明书再上 frame 语法)")
g = p.call("sys_guide", {"topic": "behavior"})
import json
s = json.dumps(g, ensure_ascii=False)
i = s.find("frame")
print("   guide 提到 frame:", s[max(0,i-60):i+120].replace("\n", " ")[:170] if i >= 0 else "无")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/frame", "actions": [
    {"fill": {"selector": "#note", "frame": "#app", "text": "iframe 里的字"}},
    {"click": {"selector": "#save", "frame": "#app"}},
    {"read": {"selector": "#saved", "frame": "#app", "as": "mark"}},
], "timeout": 60000})
for st in ((r.get("metadata") or {}).get("actions") or []):
    print("   step:", st.get("action"), "ok:", st.get("ok"), str(st.get("error"))[:90])
print("P02b DONE")
