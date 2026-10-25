"""P2 修复的真机复验（2026-10-25）：登录 POST 的 Set-Cookie 落 jar。

POST /login 带 session_id（302 + Set-Cookie）→ 回执应报 session_id 与
session_cookie_names；随后同 id 的 web_read /account 应拿到已登录页
（「欢迎回来」）。
"""
import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:9201/mcp"
c = httpx.Client(trust_env=False, timeout=120.0)
sid = None
iid = 0


def rpc(method, params=None):
    global iid, sid
    iid += 1
    p = {"jsonrpc": "2.0", "method": method, "id": iid}
    if params is not None:
        p["params"] = params
    h = {"Accept": "application/json, text/event-stream"}
    if sid:
        h["Mcp-Session-Id"] = sid
    r = c.post(BASE, json=p, headers=h)
    low = {k.lower(): v for k, v in r.headers.items()}
    if low.get("mcp-session-id"):
        sid = low["mcp-session-id"]
    body = r.text
    if "text/event-stream" in low.get("content-type", ""):
        data = None
        for line in body.splitlines():
            if line.startswith("data:"):
                data = line[5:].strip()
        body = data or "{}"
    resp = json.loads(body or "{}")
    result = resp.get("result") or {}
    texts = [x.get("text", "") for x in (result.get("content") or [])
             if isinstance(x, dict)]
    return json.loads(texts[0]) if texts else {}


def call(tool, args):
    r = rpc("tools/call", {"name": tool, "arguments": args})
    print(f"· {tool}: ok={r.get('content_ok')} err={str(r.get('error'))[:90]}")
    return r


rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                   "clientInfo": {"name": "probe-p2", "version": "0"}})
c.post(BASE, json={"jsonrpc": "2.0", "method": "notifications/initialized"},
       headers={"Accept": "application/json, text/event-stream"})

call("cred_defaults", {"set": {"allow_private": True}})

r = call("web_request", {"url": "http://127.0.0.1:8399/login",
                         "method": "POST",
                         "body": {"username": "sim-user",
                                  "password": "not-a-real-secret"},
                         "confirm": True, "session_id": "sim-live",
                         "include_headers": True})
meta = r.get("metadata") or {}
print("  status:", r.get("status"),
      "| session_id:", r.get("session_id"),
      "| cookie names:", r.get("session_cookie_names"),
      "| redirect:", json.dumps(meta.get("redirect"), ensure_ascii=False)[:120])
time.sleep(3)
r = call("web_read", {"url": "http://127.0.0.1:8399/account",
                      "session_id": "sim-live"})
text = (r.get("content") or [""])[0]
print("  /account with same session:", r.get("content_ok"),
      "chars:", r.get("total_extracted_chars"))
print("  text:", text[:120].replace("\n", " / "))
print("  VERDICT:", "PASS" if ("欢迎" in text or "account" in text.lower()
                               or "欢迎回来" in text) and "还没有登录" not in text
      else "FAIL")
