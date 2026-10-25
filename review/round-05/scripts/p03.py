"""P03 蔺相宜 · 法务 L2 —— 已下线文件的 archive 档真触发。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(3, "蔺相宜·法务")
p.set_scenario("S1-外网已下线页(404/410 走 archive)")
r = p.call("web_read", {"url": "https://www.specification.cn/standard/detail/404-nope", "timeout": 45000})
p.observe("dead-standard", r, "error", "summary")
p.pause(9)
p.set_scenario("S2-fixture 404 对照")
p.call("cred_defaults", {"set": {"allow_private": True}})
r = p.call("web_read", {"url": "http://127.0.0.1:8399/no-such-page", "timeout": 30000})
p.observe("fixture-404", r, "error")
p.pause(7)
p.set_scenario("S3-sys_state")
r = p.call("sys_state", {})
p.observe("state", r, "summary")
print("P03 DONE")
