"""P01 周岚 · 研究员 L2 —— 中文名保存正文 · 覆盖门 · 字节哈希核对。"""
import hashlib
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

F = Path(__file__).resolve().parent.parent / "sessions" / "files"
F.mkdir(parents=True, exist_ok=True)
target = F / "研究笔记.md"
target.unlink(missing_ok=True)

p = PersonaSim(1, "周岚·研究员")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-中文搜索")
r = p.call("web_search", {"query": "网页 归档 保存 工具", "max_results": 2})
print(f"   {str(r.get('summary'))[:140]}")

p.pause(4)
p.set_scenario("S2-保存正文到中文文件名")
u = "http://127.0.0.1:8399/article?cb=p01save"
r = p.call("web_read", {"url": u, "save_to": str(target)})
meta = r.get("metadata") or {}
disk = target.read_bytes() if target.exists() else b""
sha = hashlib.sha256(disk).hexdigest()
print(f"   saved_to={meta.get('saved_to')} bytes={meta.get('bytes')} disk={len(disk)}")
print(f"   sha_match={sha == meta.get('sha256')} content={''.join(str(x) for x in (r.get('content') or []))[:70]!r}")

p.pause(3)
p.set_scenario("S3-同路径二次保存（覆盖门）")
r = p.call("web_read", {"url": u + "&again=1", "save_to": str(target)})
print(f"   err={str(r.get('error'))[:150]}")
print(f"   next={str(r.get('next_action'))[:120]}")

p.pause(3)
p.set_scenario("S4-换名再存（应成功）")
t2 = F / "研究笔记-副本.md"
t2.unlink(missing_ok=True)
r = p.call("web_read", {"url": u + "&again=2", "save_to": str(t2)})
print(f"   ok={r.get('content_ok')} exists={t2.exists()} bytes={(t2.stat().st_size if t2.exists() else None)}")
print("P01 DONE")
