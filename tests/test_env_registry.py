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


def test_wire_text_never_names_an_unregistered_knob():
    """连接期文案里出现的每一个 ``DHOLE_*`` 名字都必须是活旋钮。

    W1.1 只盯"整串恰好是旋钮名"的字面量（那是 ``os.environ.get()`` 的形状），
    夹在长描述里的一句"需要 operator 设 DHOLE_NEURAL_FOCUS"它看不见 —— 而那正是
    最贵的一类谎：它写在 tools/list 里，每次连接都付费，agent 照着它去教用户改一个
    改了没有任何作用的环境变量。17.0 注销了 27 个旋钮，这条守卫是它们不复活的门。

    扫的是**渲染后的对象**（instructions + 工具声明 + inputSchema + 每个 Field 的
    description），不是源码文本：源码里"某旋钮已注销"的注释是合法教学，wire 里不是。
    """
    import dhole_mcp.server as server_mod

    registered = {e.name for e in env_registry.ALL}
    # 不含 DHOLE_ 字样的同族谎话：B1 删掉的是"许可门"这个概念本身，而文案里留下
    # 的说法常常不点名任何旋钮（"the operator's flag is the permission"就是这句），
    # 所以只扫名字会漏。这几个短语在合法的 17.0 文本里没有任何用处。
    dead_phrases = ("operator's flag", "the flag is on", "with the permission",
                    "whatever the operator configured", "opt-in tool")
    hits: dict[str, list[str]] = {}
    phrase_hits: dict[str, list[str]] = {}

    def walk(obj, where):
        if isinstance(obj, str):
            for name in re.findall(r"DHOLE_[A-Z0-9_]+", obj):
                if name not in registered:
                    hits.setdefault(name, []).append(where)
            for phrase in dead_phrases:
                if phrase in obj:
                    phrase_hits.setdefault(phrase, []).append(where)
        elif isinstance(obj, dict):
            for key, value in obj.items():
                walk(value, where if key == "description" else f"{where}.{key}")
        elif isinstance(obj, (list, tuple)):
            for value in obj:
                walk(value, where)

    walk(server_mod.DHOLE_INSTRUCTIONS, "INSTRUCTIONS")
    for tool_def in server_mod.MasterFetchServer._TOOL_DEFS:
        walk(tool_def, tool_def.get("name", "?"))
    for tool_def in server_mod.MasterFetchServer._TOOL_DEFS:
        name = tool_def.get("name", "?")
        method = getattr(server_mod.MasterFetchServer, name, None)
        for pname, ann in (getattr(method, "__annotations__", None) or {}).items():
            walk(getattr(ann, "description", None), f"{name}.{pname}")

    assert not hits, (
        f"连接期文案点了未注册（多半是已注销）的旋钮: "
        f"{ {k: sorted(set(v))[:3] for k, v in hits.items()} } —— "
        f"运行时它已经什么都不做，这句话就是在教用户改一个无效的设置")
    assert not phrase_hits, (
        f"连接期文案还在描述一道已删除的许可门: "
        f"{ {k: sorted(set(v))[:3] for k, v in phrase_hits.items()} } —— "
        f"17.0 的能力全部默认在线，按次参数就是唯一的门")


def test_skill_corpus_never_names_an_unregistered_knob():
    """随包说明书（``skills/dhole-web/**``）与 wire 同一档：它也是 agent 照做的文本。

    这批里有三处就是这么漏的 —— fields.md 还在教 ``DHOLE_WIRE_FULL=1``，
    troubleshooting.md 还在教 ``DHOLE_ENGINE_HEARTBEAT`` / ``DHOLE_ENGINE_COOLDOWN``。
    与 W1.5（"每个活旋钮都要在配置文档里出现"）方向相反：那条盯覆盖率，这条盯谎话。
    """
    registered = {e.name for e in env_registry.ALL}
    bad: dict[str, list[str]] = {}
    for path in sorted((SRC_ROOT / "skills" / "dhole-web").rglob("*.md")):
        for name in re.findall(r"DHOLE_[A-Z0-9_]+", path.read_text(encoding="utf-8")):
            if name not in registered:
                bad.setdefault(name, []).append(path.name)
    assert not bad, (
        f"说明书里教了未注册的旋钮: { {k: sorted(set(v)) for k, v in bad.items()} } —— "
        f"设了没有任何作用，这段话就是在浪费读者的时间")


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


def test_registry_covers_all_12_env_vars():
    """17.0 大众化一刀切把注册表从 39 收到 11（§3-20：注销的旋钮运行时静默
    忽略，只有 --doctor 认识它们）；G25 评审为 HTTP 传输加回
    `DHOLE_HTTP_TOKEN` 到 12，为凭据落盘开关加回 `DHOLE_PERSIST_CREDENTIALS`
    到 13。再删一个必须先改这里 —— 在有人 --doctor 的输出说谎之前先红。
    加回或新增一个旋钮同理。
    """
    assert len(env_registry.ALL) == 13, (
        f"expected 13 env vars, found {len(env_registry.ALL)}: "
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
    """The ``secret`` kind (17.0: Tavily + 博查；G25 评审加 HTTP 传输的
    bearer token —— brightdata/exa 的 keyed 引擎已删). Their ``default`` is a
    marker string, not a value - W2's config audit depends on it.
    """
    secrets = [e for e in env_registry.ALL if e.kind == "secret"]
    assert len(secrets) == 3
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
    """§A.4 的 fail_safe=closed 门。17.0 收敛后剩两道：内网放行与 robots
    忽略 —— CDP 与 SSRF 复查的门已经从「旋钮」变成「写死 + 审计/自动判定」，
    比旋钮更硬，所以不再出现在这张表里。17.1 加回第三道：凭据落盘开关的
    拼错值必须停在内存（safe 一侧），而不是被 read_flag 当成 ON。"""
    closed = sorted(e.name for e in env_registry.ALL if e.fail_safe == "closed")
    assert closed == sorted([
        "DHOLE_ALLOW_PRIVATE_HOSTS",
        "DHOLE_IGNORE_ROBOTS",
        "DHOLE_PERSIST_CREDENTIALS",
    ])


def test_read_at_import_matches_plan_a1():
    """Import-time reads. 17.0 收敛后剩两个：内容默认值与工具白名单；G25 评审
    的 `DHOLE_HTTP_TOKEN` 在 serve() 读（进程启动即定，改了要重启）—— 同属
    "restart the host" 一档。17.1 的 `DHOLE_PERSIST_CREDENTIALS` 同档：store
    是模块级单例连接，连接一开模式就绑死，中途改旗搬不动已开的库。
    （DHOLE_HOME 也是 ENV_ONLY，但它是 bootstrap —— 定位配置文件的变量不能等
    配置文件来设，所以不进注册表。）drift 在这意味着 W2 的 "restart the host"
    提示要么空响、要么漏报。
    """
    imports = sorted(e.name for e in env_registry.ALL if e.read_at == "import")
    assert imports == sorted([
        "DHOLE_DEFAULT_CONTENT_CHARS",
        "DHOLE_HTTP_TOKEN",
        "DHOLE_PERSIST_CREDENTIALS",
        "DHOLE_TOOLS",
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

    entry_flag = env_registry.by_name("DHOLE_IGNORE_ROBOTS")
    assert env_registry.is_valid_value(entry_flag, "on") == (True, "")
    assert env_registry.is_valid_value(entry_flag, "") == (True, "")
    ok, why = env_registry.is_valid_value(entry_flag, "yes-please")
    assert not ok and "not a recognised" in why
