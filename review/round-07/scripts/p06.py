"""P06 慕容雪 · 自媒体 L1 —— 工具清单对照。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(6, "慕容雪·自媒体")
p.set_scenario("S1-start 清单")
r = p.call("sys_guide", {"topic": "start"})
import json
s = json.dumps(r, ensure_ascii=False)
print("   13 tools 在:", "13 tools" in s, "| eleven/ten 残留:", ("eleven" in s or "ten tools" in s))
p.pause(6)
p.set_scenario("S2-浅用两颗")
p.call("cred_defaults", {"set": {"allow_private": True}})
r = p.call("web_search", {"query": "短视频 运营 技巧", "max_results": 3})
p.observe("search", r, "summary")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article", "timeout": 30000})
p.observe("read", r, "content_ok")
r = p.call("web_recall", {"query": "短视频"})
p.rows(r, "results", 2, 100)
print("P06 DONE")
