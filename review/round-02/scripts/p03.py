"""P03 小北 · 17 岁学生创作者 · L1 —— round-02 真实模拟（错字/被挡/求助）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(3, "小北·学生")

p.set_scenario("S1-错字搜索")
r = p.call("web_search", {"query": "原神 4.8 版本更新 内容 一览", "max_results": 5})
p.observe("search", r, "summary", "engines_used")
p.rows(r, "results", 3, 120)
p.pause(9)

p.set_scenario("S2-点进去")
r = p.call("web_read", {"url": "https://www.miyoushe.com/ys/", "timeout": 30000})
p.observe("read", r, "error", "content_ok", "summary", "diagnosis.code")
p.pause(8)
r = p.call("web_read", {"url": "https://www.bilibili.com/v/game/esports/", "timeout": 30000})
p.observe("read2", r, "error", "content_ok", "summary", "diagnosis.code", "page_type")
p.pause(10)

p.set_scenario("S3-求助路标")
r = p.call("sys_guide", {"topic": "start"})
p.observe("guide", r, "summary")
p.pause(8)

p.set_scenario("S4-变卦换词")
r = p.call("web_search", {"query": "原神 5.0 新地图 爆料", "max_results": 5})
p.observe("search2", r, "summary")
p.rows(r, "results", 3, 120)

print("P03 DONE")
