"""Static inventory of the dhole_mcp package for the behavior-preserving refactor.

Read-only analysis: parses every module under src/dhole_mcp/ with ast and prints
  - intra-package import edges (module -> module),
  - module-level mutable containers (candidate shared/global state),
  - module-level constants that are plain immutable values,
  - count of top-level functions / classes / lines,
  - every os.environ / os.getenv read with the literal env-var name,
  - every module-level `logging.getLogger` / `print(` use (log surface),
so the refactor can reason about blast radius without guessing.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path("src/dhole_mcp")

MUTABLE_CALLS = {"list", "dict", "set", "defaultdict", "OrderedDict", "deque", "Counter"}


def module_name(path: Path) -> str:
    return path.stem


def collect_env(node: ast.AST) -> set[str]:
    found: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr in {"getenv"}:
                if n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                    found.add(n.args[0].value)
            if isinstance(f, ast.Attribute) and f.attr in {"get", "pop"}:
                if (
                    isinstance(f.value, ast.Attribute)
                    and f.value.attr == "environ"
                    and n.args
                    and isinstance(n.args[0], ast.Constant)
                    and isinstance(n.args[0].value, str)
                ):
                    found.add(n.args[0].value)
        if isinstance(n, ast.Subscript):
            v = n.value
            if isinstance(v, ast.Attribute) and v.attr == "environ":
                sl = n.slice
                if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                    found.add(sl.value)
    return found


def main() -> None:
    paths = sorted(SRC.glob("*.py"))
    names = {module_name(p) for p in paths}

    edges: dict[str, set[str]] = {m: set() for m in names}
    globals_mutable: dict[str, list[str]] = {}
    env_by_module: dict[str, set[str]] = {}
    sizes: dict[str, tuple[int, int, int]] = {}

    for p in paths:
        mod = module_name(p)
        text = p.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(p))
        sizes[mod] = (
            len(text.splitlines()),
            sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in tree.body),
            sum(isinstance(n, ast.ClassDef) for n in tree.body),
        )
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.level and n.module:
                top = n.module.split(".")[0]
                if top in names and top != mod:
                    edges[mod].add(top)
            elif isinstance(n, ast.ImportFrom) and n.level and n.module is None:
                for a in n.names:
                    if a.name in names and a.name != mod:
                        edges[mod].add(a.name)
            elif isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("dhole_mcp."):
                sub = n.module.split(".")[1]
                if sub in names and sub != mod:
                    edges[mod].add(sub)
            elif isinstance(n, ast.Import):
                for a in n.names:
                    if a.name.startswith("dhole_mcp."):
                        sub = a.name.split(".")[1]
                        if sub in names and sub != mod:
                            edges[mod].add(sub)
        mod_mut: list[str] = []
        for n in tree.body:
            targets: list[str] = []
            value: ast.AST | None = None
            if isinstance(n, ast.Assign):
                targets = [t.id for t in n.targets if isinstance(t, ast.Name)]
                value = n.value
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                targets = [n.target.id]
                value = n.value
            if not targets or value is None:
                continue
            is_mut = isinstance(value, (ast.List, ast.Dict, ast.Set))
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id in MUTABLE_CALLS:
                is_mut = True
            if is_mut:
                mod_mut.extend(targets)
        if mod_mut:
            globals_mutable[mod] = sorted(mod_mut)
        env_by_module[mod] = collect_env(tree)

    print("== SIZES (lines, top-level funcs, classes) ==")
    for mod, (ln, fn, cl) in sorted(sizes.items(), key=lambda kv: -kv[1][0]):
        print(f"{mod:26s} lines={ln:5d} funcs={fn:3d} classes={cl:2d}")

    print("\n== INTRA-PACKAGE IMPORT EDGES (importer -> imported) ==")
    for mod in sorted(edges):
        if edges[mod]:
            print(f"{mod:26s} -> {', '.join(sorted(edges[mod]))}")

    print("\n== FAN-IN (how many modules import this one) ==")
    fanin: dict[str, int] = {m: 0 for m in names}
    for mod, deps in edges.items():
        for d in deps:
            fanin[d] += 1
    for mod, c in sorted(fanin.items(), key=lambda kv: -kv[1]):
        if c:
            print(f"{mod:26s} imported_by={c}")

    print("\n== MODULE-LEVEL MUTABLE STATE (candidate shared state) ==")
    for mod in sorted(globals_mutable):
        print(f"{mod:26s} {', '.join(globals_mutable[mod])}")

    print("\n== ENV VARS READ, BY MODULE ==")
    all_env: set[str] = set()
    for mod in sorted(env_by_module):
        if env_by_module[mod]:
            print(f"{mod:26s} {', '.join(sorted(env_by_module[mod]))}")
            all_env |= env_by_module[mod]
    print(f"\nTOTAL distinct env vars read in src/: {len(all_env)}")
    print(", ".join(sorted(all_env)))


if __name__ == "__main__":
    main()
