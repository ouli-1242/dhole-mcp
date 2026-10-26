"""P02 满仓 · 数据工程师 L3 —— 各 max_* 负数/0/浮点/巨大值/字符串；clamp 与拒绝是否具名。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

p = PersonaSim(2, "满仓·数据工程师")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-max_content_chars 负数")
r = p.call("web_read", {"url": ROOT + "/article?cb=p02a", "max_content_chars": -50})
print("   err:", str(r.get("error"))[:180], "| chars:", r.get("total_extracted_chars"))

p.pause(2)
p.set_scenario("S2-max_content_chars 零")
r = p.call("web_read", {"url": ROOT + "/article?cb=p02b", "max_content_chars": 0})
print("   err:", str(r.get("error"))[:180], "| chars:", r.get("total_extracted_chars"))

p.pause(2)
p.set_scenario("S3-max_content_chars 浮点")
r = p.call("web_read", {"url": ROOT + "/article?cb=p02c", "max_content_chars": 3.5})
print("   err:", str(r.get("error"))[:180], "| chars:", r.get("total_extracted_chars"))

p.pause(2)
p.set_scenario("S4-max_content_chars 十亿")
r = p.call("web_read", {"url": ROOT + "/article?cb=p02d", "max_content_chars": 10**9})
print("   err:", str(r.get("error"))[:160], "| chars:", r.get("total_extracted_chars"))

p.pause(3)
p.set_scenario("S5-crawl max_pages 负数")
r = p.call("web_crawl", {"url": ROOT + "/collections?cb=p02", "max_pages": -3,
                         "max_total_chars": 2000, "timeout": 30000})
print("   err:", str(r.get("error"))[:220], "| pages:", r.get("pages_fetched"))

p.pause(3)
p.set_scenario("S6-discover max_pages 字符串")
r = p.call("web_discover", {"url": ROOT + "/nav?cb=p02", "max_pages": "5",
                            "timeout": 30000})
print("   err:", str(r.get("error"))[:220], "| urls:", len(r.get("urls") or []))

p.pause(3)
p.set_scenario("S7-feed max_items 浮点")
r = p.call("feed_read", {"urls": [ROOT + "/feed.xml?cb=p02"], "max_items": 2.5})
print("   err:", str(r.get("error"))[:200], "| items:", len(r.get("items") or []))

p.pause(2)
p.set_scenario("S8-收尾正常读")
r = p.call("web_read", {"url": ROOT + "/article?cb=p02e", "max_content_chars": 800})
print("   ok:", r.get("content_ok"), "chars:", r.get("total_extracted_chars"))
