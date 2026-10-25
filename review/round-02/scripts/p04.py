"""P04 覃川 · 数据工程师 · L3 猎手 —— round-02 真实模拟（nested/批量/offset/resume）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(4, "覃川·数据工程")

p.set_scenario("S1-nested 列表+详情")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/collections",
                        "nested_selector": ".work-card a",
                        "nested_fields": {"title": "text", "href": "href"}, "cache_ttl": 0})
p.observe("nested", r, "summary", "content_ok")
p.rows(r, "results", 4, 120)
p.pause(8)

p.set_scenario("S2-resume 照抄")
r = p.call("web_crawl", {"url": "http://127.0.0.1:8399/work/1", "max_pages": 2, "cache_ttl": 0})
p.observe("crawl", r, "summary", "next_resume_token")
na = r.get("next_action") or ""
print("      next_action:", na[:220])
# 照抄自荐形状：从 next_action 里抄 resume_token 与 url（回归#4——本轮句子自带 url）
import re
m = re.search(r"resume_token='([^']+)'", na)
mu = re.search(r"url='([^']+)'", na)
if m:
    args = {"cache_ttl": 0}
    if mu:
        args["url"] = mu.group(1)
    args["resume_token"] = m.group(1)
    print("      照抄参数:", {k: args[k] for k in ("url", "resume_token") if k in args})
    r = p.call("web_crawl", args)
    p.observe("resume", r, "summary")
else:
    print("      （next_action 没有自荐 resume——如实记录）")
p.pause(10)

p.set_scenario("S3-批量 12 + 超额")
urls = [f"http://127.0.0.1:8399/work/{i}" for i in range(1, 13)]
r = p.call("web_read", {"urls": urls, "max_content_chars": 300, "cache_ttl": 0})
p.observe("batch12", r, "summary")
res = r.get("results") or []
print("      行键:", list(res[0].keys())[:8] if res else "无行")
p.pause(8)
r = p.call("web_read", {"urls": urls[:11] + ["http://127.0.0.1:8399/work/13"] + urls[11:],
                        "max_content_chars": 300, "cache_ttl": 0})
p.observe("batch13", r, "summary", "error")
p.pause(8)

p.set_scenario("S4-offset 三连对账")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/longaside", "max_content_chars": 800, "cache_ttl": 0})
p.observe("chunk1", r, "summary", "next_offset", "total_extracted_chars")
off = r.get("next_offset")
if off:
    r2 = p.call("web_read", {"url": "http://127.0.0.1:8399/longaside", "max_content_chars": 800,
                             "offset": off, "cache_ttl": 0})
    p.observe("chunk2", r2, "summary", "next_offset")
    print("      chunk2 head:", ((r2.get('content') or [''])[0])[:80].replace(chr(10), ' '))

print("P04 DONE")
