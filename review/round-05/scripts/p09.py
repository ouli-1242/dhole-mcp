"""P09 宿雨 · 学术编辑 L1 —— 三层全空手的最终话术。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(9, "宿雨·学术编辑")
p.set_scenario("S1-编造 DOI 的落地页")
p.call("cred_defaults", {"set": {"allow_private": True}})
r = p.call("web_read", {"url": "https://doi.org/10.9999/fake.2026.99999", "timeout": 45000})
p.observe("fake-doi", r, "error", "summary")
p.pause(8)
p.set_scenario("S2-check 档再试")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/no-such-page", "extraction_type": "check",
                        "timeout": 30000})
p.observe("check-404", r, "error", "summary")
p.pause(7)
p.set_scenario("S3-recall 找旧存档")
r = p.call("web_recall", {"query": "参考文献 核验"})
p.rows(r, "results", 3, 110)
print("P09 DONE")
