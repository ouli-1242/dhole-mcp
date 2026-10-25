"""P06 黎照4 · 气象员 L2 —— web_request 深水（redirect/headers/body 形）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(6, "黎照4·气象员")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-GET 语义门(再证)")
r = p.call("web_request", {"url": "http://127.0.0.1:8399/feed.xml", "method": "GET"})
print(f"   err: {str(r.get('error'))[:140]}")

p.pause(6)
p.set_scenario("S2-POST redirect 端点账(写闸 confirm)")
r = p.call("web_request", {"url": "http://127.0.0.1:8399/login", "method": "POST",
                           "body": {"username": "qixiang", "password": "pw"},
                           "session_id": "s6r", "confirm": True})
print(f"   ok: {not r.get('error')} | status: {r.get('status')} | redirect 端点: {r.get('redirected_to') or r.get('final_url') or '(无字段)'}")
print(f"   summary: {str(r.get('summary'))[:140]}")

p.pause(6)
p.set_scenario("S3-include_headers(头披露+Cookie 不泄值)")
r = p.call("web_request", {"url": "http://127.0.0.1:8399/feed.xml", "method": "HEAD",
                           "include_headers": True})
hdrs = r.get("headers") or r.get("response_headers") or {}
ck = {k: v for k, v in (hdrs.items() if isinstance(hdrs, dict) else []) if "cookie" in k.lower()}
print(f"   status: {r.get('status')} | headers 型: {type(hdrs).__name__} | cookie 行: {str(ck)[:80]}")
print(f"   err: {str(r.get('error'))[:80]}")

p.pause(6)
p.set_scenario("S4-content_type 自定义 body")
r = p.call("web_request", {"url": "http://127.0.0.1:8399/login", "method": "POST",
                           "body": {"username": "q2", "password": "pw"},
                           "content_type": "json", "session_id": "s6r", "confirm": True})
print(f"   ok: {not r.get('error')} | status: {r.get('status')} | err: {str(r.get('error'))[:90]}")
print("P06 DONE")
