"""P01 追加：按报错建议用 ISO 日期重试（真人自然会做的事）。"""
import sys, json
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

p = PersonaSim(1, "陈曦·数据分析")
p.set_scenario("S5-按提示改ISO重试")
p.seq = 8
r = p.call("feed_read", {"urls": [ROOT + "/feed.xml"], "since": "2026-10-22"})
p.observe("feed iso", r, "summary")
p.rows(r, "feeds", 2, 200)
p.pause(6)
r = p.call("feed_read", {"urls": [ROOT + "/feed.xml"], "since": "2026-10-15", "max_items": 3})
p.observe("both caps iso", r, "summary")
p.rows(r, "feeds", 2, 200)
print("P01B DONE")
