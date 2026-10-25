"""P01 温旖 · 内容创作者 L2 —— 选题库跨会话（R02-007 回归：同页两抓单行 variants）。"""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(1, "温旖·创作者")

p.set_scenario("S1-找选题")
r = p.call("web_search", {"query": "独立开发者 收入 报告 2026", "max_results": 5})
p.observe("search", r, "summary", "engines_used")
p.rows(r, "results", 3, 120)
p.pause(7)

p.set_scenario("S2-存两篇(变卦:先只要这几篇)")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/collections", "timeout": 30000})
p.observe("read1", r, "content_ok", "summary")
p.pause(6)
r = p.call("web_read", {"url": "http://127.0.0.1:8399/collections", "timeout": 30000})
p.observe("read1-again", r, "content_ok", "summary")
p.pause(6)
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article", "timeout": 30000})
p.observe("read2", r, "content_ok", "summary")
p.pause(8)


p.set_scenario("S2b-按报错指引开门再存(真人会照做)")
r = p.call("cred_defaults", {"set": {"allow_private": True}})
p.observe("cred", r, "summary")
p.pause(4)
r = p.call("web_read", {"url": "http://127.0.0.1:8399/collections", "timeout": 30000})
p.observe("read1", r, "content_ok", "summary")
p.pause(6)
r = p.call("web_read", {"url": "http://127.0.0.1:8399/collections", "timeout": 30000})
p.observe("read1-again", r, "content_ok", "summary")
p.pause(6)
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article", "timeout": 30000})
p.observe("read2", r, "content_ok", "summary")
p.pause(8)

p.set_scenario("S3-跨会话二访(明天)")
pb = PersonaSim(1, "温旖·二访")  # 新 initialize = 新会话
r = pb.call("web_recall", {"query": "推荐专题 选题"})
p.observe("recall", r, "summary", "engine")
p.rows(r, "results", 5, 140)
p.pause(6)
r = pb.call("web_recall", {"query": "文章"})
p.observe("recall2", r, "summary")
p.rows(r, "results", 5, 140)

print("P01 DONE")
