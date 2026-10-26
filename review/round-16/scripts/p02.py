"""P02 莱拉 · 阿拉伯语译者 L2 —— RTL 正文 · 阿语 URL 参数 · 阿语登录名。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(2, "莱拉·阿拉伯语译者")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-RTL 正文保真")
U = "http://127.0.0.1:8398/ar.html?cb=عربي"
r = p.call("web_read", {"url": U})
txt = "".join(str(x) for x in (r.get("content") or []))
print(f"   ok={r.get('content_ok')} chars={r.get('total_extracted_chars')} "
      f"has_arabic={any('\u0600' <= ch <= '\u06ff' for ch in txt)}")
print(f"   head={txt[:90]!r}")

p.pause(3)
p.set_scenario("S2-同页复读（阿语缓存键）")
r = p.call("web_read", {"url": U})
print(f"   cached={r.get('cached')} same_chars={r.get('total_extracted_chars')}")

p.pause(4)
p.set_scenario("S3-阿语用户名登录 + 会话读")
r = p.call("web_request", {"url": "http://127.0.0.1:8399/login", "method": "POST",
                           "body": {"username": "ليلى", "password": "pw"},
                           "session_id": "p02ar", "confirm": True})
print(f"   login status={r.get('status')} err={str(r.get('error'))[:80]}")
a = p.call("web_read", {"url": "http://127.0.0.1:8399/account", "session_id": "p02ar"})
print(f"   account ok={a.get('content_ok')} chars={a.get('total_extracted_chars')} "
      f"head={''.join(str(x) for x in (a.get('content') or []))[:50]!r}")

p.pause(3)
p.set_scenario("S4-state")
r = p.call("sys_state", {})
print(f"   message: {str(r.get('message'))[:140]}")
print("P02 DONE")
