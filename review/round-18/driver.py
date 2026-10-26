"""round-18 进程内驱动（P1 真实模拟，端口 9218）。

契约与 loop/ref/harness/dhole_cli.py 对齐：原始 JSON-RPC、trust_env=False、
协议 2025-03-26、Mcp-Session-Id 复用。每发落盘：
  sessions/persona-NN/<seq>_<tool>.json   完整 wire 回执
  sessions/calls.jsonl                    追加台账（tag/tool/args/wall/ok/error）
stdout 只出紧凑读数，供报告撰写引用。
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROUND = Path(__file__).resolve().parent
SESSIONS = ROUND / "sessions"
BASE = "http://127.0.0.1:9218/mcp"
ROOT = "http://127.0.0.1:8399"
PROTOCOL = "2025-03-26"

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


class Client:
    def __init__(self) -> None:
        self.c = httpx.Client(trust_env=False, timeout=300.0)
        self.sid = None
        self.iid = 0

    def post(self, payload):
        headers = {"Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json"}
        if self.sid:
            headers["Mcp-Session-Id"] = self.sid
        r = self.c.post(BASE, json=payload, headers=headers)
        low = {k.lower(): v for k, v in r.headers.items()}
        if low.get("mcp-session-id"):
            self.sid = low["mcp-session-id"]
        if r.status_code >= 400:
            return {"_http": r.status_code, "_body": r.text[:300]}
        ct = low.get("content-type", "")
        if "text/event-stream" in ct:
            data = None
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    data = line[5:].strip()
            return json.loads(data) if data else None
        return json.loads(r.text) if r.text else None

    def rpc(self, method, params=None):
        self.iid += 1
        payload = {"jsonrpc": "2.0", "method": method, "id": self.iid}
        if params is not None:
            payload["params"] = params
        t0 = time.monotonic()
        resp = self.post(payload)
        return resp, time.monotonic() - t0


class PersonaSim:
    def __init__(self, pno, name):
        self.pno = pno
        self.name = name
        self.tag_prefix = "p%02d" % pno
        self.dir = SESSIONS / ("persona-%02d" % pno)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.client = Client()
        resp, _ = self.client.rpc("initialize", {
            "protocolVersion": PROTOCOL, "capabilities": {},
            "clientInfo": {"name": "round18-p%02d" % pno, "version": "1"}})
        self.client.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        info = ((resp or {}).get("result") or {}).get("serverInfo")
        print("[p%02d %s] session %s server=%s" % (pno, name, self.client.sid, info))
        self.seq = 0
        self.scenario = ""

    def set_scenario(self, sname):
        self.scenario = sname
        print("")
        print("---- p%02d %s ----" % (self.pno, sname))

    def pause(self, seconds, why="throttle"):
        print("      (pause %.0fs - %s)" % (seconds, why))
        time.sleep(seconds)

    def call(self, tool, args, note=""):
        self.seq += 1
        tag = "%s-%s#%02d" % (self.tag_prefix, self.scenario or "misc", self.seq)
        t0 = time.monotonic()
        resp, _ = self.client.rpc("tools/call", {"name": tool, "arguments": args})
        wall = time.monotonic() - t0
        parsed = {}
        if isinstance(resp, dict):
            result = resp.get("result") or {}
            texts = [c.get("text", "") for c in (result.get("content") or [])
                     if isinstance(c, dict)]
            if texts:
                try:
                    parsed = json.loads(texts[0])
                except Exception:
                    parsed = {"_raw": texts[0][:600]}
            elif result.get("structuredContent"):
                parsed = result["structuredContent"]
            elif resp.get("error"):
                parsed = {"_rpc_error": resp["error"]}
            else:
                parsed = {"_empty": True}
        else:
            parsed = {"_no_resp": True}
        parsed["_wall_s"] = round(wall, 2)
        parsed["_tool"] = tool
        parsed["_tag"] = tag
        parsed["_ts"] = datetime.now(timezone.utc).isoformat()
        if note:
            parsed["_note"] = note
        (self.dir / ("%02d_%s.json" % (self.seq, tool))).write_text(
            json.dumps(parsed, ensure_ascii=False, indent=1), encoding="utf-8")
        with (SESSIONS / "calls.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": parsed["_ts"], "tag": tag, "persona": self.name,
                "tool": tool, "args": args, "wall_s": round(wall, 2),
                "ok": parsed.get("content_ok"),
                "error": str(parsed.get("error"))[:200] if parsed.get("error") else None,
                "diagnosis": parsed.get("diagnosis"),
            }, ensure_ascii=False) + "\n")
        err = parsed.get("error")
        diag = (parsed.get("diagnosis") or {}).get("code", "")
        head = (parsed.get("summary") or "")[:100]
        chars = parsed.get("total_extracted_chars") or ""
        print("[%s] %s %5.1fs ok=%s err=%s %s chars=%s" % (
            tag, tool, wall, parsed.get("content_ok"),
            str(err)[:90] if err else "-", diag, chars))
        if head:
            print("      summary: " + head)
        return parsed

    def observe(self, label, parsed, *fields):
        vals = {}
        for f in fields:
            v = parsed
            for k in f.split("."):
                v = (v or {}).get(k) if isinstance(v, dict) else None
            if v is not None:
                vals[f] = v
        if vals:
            print("      %s: %s" % (label, json.dumps(vals, ensure_ascii=False)[:300]))

    def rows(self, parsed, key="results", n=5, width=100):
        for x in (parsed.get(key) or [])[:n]:
            if isinstance(x, dict):
                print("   -", json.dumps({k: x.get(k) for k in
                      ("url", "title", "content_ok", "total_extracted_chars", "date",
                       "is_stale", "relevance_score", "error") if x.get(k) is not None},
                      ensure_ascii=False)[:width])
            else:
                print("   -", str(x)[:width])
