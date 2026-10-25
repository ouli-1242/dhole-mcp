"""P01 阎立本 · 数据分析 L2 —— structured 档 + css_selector 对照(含 0 匹配)。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(1, "阎立本·数据分析")
p.call("cred_defaults", {"set": {"allow_private": True}})
p.set_scenario("S1-structured 档抓卡片页")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/collections", "extraction_type": "structured",
                        "timeout": 30000})
p.observe("structured", r, "content_ok", "total_extracted_chars", "summary")
print("   content head:", str(r.get("content"))[:220])
p.pause(8)
p.set_scenario("S2-css_selector 缩取(.card 标题)")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/collections?s=1", "cache_ttl": 0,
                        "css_selector": ".card a", "timeout": 30000})
p.observe("selector-hits", r, "content_ok", "total_extracted_chars", "summary")
p.pause(7)
p.set_scenario("S3-css_selector 0 匹配")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/collections?s=2", "cache_ttl": 0,
                        "css_selector": ".no-such-class-xyz", "timeout": 30000})
p.observe("selector-miss", r, "content_ok", "error", "summary")
print("P01 DONE")
