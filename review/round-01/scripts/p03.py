"""P03 Sarah · 自媒体内容作者 · L1 —— round-01 真实模拟。"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(3, "Sarah·自媒体")
p.seq = 0
picked = {}

# S1 查资料 + 读一条
p.set_scenario("S1-查资料")
r = p.call("web_search", {"query": "猫为什么踩奶", "max_results": 5})
p.rows(r, "results", 5, 130)
rows = r.get("results") or []
if rows:
    picked["url"] = rows[0].get("url")
    p.pause(6, "选了一条点进去")
    r2 = p.call("web_read", {"url": picked["url"], "max_content_chars": 6000})
    p.observe("read", r2, "content_ok", "total_extracted_chars", "page_type", "fetcher_used")

# S2 卡住求助（不知道下一步用什么）
p.pause(30, "场景间隔")
p.set_scenario("S2-卡住求助")
r = p.call("sys_guide", {"topic": "start"})
p.observe("guide", r, "total_extracted_chars", "summary")

# S3 变卦：换关键词重搜 + 读
p.pause(30, "场景间隔")
p.set_scenario("S3-变卦重搜")
r = p.call("web_search", {"query": "猫呼噜声很大 原因", "max_results": 5})
p.rows(r, "results", 5, 130)
rows = r.get("results") or []
if rows:
    picked["url2"] = rows[0].get("url")
    p.pause(6)
    r2 = p.call("web_read", {"url": picked["url2"], "max_content_chars": 6000})
    p.observe("read2", r2, "content_ok", "total_extracted_chars", "page_type")

# S4 挑刺：这文章什么时候发的？
p.pause(30, "场景间隔")
p.set_scenario("S4-挑来源日期")
if picked.get("url"):
    r = p.call("web_read", {"url": picked["url"], "max_content_chars": 3000})
    p.observe("date check", r, "metadata.date", "metadata.published",
              "metadata.title", "total_extracted_chars")

print("P03 DONE")
