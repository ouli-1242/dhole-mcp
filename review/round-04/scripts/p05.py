"""P05 佟既明 · 游戏策划 L2 —— 表格数字与页面数据对账（故意差一格）。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
HERE = Path(__file__).resolve().parent.parent
p = PersonaSim(5, "佟既明·策划")
p.set_scenario("S1-读配表(多sheet)")
r = p.call("doc_parse", {"file_path": str(HERE / "sessions" / "files" / "区域报表.xlsx")})
p.observe("xlsx", r, "total_extracted_chars")
c = r.get("content")
c = ''.join(c) if isinstance(c, list) else str(c)
print("   上海行:", [l for l in c.splitlines() if "上海" in l][:1])
p.pause(7)
p.set_scenario("S2-抓线上数据源")
p.call("cred_defaults", {"set": {"allow_private": True}})
r = p.call("web_read", {"url": "http://127.0.0.1:8399/work/1", "timeout": 30000})
p.observe("work1", r, "content_ok", "total_extracted_chars")
c = r.get("content"); c = ''.join(c) if isinstance(c, list) else str(c)
print("   work1 正文头:", c[:120].replace("\n", " "))
p.pause(7)
p.set_scenario("S3-交叉验证(外部)")
r = p.call("web_research", {"query": "游戏数值策划 配表 校验 工具流", "timeout": 60000})
p.observe("research", r, "summary")
print("P05 DONE")
