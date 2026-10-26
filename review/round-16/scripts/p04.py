"""P04 陈默 · 数据录入员 L1 —— GBK/UTF-16 CSV · 编码参数 · 手册。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(4, "陈默·数据录入员")
p.call("cred_defaults", {"set": {"allow_private": True}})
F = Path(__file__).resolve().parent.parent / "sessions" / "files"

p.set_scenario("S1-GBK CSV（缺省）")
r = p.call("doc_parse", {"file_path": str(F / "gbk_table.csv")})
txt = "".join(str(x) for x in (r.get("content") or []))
print(f"   ok={r.get('content_ok')} chars={len(txt)} 北京={'北京' in txt} summary={str(r.get('summary'))[:90]}")

p.pause(3)
p.set_scenario("S2-GBK CSV（encoding=gbk）")
r = p.call("doc_parse", {"file_path": str(F / "gbk_table.csv"), "encoding": "gbk"})
txt = "".join(str(x) for x in (r.get("content") or []))
print(f"   ok={r.get('content_ok')} chars={len(txt)} 上海={'上海' in txt}")

p.pause(3)
p.set_scenario("S3-UTF-16 CSV")
r = p.call("doc_parse", {"file_path": str(F / "utf16_table.csv")})
txt = "".join(str(x) for x in (r.get("content") or []))
print(f"   ok={r.get('content_ok')} chars={len(txt)} 咖啡={'咖啡' in txt}")

p.pause(3)
p.set_scenario("S4-查手册确认字段读法")
r = p.call("sys_guide", {"topic": "fields"})
raw = str(r.get("content") or r.get("_raw") or "")
print(f"   len={len(raw)} head={raw[:100]!r}")
print("P04 DONE")
