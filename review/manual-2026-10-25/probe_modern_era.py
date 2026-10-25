"""2026-07-28 新代际探测（无状态姿势）：不 initialize、不带会话，带
Mcp-Protocol-Version 头直发 server/discover、tools/list、tools/call。
"""
import json

import httpx

BASE = "http://127.0.0.1:9201/mcp"
H = {"Accept": "application/json, text/event-stream",
     "Content-Type": "application/json",
     "Mcp-Protocol-Version": "2026-07-28"}


def post(payload):
    c = httpx.Client(trust_env=False, timeout=30.0)
    payload.setdefault("params", {})
    payload["params"].setdefault("_meta", {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {}})
    h = {**H, "Mcp-Method": payload["method"]}
    if payload["method"] == "tools/call":
        h["Mcp-Name"] = payload["params"]["name"]
    r = c.post(BASE, json=payload, headers=h)
    low = {k.lower(): v for k, v in r.headers.items()}
    body = r.text
    if "text/event-stream" in low.get("content-type", ""):
        data = None
        for line in body.splitlines():
            if line.startswith("data:"):
                data = line[5:].strip()
        body = data or "{}"
    return r.status_code, json.loads(body or "{}"), low


def main():
    st, resp, _ = post({"jsonrpc": "2.0", "id": 1, "method": "server/discover",
                        "params": {}})
    result = resp.get("result") or {}
    print(f"server/discover: {st} supported_versions="
          f"{result.get('supported_versions')} server="
          f"{(result.get('serverInfo') or {}).get('name')}")

    st, resp, _ = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                        "params": {}})
    tools = [t.get("name") for t in ((resp.get("result") or {}).get("tools") or [])]
    print(f"tools/list: {st} count={len(tools)} first={tools[:3]}")

    st, resp, _ = post({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "sys_guide",
                                   "arguments": {"topic": "start"}}})
    ok = bool((resp.get("result") or {}).get("content"))
    print(f"tools/call: {st} content={ok}")

    st, resp, _ = post({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                        "params": {"name": "web_recall",
                                   "arguments": {"query": "dhole"}}})
    print(f"tools/call(recall): {st} ok={bool((resp.get('result') or {}).get('content'))}")


if __name__ == "__main__":
    main()
