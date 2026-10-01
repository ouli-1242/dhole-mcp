"""Every capability that ships OFF must be reachable without editing JSON.

The complaint this batch exists for was not "there are too many options" but
"the options that unlock features are only visible to someone who already read
the docs". So each default-off flag is classified here, and the classification
is DERIVED from `env_registry` rather than from a list someone maintains next to
the code: adding a new gate is the moment this test matters, and a hand-kept
checklist cannot notice a name it has never seen.

Three ways out, per flag:
  * a `dhole init` flag (a command the user can run, or the agent can name);
  * a per-call parameter that supersedes it (the flag is a permission, the call
    carries the willingness);
  * it gates nothing (a wire shape, an ops preference, a privacy opt-in, or
    something dhole now auto-detects).

Anything else is the original bug and fails here. The excuse table is explicit
and checked from both directions: a flag that stops existing leaves a stale
excuse behind, and a stale excuse is how a guard stops covering anything.
"""

from __future__ import annotations

import ast
import pathlib

from dhole_mcp import env_registry as er

REPO = pathlib.Path(__file__).resolve().parents[1]
INIT_SRC = REPO / "src" / "dhole_mcp" / "init.py"
CORPUS = REPO / "src" / "dhole_mcp" / "skills" / "dhole-web"

#: Flags `dhole init` can turn on. The mapping itself is read from init.py below,
#: so this set is only the expectation - the test compares it with the source.
INIT_ENABLED = {"DHOLE_TASKS", "DHOLE_VAULT", "DHOLE_NEURAL_FOCUS",
                "DHOLE_CDP_ATTACH", "DHOLE_CDP_HOSTS"}

#: Flags that gate NO capability a user is trying to reach, each with the reason
#: it does not need a command. This is the load-bearing table: a new gate lands
#: here only if the sentence is true.
NOT_A_GATE = {
    "DHOLE_MULTIMODAL":
        "declares what the HOST can do (can this agent see images?) - the caller "
        "cannot know it about itself, so a per-call parameter would be a guess",
    "DHOLE_IGNORE_ROBOTS":
        "compliance is the operator's standing decision; the per-call "
        "ignore_robots=true path already exists behind the user's explicit yes",
    "DHOLE_USAGE_LOG":
        "privacy opt-in for a local log; it unlocks no tool and no content",
    "DHOLE_NO_AUTO_REPAIR":
        "ops preference (print the fix instead of running it); nothing is gated",
    "DHOLE_NO_BROWSER_PREWARM":
        "startup cost preference; the browser tier works with it on",
    "DHOLE_SEARCH_FEEDBACK":
        "a process-wide ranking signal; making it per-call would fragment the "
        "very history it reads",
    "DHOLE_OUTPUT_SCHEMA":
        "wire shape, declared in tools/list - it is by definition decided at "
        "connect time, not per call",
    "DHOLE_STRUCTURED_CONTENT": "wire shape, same reason",
    "DHOLE_WIRE_FULL": "wire shape, same reason",
    "DHOLE_SSRF_DNS_RECHECK":
        "auto-detected since W12 (the fake-ip probe), so the flag is only a "
        "manual override of something that now fixes itself",
}


def unexplained_gates(entries=None) -> list[str]:
    """Default-off flags with neither a command nor a reason.

    One function, because the self-check test has to drive the SAME scan the
    guard uses - a second copy of the loop would prove the copy works, not that
    the guard does.
    """
    out = []
    for entry in (er.ALL if entries is None else entries):
        if entry.kind != "flag" or entry.default != "off":
            continue
        if entry.name in INIT_ENABLED or entry.name in NOT_A_GATE:
            continue
        out.append(entry.name)
    return out


def _init_enabled_names(src: str | None = None) -> set[str]:
    """Every env name `dhole init` writes, read out of init.py's own source.

    Parsed rather than imported: `--tasks`/`--vault`/`--neural` live in a dict
    literal and `--cdp` writes two more, so this is the only way to ask the
    question "which gates can a command open?" without maintaining the answer
    twice.
    """
    tree = ast.parse(src if src is not None else INIT_SRC.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "want" in targets and isinstance(node.value, ast.Dict):
            # {"--tasks": "DHOLE_TASKS", ...}
            for value in node.value.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    found.add(value.value)
        if isinstance(node.value, ast.Dict):
            # `to_write = {"DHOLE_CDP_HOSTS": ...}` and its conditional
            # `to_write["DHOLE_CDP_ATTACH"] = "1"` are how the CDP pair is built:
            # one literal key plus one subscript assignment (whose VALUE is a
            # plain string, not a dict - checking subscripts only inside the dict
            # branch is how ATTACH went missing). Reading only the dict would call
            # ATTACH "not opened by any command", which is the opposite of true,
            # and the kind of wrong the guard must not produce.
            keys = [k.value for k in node.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            found.update(k for k in keys if str(k).startswith("DHOLE_CDP_"))
        for target in node.targets:
            if (isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)
                    and target.slice.value.startswith("DHOLE_CDP_")):
                found.add(target.slice.value)
    return found


class TestAGateIsNotADeadEnd:

    def test_the_registry_is_the_source_of_the_flag_list(self):
        """If a default-off flag is neither reachable by command nor excused, it
        is the exact thing this batch was for."""
        assert not unexplained_gates(), (
            f"这些默认关的开关只能靠编辑 env 块打开：{unexplained_gates()}。要么给它"
            f"一条 `dhole init --x`，要么在 NOT_A_GATE 里写下它为什么不锁能力。")

    def test_init_actually_opens_what_the_test_claims(self):
        """The other direction: INIT_ENABLED is a promise about init.py, and a
        flag dropped from the command must not keep passing as 'reachable'."""
        assert _init_enabled_names() == INIT_ENABLED, (
            "dhole init 实际写的变量名与测试里的承诺不一致 —— 要么命令少了一个闸，"
            "要么这里的表过期了。")

    def test_the_command_losing_a_gate_is_caught(self):
        """And the same for the other table: if `--neural` stopped writing its
        flag, INIT_ENABLED must not keep calling it reachable by command."""
        src = INIT_SRC.read_text(encoding="utf-8")
        # Rename what --neural writes (still a valid dict literal, so the parse
        # succeeds and the SET is what changes): a gate the command stops naming
        # has to disappear from the derived answer.
        renamed = src.replace('"--neural": "DHOLE_NEURAL_FOCUS"',
                              '"--neural": "DHOLE_SOMETHING_ELSE"')
        assert renamed != src, "init.py 的写法变了，这条自检要跟着改"
        assert "DHOLE_NEURAL_FOCUS" not in _init_enabled_names(renamed)
        assert "DHOLE_NEURAL_FOCUS" in _init_enabled_names()

    def test_no_stale_excuses(self):
        """Every name in either table must still be a knob dhole actually reads.

        An excuse for a variable that no longer exists protects nothing, and a
        promised command for one that never existed is a bug report waiting to
        happen. Registration is per NAME (any kind): DHOLE_CDP_HOSTS is a
        hostlist and DHOLE_SSRF_DNS_RECHECK defaults ON, so neither appears in
        the default-off scan yet both belong in a table here.
        """
        registered = {e.name for e in er.ALL}
        stale = sorted((set(NOT_A_GATE) | INIT_ENABLED) - registered)
        assert not stale, f"这些名字注册表里已经没有，条目该删：{stale}"
        misfiled = sorted(n for n in NOT_A_GATE
                          if (e := er.by_name(n)) is not None
                          and e.kind == "flag" and e.default == "off"
                          and n in INIT_ENABLED)
        assert not misfiled, f"同一个名字既在'命令能打开'又在'它不锁能力'：{misfiled}"

    def test_every_gate_flag_is_described_in_the_corpus(self):
        """A command nobody can find is still a dead end, just a shorter one - so
        the handbook (which is what dhole_doc serves) must name both the flag and
        the way in."""
        text = ""
        for path in sorted(CORPUS.rglob("*.md")):
            text += path.read_text(encoding="utf-8")
        for name in sorted(INIT_ENABLED | set(NOT_A_GATE)):
            assert name in text, f"{name} 在语料里一次都没出现 - 用户只能靠 README"

    def test_the_cdp_command_is_named_where_an_agent_would_look(self):
        """`dhole init --cdp` is the answer to two errors the agent meets at call
        time (cdp_attach_disabled / cdp_host_not_allowed), so the error's own
        playbook rows have to carry it."""
        text = ""
        for path in sorted(CORPUS.rglob("*.md")):
            text += path.read_text(encoding="utf-8")
        for row_key in ("cdp_attach_disabled", "cdp_host_not_allowed"):
            lines = [line for line in text.splitlines() if row_key in line]
            assert lines, f"没有任何一行讲 {row_key}"
            assert any("init --cdp" in line for line in lines), \
                f"{row_key} 的处置行没有给出那条命令"

    def test_the_scan_reacts_to_a_gate_nobody_thought_about(self, monkeypatch):
        """机制自检：这条守卫的全部意义是"下一个新闸门不会悄悄只肯由 env 打开"，
        所以它必须能对一条不存在的旋钮变红。手工数一遍表是数不出这件事的。"""
        extra = er.EnvVar(name="DHOLE_FAKE_GATE", kind="flag", default="off",
                          read_at="call", scenario="test", summary="a made-up gate",
                          read_by=("server",))
        monkeypatch.setattr(er, "ALL", list(er.ALL) + [extra])
        assert unexplained_gates() == ["DHOLE_FAKE_GATE"]

    def test_neural_focus_is_a_permission_not_a_dead_end(self):
        """The one flag that also has a per-call parameter: the corpus must say
        which side is which, or 'bm25' looks like it is being ignored."""
        text = ""
        for path in sorted(CORPUS.rglob("*.md")):
            text += path.read_text(encoding="utf-8")
        assert "focus_engine" in text
        assert "init --neural" in text
