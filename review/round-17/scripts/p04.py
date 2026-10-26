"""P04 沈括 · 档案员 L2 —— max_save_bytes 小帽/正常帽 · recall。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

F = Path(__file__).resolve().parent.parent / "sessions" / "files"
small = F / "cap50.md"
big = F / "cap10k.md"
for f in (small, big):
    f.unlink(missing_ok=True)

p = PersonaSim(4, "沈括·档案员")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-50 字节帽（应拒）")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article?cb=p04cap",
                        "save_to": str(small), "max_save_bytes": 50})
print(f"   ok={r.get('content_ok')} err={str(r.get('error'))[:190]}")
print(f"   wrote={small.exists()}")

p.pause(3)
p.set_scenario("S2-10KB 帽（应成）")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article?cb=p04cap2",
                        "save_to": str(big), "max_save_bytes": 10000})
meta = r.get("metadata") or {}
print(f"   ok={r.get('content_ok')} bytes={meta.get('bytes')} on_disk={big.stat().st_size if big.exists() else None}")

p.pause(4)
p.set_scenario("S3-recall 找回归档页")
r = p.call("web_recall", {"query": "归档 保存 笔记", "limit": 4})
print(f"   results={len(r.get('results') or [])} terms={str(r.get('terms_found'))[:90]}")
print("P04 DONE")
