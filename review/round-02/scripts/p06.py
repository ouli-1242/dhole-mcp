"""P06 老范 · 45 岁建材店老板 · L1 极低耐心 —— round-02 真实模拟。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(6, "老范·建材老板")

p.set_scenario("S1-错字搜索")
r = p.call("web_search", {"query": "P.O42.5 水泥 什么意思", "max_results": 4})
p.observe("search", r, "summary")
p.rows(r, "results", 3, 110)
p.pause(8)

p.set_scenario("S2-读第一条")
r = p.call("web_read", {"url": "https://baike.baidu.com/item/%E6%99%AE%E9%80%9A%E7%A1%85%E9%85%B8%E7%9B%90%E6%B0%B4%E6%B3%A5", "timeout": 25000})
p.observe("read", r, "content_ok", "summary", "diagnosis.code")
p.pause(8)

p.set_scenario("S3-一次失败就放弃")
r = p.call("web_read", {"url": "https://www.specification.cn/standard/detail/404-nope", "timeout": 25000})
p.observe("fail", r, "error", "diagnosis.code", "next_action")
p.pause(6)

p.set_scenario("S4-让 AI 自己概括")
r = p.call("web_read", {"url": "https://zh.wikipedia.org/wiki/%E6%B0%B4%E6%B3%A5", "max_content_chars": 1500})
p.observe("wiki", r, "content_ok", "summary")

print("P06 DONE")
