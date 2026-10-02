"""17.0 守卫：**没有任何能力在门后面**。

B1 之前这条测试的分类是"每个默认关的开关，必须有一条命令或一个理由能打开它"。
17.0 大众化一刀切把问题本身删了：不存在默认关的能力 —— opt-in 工具组全量注册，
元素截图常开，neural focus 变成按次参数，wire 形状固定。所以这条守卫**反转**：

  * 注册表里剩下的 flag 必须逐个有"它不锁能力"的理由（隐私 opt-in / 操作者的
    长期合规决定），且理由由这张表人工持有 —— 新增一个默认关的**能力**旋钮，
    没有借口就会在这里变红；
  * `dhole init` 不得再写任何能力闸（它如今只做安装与客户端登记）；
  * 机制自检保留：对一条捏造的默认关旋钮，扫描必须能变红。

The complaint this batch originally existed for was "the options that unlock
features are only visible to someone who already read the docs". The 17.0 answer
was to stop having unlock options: everything a user might reach for is on by
default. This file keeps the scan so the next default-off capability knob cannot
sneak in with only an env edit as its key.
"""

from __future__ import annotations

import ast
import pathlib

from dhole_mcp import env_registry as er

REPO = pathlib.Path(__file__).resolve().parents[1]
INIT_SRC = REPO / "src" / "dhole_mcp" / "init.py"
CORPUS = REPO / "src" / "dhole_mcp" / "skills" / "dhole-web"

#: Flags that gate NO capability a user is trying to reach, each with the reason
#: it does not need a command. This is the load-bearing table: a new default-off
#: flag lands here only if the sentence is true.
NOT_A_GATE = {
    "DHOLE_IGNORE_ROBOTS":
        "compliance is the operator's standing decision; the per-call "
        "ignore_robots=true path already exists behind the user's explicit yes",
    "DHOLE_USAGE_LOG":
        "privacy opt-in for a local log; it unlocks no tool and no content",
}


def unexplained_gates(entries=None) -> list[str]:
    """Default-off flags with no excuse.

    One function, because the self-check test has to drive the SAME scan the
    guard uses - a second copy of the loop would prove the copy works, not that
    the guard does.
    """
    out = []
    for entry in (er.ALL if entries is None else entries):
        if entry.kind != "flag" or entry.default != "off":
            continue
        if entry.name in NOT_A_GATE:
            continue
        out.append(entry.name)
    return out


def _init_enabled_names(src: str | None = None) -> set[str]:
    """Every env name `dhole init` writes, read out of init.py's own source.

    Parsed rather than imported: the question is "which gates can a command
    open?", and in 17.0 the answer must be none — init registers MCP clients
    and checks the install, it does not hand out capabilities.
    """
    tree = ast.parse(src if src is not None else INIT_SRC.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Subscript)
                        and isinstance(target.slice, ast.Constant)
                        and isinstance(target.slice.value, str)
                        and target.slice.value.startswith("DHOLE_")):
                    found.add(target.slice.value)
            keys = [k.value for k in getattr(node.value, "keys", [])
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            found.update(k for k in keys if k.startswith("DHOLE_"))
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "set_entry":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                        and arg.value.startswith("DHOLE_"):
                    found.add(arg.value)
    return found


class TestNoCapabilityBehindAGate:

    def test_the_registry_holds_no_unexplained_default_off_flag(self):
        """If a default-off flag is neither excused nor command-openable, it is
        the exact thing the original batch was for."""
        assert not unexplained_gates(), (
            f"这些默认关的开关只能靠编辑 env 块打开：{unexplained_gates()}。17.0 不收"
            f"新的能力闸 —— 要么默认开，要么在 NOT_A_GATE 里写下它为什么不锁能力。")

    def test_init_opens_no_gate_at_all(self):
        """17.0 的 init 不写任何 DHOLE_* 能力闸：安装与登记，仅此而已。"""
        assert _init_enabled_names() == set(), (
            f"dhole init 仍在写这些变量：{sorted(_init_enabled_names())}。"
            "能力旋钮已全部注销或改为按次参数 —— 这个命令不该再有开关可写。")

    def test_no_stale_excuses(self):
        """Every name in the excuse table must still be a knob dhole reads.

        An excuse for a variable that no longer exists protects nothing.
        """
        registered = {e.name for e in er.ALL}
        stale = sorted(set(NOT_A_GATE) - registered)
        assert not stale, f"这些名字注册表里已经没有，条目该删：{stale}"

    def test_every_surviving_flag_is_described_in_the_corpus(self):
        """A knob nobody can find is a dead end all the same - so the handbook
        (which is what dhole_doc serves) must name what remains and why."""
        text = ""
        for path in sorted(CORPUS.rglob("*.md")):
            text += path.read_text(encoding="utf-8")
        for name in sorted(set(NOT_A_GATE)):
            assert name in text, f"{name} 在语料里一次都没出现 - 用户只能靠 README"

    def test_the_confirm_semantics_are_named_where_an_agent_would_look(self):
        """`cdp_authorization_required` is the error an agent meets at call
        time; the corpus's own playbook rows must carry the confirm=true way
        out (the old init --cdp command is gone)."""
        text = ""
        for path in sorted(CORPUS.rglob("*.md")):
            text += path.read_text(encoding="utf-8")
        lines = [line for line in text.splitlines()
                 if "cdp_authorization_required" in line]
        assert lines, "没有任何一行讲 cdp_authorization_required"
        assert any("confirm" in line for line in lines), \
            "cdp_authorization_required 的处置行没有给出 confirm 的出路"

    def test_the_scan_reacts_to_a_gate_nobody_thought_about(self, monkeypatch):
        """机制自检：这条守卫的全部意义是"下一个新闸门不会悄悄只肯由 env 打开"，
        所以它必须能对一条不存在的旋钮变红。手工数一遍表是数不出这件事的。"""
        extra = er.EnvVar(name="DHOLE_FAKE_GATE", kind="flag", default="off",
                          read_at="call", scenario="test", summary="a made-up gate",
                          read_by=("server",))
        monkeypatch.setattr(er, "ALL", list(er.ALL) + [extra])
        assert unexplained_gates() == ["DHOLE_FAKE_GATE"]

    def test_focus_engine_is_a_parameter_not_a_permission(self):
        """The old permission/willingness split is gone; the corpus must say
        focus_engine is a plain per-call choice, or 'bm25' reads like a
        workaround for something."""
        text = ""
        for path in sorted(CORPUS.rglob("*.md")):
            text += path.read_text(encoding="utf-8")
        assert "focus_engine" in text
        assert "init --neural" not in text
