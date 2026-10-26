"""round-18 P0：拉 tools/list，落盘 schema 供画像脚本按真实字段名误用。"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:9218/mcp"
OUT = Path(__file__).resolve().parent.parent / "sessions" / "tools-list.json"

c = httpx.Client(trust_env=False, timeout=30.0)
sid = None


def post(payload):
    global sid
    headers = {"Accept": "application/json, text/event-stream",
               "Content-Type": "application/json"}
    if sid:
        headers["Mcp-Session-Id"] = sid
    r = c.post(BASE, json=payload, headers=headers)
    for k, v in r.headers.items():
        if k.lower() == "mcp-session-id":
            sid = v
    if "text/event-stream" in r.headers.get("content-type", ""):
        for line in r.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        return None
    return json.loads(r.text) if r.text else None


post({"jsonrpc": "2.0", "method": "initialize", "id": 1, "params": {
    "protocolVersion": "2025-03-26", "capabilities": {},
    "clientInfo": {"name": "round18-probe", "version": "1"}}})
post({"jsonrpc": "2.0", "method": "notifications/initialized"})
resp = post({"jsonrpc": "2.0", "method": "tools/list", "id": 2})
tools = (resp.get("result") or {}).get("tools") or []
OUT.write_text(json.dumps(tools, ensure_ascii=False, indent=1), encoding="utf-8")
for t in tools:
    props = list(((t.get("inputSchema") or {}).get("properties") or {}).keys())
    print(t["name"], "::", ", ".join(props))
