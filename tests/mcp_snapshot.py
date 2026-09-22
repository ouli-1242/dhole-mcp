#!/usr/bin/env python3
"""Offline MCP protocol baseline snapshot for dhole-mcp (refactor pre-flight).

Self-contained, stdlib only. Spawns ``python -m dhole_mcp`` over stdio with

  * a throwaway ``DHOLE_HOME`` (fresh directory per run, under
    ``refactor_artifacts/tmp_home/``), and
  * an offline socket guard loaded via ``sitecustomize`` (its directory is
    spliced onto the child's ``PYTHONPATH``): every ``connect()`` to a
    non-local address and every ``getaddrinfo()`` for a non-local hostname is
    refused inside the child process, so a snapshot run cannot make a real
    outbound internet request even if a code path tries to.

It then records raw JSON-RPC exchanges (initialize / notifications/initialized
/ tools/list / tools/call) as evidence files under ``refactor_artifacts/
baseline/``.

Usage (from the repo root):
    python refactor_artifacts/tools/mcp_snapshot.py
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import subprocess
import sys
import threading
import time

# --------------------------------------------------------------------------
# Paths / constants
# --------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ARTIFACTS = REPO_ROOT / "refactor_artifacts"
BASELINE = ARTIFACTS / "baseline"
TMP_ROOT = ARTIFACTS / "tmp_home"
SHIM_DIR = TMP_ROOT / "pyshim"

PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "dhole-baseline-snapshot"
CLIENT_VERSION = "1.0.0"

HANDSHAKE_TIMEOUT = 120.0      # initialize / tools/list / notifications
PER_CALL_TIMEOUT = 120.0       # per tools/call
TOTAL_CAP_SECONDS = 15 * 60.0  # whole-run ceiling

# Dead local proxy: the search engine pool reads DHOLE_SEARCH_PROXY first, so
# every engine request is sent to a closed loopback port instead of the
# internet. The standard proxy vars are set too as defense-in-depth for any
# other HTTP client with trust_env=True.
DEAD_LOCAL_PROXY = "http://127.0.0.1:9"

# The offline shim written to SHIM_DIR/sitecustomize.py and auto-imported by
# the child interpreter (its dir is on PYTHONPATH).
OFFLINE_SHIM = '''\
"""Offline socket guard installed by refactor_artifacts/tools/mcp_snapshot.py.

Auto-loaded by CPython as ``sitecustomize`` because this directory is on
PYTHONPATH for the snapshot run. It refuses every connect() to a non-local
address and every getaddrinfo() for a non-local hostname, so the snapshot can
never make a real outbound internet request. Loopback / private / link-local
addresses are still allowed (they cannot leave the machine).
"""

import ipaddress
import socket

_LOCAL_HOSTNAMES = frozenset({
    "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback",
})


def _is_local_host(host):
    if host is None:
        return True
    if isinstance(host, (bytes, bytearray)):
        try:
            host = bytes(host).decode("ascii", "replace")
        except Exception:
            return False
    text = str(host).strip()
    if not text:
        return True
    if text.lower() in _LOCAL_HOSTNAMES:
        return True
    bare = text.strip("[]").split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(bare)
    except ValueError:
        return False  # a name we cannot prove local -> treat as remote
    return not ip.is_global


_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex
_orig_getaddrinfo = socket.getaddrinfo


def _guarded_connect(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple) and address:
        if not _is_local_host(address[0]):
            raise OSError(10061, "dhole-snapshot-offline-guard: refused non-local connect to %r" % (address[0],))
    return _orig_connect(self, address)


def _guarded_connect_ex(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple) and address:
        if not _is_local_host(address[0]):
            return 10061  # no packet leaves the machine
    return _orig_connect_ex(self, address)


def _guarded_getaddrinfo(host, port, *args, **kwargs):
    if not _is_local_host(host):
        raise socket.gaierror(-2, "dhole-snapshot-offline-guard: blocked DNS for %r" % (host,))
    return _orig_getaddrinfo(host, port, *args, **kwargs)


socket.socket.connect = _guarded_connect
socket.socket.connect_ex = _guarded_connect_ex
socket.getaddrinfo = _guarded_getaddrinfo
'''

# --------------------------------------------------------------------------
# Per-tool call plan (plausible/minimal args; offline-safe targets only).
# The wrong-type and missing-argument cases are derived from the live
# tools/list schema at runtime (see build_call_plan).
# --------------------------------------------------------------------------

NONEXISTENT_FILE = str(ARTIFACTS / "does-not-exist-snapshot-probe.html")

PLAUSIBLE_CASES = {
    "smart_fetch": [
        ("minimal: loopback url (SSRF-blocked)", {"url": "http://127.0.0.1/"}),
        ("minimal: cloud metadata IP (SSRF-blocked)", {"url": "http://169.254.169.254/latest/meta-data/"}),
        ("minimal: file:// scheme (blocked scheme)", {"url": "file:///C:/Windows/win.ini"}),
    ],
    "smart_crawl": [
        ("minimal: loopback start url (SSRF-blocked)", {"url": "http://127.0.0.1/"}),
        ("minimal: cloud metadata IP (SSRF-blocked)", {"url": "http://169.254.169.254/"}),
    ],
    "screenshot": [
        ("minimal: loopback url (SSRF-blocked)", {"url": "http://127.0.0.1/"}),
        ("minimal: file:// scheme (blocked scheme)", {"url": "file:///C:/Windows/win.ini"}),
    ],
    "smart_search": [
        # Engines are routed to the dead loopback proxy: no internet egress.
        ("minimal: query via dead loopback proxy", {"query": "dhole offline snapshot probe"}),
        ("minimal: find_similar with blocked source url", {"query": "probe", "options": {"mode": "find_similar", "url": "http://127.0.0.1/"}}),
    ],
    "cache_clear": [
        ("minimal: no args (expired entries only)", {}),
        ("minimal: engine_state reset", {"engine_state": True}),
        ("minimal: all=true (throwaway DHOLE_HOME)", {"all": True}),
    ],
    "parse": [
        ("minimal: nonexistent local path", {"file_path": NONEXISTENT_FILE}),
    ],
    "feed_fetch": [
        ("minimal: loopback feed url (SSRF-blocked)", {"urls": ["http://127.0.0.1/feed.xml"]}),
        ("minimal: cloud metadata feed url (SSRF-blocked)", {"urls": ["http://169.254.169.254/feed"]}),
    ],
    "resolve_url": [
        ("minimal: loopback url (SSRF-blocked)", {"url": "http://127.0.0.1/"}),
        ("minimal: scheme-relative input", {"url": "//127.0.0.1/"}),
        ("minimal: cloud metadata IP (SSRF-blocked)", {"url": "http://169.254.169.254/"}),
    ],
}

TOOL_ORDER = [
    "smart_fetch", "smart_crawl", "screenshot", "smart_search",
    "cache_clear", "parse", "feed_fetch", "resolve_url",
]


def _wrong_type_value(prop_schema: dict):
    t = (prop_schema or {}).get("type")
    if t == "string":
        return 12345
    if t == "array":
        return 12345
    if t == "object":
        return "not-an-object"
    if t == "boolean":
        return "not-a-bool"
    if t == "integer":
        return "not-an-int"
    return 12345


def build_call_plan(tools_by_name: dict):
    """Build the ordered tools/call plan, deriving wrong-type args from schemas."""
    plan = []
    for tool in TOOL_ORDER:
        schema = (tools_by_name.get(tool) or {}).get("inputSchema") or {}
        props = schema.get("properties") or {}
        required = list(schema.get("required") or [])
        primary = required[0] if required else (next(iter(props), None))

        for label, args in PLAUSIBLE_CASES.get(tool, []):
            plan.append({"tool": tool, "kind": "plausible", "label": label, "args": args})

        # (b) required/missing-argument case
        plan.append({
            "tool": tool, "kind": "missing-required", "args": {},
            "label": "empty args" + (" (tool has NO required args; this is valid)" if not required else ""),
        })

        # (c) wrong-type case on the primary/first-required property
        if primary is not None:
            plan.append({
                "tool": tool, "kind": "wrong-type",
                "label": "wrong type: %s=%r where %s expected" % (
                    primary, _wrong_type_value(props.get(primary) or {}),
                    (props.get(primary) or {}).get("type")),
                "args": {primary: _wrong_type_value(props.get(primary) or {})},
            })
    return plan


# --------------------------------------------------------------------------
# Minimal JSON-RPC-over-stdio client
# --------------------------------------------------------------------------

class StdioClient:
    def __init__(self, argv, cwd, env):
        self.proc = subprocess.Popen(
            argv, cwd=str(cwd), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self._cond = threading.Condition()
        self._by_id: dict = {}          # id -> (raw_line, parsed, ts)
        self.messages: list = []        # every stdout message, in order
        self.stderr_text = ""
        self._stderr_chunks: list = []
        self._next_id = 1
        self._closed = False
        self._t_out = threading.Thread(target=self._read_stdout, daemon=True)
        self._t_err = threading.Thread(target=self._read_stderr, daemon=True)
        self._t_out.start()
        self._t_err.start()

    # -- background readers -------------------------------------------------
    def _read_stdout(self):
        stream = self.proc.stdout
        assert stream is not None
        try:
            for raw in iter(stream.readline, b""):
                text = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not text:
                    continue
                try:
                    parsed = json.loads(text)
                except Exception:
                    parsed = None
                with self._cond:
                    self.messages.append((text, parsed))
                    if isinstance(parsed, dict) and parsed.get("id") is not None:
                        self._by_id[parsed["id"]] = (text, parsed, time.time())
                    self._cond.notify_all()
        except Exception:
            pass
        finally:
            with self._cond:
                self._closed = True
                self._cond.notify_all()

    def _read_stderr(self):
        stream = self.proc.stderr
        assert stream is not None
        try:
            for raw in iter(stream.readline, b""):
                self._stderr_chunks.append(raw.decode("utf-8", "replace"))
        except Exception:
            pass

    # -- protocol -----------------------------------------------------------
    def send(self, obj) -> str:
        line = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        assert self.proc.stdin is not None
        self.proc.stdin.write((line + "\n").encode("utf-8"))
        self.proc.stdin.flush()
        return line

    def notify(self, method, params=None) -> str:
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        return self.send(msg)

    def request(self, method, params, timeout):
        """Send a request and wait for its response.

        Returns (request_line, response_line_or_None, parsed_or_None, marker,
        duration). marker is None (ok), 'TIMEOUT' or 'SERVER_EXIT'.
        """
        rid = self._next_id
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        t0 = time.time()
        req_line = self.send(msg)
        deadline = t0 + timeout
        with self._cond:
            while True:
                if rid in self._by_id:
                    raw, parsed, _ts = self._by_id[rid]
                    return req_line, raw, parsed, None, time.time() - t0
                if self._closed or self.proc.poll() is not None:
                    return req_line, None, None, "SERVER_EXIT", time.time() - t0
                remaining = deadline - time.time()
                if remaining <= 0:
                    return req_line, None, None, "TIMEOUT", time.time() - t0
                self._cond.wait(timeout=min(remaining, 2.0))

    # -- lifecycle ----------------------------------------------------------
    def shutdown(self, grace=8.0):
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=grace)
        except Exception:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        try:
            self._t_err.join(timeout=3)
        except Exception:
            pass
        self.stderr_text = "".join(self._stderr_chunks)


def _answer_server_request(client: StdioClient, parsed: dict):
    """Politely refuse any server->client request so the server never blocks."""
    try:
        client.send({
            "jsonrpc": "2.0", "id": parsed["id"],
            "error": {"code": -32601, "message": "Not supported by snapshot client"},
        })
    except Exception:
        pass


# --------------------------------------------------------------------------
# Evidence writers
# --------------------------------------------------------------------------

def _read_text(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def write_text(path: pathlib.Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    BASELINE.mkdir(parents=True, exist_ok=True)
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    SHIM_DIR.mkdir(parents=True, exist_ok=True)
    write_text(SHIM_DIR / "sitecustomize.py", OFFLINE_SHIM)

    run_id = time.strftime("%Y%m%d-%H%M%S")
    run_home = TMP_ROOT / ("run-%s" % run_id)
    suffix = 0
    while run_home.exists():
        suffix += 1
        run_home = TMP_ROOT / ("run-%s-%d" % (run_id, suffix))
    (run_home / "state").mkdir(parents=True, exist_ok=False)

    env = os.environ.copy()
    env["DHOLE_HOME"] = str(run_home / "state")   # fresh, empty, per-run
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT / "src"), str(SHIM_DIR)])
    env["DHOLE_SEARCH_PROXY"] = DEAD_LOCAL_PROXY
    env["HTTP_PROXY"] = env["HTTPS_PROXY"] = env["ALL_PROXY"] = DEAD_LOCAL_PROXY
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    for stale in ("DHOLE_WORKDIR", "DHOLE_USAGE_LOG", "DHOLE_DEFAULT_ENGINES",
                  "DHOLE_SEARCH_DEADLINE", "DHOLE_SEARCH_FEEDBACK", "DHOLE_HOME_LEGACY"):
        env.pop(stale, None)

    argv = [sys.executable, "-m", "dhole_mcp"]
    print("[snapshot] repo root : %s" % REPO_ROOT)
    print("[snapshot] DHOLE_HOME: %s" % env["DHOLE_HOME"])
    print("[snapshot] spawning  : %s (cwd=%s)" % (" ".join(argv), REPO_ROOT))

    client = StdioClient(argv, REPO_ROOT, env)
    run_start = time.time()
    calls = []          # per tools/call records
    phases = []         # initialize / tools/list records

    def _raw_wire_note(rec):
        lines = ["REQUEST  :: %s" % rec["request_raw"]]
        if rec["response_raw"] is not None:
            lines.append("RESPONSE :: %s" % rec["response_raw"])
        else:
            lines.append("RESPONSE :: <%s>" % rec["marker"])
        return "\n".join(lines)

    try:
        # -- 1. initialize ---------------------------------------------------
        init_params = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
        }
        req, raw, parsed, marker, dur = client.request("initialize", init_params, HANDSHAKE_TIMEOUT)
        init_rec = {"method": "initialize", "params": init_params,
                    "request_raw": req, "response_raw": raw, "response": parsed,
                    "marker": marker, "duration_s": round(dur, 3)}
        phases.append(("initialize", init_rec))
        negotiated = None
        if parsed and isinstance(parsed.get("result"), dict):
            negotiated = parsed["result"].get("protocolVersion")
        print("[snapshot] initialize -> marker=%s protocolVersion=%s (%.2fs)"
              % (marker, negotiated, dur))

        # -- 2. notifications/initialized ------------------------------------
        notif_line = client.notify("notifications/initialized", {})
        phases.append(("notifications/initialized", {"request_raw": notif_line, "response_raw": None,
                                                     "response": None, "marker": None, "duration_s": 0.0}))

        # -- 3. tools/list ----------------------------------------------------
        req, raw, parsed, marker, dur = client.request("tools/list", {}, HANDSHAKE_TIMEOUT)
        tools_rec = {"request_raw": req, "response_raw": raw, "response": parsed,
                     "marker": marker, "duration_s": round(dur, 3)}
        phases.append(("tools/list", tools_rec))
        tools_result = {}
        tools_by_name = {}
        if parsed and isinstance(parsed.get("result"), dict):
            tools_result = parsed["result"]
            for t in tools_result.get("tools") or []:
                if isinstance(t, dict) and t.get("name"):
                    tools_by_name[t["name"]] = t
        print("[snapshot] tools/list -> marker=%s tools=%d (%.2fs)"
              % (marker, len(tools_by_name), dur))

        # -- 4. tools/call -----------------------------------------------------
        plan = build_call_plan(tools_by_name)
        print("[snapshot] call plan: %d tools/call requests" % len(plan))
        for idx, case in enumerate(plan, start=1):
            elapsed = time.time() - run_start
            remaining_total = TOTAL_CAP_SECONDS - elapsed
            if remaining_total <= 1.0:
                calls.append({**case, "seq": idx, "request_raw": None, "response_raw": None,
                              "response": None, "marker": "TOTAL_CAP", "duration_s": 0.0})
                print("[snapshot] %2d/%d %-12s TOTAL_CAP" % (idx, len(plan), case["tool"]))
                continue
            params = {"name": case["tool"], "arguments": case["args"]}
            req, raw, parsed, marker, dur = client.request(
                "tools/call", params, min(PER_CALL_TIMEOUT, remaining_total))
            if parsed is None:
                # may be a server->client request we must answer, or a notification
                for _mraw, mparsed in list(client.messages):
                    if isinstance(mparsed, dict) and mparsed.get("method"):
                        _answer_server_request(client, mparsed)
            rec = {**case, "seq": idx, "params": params, "request_raw": req,
                   "response_raw": raw, "response": parsed, "marker": marker,
                   "duration_s": round(dur, 3)}
            calls.append(rec)
            is_err = None
            if parsed and isinstance(parsed.get("result"), dict):
                is_err = bool(parsed["result"].get("isError"))
            print("[snapshot] %2d/%d %-12s %-16s error=%s (%.2fs)"
                  % (idx, len(plan), case["tool"], case["kind"], is_err, dur))
    finally:
        client.shutdown()
        stderr_text = client.stderr_text or "".join(client._stderr_chunks)

        # -- write size metrics -------------------------------------------
        instructions = None
        init_resp = phases[0][1].get("response") if phases else None
        if init_resp and isinstance(init_resp.get("result"), dict):
            instructions = init_resp["result"].get("instructions")

        def _sizes(obj):
            if obj is None:
                return None
            default = json.dumps(obj)
            compact = json.dumps(obj, separators=(",", ":"))
            compact_noascii = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
            return {
                "chars": len(obj) if isinstance(obj, str) else None,
                "json_default_chars": len(default),
                "json_compact_chars": len(compact),
                "json_compact_ensure_ascii_false_chars": len(compact_noascii),
                "naive_chars_div_4_default": round(len(default) / 4.0, 1),
                "naive_chars_div_4_compact": round(len(compact) / 4.0, 1),
            }

        metrics = {
            "measured_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "repo_root": str(REPO_ROOT),
            "dhole_home_used": env.get("DHOLE_HOME"),
            "protocol_version_requested": PROTOCOL_VERSION,
            "protocol_version_returned": (init_resp or {}).get("result", {}).get("protocolVersion") if init_resp else None,
            "server_info": (init_resp or {}).get("result", {}).get("serverInfo") if init_resp else None,
            "tool_count": len(tools_by_name),
            "tool_names": list(tools_by_name.keys()),
            "instructions_raw_string_chars": (len(instructions) if isinstance(instructions, str) else None),
            "instructions": _sizes(instructions) if isinstance(instructions, str) else None,
            "tools_list_result": _sizes(tools_result) if tools_result else None,
            "tools_list_full_envelope_chars": len(phases[2][1]["response_raw"]) if len(phases) > 2 and phases[2][1]["response_raw"] else None,
            "notes": [
                "Character counts only - no tokenizer is available offline. The README (v14.6, not re-measured since) claims 333 + 2931 = 3264 cl100k_base tokens; that exact tokenizer/count is NOT reproduced here.",
                "chars_raw_string_chars is the literal Python string length of result.instructions.",
                "json_* sizes are json.dumps of the value (default ensure_ascii=True escapes non-ASCII); compact uses separators=(',',':').",
                "All traffic was offline-safe: loopback/metadata/file URLs rejected by the server SSRF guard, a nonexistent local path for parse, and search engines routed to a dead loopback proxy.",
            ],
        }
        write_text(BASELINE / "mcp_size_metrics.json",
                   json.dumps(metrics, indent=2, sort_keys=False, ensure_ascii=False) + "\n")

        # -- raw wire ---------------------------------------------------------
        wire = []
        for name, rec in phases:
            wire.append("### %s" % name)
            wire.append(_raw_wire_note(rec))
        for rec in calls:
            wire.append("### tools/call #%d tool=%s kind=%s label=%s" % (
                rec["seq"], rec.get("tool"), rec.get("kind"), rec.get("label")))
            wire.append(_raw_wire_note(rec))
        write_text(BASELINE / "mcp_raw_wire.txt", "\n".join(wire) + "\n")

        # -- initialize -------------------------------------------------------
        if phases:
            init = phases[0][1]
            if init.get("response_raw"):
                write_text(BASELINE / "mcp_initialize.json", init["response_raw"] + "\n")
            if init.get("response") is not None:
                write_text(BASELINE / "mcp_initialize_pretty.json",
                           json.dumps(init["response"], indent=2, sort_keys=False, ensure_ascii=False) + "\n")

        # -- tools/list -------------------------------------------------------
        if len(phases) > 2:
            trec = phases[2][1]
            if trec.get("response_raw"):
                # verbatim raw envelope (single line, byte-exact)
                write_text(BASELINE / "mcp_tools_list.json", trec["response_raw"] + "\n")
            if trec.get("response") is not None:
                write_text(BASELINE / "mcp_tools_list_pretty.json",
                           json.dumps(trec["response"], indent=2, sort_keys=False, ensure_ascii=False) + "\n")
            if tools_result:
                # explicit requirement: json.dumps(result, indent=2, sort_keys=False) verbatim
                write_text(BASELINE / "mcp_tools_list_result_pretty.json",
                           json.dumps(tools_result, indent=2, sort_keys=False, ensure_ascii=False))

        # -- tools/call -------------------------------------------------------
        envelopes = [rec["response"] for rec in calls if rec.get("response") is not None]
        call_meta = [
            {
                "seq": rec["seq"], "tool": rec.get("tool"), "kind": rec.get("kind"),
                "label": rec.get("label"), "arguments": rec.get("args"),
                "marker": rec.get("marker"), "duration_s": rec.get("duration_s"),
                "isError": (rec["response"] or {}).get("result", {}).get("isError")
                if isinstance((rec.get("response") or {}).get("result"), dict) else None,
            }
            for rec in calls
        ]
        write_text(BASELINE / "mcp_tools_call.json",
                   json.dumps(envelopes, indent=2, sort_keys=False, ensure_ascii=False) + "\n")
        # companion index (not part of the raw envelopes)
        write_text(BASELINE / "mcp_tools_call_index.json",
                   json.dumps(call_meta, indent=2, sort_keys=False, ensure_ascii=False) + "\n")

        # -- human log --------------------------------------------------------
        log = []
        log.append("dhole MCP baseline snapshot - raw per-call log")
        log.append("run_started : %s" % _dt.datetime.fromtimestamp(run_start).isoformat(timespec="seconds"))
        log.append("command     : %s  (cwd=%s)" % (" ".join(argv), REPO_ROOT))
        log.append("env         : DHOLE_HOME=%s" % env.get("DHOLE_HOME"))
        log.append("              PYTHONPATH=%s" % env.get("PYTHONPATH"))
        log.append("              DHOLE_SEARCH_PROXY=%s" % env.get("DHOLE_SEARCH_PROXY"))
        log.append("              offline socket guard: %s" % (SHIM_DIR / "sitecustomize.py"))
        log.append("protocol    : requested=%s returned=%s" % (
            PROTOCOL_VERSION, metrics.get("protocol_version_returned")))
        log.append("")
        for name, rec in phases:
            log.append("=" * 78)
            log.append("PHASE  %s   marker=%s  duration=%.3fs" % (name, rec.get("marker"), rec.get("duration_s") or 0.0))
            if rec.get("params") is not None:
                log.append("PARAMS: %s" % json.dumps(rec["params"], ensure_ascii=False))
            log.append(_raw_wire_note(rec))
            log.append("")
        for rec in calls:
            log.append("=" * 78)
            log.append("CALL %d/%d  tool=%s  kind=%s" % (rec["seq"], len(calls), rec.get("tool"), rec.get("kind")))
            log.append("LABEL : %s" % rec.get("label"))
            log.append("ARGS  : %s" % json.dumps(rec.get("args"), ensure_ascii=False))
            log.append("METRIC: marker=%s duration=%.3fs" % (rec.get("marker"), rec.get("duration_s") or 0.0))
            log.append(_raw_wire_note(rec))
            log.append("")
        write_text(BASELINE / "mcp_calls_log.txt", "\n".join(log) + "\n")

        # -- stderr ------------------------------------------------------------
        write_text(BASELINE / "mcp_server_stderr.log", stderr_text)

    # -- summary -------------------------------------------------------------
    oks = sum(1 for r in calls if isinstance((r.get("response") or {}).get("result"), dict)
              and not (r["response"]["result"].get("isError")))
    errs = sum(1 for r in calls if isinstance((r.get("response") or {}).get("result"), dict)
               and r["response"]["result"].get("isError"))
    touts = [r["seq"] for r in calls if r.get("marker")]
    print("")
    print("[snapshot] done in %.1fs" % (time.time() - run_start))
    print("[snapshot] tools/call: total=%d isError=True=%d isError=False=%d markers=%s"
          % (len(calls), errs, oks, touts or "none"))
    print("[snapshot] artifacts under %s" % BASELINE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
