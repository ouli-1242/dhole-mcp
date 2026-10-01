"""W1 · anti-drift tests for the env registry.

The registry is a third copy of env knowledge (code + configuration.md + this
file). These tests are the only thing keeping it from becoming a liability -
see plan §3 W1 "风险" and §2 P2. Every one of the five checks is load-bearing;
deleting one to make the test suite pass is not the fix.

W1.4 relaxes the plan's literal wording: the plan says the read site "must not
be inside any FunctionDef", which would reject DHOLE_TOOLS / DHOLE_TASKS /
DHOLE_VAULT - they are read inside ``_parse_enabled_tools`` but the result is
assigned to a module-level constant. Semantically those ARE import-time reads
(changing them requires restarting the host). The AST test here accepts either
a module-level read OR a read inside a function that IS called at module level
in the same file. That matches W2's contract: ``read_at`` is a *restart* hint,
not a syntactic position.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from dhole_mcp import env_registry

SRC_ROOT = Path(env_registry.__file__).parent
DOC_PATH = (SRC_ROOT / "skills" / "dhole-web" / "references" / "configuration.md")

ENV_LITERAL = re.compile(r"^DHOLE_[A-Z0-9_]+$")


# ─── helpers ────────────────────────────────────────────────────────────
def _py_files() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _string_constants(tree: ast.AST) -> list[tuple[str, int]]:
    """(value, lineno) for every ``str`` constant in the tree.

    Only ``ast.Constant`` with ``str`` value counts - ``ast.Name`` (e.g. the
    ``DHOLE_INSTRUCTIONS`` module constant) and dict identifiers (e.g.
    ``_DHOLE_TO_BACKEND``) are excluded structurally, which is the whole point
    of AST-scanning instead of text grep.
    """
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append((node.value, getattr(node, "lineno", -1)))
    return out


def _module_level_call_names(tree: ast.Module) -> set[str]:
    """Names of functions *called from* the module body (top-level).

    Skips ``def``/``async def``/``class`` statements themselves - a call inside
    a function body is not "module level", only calls that run on import are.
    """
    names: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                names.add(node.func.id)
    return names


def _literal_sites(tree: ast.AST, value: str) -> list[tuple[int, str | None]]:
    """Every occurrence of ``value`` as a string constant, tagged with the
    nearest enclosing function name (or ``None`` at module level).
    """
    sites: list[tuple[int, str | None]] = []

    def walk(node: ast.AST, current_func: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if (isinstance(child, ast.Constant)
                    and isinstance(child.value, str)
                    and child.value == value):
                sites.append((getattr(child, "lineno", -1), current_func))
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, child.name)
            else:
                walk(child, current_func)

    walk(tree, None)
    return sites


# ─── W1.1 positive anti-drift ───────────────────────────────────────────
def test_w1_1_every_env_string_literal_in_src_is_registered():
    """Every ``"DHOLE_..."`` string constant in src/ must be registered.

    Whitelist is EMPTY by design (plan W1.1). If this test starts failing,
    the fix is to add the missing entry to env_registry.ALL, not to teach the
    test to tolerate the drift.
    """
    registered = {e.name for e in env_registry.ALL}
    observed: dict[str, list[Path]] = {}
    for path in _py_files():
        tree = _parse(path)
        for value, _lineno in _string_constants(tree):
            if ENV_LITERAL.match(value) and value not in registered:
                observed.setdefault(value, []).append(path)
    assert not observed, (
        f"unregistered DHOLE_* string literals: "
        f"{ {name: [str(p.relative_to(SRC_ROOT)) for p in files] for name, files in observed.items()} }"
    )


def test_w1_1_ast_approach_excludes_non_env_names():
    """``DHOLE_INSTRUCTIONS`` (module constant) and ``_DHOLE_TO_BACKEND``
    (dict name) are ``ast.Name`` nodes, never ``ast.Constant`` - the AST scan
    must not report them, so no text-regex whitelist is needed.
    """
    server_file = SRC_ROOT / "server.py"
    metasearch_file = SRC_ROOT / "search_metasearch.py"
    literals_server = {v for v, _ in _string_constants(_parse(server_file))}
    literals_metasearch = {v for v, _ in _string_constants(_parse(metasearch_file))}
    assert "DHOLE_INSTRUCTIONS" not in literals_server
    assert "_DHOLE_TO_BACKEND" not in literals_metasearch
    assert "DHOLE_TO_BACKEND" not in literals_metasearch | literals_server


# ─── W1.2 reverse anti-drift ────────────────────────────────────────────
def test_w1_2_every_registry_name_is_read_as_string_in_src():
    """A registry entry that no reader in src/ actually parses is dead weight -
    either the code was removed or the name was made up. Fail loudly.
    """
    literals: set[str] = set()
    for path in _py_files():
        for value, _lineno in _string_constants(_parse(path)):
            if ENV_LITERAL.match(value):
                literals.add(value)
    missing = sorted({e.name for e in env_registry.ALL} - literals)
    assert not missing, f"registry names never appear as a string constant in src/: {missing}"


# ─── W1.3 no duplicates ─────────────────────────────────────────────────
def test_w1_3_registry_has_no_duplicate_names():
    names = [e.name for e in env_registry.ALL]
    assert len(names) == len(set(names))


def test_registry_covers_all_39_env_vars():
    """Plan §1.1 pins the count at 39; a 40th env var must go through the
    plan review, and a dropped one must fail this before someone's doctor
    output lies about coverage.
    """
    assert len(env_registry.ALL) == 39, (
        f"expected 39 env vars, found {len(env_registry.ALL)}: "
        f"{sorted(e.name for e in env_registry.ALL)}"
    )


# ─── W1.4 read_at correctness ───────────────────────────────────────────
def test_w1_4_import_time_reads_reachable_from_module_body():
    """Every entry tagged ``read_at="import"`` must have at least one read
    that runs at import: either a literal at module level, or a literal
    inside a function that is called at module level in the same file.
    """
    trees: dict[Path, ast.Module] = {p: _parse(p) for p in _py_files()}
    module_calls: dict[Path, set[str]] = {
        p: _module_level_call_names(t) for p, t in trees.items()
    }

    for entry in env_registry.ALL:
        if entry.read_at != "import":
            continue
        hit = False
        for path, tree in trees.items():
            for _lineno, enclosing in _literal_sites(tree, entry.name):
                if enclosing is None:
                    hit = True
                    break
                if enclosing in module_calls[path]:
                    hit = True
                    break
            if hit:
                break
        assert hit, (
            f"{entry.name} claims read_at='import' but no read site is "
            f"reachable from a module body statement"
        )


# ─── W1.5 documentation coverage ────────────────────────────────────────
def test_w1_5_every_registry_name_appears_in_configuration_doc():
    """Plan W1.5 explicitly predicts this to fail on DHOLE_UPDATE_PACKAGE and
    DHOLE_UPDATE_INDEX_URL - W6.4 closes the gap. Until then this test is a
    scoreboard, not a gate on the W1 commit alone.
    """
    text = DOC_PATH.read_text(encoding="utf-8")
    missing = [e.name for e in env_registry.ALL if e.name not in text]
    assert not missing, (
        f"configuration.md is missing entries for: {missing}"
    )


# ─── registry-shape guards ───────────────────────────────────────────
def test_secret_kind_never_default_echos_value():
    """The four API-key entries are the ``secret`` kind. Their ``default`` is
    a marker string, not a value - W2's config audit depends on it.
    """
    secrets = [e for e in env_registry.ALL if e.kind == "secret"]
    assert len(secrets) == 4
    for entry in secrets:
        assert entry.default == "", (
            f"{entry.name} is secret but has a non-empty default - W2 will "
            f"print it"
        )
        # display_value on any raw must never contain the raw
        sample = "hunter2-super-secret-token"
        assert sample not in entry.display_value(sample)
        assert entry.display_value(sample).startswith("set (len=")


def test_fail_safe_closed_matches_plan_d1_gate_list():
    """Plan §A.4 names exactly 4 gates as ``fail_safe=closed`` (D1 scope)."""
    closed = sorted(e.name for e in env_registry.ALL if e.fail_safe == "closed")
    assert closed == sorted([
        "DHOLE_ALLOW_PRIVATE_HOSTS",
        "DHOLE_CDP_ATTACH",
        "DHOLE_IGNORE_ROBOTS",
        "DHOLE_SSRF_DNS_RECHECK",
    ])


def test_read_at_import_matches_plan_a1():
    """Plan §A.1 enumerates 11 import-time reads. A drift here means W2's
    "restart the host" line either fires on a call-time var (noise) or
    stays silent on an import-time var (misleading).
    """
    imports = sorted(e.name for e in env_registry.ALL if e.read_at == "import")
    assert imports == sorted([
        "DHOLE_BROWSER_IDLE_TIMEOUT",
        "DHOLE_BRIGHTDATA_COUNTRY",
        "DHOLE_BRIGHTDATA_ZONE",
        "DHOLE_DEFAULT_CONTENT_CHARS",
        "DHOLE_OUTPUT_SCHEMA",
        "DHOLE_SEARCH_DEADLINE",
        "DHOLE_STRUCTURED_CONTENT",
        "DHOLE_TASKS",
        "DHOLE_TOOLS",
        "DHOLE_VAULT",
        "DHOLE_WIRE_FULL",
    ])


# ─── W1 helpers ──────────────────────────────────────────────────────
def test_by_name_round_trips_every_entry():
    for entry in env_registry.ALL:
        assert env_registry.by_name(entry.name) is entry
    assert env_registry.by_name("DHOLE_NOT_A_REAL_VAR") is None


def test_scenario_labels_are_nonempty_and_stable():
    """W2 groups config by scenario. An empty or drifting label would break
    the section headers and make the doctor output feel arbitrary.
    """
    for entry in env_registry.ALL:
        assert entry.scenario and entry.scenario.strip() == entry.scenario
        # summary is user-facing; one short sentence is enough
        assert entry.summary and entry.summary.strip() == entry.summary
        assert len(entry.summary) < 160


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("YES", True), ("on", True),
    ("0", False), ("false", False), ("No", False), ("off", False),
    ("none", False), ("NULL", False), ("", False),
])
def test_read_flag_recognised_tokens_do_not_warn(value, expected):
    """W4.2: a recognised token must read the same as before AND not add a
    warning - the warning list is only for genuinely unrecognised values.
    """
    env_registry.clear_warnings()
    import os
    os.environ["DHOLE_W4_TEST_FLAG"] = value
    try:
        got = env_registry.read_flag("DHOLE_W4_TEST_FLAG")
        assert got == expected
        assert env_registry.warnings_snapshot() == ()
    finally:
        del os.environ["DHOLE_W4_TEST_FLAG"]
        env_registry.clear_warnings()


def test_read_flag_non_safe_keeps_env_flag_semantics_but_warns():
    """W4.1: unrecognised -> ON (matches ``_env_flag``). The new part is only
    that a warning is recorded.
    """
    env_registry.clear_warnings()
    import os
    os.environ["DHOLE_W4_TEST_FLAG"] = "yes-please"
    try:
        assert env_registry.read_flag("DHOLE_W4_TEST_FLAG") is True
        snaps = env_registry.warnings_snapshot()
        assert len(snaps) == 1
        assert snaps[0].name == "DHOLE_W4_TEST_FLAG"
        assert "yes-please" in snaps[0].message
        assert "ON" in snaps[0].message
    finally:
        del os.environ["DHOLE_W4_TEST_FLAG"]
        env_registry.clear_warnings()


def test_read_flag_fail_safe_direction_is_off_not_on():
    """D1: for the 4 safety gates an unrecognised value goes OFF (safe).
    This is the ONLY behavioural change W4 makes, and it is scoped to
    entries marked ``fail_safe="closed"``.
    """
    env_registry.clear_warnings()
    import os
    os.environ["DHOLE_W4_TEST_GATE"] = "flase"
    try:
        assert env_registry.read_flag(
            "DHOLE_W4_TEST_GATE", default=False, fail_safe=True) is False
        snaps = env_registry.warnings_snapshot()
        assert len(snaps) == 1
        assert "OFF" in snaps[0].message
        assert "fail-safe" in snaps[0].message
    finally:
        del os.environ["DHOLE_W4_TEST_GATE"]
        env_registry.clear_warnings()


def test_read_int_records_warning_on_non_numeric():
    env_registry.clear_warnings()
    import os
    os.environ["DHOLE_W4_TEST_INT"] = "abc"
    try:
        assert env_registry.read_int("DHOLE_W4_TEST_INT", 40000) == 40000
        snaps = env_registry.warnings_snapshot()
        assert len(snaps) == 1
        assert "'abc'" in snaps[0].message
        assert "using default 40000" in snaps[0].message
    finally:
        del os.environ["DHOLE_W4_TEST_INT"]
        env_registry.clear_warnings()


def test_is_valid_value_matches_registry_kind():
    """The doctor's colouring of a set env depends on this - if it drifts,
    doctor says the value is fine while the actual reader fell back.
    """
    entry_int = env_registry.by_name("DHOLE_DEFAULT_CONTENT_CHARS")
    assert env_registry.is_valid_value(entry_int, "8000") == (True, "")
    ok, why = env_registry.is_valid_value(entry_int, "abc")
    assert not ok and "not an integer" in why
    ok, why = env_registry.is_valid_value(entry_int, "1")
    assert not ok and "outside 500..200000" in why

    entry_flag = env_registry.by_name("DHOLE_MULTIMODAL")
    assert env_registry.is_valid_value(entry_flag, "on") == (True, "")
    assert env_registry.is_valid_value(entry_flag, "") == (True, "")
    ok, why = env_registry.is_valid_value(entry_flag, "yes-please")
    assert not ok and "not a recognised" in why
