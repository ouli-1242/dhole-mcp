"""P09 罗薇 · 图书管理员 L2 —— doc_parse 路径形状 / feed_read 非 feed 与类型错 / recall 空白词。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

F = Path(__file__).resolve().parent.parent / "sessions" / "files"
F.mkdir(parents=True, exist_ok=True)
png = F / "not-a-doc.png"
png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)

p = PersonaSim(9, "罗薇·图书管理员")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-doc_parse 不存在的文件")
r = p.call("doc_parse", {"file_path": str(F / "nope.md")})
print("   err:", str(r.get("error"))[:220])

p.pause(2)
p.set_scenario("S2-doc_parse 目录")
r = p.call("doc_parse", {"file_path": str(F)})
print("   err:", str(r.get("error"))[:220])

p.pause(2)
p.set_scenario("S3-doc_parse 传 URL")
r = p.call("doc_parse", {"file_path": ROOT + "/article"})
print("   err:", str(r.get("error"))[:240])

p.pause(2)
p.set_scenario("S4-doc_parse 二进制后缀")
r = p.call("doc_parse", {"file_path": str(png)})
print("   err:", str(r.get("error"))[:200], "| summary:", str(r.get("summary"))[:120])

p.pause(3)
p.set_scenario("S5-feed_read 非 feed 页")
r = p.call("feed_read", {"urls": [ROOT + "/article?cb=p09"], "max_items": 2})
print("   err:", str(r.get("error"))[:200], "| items:", len(r.get("items") or []))
print("   summary:", str(r.get("summary"))[:180])

p.pause(2)
p.set_scenario("S6-feed_read urls 是字符串")
r = p.call("feed_read", {"urls": ROOT + "/feed.xml", "max_items": 2})
print("   err:", str(r.get("error"))[:220])

p.pause(2)
p.set_scenario("S7-recall 空白查询")
r = p.call("web_recall", {"query": "   ", "limit": 2})
print("   err:", str(r.get("error"))[:220])
