"""P02 沙青 · 技术写作 L3 猎手 —— crawl resume 跨会话 + 私网豁免边界 + sys_state。"""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(2, "沙青·技术写作")

p.set_scenario("S1-整站地图(长任务)")
r = p.call("cred_defaults", {"set": {"allow_private": True}})
p.observe("cred", r, "summary")
r = p.call("web_crawl", {"url": "http://127.0.0.1:8399/collections", "max_pages": 3, "cache_ttl": 0})
p.observe("crawl1", r, "summary", "next_action")
p.pause(8)

p.set_scenario("S2-跨会话 resume(照抄)")
resume_token = ""
for k in ("resume_token",):
    nxt = (r.get("next_action") or {})
    resume_token = nxt.get(k, "") if isinstance(nxt, dict) else ""
pb = PersonaSim(2, "沙青·续爬")  # 新会话
if resume_token:
    r2 = pb.call("web_crawl", {"url": "http://127.0.0.1:8399/collections",
                               "resume_token": resume_token, "max_pages": 3, "cache_ttl": 0})
    p.observe("crawl2-resume", r2, "summary", "next_action")
else:
    print("      (无 resume_token 可续 - 记为观察)")
p.pause(8)

p.set_scenario("S3-内网 staging 页")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/private/secret", "timeout": 20000})
p.observe("private-read", r, "content_ok", "error", "summary")
p.pause(6)

p.set_scenario("S4-豁免到底跟着谁(跨会话猎手题)")
pc = PersonaSim(2, "沙青·裸会话")  # 新会话,不设 cred_defaults
r = pc.call("web_read", {"url": "http://127.0.0.1:8399/private/secret", "timeout": 20000})
p.observe("private-nowaiver", r, "content_ok", "error", "summary")
p.pause(8)

p.set_scenario("S5-状态自查")
r = p.call("sys_state", {})
p.observe("state", r, "summary")
print("P02 DONE")
