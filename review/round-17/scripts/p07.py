"""P07 林一 · 教师 L1 —— 父目录不存在的两种语义 · 手册。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

F = Path(__file__).resolve().parent.parent / "sessions" / "files"
missing_read = F / "not-yet-r17" / "note.md"
missing_shot = F / "not-yet-r17b" / "shot.png"
for d in (missing_read.parent, missing_shot.parent):
    if d.exists():
        for f in d.iterdir():
            f.unlink()
        d.rmdir()

p = PersonaSim(7, "林一·教师")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-web_read 存到不存在的父目录")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article?cb=p07dir",
                        "save_to": str(missing_read)})
print(f"   err={str(r.get('error'))[:170]}")
print(f"   next={str(r.get('next_action'))[:140]}")

p.pause(3)
p.set_scenario("S2-截图存到不存在的父目录")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/article?cb=p07shot",
                       "timeout": 45000,
                       "actions": [{"screenshot": {"save_to": str(missing_shot),
                                                   "full_page": True}}]})
acts = (r.get("metadata") or {}).get("actions") or []
a = acts[0] if acts else {}
print(f"   ok={a.get('ok')} err={str(a.get('error'))[:110]} "
      f"created_parent={missing_shot.parent.exists()} file={missing_shot.exists()}")

p.pause(3)
p.set_scenario("S3-查调用手册")
r = p.call("sys_guide", {"topic": "call"})
raw = str(r.get("content") or r.get("_raw") or "")
print(f"   len={len(raw)} head={raw[:80]!r}")
print("P07 DONE")
