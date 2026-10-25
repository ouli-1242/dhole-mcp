"""P09 陶知遥 · 研究助理 L2 —— 证据包引用回链 + 隔离。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(9, "陶知遥·研究助理")
p.set_scenario("S1-证据包")
r = p.call("web_research", {"query": "RAG 检索增强 幻觉 缓解 2026", "timeout": 90000})
p.observe("research", r, "summary")
rows = r.get("sources") or r.get("rows") or []
for s in rows[:4]:
    print("   src:", str(s.get("url"))[:60], "| ok:", s.get("content_ok", s.get("ok")))
p.pause(8)
p.set_scenario("S2-recall 查准")
p.call("cred_defaults", {"set": {"allow_private": True}})
p.call("web_read", {"url": "http://127.0.0.1:8399/longaside", "timeout": 30000})
r = p.call("web_recall", {"query": "抓取器 两层 升级"})
p.rows(r, "results", 3, 110)
print("P09 DONE")
