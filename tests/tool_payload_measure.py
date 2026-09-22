"""Independently measure the MCP wire payload from the SOURCE, without importing it.

Why this exists: the wire payload size is published (CHANGELOG 15.0: instructions
339 / tools/list 2,911 / connect 3,250 cl100k_base; README keeps the one-line
summary) and drifts whenever a description or schema changes, and chars are the
comparable half.
The published token counts use a specific rendering - reproduce them exactly as:
per tool, ``len(tiktoken.get_encoding("cl100k_base").encode(json.dumps(tool)))``
with **json.dumps defaults** (separators ", "/": ", ensure_ascii=True), summed;
instructions counted once the same way. Compact / indent=2 renderings give
different numbers (2,561 / 3,266 for the same payload), so an unstated rendering
is not reproducible.
There are two independent ways to get the real numbers:

  1. bind to the server over stdio and read the raw `initialize` / `tools/list`
     responses  -> tests/mcp_snapshot.py
  2. read the source constants directly -> this file

Method 2 deliberately does NOT import `dhole_mcp`: importing the package runs module
level code and can touch the filesystem. Instead it parses `server.py` with `ast`,
finds the module-level `DHOLE_INSTRUCTIONS` assignment and the `_TOOL_DEFS`
assignment, and reconstructs the exact payload with `ast.literal_eval` + `json.dumps`.
That makes this measurement independent of runtime state, HOME, env vars and network.

This is the diagnostic for the wire-size budgets in `tests/test_tool_descriptions.py`:
when a budget fails, this prints the exact per-tool numbers it is comparing.

Reported sizes:
  - character counts (exact, reproducible)
  - `len/4` as a rough token estimate
Token counts are NOT reported as authoritative because this script has no
tokenizer (the README's numbers come from a separate cl100k_base run); only
characters are exactly comparable.

Usage (from anywhere):
    python tests/tool_payload_measure.py [--out DIR]

Without ``--out`` it only prints; nothing is written into the repo.
"""

from __future__ import annotations

import ast
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER = REPO_ROOT / "src" / "dhole_mcp" / "server.py"


def _output_dir(argv: list[str]) -> pathlib.Path | None:
    if "--out" not in argv:
        return None
    i = argv.index("--out")
    if i + 1 >= len(argv):
        raise SystemExit("--out needs a directory")
    d = pathlib.Path(argv[i + 1]).expanduser().resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


OUT = _output_dir(sys.argv[1:])


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
        if label == "mcp-minimal payload" and OUT is not None:
            (OUT / "tools_list_from_source.min.json").write_text(compact_text, encoding="utf-8")
            (OUT / "tools_list_from_source.pretty.json").write_text(pretty_text, encoding="utf-8")
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
    if OUT is not None:
        (OUT / "tool_payload_measure.txt").write_text(text, encoding="utf-8")
        print("written under %s" % OUT)
    print(text)


if __name__ == "__main__":
    main()
