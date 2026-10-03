"""Stand-in for the dhole MCP server in the skill-accuracy measurement.

The MCP server is not attached to the harness session, so a subagent cannot call
``web_read`` through it. This script routes a JSON argument blob to the real
``MasterFetchServer._dispatch`` and prints the exact ``TextContent`` payload an
MCP client would receive — the agent's *argument choices* are what we measure,
and those are unaffected by the plumbing.

Every call is appended to ``--record`` as JSONL so the grader reads the server-
side truth rather than the subagent's recollection of what it sent.

  python evals/dhole_eval_cli.py --record out/calls.jsonl '{"url":"...","nested_selector":"a"}'
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    # The console on Windows defaults to the machine codepage (GBK here), and a
    # page body is not ASCII: three calibration runs lost calls to
    # `UnicodeEncodeError: 'gbk' codec can't encode character '\u2764'` — a crash
    # in THIS wrapper, not in dhole, and one the agent cannot tell apart from a
    # tool failure. Replace rather than raise: a mangled glyph in a transcript is
    # noise, a crashed harness is a wrong measurement.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", default="")
    ap.add_argument("--tool", default="web_read")
    ap.add_argument("args_json")
    ns = ap.parse_args()

    try:
        args = json.loads(ns.args_json)
    except json.JSONDecodeError as e:
        print(f"BAD_JSON: {e}", file=sys.stderr)
        return 2

    from dhole_mcp.server import MasterFetchServer

    t0 = time.time()
    server = MasterFetchServer()
    err = None
    try:
        result = asyncio.run(server._dispatch(ns.tool, args))
    except BaseException as e:  # noqa: BLE001 — recorded, then surfaced below
        err = f"{type(e).__name__}: {e}"
        result = None
    elapsed = round(time.time() - t0, 2)

    parts = result if isinstance(result, list) else (result[0] if isinstance(result, tuple) else [])
    text = "\n".join(getattr(p, "text", "") for p in parts)

    if ns.record:
        Path(ns.record).parent.mkdir(parents=True, exist_ok=True)
        with open(ns.record, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "tool": ns.tool, "args": args, "seconds": elapsed,
                "error": err, "chars": len(text),
                "text_head": text[:60000],
            }, ensure_ascii=False) + "\n")

    if err:
        print(f"CALL_FAILED: {err}", file=sys.stderr)
        return 1
    # A sidecar copy of the SAME text: the Windows console decodes this pipe with
    # the machine codepage, so CJK bodies arrive to the caller as mojibake even
    # though the bytes are correct. Reading the file is the way to see what the
    # tool actually said.
    if ns.record:
        try:
            Path(ns.record).with_suffix(".last.txt").write_text(text, encoding="utf-8")
        except OSError:
            pass
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
