"""P10 凤九霄 · UI 设计师 L1 —— 表格形态 + 跨会话找回。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(10, "凤九霄·设计")
p.call("cred_defaults", {"set": {"allow_private": True}})
p.set_scenario("S1-读规范表格页")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article", "timeout": 30000})
c = r.get("content"); c = ''.join(c) if isinstance(c, list) else str(c)
print("   markdown 表格形态:", "| --- |" in c or "<table" in c, "| len:", len(c))
p.pause(7)
p.set_scenario("S2-跨会话找回")
pb = PersonaSim(10, "凤九霄·下周")
r = pb.call("web_recall", {"query": "抓取 配置 缓存"})
p.rows(r, "results", 3, 110)
p.pause(6)
p.set_scenario("S3-求助")
r = pb.call("sys_guide", {"topic": "call"})
p.observe("guide", r, "summary")
print("P10 DONE")
