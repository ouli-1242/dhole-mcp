"""P04b 老纪补条 —— web_request 只用写动词，URL 形状改以 POST 探；末发正常 POST。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

p = PersonaSim(4, "老纪·运维工程师")
p.set_scenario("补条")

r = p.call("web_request", {"url": "127.0.0.1:8399/checkout", "method": "POST",
                           "body": "item=x"})
print("   no-scheme err:", str(r.get("error"))[:200], "| next:", str(r.get("next_action"))[:140])

p.pause(2)
r = p.call("web_request", {"url": "ftp://127.0.0.1/x", "method": "POST", "body": "x"})
print("   ftp err:", str(r.get("error"))[:200])

p.pause(2)
r = p.call("web_request", {"url": "file:///C:/Windows/win.ini", "method": "POST",
                           "body": "x"})
print("   file err:", str(r.get("error"))[:200])

p.pause(2)
r = p.call("web_request", {"url": ROOT + "/checkout?cb=p04b", "method": "POST",
                           "body": "item=机械键盘&qty=1",
                           "content_type": "application/x-www-form-urlencoded"})
print("   normal ok:", r.get("content_ok"), "status:", (r.get("metadata") or {}).get("status"),
      "err:", str(r.get("error"))[:120])
print("   summary:", str(r.get("summary"))[:160])
