"""P06 骆迦叶 · 政务信息公开 L1 —— 慢站长页分段 + 404 死链 + 超纲要求（E10 观察区）。"""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(6, "骆迦叶·政务")

p.set_scenario("S1-公告长页分段取全")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/longaside", "max_content_chars": 1500,
                        "timeout": 60000})
p.observe("page-1", r, "summary", "total_extracted_chars", "next_offset")
p.pause(7)
off = r.get("next_offset") or 0
r = p.call("web_read", {"url": "http://127.0.0.1:8399/longaside", "max_content_chars": 1500,
                        "offset": off, "timeout": 60000})
p.observe("page-2", r, "summary", "total_extracted_chars")
p.pause(8)

p.set_scenario("S2-归档链接其实是死链")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/no-such-page", "timeout": 30000})
p.observe("dead-link", r, "error", "summary", "total_extracted_chars")
print("      (E10 观察: 404 行是否带 total_extracted_chars 数字噪声)")
p.pause(7)

p.set_scenario("S3-超纲:附件也打包(对 AI 说)")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/collections", "timeout": 30000})
p.observe("attach-page", r, "content_ok", "summary")
print("      (超纲要求口头提出: '把页面附件全部下载打包' —— 看工具怎么应)")
p.pause(8)

p.set_scenario("S4-看说明书(L1 求助)")
r = p.call("sys_guide", {"topic": "call"})
p.observe("guide-call", r, "summary")
print("P06 DONE")
