"""P03 蒲国栋 · 咨询顾问 L2 —— 批量读(含坏 URL) + 长文 offset + 显式 timeout 回归(R02-004)。"""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(3, "蒲国栋·咨询")

p.set_scenario("S1-竞品批量(3个坏路径混入)")
urls = [f"http://127.0.0.1:8399/work/{i}" for i in range(1, 8)] + [
    "http://127.0.0.1:8399/nope-a", "http://127.0.0.1:8399/nope-b"]
r = p.call("web_read", {"urls": urls, "timeout": 45000})
p.observe("batch", r, "summary")
p.rows(r, "results", 10, 110)
p.pause(9)

p.set_scenario("S2-长报告只读后半(offset)")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/longaside", "max_content_chars": 1200,
                        "timeout": 30000})
p.observe("long-1", r, "summary", "total_extracted_chars", "next_offset")
p.pause(6)
r = p.call("web_read", {"url": "http://127.0.0.1:8399/longaside", "max_content_chars": 1200,
                        "offset": (r.get("next_offset") or 0), "timeout": 30000})
p.observe("long-2", r, "summary", "total_extracted_chars")
p.pause(8)

p.set_scenario("S3-给慢点的时间(显式 timeout)")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/longaside?explicit=1",
                        "timeout": 60000, "cache_ttl": 0})
p.observe("explicit-60s", r, "summary")
print("      (期望: summary 不出现 learned ceiling 5000ms 压制句 —— R02-004 回归)")
p.pause(8)

p.set_scenario("S4-交叉验证一把")
r = p.call("web_research", {"query": "SaaS 定价页 最佳实践 2026", "timeout": 60000})
p.observe("research", r, "summary")
print("P03 DONE")
