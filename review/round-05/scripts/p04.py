"""P04 弭兵 · 数据工程 L3 —— 批量混坏(死端口/404/私网) + 显式 timeout 分摊。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(4, "弭兵·数据工程")
p.set_scenario("S1-批量 8 发混 3 坏")
p.call("cred_defaults", {"set": {"allow_private": True}})
urls = [f"http://127.0.0.1:8399/work/{i}" for i in (1, 2, 3, 4, 5)] + [
    "http://127.0.0.1:9391/", "http://127.0.0.1:8399/no-such-page",
    "http://10.255.255.1:81/"]
r = p.call("web_read", {"urls": urls, "timeout": 45000})
p.observe("batch", r, "summary")
p.rows(r, "results", 8, 120)
p.pause(9)
p.set_scenario("S2-显式 timeout 的批量")
r = p.call("web_read", {"urls": [f"http://127.0.0.1:8399/work/{i}?t=1" for i in (1, 2)],
                        "timeout": 60000})
p.observe("batch-explicit", r, "summary")
p.pause(8)
p.set_scenario("S3-sys_state 对账")
r = p.call("sys_state", {})
p.observe("state", r, "summary")
print("P04 DONE")
