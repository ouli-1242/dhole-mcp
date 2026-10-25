"""P04 补测：/collections max_pages=2 触发 resume 自荐 → 逐字照抄（回归#4）。"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(4, "覃川·数据工程")

p.set_scenario("S2b-resume 照抄 /collections")
r = p.call("web_crawl", {"url": "http://127.0.0.1:8399/collections", "max_pages": 2, "cache_ttl": 0})
na = r.get("next_action") or ""
print("      next_action:", na[:260])
m = re.search(r"resume_token='([^']+)'", na)
mu = re.search(r"web_crawl\(url='([^']+)'" if "web_crawl(url=" in na else r"url='([^']+)'", na)
if m:
    args = {"resume_token": m.group(1), "cache_ttl": 0}
    if mu:
        args["url"] = mu.group(1)
    print("      照抄:", {k: args[k] for k in ("url", "resume_token") if k in args})
    r2 = p.call("web_crawl", args)
    p.observe("resume", r2, "summary")
    print("      resume summary:", (r2.get("summary") or "")[:160])
else:
    print("      无自荐 resume")

print("P04B DONE")
