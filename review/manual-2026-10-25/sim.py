"""手动真实模拟（2026-10-25）：dhole HTTP 服务 9201，13 颗工具 × 10 场景。

环境事实（ref/env-facts.md）：mcp 2.2.0 客户端与 dhole 协议版本谈不拢 → 直发
原始 JSON-RPC；本机 httpx 会被注册表系统代理截走回环请求 → trust_env=False。
真实 ~/.dhole 状态、真实网络、真实节流（搜索 ≥10s、重调用 ≥8s）。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:9201/mcp"
OUT = Path("review/manual-2026-10-25")
LOG = OUT / "sessions" / "calls.jsonl"
EV = OUT / "results"
EV.mkdir(parents=True, exist_ok=True)
ROOT = "http://127.0.0.1:8399"


class Client:
    def __init__(self):
        self.c = httpx.Client(trust_env=False, timeout=300.0)
        self.sid: str | None = None
        self.iid = 0

    def post(self, payload: dict) -> dict | None:
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

    def rpc(self, method: str, params: dict | None = None):
        self.iid += 1
        payload = {"jsonrpc": "2.0", "method": method, "id": self.iid}
        if params is not None:
            payload["params"] = params
        t0 = time.monotonic()
        resp = self.post(payload)
        return resp, time.monotonic() - t0


C = Client()
_seq = 0


def call(tool: str, args: dict, *, expect_error: bool = False) -> dict:
    """一发真实 tools/call：落 calls.jsonl，回 wire 字典。"""
    global _seq
    _seq += 1
    t0 = time.monotonic()
    resp, _ = C.rpc("tools/call", {"name": tool, "arguments": args})
    wall = time.monotonic() - t0
    parsed: dict = {}
    if isinstance(resp, dict):
        result = resp.get("result") or {}
        texts = [c.get("text", "") for c in (result.get("content") or [])
                 if isinstance(c, dict)]
        if texts:
            try:
                parsed = json.loads(texts[0])
            except Exception:
                parsed = {"_raw": texts[0][:400]}
        elif result.get("structuredContent"):
            parsed = result["structuredContent"]
        elif resp.get("error"):
            parsed = {"_rpc_error": resp["error"]}
        else:
            parsed = {"_empty": True}
    else:
        parsed = {"_no_resp": True}
    parsed.setdefault("_wall_s", round(wall, 2))
    parsed["_seq"] = _seq
    parsed["_tool"] = tool
    (EV / f"{_seq:02d}_{tool}.json").write_text(
        json.dumps(parsed, ensure_ascii=False, indent=1), encoding="utf-8")
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(), "seq": _seq,
            "tool": tool, "args": args, "wall_s": round(wall, 2),
            "ok": parsed.get("content_ok"),
            "error": (str(parsed.get("error"))[:160]
                      if parsed.get("error") else None),
            "diagnosis": parsed.get("diagnosis"),
        }, ensure_ascii=False) + "\n")
    # 紧凑读数
    err = parsed.get("error")
    diag = (parsed.get("diagnosis") or {}).get("code", "")
    head = (parsed.get("summary") or "")[:90]
    chars = parsed.get("total_extracted_chars") or ""
    print(f"[{_seq:02d}] {tool} {wall:5.1f}s ok={parsed.get('content_ok')} "
          f"err={str(err)[:70] if err else '-'} {diag} chars={chars} {head}")
    return parsed


def observe(label: str, parsed: dict, *fields) -> None:
    vals = {}
    for f in fields:
        v = parsed
        for k in f.split("."):
            v = (v or {}).get(k) if isinstance(v, dict) else None
        if v is not None:
            vals[f] = v
    print(f"      {label}: {json.dumps(vals, ensure_ascii=False)[:240]}")


# ── S1 · 服务认知 ────────────────────────────────────────────────────
print("== S1 服务认知 ==")
resp, _ = C.rpc("initialize", {
    "protocolVersion": "2025-11-25", "capabilities": {},
    "clientInfo": {"name": "manual-sim", "version": "0"}})
print("initialize:", (resp or {}).get("result", {}).get("serverInfo"))
C.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
resp, _ = C.rpc("tools/list", {})
tools = [t["name"] for t in resp["result"]["tools"]]
print(f"tools: {len(tools)} → {tools}")
r = call("sys_state", {})
observe("census", r, "cache", "sessions")
r = call("sys_state", {"show": ["models"]})
observe("models", r, "models", "state.models")
r = call("sys_guide", {"topic": "start"})
observe("guide", r, "total_extracted_chars")

# ── S2 · 真实摩擦（私网门）───────────────────────────────────────────
print("== S2 私网摩擦 ==")
time.sleep(6)
r = call("web_read", {"url": f"{ROOT}/"})
observe("expect refusal", r, "error", "diagnosis.code", "next_action")
r = call("cred_defaults", {"set": {"allow_private": True}})
observe("cred", r, "summary")
r = call("web_read", {"url": f"{ROOT}/"})
observe("after waiver", r, "content_ok", "total_extracted_chars")

# ── S3 · 建图与爬取（本地）───────────────────────────────────────────
print("== S3 建图与爬取 ==")
time.sleep(8)
r = call("web_discover", {"url": f"{ROOT}/", "sitemap": True, "max_pages": 5})
observe("discover", r, "summary", "plan_id", "links.total_found")
time.sleep(8)
r = call("web_crawl", {"url": f"{ROOT}/", "max_pages": 4, "max_depth": 1,
                       "search": "登录", "max_content_chars_per": 2000})
observe("crawl", r, "summary", "pages_crawled")

# ── S4 · 表单操作员（web_act 三颗新动作走真服务）─────────────────────
print("== S4 web_act 新动作 ==")
time.sleep(8)
art = (OUT / "artifacts").resolve()
art.mkdir(exist_ok=True)
r = call("web_act", {
    "url": f"{ROOT}/dropdown", "timeout": 90000,
    "actions": [
        {"select": {"selector": "#country", "option": "CN"}},
        {"select": {"selector": "#country", "option": "日本"}},
        {"select": {"selector": "#tags", "option": ["r", "绿"]}},
        {"pdf": {"save_to": str(art / "dropdown.pdf")}},
        {"click": "#to-about"},
        {"navigate": "back"},
        {"read": {"selector": "#status", "as": "st"}},
        {"read": {"selector": "#note", "property": "value", "as": "note"}},
    ]})
for a in (r.get("metadata") or {}).get("actions") or []:
    print("   ·", json.dumps(a, ensure_ascii=False)[:190])
time.sleep(8)
r = call("web_act", {
    "url": f"{ROOT}/dialog", "timeout": 60000,
    "actions": [
        {"click": "#delbtn"},
        {"dialog": "accept"},
        {"click": "#delbtn"},
        {"dialog": {"answer": "accept", "prompt_text": "模拟员"}},
        {"click": "#namebtn"},
        {"read": "#status"},
    ]})
for a in (r.get("metadata") or {}).get("actions") or []:
    print("   ·", json.dumps(a, ensure_ascii=False)[:190])
time.sleep(8)
r = call("web_act", {
    "url": f"{ROOT}/upload", "timeout": 60000,
    "actions": [
        {"upload": {"selector": "#doc", "files": ["tests/sample.docx"]}},
        {"click": "#send"},
    ]})
for a in (r.get("metadata") or {}).get("actions") or []:
    print("   ·", json.dumps(a, ensure_ascii=False)[:190])
pend = any(a.get("pending_approval") for a in (r.get("metadata") or {}).get("actions") or [])
print("   submit gate paused:", pend)
if pend:
    time.sleep(6)
    r = call("web_act", {
        "url": f"{ROOT}/upload", "timeout": 60000,
        "actions": [
            {"upload": {"selector": "#doc", "files": ["tests/sample.docx"]}},
            {"click": {"selector": "#send", "confirm": True}},
        ]})
    for a in (r.get("metadata") or {}).get("actions") or []:
        print("   ·", json.dumps(a, ensure_ascii=False)[:190])
    observe("receipt", r, "content", "total_extracted_chars")

# ── S5 · 写请求纪律 ──────────────────────────────────────────────────
print("== S5 web_request ==")
time.sleep(6)
r = call("web_request", {"url": f"{ROOT}/login", "method": "POST",
                         "body": {"username": "sim-user",
                                  "password": "not-a-real-secret"}})
observe("no confirm", r, "error", "diagnosis.code")
time.sleep(6)
r = call("web_request", {"url": f"{ROOT}/login", "method": "POST",
                         "body": {"username": "sim-user",
                                  "password": "not-a-real-secret"},
                         "confirm": True, "session_id": "sim-main",
                         "include_headers": True})
observe("post", r, "content_ok", "status", "metadata.redirect")
time.sleep(6)
r = call("web_read", {"url": f"{ROOT}/account", "session_id": "sim-main"})
observe("account", r, "content_ok", "total_extracted_chars",
        "diagnosis.code")

# ── S6 · 变更监控 ────────────────────────────────────────────────────
print("== S6 check ==")
r = call("web_read", {"url": f"{ROOT}/checkout", "extraction_type": "check"})
observe("baseline", r, "content_ok", "summary")
time.sleep(6)
r = call("web_read", {"url": f"{ROOT}/checkout", "extraction_type": "check"})
observe("recheck", r, "content_ok", "changed", "summary")

# ── S7 · 真实阅读 ────────────────────────────────────────────────────
print("== S7 真实阅读 ==")
time.sleep(8)
r = call("web_read", {"url": "https://en.wikipedia.org/wiki/Dhole",
                      "focus": "conservation status", "focus_mode": "best",
                      "max_content_chars": 20000})
observe("wiki", r, "content_ok", "total_extracted_chars", "page_type",
        "fetcher_used")
time.sleep(8)
r = call("web_read", {"urls": ["https://example.com/",
                               "https://www.mozilla.org/en-US/about/"],
                      "max_content_chars": 3000})
pages = r.get("pages") or []
print("   batch:", [(p.get("url", "")[:40], p.get("content_ok"),
                     p.get("total_extracted_chars")) for p in pages])
time.sleep(8)
r = call("web_read", {"url": "https://arxiv.org/pdf/1706.03762v7",
                      "pages": "1", "timeout": 90000})
observe("arxiv pdf", r, "content_ok", "total_extracted_chars", "page_type",
        "diagnosis.code")

# ── S8 · 订阅 ────────────────────────────────────────────────────────
print("== S8 feed ==")
time.sleep(8)
r = call("feed_read", {"urls": ["https://hnrss.org/frontpage",
                                "https://www.ruanyifeng.com/blog/atom.xml"],
                       "max_items": 4})
feeds = r.get("feeds") or []
for f in feeds:
    print("   ·", f.get("url", "")[:48], "ok:", f.get("ok") if "ok" in f else
          (f.get("content_ok") if "content_ok" in f else "?"),
          "items:", len(f.get("items") or []),
          str(f.get("error"))[:60] if f.get("error") else "")

# ── S9 · 本地文档 ────────────────────────────────────────────────────
print("== S9 doc_parse ==")
for f, extra in [("tests/sample.docx", {}), ("tests/sample.xlsx", {}),
                 ("tests/background_checks.pdf", {"pages": "1-2"})]:
    time.sleep(4)
    r = call("doc_parse", {"file_path": f, **extra})
    observe(f, r, "total_extracted_chars", "metadata.encoding")

# ── S10 · 搜索与研究（节流 ≥10s）────────────────────────────────────
print("== S10 search ==")
time.sleep(10)
r = call("web_search", {"query": "豺 dhole 动物 保护现状", "max_results": 5})
rows = r.get("results") or []
print("   rows:", [(str(x.get("url"))[:44], str(x.get("relevance_score"))[:4],
                    x.get("engines_consensus")) for x in rows[:5]])
observe("consensus", r, "engines_consensus")
time.sleep(12)
r = call("web_search", {"query": "python asyncio task scheduling",
                        "max_results": 5, "sort": "relevance"})
rows = r.get("results") or []
print("   rows:", [(str(x.get("url"))[:44], str(x.get("relevance_score"))[:4])
                   for x in rows[:5]])
time.sleep(12)
r = call("web_research", {"query": "What is the conservation status of the dhole?",
                          "max_sources": 2, "per_source_chars": 3000,
                          "timeout": 150000})
for s in r.get("sources") or r.get("evidence") or []:
    if isinstance(s, dict):
        print("   ·", str(s.get("url"))[:50], "ok:", s.get("content_ok"),
              "chars:", s.get("total_extracted_chars"),
              str(s.get("error"))[:60] if s.get("error") else "")
observe("research", r, "summary")

# ── S11 · recall + 收尾 ─────────────────────────────────────────────
print("== S11 recall ==")
time.sleep(6)
r = call("web_recall", {"query": "dhole conservation status", "limit": 5})
for row in (r.get("results") or r.get("rows") or [])[:5]:
    print("   ·", str(row.get("url"))[:52], str(row.get("title"))[:36],
          "stale:", row.get("is_stale"))
observe("recall", r, "summary")
r = call("sys_state", {})
observe("final census", r, "cache", "sessions")

print("DONE")
