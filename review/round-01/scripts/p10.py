"""P10 Helen · 市场增长 · L2 —— round-01 真实模拟。"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(10, "Helen·增长")
p.seq = 0

# S1 收窄引擎 + 本周（回归#3 降级池诚实）
p.set_scenario("S1-收窄搜索")
r = p.call("web_search", {"query": "内容营销 增长 案例 2026", "max_results": 5,
                          "engines": ["baidu", "sogou"], "freshness": "week"})
p.observe("search", r, "summary", "engines_used", "engine_detail",
          "date_filter")
p.rows(r, "results", 5, 120)
p.pause(12)

# S2 既要又要还要（引擎超上限）
p.set_scenario("S2-既要还要")
r = p.call("web_search", {"query": "增长 案例", "engines": ["baidu","bing","sogou","so360","bing_global","duckduckgo","yahoo","brave","shenma","yandex","seznam"], "max_results": 5})
p.observe("11 engines", r, "error", "diagnosis.code")
p.pause(12)
r = p.call("web_search", {"query": "增长 案例", "engines": ["baidu","sogou"],
                          "freshness": "week", "max_results": 5})
p.observe("收窄+周", r, "summary", "engines_used")
p.pause(30, "场景间隔")

# S3 跨会话召回 + 批量读 + 订阅链
p.set_scenario("S3-召回与链")
r = p.call("web_recall", {"query": "内容营销 增长", "limit": 5})
p.observe("recall", r, "summary")
p.rows(r, "results", 5, 130)
p.pause(8)
r = p.call("web_read", {"urls": ["https://example.com/", "https://www.mozilla.org/en-US/about/"], "max_content_chars": 2000})
p.observe("batch", r, "summary")
p.pause(8)
r = p.call("feed_read", {"urls": ["https://hnrss.org/frontpage"], "max_items": 3})
p.observe("hn", r, "summary")
p.rows(r, "feeds", 2, 140)

print("P10 DONE")
