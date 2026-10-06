"""Independently measure the MCP wire payload from the SOURCE, without importing it.

Why this exists: the wire payload size is published (README + CHANGELOG 16.0) and
drifts whenever a description or schema changes. The one cl100k_base run that was
actually made measured 2,141 / 19,254 / 21,395 chars as 514 / 4,631 / 5,145
tokens - a ratio of 4.16 chars per token. Chars are the half this script can
produce offline; the token figures quoted in the docs are estimates at that
measured ratio, and this file says so rather than re-quoting a count nobody
re-measured.

Token counts are rendering-dependent, so the rendering is part of the number.
Every table size below is **dated** — a number without a date reads as the
present and rots silently (this header itself rotted once: it claimed
"today's table is 19,141 chars" from a 13-tool era long past). On 2026-10-07:
2,486 instructions + 28,540 compact tools/list = 31,026 connect. An unstated
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
The wire-size budget is alive, not gone: `CONNECT_CEILING` in
`tests/test_wire_moves_land_in_the_manual.py` (connect total, only down) plus
the two shape ratchets in `tests/test_wire_shape.py` (per-tool params,
per-description chars) — so a stale number here is a test going red, not just
a description getting longer without anyone re-measuring it.

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
PKG = SERVER.parent

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


_TREES: list[ast.Module] | None = None


def _package_trees() -> list[ast.Module]:
    """server.py plus every module that has been split out of it (17.0 B6).

    `_TOOL_DEFS` still lives in server.py, but a description it points at may now
    be a constant in rungs.py / quality.py / models.py. Reading only server.py
    made such a reference unresolvable - and this script's whole contract is to
    refuse rather than publish a payload smaller than the wire really is. The
    fix is to look in the module the constant actually lives in, not to relax
    the refusal.
    """
    global _TREES
    if _TREES is None:
        _TREES = []
        for path in sorted(PKG.glob("*.py")):
            try:
                _TREES.append(ast.parse(path.read_text(encoding="utf-8"),
                                        filename=str(path)))
            except (OSError, SyntaxError):
                continue
    return _TREES


def find_frozenset(tree: ast.Module, name: str) -> frozenset:
    """Read a `frozenset({...})` (or plain set literal) module constant."""
    node = find_assignment(tree, name)
    if isinstance(node, ast.Call) and node.args:
        return frozenset(ast.literal_eval(node.args[0]))
    return frozenset(ast.literal_eval(node))


def _string_constants(tree: ast.Module) -> dict:
    """Every ``NAME = "literal"`` the payload can point at, module level or class body."""
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            for t in targets:
                if isinstance(t, ast.Name):
                    found.setdefault(t.id, value.value)
    return found


def literal_node(tree: ast.Module, name: str):
    """``ast.literal_eval`` the assignment, after inlining string constants.

    The script never imports the package (importing runs module-level code and can
    touch the filesystem), so ``_TOOL_DEFS`` has to be readable as a literal. A
    description that lives in its own constant is a ``Name`` node, and
    ``literal_eval`` refuses those - inline them, and refuse loudly if a node still
    isn't a literal, because silently skipping it would understate the wire.
    """
    constants = _string_constants(tree)
    for other in _package_trees():
        if other is tree:
            continue
        for key, value in _string_constants(other).items():
            constants.setdefault(key, value)
    unresolved: list[str] = []

    class _Inline(ast.NodeTransformer):
        def visit_Name(self, node):
            if node.id in constants:
                return ast.copy_location(ast.Constant(value=constants[node.id]), node)
            unresolved.append(node.id)
            return node

    node = find_assignment(tree, name)
    if node is None:
        for other in _package_trees():
            node = find_assignment(other, name)
            if node is not None:
                break
    node = _Inline().visit(node)
    ast.fix_missing_locations(node)
    if unresolved:
        raise SystemExit(
            f"{name} references {sorted(set(unresolved))}, which is not a string "
            f"literal this script can read; the payload would come out smaller "
            f"than the wire really is")
    return ast.literal_eval(node)


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


def _wire_for(tool_defs: list[dict], names: set, parts: tuple[str, dict, str]) -> tuple[int, int]:
    """(compact tools/list chars, instructions chars) for an enabled name set."""
    intro, routing, rules = parts
    lines = "".join(line for name, line in routing.items() if name in names)
    instr = intro + (f"Routing:\n{lines}" if lines else "") + rules
    minimal = [
        {"name": td["name"], "description": td["description"],
         "inputSchema": td["inputSchema"],
         **({"annotations": td["annotations"]} if "annotations" in td else {})}
        for td in tool_defs if td["name"] in names
    ]
    compact = len(json.dumps({"tools": minimal}, separators=(",", ":"),
                             ensure_ascii=False))
    return compact, len(instr)


def default_wire_report(tool_defs: list[dict],
                        parts: tuple[str, dict, str]) -> list[str]:
    """The payload an ordinary connect actually pays, next to the full literal.

    17.0: there are no opt-in groups — the default wire IS every tool, so the
    default connect total and the full literal coincide. This section stays
    because the two numbers can drift apart again the moment someone adds a
    filter, and measuring only the literal would then publish a figure the
    default wire never reaches.
    """
    all_names = {td["name"] for td in tool_defs}
    compact, instr_chars = _wire_for(tool_defs, all_names, parts)

    out = ["", "-- default connect (what an ordinary client pays) --"]
    out.append(f"tools advertised     : {len(all_names)} of {len(tool_defs)}"
               "  (no opt-in groups in 17.0)")
    out.append(f"instructions chars   : {instr_chars}")
    out.append(f"tools/list compact   : {compact}")
    out.append(f"connect total chars  : {compact + instr_chars}"
               "  (instructions + compact tools/list)")
    return out


def budget_report(tool_defs: list[dict], top: int = 20) -> list[str]:
    """把 connect 面的字符拆开算在谁头上：工具描述、参数描述、还是 schema 骨架。

    瘦身的账要算在对的那一块上：一颗 900 字符的参数说明搬进 sys_guide 就省 900，
    而注册工具的键名 + 类型 + required 那一层骨架压不出多少。同理，**重复**是
    唯一一种不用牺牲信息就能拿回来的量 —— 同一句话在三颗工具里各写一遍，
    下线其中两遍不减少任何人能查到东西。
    """
    out: list[str] = ["", "-- where the connect chars go (per description) --"]
    rows: list[tuple[int, str, str]] = []
    tool_desc = 0
    param_desc = 0
    for td in tool_defs:
        d = td.get("description") or ""
        tool_desc += len(d)
        for name, spec in (td.get("inputSchema", {}).get("properties", {}) or {}).items():
            pd = spec.get("description") or ""
            param_desc += len(pd)
            rows.append((len(pd), f"{td['name']}.{name}", pd))
    out.append(f"tool descriptions    : {tool_desc}")
    out.append(f"param descriptions   : {param_desc}")
    out.append("largest single descriptions:")
    for n, key, text in sorted(rows, reverse=True)[:top]:
        out.append(f"  {n:5d}  {key:34s} {text[:58].replace(chr(10), ' ')}")
    by_text: dict[str, list[str]] = {}
    for n, key, text in rows:
        if n >= 120:            # 短句重复不是可省的量，只有成段的知识才是
            by_text.setdefault(text, []).append(key)
    dup = [(len(t), sorted(ks)) for t, ks in by_text.items() if len(ks) > 1]
    dup.sort(reverse=True)
    if dup:
        out.append(f"identical param text in more than one tool ({len(dup)} 段，"
                   "每一段只留一处即可):")
        for n, ks in dup[:8]:
            out.append(f"  {n:5d} × {len(ks)}  {', '.join(ks)}")
    return out


def bulk_payload_report() -> list[str]:
    """结果面的尺子：一次调用往线上发多少字符的**正文与样板**（不是 tools/list）。

    连接面一直有棘轮（`CONNECT_CEILING`），结果面没有过任何可复用的尺子 —— 于是
    2026-10-05 实测里那两件事都没被量到：一次 `web_read(urls=[20 颗])` 把同一段
    273–361 字的 SSRF 判词印了 20 份（5.5–7.2KB 样板），而一次默认爬取交回 223,599
    字符（约 81k token）被宿主截断落盘。历史病例参照 crawl.py 的序列化器注释：
    docs.vllm.ai 的 1000 行 URL 图 = 353KB JSON / 70KB URL = 5.0×。

    这里量的是形状而不是真抓取：一行 `ResponseModel` 用真实字段的典型长度拼出来，
    20 行就是那一次批量会付的量；重复检测用 `duplication_report` 的同一条规矩
    （≥120 字符的整段重复才算样板，短句重复省不下什么）。
    """
    sys.path.insert(0, str(REPO_ROOT / "src"))
    lines = ["", "-- result side (a call's own payload, not tools/list) --"]
    try:
        from dhole_mcp.crawl import CrawlPage, CrawlResponseModel
        from dhole_mcp.models import BulkResponseModel, ResponseModel
        from dhole_mcp.wire import _wire_dump
    except Exception as exc:  # noqa: BLE001 - report, never hide
        lines.append(f"SKIPPED: cannot import the response models "
                     f"({type(exc).__name__}: {exc})")
        return lines

    body = ("# Title\n\n" + " ".join(
        f"sentence {i} carrying a plausible amount of page text" for i in range(300)))

    def _row(i: int, note: str = "") -> ResponseModel:
        meta = {"title": f"Page {i}", "description": "x" * 60}
        if note:
            meta["ssrf_recheck"] = note
        return ResponseModel(url=f"https://a.test/{i}", status=200,
                             content=[body], fetcher_used="http",
                             extracted_type="markdown", content_ok=True,
                             total_extracted_chars=len(body), metadata=meta)

    note = ("DNS recheck skipped on this machine: the resolver answers every public "
            "name with an address in 198.18.0.0/15 (fake-ip proxy). IP-literal, "
            "hostname and cloud-metadata guards still applied.")
    for count in (1, 20):
        wired = _wire_dump(BulkResponseModel(
            results=[_row(i, note) for i in range(count)],
            total=count, successful=count))
        text = json.dumps(wired, ensure_ascii=False, separators=(",", ":"))
        repeats = text.count("DNS recheck skipped")
        lines.append(f"BulkResponseModel rows={count:2d}  chars={len(text):,}  "
                     f"ssrf_recheck occurrences={repeats}")
        if count > 1 and repeats > 1:
            lines.append(f"  BOILERPLATE: the same sentence ships {repeats}x "
                         f"({(repeats - 1) * len(note):,} spare chars) - it belongs "
                         "once on the envelope")

    crawl_pages = [
        CrawlPage(url=f"https://a.test/{i}", status=200, content_ok=True,
                  content=[body[:8000]], content_chars=min(len(body), 8000))
        for i in range(40)]
    big = CrawlResponseModel(start_url="https://a.test", pages=crawl_pages,
                             pages_crawled=len(crawl_pages))
    n = len(json.dumps(_wire_dump(big), ensure_ascii=False, separators=(",", ":")))
    lines.append(f"CrawlResponseModel 40 pages x 8,000 chars -> {n:,} chars "
                 f"({n / _TOKENS:,.0f} rough tokens) - the shape a default-sized "
                 "crawl pays without max_total_chars")
    return lines


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        # 这份报告会打印中文小节名与描述样本。Windows 控制台默认 cp936，于是
        # "省了多少字"旁边是一串乱码 —— 一把读不出字的尺子等于一把会说谎的尺子。
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    src = SERVER.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(SERVER))

    instructions = literal_node(tree, "DHOLE_INSTRUCTIONS")
    tool_defs = literal_node(tree, "_TOOL_DEFS")
    parts = (literal_node(tree, "_INSTRUCTIONS_INTRO"),
             literal_node(tree, "_INSTRUCTIONS_ROUTING"),
             literal_node(tree, "_INSTRUCTIONS_RULES"))

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
    out.extend(default_wire_report(tool_defs, parts))
    out.extend(duplication_report(tool_defs, instructions))
    out.extend(bulk_payload_report())
    out.extend(budget_report(tool_defs))

    text = "\n".join(out) + "\n"
    if OUT is not None:
        (OUT / "tool_payload_measure.txt").write_text(text, encoding="utf-8")
        print("written under %s" % OUT)
    print(text)


if __name__ == "__main__":
    main()
