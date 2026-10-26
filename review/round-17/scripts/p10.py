"""P10 艾米 · QA L3 —— save_to 边界（目录/空/任意后缀）· state · guide。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

F = Path(__file__).resolve().parent.parent / "sessions" / "files"
raw = F / "raw17.html"
raw.unlink(missing_ok=True)

p = PersonaSim(10, "艾米·QA")
p.call("cred_defaults", {"set": {"allow_private": True}})
U = "http://127.0.0.1:8399/article?cb=p10edge"

p.set_scenario("S1-save_to 指向目录")
r = p.call("web_read", {"url": U, "save_to": str(F)})
print(f"   err={str(r.get('error'))[:150]}")

p.pause(3)
p.set_scenario("S2-save_to 为空串")
r = p.call("web_read", {"url": U, "save_to": ""})
print(f"   ok={r.get('content_ok')} content={''.join(str(x) for x in (r.get('content') or []))[:70]!r}")

p.pause(3)
p.set_scenario("S3-任意后缀 .html 保存原始字节")
r = p.call("web_read", {"url": U, "save_to": str(raw)})
meta = r.get("metadata") or {}
print(f"   bytes={meta.get('bytes')} disk={(raw.stat().st_size if raw.exists() else None)} "
      f"looks_html={raw.read_bytes()[:15].lower().startswith(b'<!doctype') if raw.exists() else None}")

p.pause(3)
p.set_scenario("S4-state 与手册")
r = p.call("sys_state", {})
print(f"   state: {str(r.get('message'))[:120]}")
r = p.call("sys_guide", {"topic": "start"})
print(f"   guide_len={len(str(r.get('content') or r.get('_raw') or ''))}")
print("P10 DONE")
