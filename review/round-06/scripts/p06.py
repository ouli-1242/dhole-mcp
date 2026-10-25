"""P06 百里萱 · 教务 L1 —— 跨会话课表存取。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(6, "百里萱·教务")
p.set_scenario("S1-存课表页")
p.call("cred_defaults", {"set": {"allow_private": True}})
r = p.call("web_read", {"url": "http://127.0.0.1:8399/collections", "timeout": 30000})
p.observe("save", r, "content_ok")
p.pause(7)
p.set_scenario("S2-跨会话找回")
pb = PersonaSim(6, "百里萱·明天")
r = pb.call("web_recall", {"query": "专题 推荐"})
p.rows(r, "results", 3, 120)
p.pause(6)
p.set_scenario("S3-求助路标")
r = pb.call("sys_guide", {"topic": "start"})
p.observe("guide", r, "summary")
print("P06 DONE")
