"""P09 聂怀古 · 文物修复师 L3 猎手 —— 链条⑨第一拒链上中环抽样：断点具名、链可续。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(9, "聂怀古·文物修复师")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-链首 read 成功")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/"})
print(f"   status={r.get('status')}（链首应为 200）")

p.set_scenario("S2-中环拒：request GET")
r = p.call("web_request", {"url": "http://127.0.0.1:8399/", "method": "GET"})
print(f"   err: {str(r.get('error'))[:130]}")

p.set_scenario("S3-中环拒：read 0 匹配 selector")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article", "css_selector": "#no-such"})
print(f"   err: {str(r.get('error'))[:130]}")

p.set_scenario("S4-中环拒：search max_results=0")
r = p.call("web_search", {"query": "dhole", "max_results": 0})
print(f"   err: {str(r.get('error'))[:130]}")

p.set_scenario("S5-中环拒：research max_sources=0")
r = p.call("web_research", {"query": "dhole mcp", "max_sources": 0})
print(f"   err: {str(r.get('error'))[:130]}")

p.set_scenario("S6-中环拒：crawl max_pages=0")
r = p.call("web_crawl", {"url": "http://127.0.0.1:8399/", "max_pages": 0})
print(f"   err: {str(r.get('error'))[:130]}")

p.set_scenario("S7-中环拒：doc_parse 不存在")
r = p.call("doc_parse", {"file_path": "Z:/no/such/file.csv"})
print(f"   err: {str(r.get('error'))[:130]}")

p.set_scenario("S8-中环拒：act 未知 action")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/", "action": "fly"})
print(f"   err: {str(r.get('error'))[:130]}")

p.set_scenario("S9-中环拒：discover depth=-1")
r = p.call("web_discover", {"url": "http://127.0.0.1:8399/collections?items=2", "depth": -1})
print(f"   err: {str(r.get('error'))[:130]} | summary: {str(r.get('summary'))[:80]}")

p.set_scenario("S10-中环拒：recall 空查询")
r = p.call("web_recall", {"query": "   "})
print(f"   err: {str(r.get('error'))[:130]}")

p.set_scenario("S11-中环拒：feed 错参数名")
r = p.call("feed_read", {"feeds": ["http://127.0.0.1:8399/feed.xml"]})
print(f"   err: {str(r.get('error'))[:130]}")

p.set_scenario("S12-断后续链：read 成功")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/work/1"})
print(f"   status={r.get('status')}（断后链可续=200）")
print("P09 DONE")
