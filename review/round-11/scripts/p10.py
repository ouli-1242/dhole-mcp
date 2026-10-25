"""P10 越千帆5b · 手语翻译 L2 —— doc_parse/feed_read 边界（clamp 家产跨工具一致性）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(10, "越千帆5b·手语翻译")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-doc_parse 路径不存在")
r = p.call("doc_parse", {"file_path": "Z:/no/such/file.csv"})
print(f"   err: {str(r.get('error'))[:160]}")

p.pause(5)
p.set_scenario("S2-doc_parse 目录")
r = p.call("doc_parse", {"file_path": "Z:"})
print(f"   err: {str(r.get('error'))[:160]}")

p.pause(5)
p.set_scenario("S3-feed limit 越界(R09-002 家产:具名 clamp 同款?)")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "limit": 99})
print(f"   err: {str(r.get('error'))[:120] or '(收下)'} | clamped: {r.get('clamped')} | summary: {str(r.get('summary'))[:110]}")

p.pause(5)
p.set_scenario("S4-feed max_items 负数")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": -3})
print(f"   err: {str(r.get('error'))[:120] or '(收下)'} | clamped: {r.get('clamped')}")
print("P10 DONE")
