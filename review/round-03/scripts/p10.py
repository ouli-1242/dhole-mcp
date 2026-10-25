"""P10 荀羽 · 游戏攻略作者 L1 —— 特殊页面(E9 新区) + 跨会话二访 + 真实放弃点。"""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(10, "荀羽·攻略作者")

p.set_scenario("S1-搜攻略")
r = p.call("web_search", {"query": "鸣潮 2.3 版本 深渊 阵容 攻略", "max_results": 5})
p.observe("search", r, "summary")
p.rows(r, "results", 3, 120)
p.pause(8)

p.set_scenario("S2-点进去(特殊页面:签名链/视频)")
r = p.call("web_read", {"url": "https://www.miyoushe.com/ys/", "timeout": 30000})
p.observe("miyoushe", r, "content_ok", "error", "summary", "page_type")
p.pause(7)
r = p.call("web_read", {"url": "https://www.bilibili.com/v/game/esports/", "timeout": 30000})
p.observe("bilibili", r, "content_ok", "error", "summary", "page_type")
print("      (E9 新区: 特殊页面给不给类型徽标)")
p.pause(8)

p.set_scenario("S3-换文字版(存下来)")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article", "timeout": 30000})
p.observe("wiki", r, "content_ok", "summary")
p.pause(8)

p.set_scenario("S4-算了+明天再说(跨会话)")
pb = PersonaSim(10, "荀羽·明天")  # 新会话
r = pb.call("web_recall", {"query": "深渊 阵容"})
p.observe("recall-tomorrow", r, "summary")
p.rows(r, "results", 4, 130)
print("P10 DONE")
