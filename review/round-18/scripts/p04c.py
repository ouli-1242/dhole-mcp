"""P04c 老纪补条 —— 写入闸按 next_action 补 confirm=true 后正常 POST。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

p = PersonaSim(4, "老纪·运维工程师")
p.set_scenario("补条-确认后写")
r = p.call("web_request", {"url": ROOT + "/checkout?cb=p04c", "method": "POST",
                           "body": "item=机械键盘&qty=1",
                           "content_type": "application/x-www-form-urlencoded",
                           "confirm": True})
print("   ok:", r.get("content_ok"), "status:", (r.get("metadata") or {}).get("status"),
      "err:", str(r.get("error"))[:120])
print("   summary:", str(r.get("summary"))[:180])
print("   content:", str(r.get("content"))[:160])
