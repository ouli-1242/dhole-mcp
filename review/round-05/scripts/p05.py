"""P05 亢明玉 · 采购 L2 —— force_fetcher 非法值 + 未知参数名。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(5, "亢明玉·采购")
p.set_scenario("S1-非法 force_fetcher")
p.call("cred_defaults", {"set": {"allow_private": True}})
r = p.call("web_read", {"url": "http://127.0.0.1:8399/collections", "force_fetcher": "magic"})
p.observe("bad-fetcher", r, "error")
p.pause(7)
p.set_scenario("S2-未知参数名")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/collections?u=1", "cache_ttl": 0,
                        "magic_param": True})
p.observe("unknown-param", r, "error", "summary")
p.pause(7)
p.set_scenario("S3-stealthy 正形")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/collections?u=2", "cache_ttl": 0,
                        "force_fetcher": "stealthy", "timeout": 45000})
p.observe("stealthy-ok", r, "content_ok", "summary")
print("P05 DONE")
