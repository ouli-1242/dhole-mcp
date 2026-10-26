"""P3 探针 D —— index/bool/session 类型错与空 url 列表。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

BASE = ROOT + "/frame/inner?cb=p14"
p = PersonaSim(14, "P3 探针D")
p.call("cred_defaults", {"set": {"allow_private": True}})


def call(label, tool, args):
    p.set_scenario(label)
    r = p.call(tool, args)
    acts = (r.get("metadata") or {}).get("actions") or []
    a0 = acts[0] if acts else {}
    print("   err:", str(r.get("error"))[:180],
          "| act0:", a0.get("ok"), str(a0.get("error"))[:140] if a0.get("error") else "")
    return r


call("D1-click-index-字符串", "web_act",
     {"url": BASE, "timeout": 30000, "actions": [{"click": {"index": "3"}}]})
call("D2-click-index-布尔", "web_act",
     {"url": BASE, "timeout": 30000, "actions": [{"click": {"index": True}}]})
call("D3-screenshot-index-字符串", "web_act",
     {"url": BASE, "timeout": 30000, "actions": [{"screenshot": {"index": "0"}}]})
call("D4-read-urls-空数组", "web_read", {"urls": []})
call("D5-read-url-列表", "web_read", {"url": [BASE]})
call("D6-act-session_id-数字", "web_act",
     {"url": BASE, "timeout": 30000, "session_id": 123, "actions": [{"click": "#note"}]})
call("D7-act-headed-字符串", "web_act",
     {"url": BASE, "timeout": 30000, "headed": "yes", "actions": [{"click": "#note"}]})
call("D8-read-session_id-数字", "web_read", {"url": BASE, "session_id": 123})
call("D9-act-confirm-数字", "web_act",
     {"url": BASE, "timeout": 30000, "confirm": 1, "actions": [{"click": "#save"}]})
call("D10-read-timeout-负", "web_read", {"url": BASE, "timeout": -1})
