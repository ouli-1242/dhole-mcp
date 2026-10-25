"""P05 云苏 · 全栈 L2 —— web_read 参数断言逐条点验。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(5, "云苏·全栈")
p.call("cred_defaults", {"set": {"allow_private": True}})
p.set_scenario("S1-urls 上限 10")
r = p.call("web_read", {"urls": [f"http://127.0.0.1:8399/work/{i}" for i in range(1, 12)]})
p.observe("urls-11", r, "error")
p.pause(7)
p.set_scenario("S2-max_content_chars 边界 499")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/longaside?mb=1", "max_content_chars": 499, "cache_ttl": 0})
p.observe("cap-499", r, "error", "summary")
p.pause(7)
p.set_scenario("S3-extraction_type 非法值")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article", "extraction_type": "pdf"})
p.observe("bad-type", r, "error")
print("P05 DONE")
