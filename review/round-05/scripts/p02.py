"""P02 梅疏影 · 编辑 L1 —— 冷门词搜索降级池。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(2, "梅疏影·编辑")
p.set_scenario("S1-冷门词")
r = p.call("web_search", {"query": "掣襟肘见 考辨 音义", "max_results": 5})
p.observe("cold", r, "summary")
p.rows(r, "results", 2, 100)
p.pause(8)
p.set_scenario("S2-换通俗词")
r = p.call("web_search", {"query": "成语 管中窥豹 意思", "max_results": 5})
p.observe("common", r, "summary")
p.rows(r, "results", 2, 100)
p.pause(6)
p.set_scenario("S3-求助")
r = p.call("sys_guide", {"topic": "start"})
p.observe("guide", r, "summary")
print("P02 DONE")
