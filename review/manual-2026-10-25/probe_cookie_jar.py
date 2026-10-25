"""定点补测：写请求的 set-cookie 去了哪（2026-10-25）。

web_request 拒收 session_id（真机 [14]）。这里验证：不带 session_id 的
POST /login 成功后，set-cookie 落在共享 jar 里吗？后续不带 session 的
web_read /account 能读到登录态吗？——决定缺口怎么记。
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
                   "clientInfo": {"name": "probe2", "version": "0"}})
c.post(BASE, json={"jsonrpc": "2.0", "method": "notifications/initialized"},
       headers={"Accept": "application/json, text/event-stream"})

r = call("web_request", {"url": "http://127.0.0.1:8399/login",
                         "method": "POST",
                         "body": {"username": "sim-user",
                                  "password": "not-a-real-secret"},
                         "confirm": True, "include_headers": True})
print("  status:", r.get("status"), "set-cookie names:",
      ((r.get("metadata") or {}).get("response_headers") or {}).get("set_cookie")
      or (r.get("metadata") or {}).get("cookies"))
time.sleep(3)
r = call("web_read", {"url": "http://127.0.0.1:8399/account"})
text = (r.get("content") or [""])[0]
print("  /account after shared-jar POST:", r.get("content_ok"),
      "chars:", r.get("total_extracted_chars"), "text:", text[:80])
