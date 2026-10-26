"""P01b 苏晓补条 —— 数字字符串 'abc' 是否拒绝；正确靶页上的 click 自愈。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

p = PersonaSim(1, "苏晓·新手产品经理")
p.set_scenario("补条")

r = p.call("web_read", {"url": ROOT + "/article?cb=p01c", "max_content_chars": "abc"})
print("   abc err:", str(r.get("error"))[:220])

p.pause(2)
r = p.call("web_read", {"url": ROOT + "/article?cb=p01d", "max_content_chars": "50"})
print("   '50' err:", str(r.get("error"))[:160], "| chars:", r.get("total_extracted_chars"))
print("   summary:", str(r.get("summary"))[:200])

p.pause(2)
r = p.call("web_act", {"url": ROOT + "/frame/inner?cb=p01e", "timeout": 30000,
                       "actions": [{"click": "#note"}]})
acts = (r.get("metadata") or {}).get("actions") or []
print("   click ok:", acts[0].get("ok") if acts else None,
      "err:", str(acts[0].get("error"))[:120] if acts else "")
