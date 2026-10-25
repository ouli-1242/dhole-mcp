"""P01 陈曦 · 数据分析师 · L2 —— round-01 真实模拟（4 场景）。"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

p = PersonaSim(1, "陈曦·数据分析")

# S1 本地报表解读（doc_parse xlsx + GBK CSV）
p.set_scenario("S1-本地报表")
r = p.call("doc_parse", {"file_path": "tests/sample.xlsx"})
p.observe("xlsx", r, "total_extracted_chars", "metadata.encoding", "metadata.sheets")
p.pause(6)
r = p.call("doc_parse", {"file_path": "tests/gbk_sample.csv"})
p.observe("gbk csv", r, "total_extracted_chars", "metadata.encoding")

# S2 盯竞品博客增量（第一次打本地靶站 -> 预期私网门具名拒绝 -> 摩擦路线）
p.pause(30, "场景间隔")
p.set_scenario("S2-盯站增量")
r = p.call("feed_read", {"urls": [ROOT + "/feed.xml"], "since": "3d"})
p.observe("feed first", r, "error", "diagnosis.code", "next_action")
if r.get("error"):
    p.pause(8, "读懂拒绝后放行")
    r2 = p.call("cred_defaults", {"set": {"allow_private": True}})
    p.observe("cred", r2, "summary", "changed")
    p.pause(4)
    r = p.call("feed_read", {"urls": [ROOT + "/feed.xml"], "since": "3d"})
p.observe("feed", r, "summary")
p.rows(r, "feeds", 3)
p.pause(10, "换真实源")
r = p.call("feed_read", {"urls": ["https://www.ruanyifeng.com/blog/atom.xml"],
                         "since": "2026-10-18", "max_items": 5})
p.observe("ruanyifeng", r, "summary")
p.rows(r, "feeds", 2)

# S3 链：feed 条目 -> 读正文（focus）
p.pause(30, "场景间隔")
p.set_scenario("S3-读正文")
r = p.call("web_read", {"url": ROOT + "/article", "focus": "结论",
                        "max_content_chars": 6000})
p.observe("article", r, "content_ok", "total_extracted_chars", "page_type",
          "fetcher_used")

# S4 既要又要：10 天增量 + 只看 3 条 + 无日期条目怎么算
p.pause(30, "场景间隔")
p.set_scenario("S4-既要又要")
r = p.call("feed_read", {"urls": [ROOT + "/feed.xml"], "since": "10d",
                         "max_items": 3})
p.observe("both caps", r, "summary", "diagnosis.code")
p.rows(r, "feeds", 2)

print("P01 DONE")
