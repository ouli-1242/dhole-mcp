"""Independently measure the MCP wire payload from the SOURCE, without importing it.

Why this exists: the README publishes a token table (instructions 333 / tools/list
2,931 / total 3,264, measured at v14.6) and admits it has not been re-measured since.
The refactor must report the real numbers. There are two independent ways to get them:

  1. bind to the server over stdio and read the raw `initialize` / `tools/list`
     responses  -> tools/mcp_snapshot.py
  2. read the source constants directly -> this file

Method 2 deliberately does NOT import `dhole_mcp`: importing the package runs module
level code and can touch the filesystem. Instead it parses `server.py` with `ast`,
finds the module-level `DHOLE_INSTRUCTIONS` assignment and the `_TOOL_DEFS`
assignment, and reconstructs the exact payload with `ast.literal_eval` + `json.dumps`.
That makes this measurement independent of runtime state, HOME, env vars and network.

Reported sizes:
  - character counts (exact, reproducible)
  - `len/4` as a rough token estimate
Token counts are NOT reported as authoritative because the tokenizer the README
author used is not recorded anywhere in the repo; only characters are comparable.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

SERVER = Path("src/dhole_mcp/server.py")


def find_assignment(tree: ast.Module, name: str) -> ast.AST | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return node.value
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return node.value
    return None


def main() -> None:
    src = SERVER.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(SERVER))

    instructions = ast.literal_eval(find_assignment(tree, "DHOLE_INSTRUCTIONS"))
    tool_defs = ast.literal_eval(find_assignment(tree, "_TOOL_DEFS"))

    names = [td["name"] for td in tool_defs]

    # Shape the payload the way MCP does: tools/list returns {"tools": [...]}.
    # `Tool(**td)` may drop keys the SDK doesn't model, so two renderings are reported:
    # the raw source payload, and the schema+name+description subset MCP always sends.
    raw_payload = {"tools": tool_defs}
    minimal_payload = {
        "tools": [
            {
                "name": td["name"],
                "description": td["description"],
                "inputSchema": td["inputSchema"],
                **({"annotations": td["annotations"]} if "annotations" in td else {}),
            }
            for td in tool_defs
        ]
    }

    def size(obj: object, indent: int | None = None, sep: tuple[str, str] | None = None) -> tuple[int, str]:
        if indent is None:
            text = json.dumps(obj, indent=indent, separators=sep, ensure_ascii=False)
        else:
            text = json.dumps(obj, indent=indent, ensure_ascii=False)
        return len(text), text

    out: list[str] = []
    out.append("== tools/list payload, measured from source ==")
    out.append(f"tool count: {len(tool_defs)}")
    out.append(f"tool names (source order): {names}")
    out.append("")
    out.append("-- instructions (DHOLE_INSTRUCTIONS) --")
    out.append(f"characters           : {len(instructions)}")
    out.append(f"lines                : {instructions.count(chr(10)) + 1}")
    out.append(f"chars/4 (rough tokens): {len(instructions) / 4:.1f}")
    out.append("")
    for label, payload in (("raw source payload", raw_payload), ("mcp-minimal payload", minimal_payload)):
        compact_chars, compact_text = size(payload, None, (",", ":"))
        pretty_chars, pretty_text = size(payload, 2)
        out.append(f"-- tools/list {label} --")
        out.append(f"compact json chars    : {compact_chars}")
        out.append(f"compact chars/4       : {compact_chars / 4:.1f}")
        out.append(f"indent=2 json chars   : {pretty_chars}")
        out.append("")
        if label == "mcp-minimal payload":
            Path("refactor_artifacts/baseline/tools_list_from_source.min.json").write_text(
                compact_text, encoding="utf-8"
            )
            Path("refactor_artifacts/baseline/tools_list_from_source.pretty.json").write_text(
                pretty_text, encoding="utf-8"
            )
    out.append("-- per-tool compact size (mcp-minimal shape) --")
    total = 0
    for td in minimal_payload["tools"]:
        n, _ = size(td, None, (",", ":"))
        total += n
        params = td["inputSchema"].get("properties", {})
        required = td["inputSchema"].get("required", [])
        out.append(f"{td['name']:14s} chars={n:5d} params={len(params):2d} required={required} ann={td.get('annotations')}")
    out.append(f"sum(per-tool)         : {total}")

    text = "\n".join(out) + "\n"
    Path("refactor_artifacts/baseline/tool_payload_measure.txt").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
