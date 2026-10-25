"""P05 苏姗 · 外贸业务员 · L2 —— round-02 真实模拟（feed 并发/跨语种/刀难二连）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(5, "苏姗·外贸")

p.set_scenario("S1-三源 feed（回归#1+#8）")
r = p.call("feed_read", {"urls": ["https://hnrss.org/frontpage",
                                  "http://127.0.0.1:8399/feed.xml",
                                  "https://www.ruanyifeng.com/blog/atom.xml"],
                         "max_items": 3})
p.observe("feeds", r, "summary")
for f in (r.get("feeds") or []):
    print("   -", f.get("source_url"), "| err:", (f.get("error") or "")[:60], "| items:", len(f.get("items") or []), "| note:", (f.get("note") or "")[:80])
p.pause(15)

p.set_scenario("S2-英文词找中文站")
r = p.call("web_search", {"query": "LED strip light supplier Shenzhen certification", "max_results": 5})
p.observe("search", r, "summary")
p.rows(r, "results", 3, 120)
p.pause(12)

p.set_scenario("S3-日文站阅读")
r = p.call("web_read", {"url": "https://www.meti.go.jp/english/press/index.html", "max_content_chars": 2000})
p.observe("jp", r, "content_ok", "summary")
p.pause(10)

p.set_scenario("S4-既要又要二连")
r = p.call("feed_read", {"urls": ["https://hnrss.org/frontpage"], "since": "3d"})
p.observe("since-3d", r, "summary", "diagnosis.code")
p.pause(6)
r = p.call("web_search", {"query": "LED certification", "max_results": 3,
                          "engines": ["baidu", "bing", "sogou", "so360", "bing_global", "duckduckgo", "yahoo", "brave", "shenma", "yandex", "seznam"]})
p.observe("11-engines", r, "summary", "families_answered_of_asked")

print("P05 DONE")
