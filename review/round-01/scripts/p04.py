"""P04 林老师 · 高校研究员 · L3 —— round-01 真实模拟。"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(4, "林老师·研究员")
p.seq = 0

# S1 学术检索 + 日期档
p.set_scenario("S1-学术检索")
r = p.call("web_search", {"query": "transformer attention mechanism survey",
                          "max_results": 6, "freshness": "year"})
p.observe("search", r, "summary", "date_filter", "engines_used")
p.rows(r, "results", 6, 120)
p.pause(12, "搜索间隔")

# S2 深挖研究包（回归#2：timeout 分摊钳制）
p.set_scenario("S2-研究包")
r = p.call("web_research", {"query": "attention mechanism survey 2025",
                            "max_sources": 2, "per_source_chars": 3000,
                            "timeout": 150000})
p.observe("research", r, "summary", "error")
for s in (r.get("sources") or r.get("evidence") or r.get("results") or []):
    if isinstance(s, dict):
        print("   src:", str(s.get("url"))[:60], "ok:", s.get("content_ok"),
              "chars:", s.get("total_extracted_chars"),
              str(s.get("error"))[:80] if s.get("error") else "")
p.pause(12)

# S3 arxiv PDF 指定页
p.set_scenario("S3-PDF指定页")
r = p.call("web_read", {"url": "https://arxiv.org/pdf/1706.03762v7",
                        "pages": "1-2", "timeout": 90000})
p.observe("pdf", r, "content_ok", "total_extracted_chars", "page_type",
          "fetcher_used", "diagnosis.code")
p.pause(12)

# S4 链：arxiv RSS + recall
p.set_scenario("S4-订阅与召回")
r = p.call("feed_read", {"urls": ["https://r.jina.ai/https://arxiv.org/rss/cs.CL"]})
p.observe("feed", r, "summary")
p.rows(r, "feeds", 2, 160)
p.pause(8)
r = p.call("web_recall", {"query": "attention mechanism survey", "limit": 5})
p.observe("recall", r, "summary")
p.rows(r, "results", 5, 130)

print("P04 DONE")
