"""P02 方卓 · 记者 L3 —— 遍历路径 · 系统目录 · 登录后保存 · research。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

F = Path(__file__).resolve().parent.parent / "sessions" / "files"
escape = F.parent / "escape17.md"
escape.unlink(missing_ok=True)
sys_target = Path("C:/Windows/Temp/r17-should-not-exist.txt")
sys_target.unlink(missing_ok=True)

p = PersonaSim(2, "方卓·记者")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-登录（为保存受保护页）")
r = p.call("web_request", {"url": "http://127.0.0.1:8399/login", "method": "POST",
                           "body": {"username": "fangzhuo", "password": "pw"},
                           "session_id": "p02save", "confirm": True})
print(f"   login status={r.get('status')}")

p.pause(3)
p.set_scenario("S2-相对遍历 ../escape17.md")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/account",
                        "session_id": "p02save", "save_to": str(escape)})
print(f"   ok={r.get('content_ok')} err={str(r.get('error'))[:110]} escaped_exists={escape.exists()}")
if escape.exists():
    escape.unlink()

p.pause(3)
p.set_scenario("S3-系统目录（应拒）")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/account",
                        "session_id": "p02save", "save_to": str(sys_target)})
print(f"   err={str(r.get('error'))[:150]} wrote={sys_target.exists()}")
if sys_target.exists():
    sys_target.unlink()

p.pause(5)
p.set_scenario("S4-research 顺手归档")
r = p.call("web_research", {"query": "web archiving save page bytes", "max_sources": 1,
                            "per_source_chars": 600, "timeout": 90000})
print(f"   sources={len(r.get('sources') or [])} failed={len(r.get('failed') or [])}")
print("P02 DONE")
