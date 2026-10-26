"""P03 米粒 · 设计师 L1 —— 整页截图/元素截图落盘 · 同路径覆盖 · PNG 头。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

F = Path(__file__).resolve().parent.parent / "sessions" / "files"
F.mkdir(parents=True, exist_ok=True)
full = F / "shot.png"
crop = F / "crop.png"
for f in (full, crop):
    f.unlink(missing_ok=True)

p = PersonaSim(3, "米粒·设计师")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-整页截图落盘")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/article?cb=p03shot",
                       "timeout": 45000,
                       "actions": [{"screenshot": {"save_to": str(full), "full_page": True}}]})
acts = (r.get("metadata") or {}).get("actions") or []
a = acts[0] if acts else {}
print(f"   ok={a.get('ok')} saved={a.get('saved')} bytes={a.get('saved_bytes')} "
      f"png_magic={full.read_bytes()[:8] == b'\x89PNG\\r\\n\\x1a\\n' if full.exists() else None}")

p.pause(3)
p.set_scenario("S2-同路径再截（覆盖语义）")
mtime0 = full.stat().st_mtime if full.exists() else 0
r = p.call("web_act", {"url": "http://127.0.0.1:8399/article?cb=p03shot2",
                       "timeout": 45000,
                       "actions": [{"screenshot": {"save_to": str(full), "full_page": True}}]})
acts = (r.get("metadata") or {}).get("actions") or []
a = acts[0] if acts else {}
print(f"   ok={a.get('ok')} err={str(a.get('error'))[:100]} "
      f"rewritten={full.stat().st_mtime != mtime0 if full.exists() else None}")

p.pause(3)
p.set_scenario("S3-元素截图（h1）")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/article?cb=p03crop",
                       "timeout": 45000,
                       "actions": [{"screenshot": {"selector": "h1", "save_to": str(crop)}}]})
acts = (r.get("metadata") or {}).get("actions") or []
a = acts[0] if acts else {}
print(f"   ok={a.get('ok')} bytes={a.get('saved_bytes')} exists={crop.exists()}")
print("P03 DONE")
