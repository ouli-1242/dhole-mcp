"""P07 华青野 · 健身教练 L1 —— 搜→读→存→跨会话找回；视频页如实。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(7, "华青野·教练")
p.set_scenario("S1-搜训练计划")
r = p.call("web_search", {"query": "居家 徒手 训练计划 一周", "max_results": 5})
p.rows(r, "results", 3, 110)
p.pause(7)
p.set_scenario("S2-存一篇+视频页")
p.call("cred_defaults", {"set": {"allow_private": True}})
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article", "timeout": 30000})
p.observe("article", r, "content_ok")
r = p.call("web_read", {"url": "https://www.bilibili.com/v/game/esports/", "timeout": 30000})
p.observe("bilibili", r, "page_type", "summary")
p.pause(7)
p.set_scenario("S3-跨会话找回")
pb = PersonaSim(7, "华青野·明天")
r = pb.call("web_recall", {"query": "抓取 缓存 说明"})
p.rows(r, "results", 3, 120)
r = pb.call("sys_guide", {"topic": "start"})
p.observe("guide", r, "summary")
print("P07 DONE")
