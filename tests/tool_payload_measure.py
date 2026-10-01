"""Independently measure the MCP wire payload from the SOURCE, without importing it.

Why this exists: the wire payload size is published (README + CHANGELOG 16.0) and
drifts whenever a description or schema changes. The one cl100k_base run that was
actually made measured 2,141 / 19,254 / 21,395 chars as 514 / 4,631 / 5,145
tokens - a ratio of 4.16 chars per token, which is why the usual chars/4 (5.4k
today) and chars/3.88 (5.6k) rules of thumb both read high. Chars are the half
this script can produce offline; where the table has since grown, the token
figures quoted in the docs are estimates at that measured ratio, and this file
says so rather than re-quoting a count nobody re-measured.
Token counts are rendering-dependent, so the rendering is part of the number:
today's table is 19,141 chars as the wire's compact ``{"tools":[...]}``, 19,512
summed per-tool ``json.dumps`` defaults, 23,215 with ``indent=2``. An unstated
rendering is not reproducible.
There are two independent ways to get the real numbers:

  1. bind to the server over stdio and read the raw `initialize` / `tools/list`
     responses  -> tests/mcp_snapshot.py
  2. read the source constants directly -> this file

Method 2 deliberately does NOT import `dhole_mcp`: importing the package runs module
level code and can touch the filesystem. Instead it parses `server.py` with `ast`,
finds the module-level `DHOLE_INSTRUCTIONS` assignment and the `_TOOL_DEFS`
assignment, and reconstructs the exact payload with `ast.literal_eval` + `json.dumps`.
That makes this measurement independent of runtime state, HOME, env vars and network.

This is the diagnostic behind the connect-cost numbers in README and CHANGELOG.
The repo deliberately has no wire-size budget any more (`test_tool_descriptions.py`
only asserts content rules, not lengths) - so the way a number goes stale here is
a description getting longer without anyone re-measuring it, not a test going red.

Reported sizes:
  - character counts (exact, reproducible)
  - `len/4.16` as a rough token estimate - 4.16 is this payload's measured
    cl100k_base chars-per-token, not a general constant. The plain `len/4`
    reading is what put "≈5.5k tokens" into the docs for a 5.1k payload.
Token counts are NOT reported as authoritative because this script has no
tokenizer (the published numbers come from a separate cl100k_base run); only
characters are exactly comparable.

Usage (from anywhere):
    python tests/tool_payload_measure.py [--out DIR] [--with-output-schema]

Without ``--out`` it only prints; nothing is written into the repo.

``--with-output-schema`` is the one measurement that has to import the package
(see ``output_schema_report``): ``outputSchema`` is built at runtime from the
response models, so there is no literal for ``ast.literal_eval`` to read. It is
off by default so the rest of the run stays independent of runtime state.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER = REPO_ROOT / "src" / "dhole_mcp" / "server.py"

# Measured chars/token of THIS payload under cl100k_base, not a model-wide
# constant: it is what the rough estimates here have to divide by to agree with
# the out-of-band tokenizer run quoted in the docstring.
_TOKENS = 4.16


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


def find_frozenset(tree: ast.Module, name: str) -> frozenset:
    """Read a `frozenset({...})` (or plain set literal) module constant."""
    node = find_assignment(tree, name)
    if isinstance(node, ast.Call) and node.args:
        return frozenset(ast.literal_eval(node.args[0]))
    return frozenset(ast.literal_eval(node))


def duplication_report(tool_defs: list[dict], instructions: str) -> list[str]:
    """Every fact the agent is told twice, found mechanically.

    Why this is a report and not an assertion: a shared sentence is sometimes the
    safety mechanism (parse lists its extensions in the description AND in
    file_path on purpose, so a client that reads only the schema still learns
    what it can open). What this catches is the other kind - the same clause
    maintained in two places until one drifts.

    n-grams of 7-9 words are the unit: shorter overlaps are ordinary English,
    longer ones are copied sentences. Overlapping hits collapse into one cluster.
    """
    surfaces: dict[str, str] = {"INSTRUCTIONS": instructions}
    for td in tool_defs:
        surfaces[f"{td['name']}.desc"] = td.get("description", "")
        for key, spec in (td.get("inputSchema", {}).get("properties") or {}).items():
            if isinstance(spec, dict) and spec.get("description"):
                surfaces[f"{td['name']}.{key}"] = spec["description"]

    grams: dict[str, set] = {}
    for name, text in surfaces.items():
        words = re.findall(r"[a-z0-9_']+", re.sub(r"\s+", " ", text).lower())
        for size in (7, 8, 9):
            for i in range(len(words) - size + 1):
                grams.setdefault(" ".join(words[i:i + size]), set()).add(name)

    clusters: list[tuple[str, list]] = []
    for gram, owners in sorted(grams.items(), key=lambda kv: -len(kv[0])):
        if len(owners) < 2 or any(gram in done for done, _ in clusters):
            continue
        clusters.append((gram, sorted(owners)))
    lines = ["", "-- facts documented in more than one place --"]
    if not clusters:
        lines.append("none")
    for gram, owners in clusters:
        lines.append(f"  {owners} :: {gram[:100]}")
    return lines


def output_schema_report(compact_chars: int, instructions_chars: int) -> list[str]:
    """The connect-time cost of ``DHOLE_OUTPUT_SCHEMA=1``, asked of the server itself.

    Why this one measurement imports when the rest of the file deliberately does
    not: ``outputSchema`` is built at runtime from the response models
    (``_output_schema_for``), so there is no literal for ``ast.literal_eval`` to
    read. That is exactly how the README's ``DHOLE_OUTPUT_SCHEMA`` row went stale
    - the numbers were taken by hand once and nothing could re-take them.

    A failure to import is REPORTED, never dropped: a silently absent section
    reads as "the cost is zero", which is the wrong answer to publish.
    """
    lines = ["", "-- DHOLE_OUTPUT_SCHEMA=1 (asked of the server, not parsed) --"]
    sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        from dhole_mcp import server as _server
    except Exception as exc:  # noqa: BLE001 - report, never hide
        lines.append(f"SKIPPED: cannot import dhole_mcp.server "
                     f"({type(exc).__name__}: {exc})")
        return lines
    total, per = _server._enabled_output_schema()
    for tool, n in sorted(per.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {tool:14s} outputSchema={n}")
    with_schema = compact_chars + total
    base = compact_chars + instructions_chars
    lines.append(f"tools/list  with schema : {with_schema}  (+{total})")
    lines.append(f"connect total with schema: {with_schema + instructions_chars}  (+{total})")
    lines.append(f"connect-time delta       : +{total * 100 / base:.1f}%")
    return lines


def default_wire_report(tool_defs: list[dict], opt_in: frozenset,
                        parts: tuple[str, dict, str]) -> list[str]:
    """The payload an ordinary connect actually pays, next to the full literal.

    Why this section exists: `_TOOL_DEFS` is the source of truth for every tool
    the server CAN advertise, but it is no longer the set it DOES advertise -
    some tools are opt-in (``_OPT_IN_TOOLS``), so measuring the literal alone
    publishes a number the default wire never reaches. That is the same class of
    error as a stale hand-copied figure, one level up: the number is reproducible
    and still wrong about the thing it claims to describe.

    Both numbers are reported so neither can be quoted for the other.
    """
    intro, routing, rules = parts
    default_names = [td["name"] for td in tool_defs if td["name"] not in opt_in]
    lines = "".join(line for name, line in routing.items() if name in set(default_names))
    default_instructions = intro + (f"Routing:\n{lines}" if lines else "") + rules

    minimal = [
        {"name": td["name"], "description": td["description"],
         "inputSchema": td["inputSchema"],
         **({"annotations": td["annotations"]} if "annotations" in td else {})}
        for td in tool_defs if td["name"] in set(default_names)
    ]
    compact = len(json.dumps({"tools": minimal}, separators=(",", ":"),
                             ensure_ascii=False))

    out = ["", "-- default connect (what an ordinary client pays) --"]
    out.append(f"tools advertised     : {len(minimal)} of {len(tool_defs)}"
               f"  (opt-in: {sorted(opt_in)})")
    out.append(f"instructions chars   : {len(default_instructions)}")
    out.append(f"tools/list compact   : {compact}")
    out.append(f"connect total chars  : {compact + len(default_instructions)}"
               "  (instructions + compact tools/list)")
    out.append("-- turning the opt-in tools on (DHOLE_TASKS=1) --")
    out.append(f"tools/list delta     : +{compact_chars_for(tool_defs) - compact}")
    out.append(f"instructions delta   : +{len(instructions_full(parts)) - len(default_instructions)}")
    return out


def compact_chars_for(tool_defs: list[dict]) -> int:
    minimal = [
        {"name": td["name"], "description": td["description"],
         "inputSchema": td["inputSchema"],
         **({"annotations": td["annotations"]} if "annotations" in td else {})}
        for td in tool_defs
    ]
    return len(json.dumps({"tools": minimal}, separators=(",", ":"),
                          ensure_ascii=False))


def instructions_full(parts: tuple[str, dict, str]) -> str:
    intro, routing, rules = parts
    return intro + "Routing:\n" + "".join(routing.values()) + rules


def main() -> None:
    src = SERVER.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(SERVER))

    instructions = ast.literal_eval(find_assignment(tree, "DHOLE_INSTRUCTIONS"))
    tool_defs = ast.literal_eval(find_assignment(tree, "_TOOL_DEFS"))
    opt_in = find_frozenset(tree, "_OPT_IN_TOOLS")
    parts = (ast.literal_eval(find_assignment(tree, "_INSTRUCTIONS_INTRO")),
             ast.literal_eval(find_assignment(tree, "_INSTRUCTIONS_ROUTING")),
             ast.literal_eval(find_assignment(tree, "_INSTRUCTIONS_RULES")))

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
    out.append(f"chars/4.16 (rough tokens): {len(instructions) / _TOKENS:.1f}")
    out.append("")
    for label, payload in (("raw source payload", raw_payload), ("mcp-minimal payload", minimal_payload)):
        compact_chars, compact_text = size(payload, None, (",", ":"))
        pretty_chars, pretty_text = size(payload, 2)
        out.append(f"-- tools/list {label} --")
        out.append(f"compact json chars    : {compact_chars}")
        out.append(f"compact chars/4.16    : {compact_chars / _TOKENS:.1f}")
        out.append(f"indent=2 json chars   : {pretty_chars}")
        out.append(f"connect total chars   : {compact_chars + len(instructions)}"
                   "  (instructions + compact tools/list)")
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
    if "--with-output-schema" in sys.argv[1:]:
        minimal_compact, _ = size(minimal_payload, None, (",", ":"))
        out.extend(output_schema_report(minimal_compact, len(instructions)))
    out.extend(default_wire_report(tool_defs, opt_in, parts))
    out.extend(duplication_report(tool_defs, instructions))

    text = "\n".join(out) + "\n"
    if OUT is not None:
        (OUT / "tool_payload_measure.txt").write_text(text, encoding="utf-8")
        print("written under %s" % OUT)
    print(text)


if __name__ == "__main__":
    main()
