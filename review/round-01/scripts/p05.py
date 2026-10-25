"""P05 王倩 · 产品经理 · L2 —— round-01 真实模拟。"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

p = PersonaSim(5, "王倩·产品")
p.seq = 0

# S1 竞品盯站增量
p.set_scenario("S1-盯站增量")
r = p.call("feed_read", {"urls": [ROOT + "/feed.xml"], "since": "2026-10-20"})
p.observe("feed", r, "summary")
p.rows(r, "feeds", 2, 150)
p.pause(10)

# S2 建图与找详情页
p.set_scenario("S2-建图找详情")
r = p.call("web_discover", {"url": ROOT + "/collections", "max_pages": 20})
p.observe("discover", r, "summary", "links")
p.pause(10)
r = p.call("web_crawl", {"url": ROOT + "/collections", "max_pages": 5,
                         "max_depth": 1, "search": "卡片"})
p.observe("crawl filter", r, "summary", "pages_crawled")
p.pause(10)
r = p.call("web_crawl", {"url": ROOT + "/collections", "max_pages": 5,
                         "max_depth": 1})
p.observe("crawl plain", r, "summary", "pages_crawled")
p.pause(30, "场景间隔")

# S3 变更监控（/changing 每次都不同）
p.set_scenario("S3-变更监控")
r = p.call("web_read", {"url": ROOT + "/changing", "extraction_type": "check"})
p.observe("check 1", r, "content_ok", "changed", "summary")
p.pause(10)
r = p.call("web_read", {"url": ROOT + "/changing", "extraction_type": "check"})
p.observe("check 2", r, "content_ok", "changed", "summary")

# S4 含糊需求（不给 URL）
p.pause(30, "场景间隔")
p.set_scenario("S4-含糊需求")
r = p.call("web_read", {"url": ROOT + "/collections",
                        "max_content_chars": 4000})
p.observe("collections", r, "content_ok", "total_extracted_chars", "page_type")

print("P05 DONE")
