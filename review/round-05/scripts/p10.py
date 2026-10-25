"""P10 戚山雨 · 客服 L1 —— 坏链接批量转述。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(10, "戚山雨·客服")
p.set_scenario("S1-客户链接批量(混 http:// 与 404)")
p.call("cred_defaults", {"set": {"allow_private": True}})
r = p.call("web_read", {"urls": ["http://127.0.0.1:8399/article",
                                 "http://127.0.0.1:8399/collections",
                                 "http://127.0.0.1:8399/nope-3",
                                 "http://127.0.0.1:8399/work/1",
                                 "http://127.0.0.1:8399/nope-5"], "timeout": 30000})
p.observe("batch", r, "summary")
p.rows(r, "results", 5, 110)
p.pause(8)
p.set_scenario("S2-求助路标")
r = p.call("sys_guide", {"topic": "start"})
p.observe("guide", r, "summary")
print("P10 DONE")
