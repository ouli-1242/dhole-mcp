"""双代际协商探测（2026-10-25）：对运行中的 9201 分别用 2026-07-28 与
2025-11-25 发 initialize，看服务器各答什么；新代际再验无状态核心的
Mcp-Session-Id 行为。
"""
import json

import httpx

BASE = "http://127.0.0.1:9201/mcp"


def init(proto: str):
    c = httpx.Client(trust_env=False, timeout=30.0)
    r = c.post(BASE, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": proto, "capabilities": {},
                                      "clientInfo": {"name": "era-probe", "version": "0"}}},
               headers={"Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json"})
    low = {k.lower(): v for k, v in r.headers.items()}
    body = r.text
    if "text/event-stream" in low.get("content-type", ""):
        data = None
        for line in body.splitlines():
            if line.startswith("data:"):
                data = line[5:].strip()
        body = data or "{}"
    resp = json.loads(body or "{}")
    result = resp.get("result") or {}
    print(f"asked {proto} -> answered {result.get('protocolVersion')!r} "
          f"| session header: {low.get('mcp-session-id', '—')[:24]} "
          f"| status {r.status_code}")
    return result.get("protocolVersion"), low.get("mcp-session-id"), c


for era in ("2026-07-28", "2025-11-25", "2025-06-18", "1999-01-01"):
    answered, sess, client = init(era)
    # 新代际的第二个问题：无 session 头时 tools/list 是否被拒（无状态核心每请求自描述）
    if era == "2026-07-28":
        r2 = client.post(BASE, json={"jsonrpc": "2.0", "id": 2,
                                     "method": "tools/list", "params": {}},
                         headers={"Accept": "application/json, text/event-stream",
                                  "Content-Type": "application/json"})
        print(f"  no-session tools/list -> status {r2.status_code} "
              f"(2026-07-28 无状态核心应可无会话调用；旧 era 应 4xx)")
    client.close()
