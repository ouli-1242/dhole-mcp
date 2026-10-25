"""P07 湛卢 · 金融研究 L2 —— feed since 增量(E8 真话观察) + 坏源点名(R01-001) + 预算(R01-008)。"""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(7, "湛卢·金融研究")

p.set_scenario("S1-早盘增量(坏源混入,不带 timeout)")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml",
                                  "http://127.0.0.1:8399/no-such-feed.xml"],
                         "max_items": 5})
p.observe("feed", r, "summary")
print("      (回归 R01-001: 顶层数点名坏源 URL)")
print("      (回归 R01-008: 不带 timeout 期望 20s 档,不出现 1s 梯子句)")
p.pause(9)

p.set_scenario("S2-三天以来(口语档位词)")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "since": "3d",
                         "max_items": 5})
p.observe("since-3d", r, "summary", "error")
print("      (E8 三现后真话观察: 3d 收不收)")
p.pause(8)

p.set_scenario("S3-改说 ISO 日期")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"],
                         "since": "2026-10-20", "max_items": 5})
p.observe("since-iso", r, "summary", "undated_count")
p.rows(r, "items", 3, 120)
p.pause(9)

p.set_scenario("S4-交叉验证(急但要用研究档)")
r = p.call("web_research", {"query": "国债收益率 曲线 倒挂 2026 解读", "timeout": 90000})
p.observe("research", r, "summary")
print("P07 DONE")
