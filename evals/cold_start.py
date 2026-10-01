#!/usr/bin/env python3
"""冷启动量尺 —— B6（拆 server.py）唯一的行为证据。

量三件事，各自独立存档，前一批和后一批对比靠 `.runtime/cold_start/latest.json`：

1. **import 剖面**：`python -X importtime -c "import dhole_mcp.server"` 的完整
   输出 + 按 self 耗时排序的 top-25 + 根模块的累计耗时。拆模块改的就是 import
   时序，这一条是它的直接读数。
2. **真实 stdio 握手**：子进程拉起 `python -m dhole_mcp`（一次性 DHOLE_HOME），
   initialize → tools/list 各自的响应耗时。跑 **3 遍**取中位数 —— Windows 上
   单次冷启动方差大，单次读数不可比。
3. **首调用**：`dhole_doc(topic='start')` 的往返耗时 —— 纯本地文件读取，把
   "服务器就绪"和"第一个请求能答"分开。

用法：
    python evals/cold_start.py            # 跑一次，存档 + 打印，和上次对比
    python evals/cold_start.py --json     # 只打印本次 JSON（供脚本用）

产物落 `evals/.runtime/cold_start/`（已 gitignore）：时间戳全档 + latest.json。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
RUNTIME = REPO_ROOT / "evals" / ".runtime" / "cold_start"

PROTOCOL_VERSION = "2025-06-18"
IMPORTTIME_LINE = re.compile(
    r"import time:\s+(\d+)\s+\|\s+(\d+)\s+\|(\s+\d+)?\s*(\S.*)")


def _child_env(extra: dict | None = None) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    home = tempfile.mkdtemp(prefix="dhole_coldstart_")
    env["DHOLE_HOME"] = home
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # 保险丝：握手与首调用都不碰网络，但别让任何意外路径出门。
    env["DHOLE_SEARCH_PROXY"] = "http://127.0.0.1:9"
    for stale in ("DHOLE_TOOLS", "DHOLE_TASKS", "DHOLE_VAULT",
                  "DHOLE_DEFAULT_ENGINES", "DHOLE_USAGE_LOG"):
        env.pop(stale, None)
    if extra:
        env.update(extra)
    return env


# ── 1. importtime ─────────────────────────────────────────────────────────────


def measure_importtime() -> dict:
    proc = subprocess.run(
        [sys.executable, "-X", "importtime", "-c", "import dhole_mcp.server"],
        cwd=str(REPO_ROOT), env=_child_env(),
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    rows = []
    for line in proc.stderr.splitlines():
        m = IMPORTTIME_LINE.match(line.strip())
        if m:
            rows.append({"self_us": int(m.group(1)),
                         "cumulative_us": int(m.group(2)),
                         "module": m.group(4)})
    if not rows:
        raise RuntimeError(f"importtime produced nothing: {proc.stderr[:400]}")
    total_us = rows[-1]["cumulative_us"]  # 最后一行是根：import dhole_mcp.server
    top25 = sorted(rows, key=lambda r: r["self_us"], reverse=True)[:25]
    return {
        "total_import_us": total_us,
        "total_import_s": round(total_us / 1e6, 3),
        "module_count": len(rows),
        "top25_by_self_us": top25,
    }


# ── 2+3. stdio 握手与首调用 ───────────────────────────────────────────────────


class _Client:
    """最小 JSON-RPC/stdio 客户端：够 handshake + 一次调用，别无其他。"""

    def __init__(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "dhole_mcp"], cwd=str(REPO_ROOT),
            env=_child_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL)
        self._cond = threading.Condition()
        self._by_id: dict = {}
        self._closed = False
        self._next_id = 1
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for raw in iter(self.proc.stdout.readline, b""):
            try:
                parsed = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                continue
            with self._cond:
                if isinstance(parsed, dict) and parsed.get("id") is not None:
                    self._by_id[parsed["id"]] = parsed
                self._cond.notify_all()

    def request(self, method, params, timeout=60.0) -> float:
        rid = self._next_id
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        with self._cond:
            t0 = time.perf_counter()
            self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
            deadline = t0 + timeout
            while rid not in self._by_id:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    raise TimeoutError(f"{method} no answer in {timeout}s")
                if self.proc.poll() is not None:
                    raise RuntimeError(f"{method}: server exited")
                self._cond.wait(timeout=min(remaining, 1.0))
            return time.perf_counter() - t0

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=8)
        except Exception:
            self.proc.kill()


def handshake_once() -> dict:
    t_spawn = time.perf_counter()
    client = _Client()
    t_init = client.request("initialize", {
        "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
        "clientInfo": {"name": "dhole-coldstart", "version": "1.0"}})
    client.notify("notifications/initialized")
    t_tools = client.request("tools/list", {})
    t_first = client.request("tools/call", {
        "name": "dhole_doc", "arguments": {"topic": "start"}})
    client.close()
    return {
        "initialize_s": round(t_init, 3),
        "tools_list_s": round(t_tools, 3),
        "first_call_s": round(t_first, 3),
        "spawn_to_tools_list_s": round(time.perf_counter() - t_spawn, 3),
    }


def measure_handshake(runs: int = 3) -> dict:
    attempts = []
    for _ in range(runs):
        try:
            attempts.append(handshake_once())
        except Exception as e:
            attempts.append({"error": str(e)})
    ok = [a for a in attempts if "error" not in a]
    out = {"runs": attempts, "ok_runs": len(ok)}
    if ok:
        med = {k: statistics.median(a[k] for a in ok)
               for k in ("initialize_s", "tools_list_s", "first_call_s",
                         "spawn_to_tools_list_s")}
        out["median"] = {k: round(v, 3) for k, v in med.items()}
    return out


# ── 存档与对比 ────────────────────────────────────────────────────────────────


def _previous() -> dict | None:
    p = RUNTIME / "latest.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="只打印本次 JSON")
    args = ap.parse_args()

    record = {
        "measured_at": dt.datetime.now().isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "importtime": measure_importtime(),
        "handshake": measure_handshake(),
    }
    prev = _previous()

    RUNTIME.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    (RUNTIME / f"cold_start-{stamp}.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (RUNTIME / "latest.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0

    it = record["importtime"]
    hs = record["handshake"]
    print(f"[cold_start] {record['measured_at']}  (python {record['python']})")
    print(f"import dhole_mcp.server : {it['total_import_s']}s "
          f"({it['module_count']} modules)")
    for row in it["top25_by_self_us"][:10]:
        print(f"  top self {row['self_us']/1000:8.1f} ms  {row['module']}")
    if "median" in hs:
        m = hs["median"]
        print(f"stdio handshake (n={hs['ok_runs']}, median): "
              f"initialize {m['initialize_s']}s · tools/list {m['tools_list_s']}s · "
              f"first call {m['first_call_s']}s · spawn→tools/list "
              f"{m['spawn_to_tools_list_s']}s")
    for a in hs["runs"]:
        if "error" in a:
            print(f"  run FAILED: {a['error']}")

    if prev:
        pit, phs = prev.get("importtime", {}), prev.get("handshake", {})
        if pit.get("total_import_s") and it["total_import_s"]:
            d = it["total_import_s"] - pit["total_import_s"]
            print(f"Δ vs {prev.get('measured_at')}: "
                  f"import {pit['total_import_s']}→{it['total_import_s']}s "
                  f"({d:+.3f}s)")
        if phs.get("median") and hs.get("median"):
            for k in ("initialize_s", "tools_list_s", "first_call_s"):
                d = hs["median"][k] - phs["median"][k]
                print(f"Δ {k}: {phs['median'][k]}→{hs['median'][k]}s ({d:+.3f}s)")
    else:
        print("(no previous run recorded — this one becomes the baseline)")
    print(f"records: {RUNTIME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
