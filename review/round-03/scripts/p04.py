"""P04 麦冬 · 中学教师 L1 —— 跨会话找回资料 + L1 求助路标（R01-002/R01-009 回归）。"""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(4, "麦冬·教师")

p.set_scenario("S1-先看看说明书在哪(L1 求助)")
r = p.call("sys_guide", {"topic": "start"})
p.observe("guide", r, "summary")
print("      (回归 R01-009: 期望 13 tools 计数在场)")
p.pause(8)

p.set_scenario("S2-存备课资料")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article", "timeout": 30000})
p.observe("read", r, "content_ok", "summary")
p.pause(7)

p.set_scenario("S3-搜站内但词用错了")
r = p.call("web_crawl", {"url": "http://127.0.0.1:8399/collections", "search": "知识点",
                         "max_pages": 5, "cache_ttl": 0})
p.observe("crawl-search", r, "summary")
print("      (回归 R01-002: 期望 matched 0 of N 且解释词只活在正文)")
p.pause(8)

p.set_scenario("S4-跨会话:上周存的页再给我")
pb = PersonaSim(4, "麦冬·下周")  # 新会话
r = pb.call("web_recall", {"query": "文章"})
p.observe("recall", r, "summary")
p.rows(r, "results", 4, 130)
p.pause(6)
r = pb.call("web_recall", {"query": "语文 阅读 知识点"})
p.observe("recall-miss", r, "summary", "terms_absent")

print("P04 DONE")
