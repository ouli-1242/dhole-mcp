"""17.0 守卫：**没有任何能力在门后面**。

B1 之前这条测试的分类是"每个默认关的开关，必须有一条命令或一个理由能打开它"。
17.0 大众化一刀切把问题本身删了：不存在默认关的能力 —— opt-in 工具组全量注册，
元素截图常开，neural focus 变成按次参数，wire 形状固定。所以这条守卫**反转**：

  * 注册表里剩下的 flag 必须逐个有"它不锁能力"的理由（隐私 opt-in / 操作者的
    长期合规决定），且理由由这张表人工持有 —— 新增一个默认关的**能力**旋钮，
    没有借口就会在这里变红；
  * 装机与选模型用的 `dhole init` / `dhole model` 已经整个删除，登记表里不许再有它们；
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
SERVER_SRC = REPO / "src" / "dhole_mcp" / "server.py"
# B6 之后 `dhole <verb>` 那张表搬进了 ops.py：守卫读的是文本，所以它跟着搬。
OPS_SRC = REPO / "src" / "dhole_mcp" / "ops.py"


def _cli_text() -> str:
    """两张源文件拼起来的文本 —— 命令表在哪份里，守卫就扫哪份。"""
    return (SERVER_SRC.read_text(encoding="utf-8") + "\n"
            + OPS_SRC.read_text(encoding="utf-8"))
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


def _cli_subcommands(src: str) -> set[str]:
    """`dhole <verb>` 的登记表，从源码文本里读出来（B6 之后它在 ops.py）。

    解析而不导入：main() 里那个 `_SUBCOMMANDS` 是个函数内局部量，导入路径摸不到它；
    而"哪个命令能开门"这个问题必须落在真实文本上，否则守卫只是在读自己的假设。
    """
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_SUBCOMMANDS"
                        for t in node.targets)
                and isinstance(node.value, (ast.Tuple, ast.List))):
            return {e.value for e in node.value.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    return set()


class TestNoCapabilityBehindAGate:

    def test_the_registry_holds_no_unexplained_default_off_flag(self):
        """If a default-off flag is neither excused nor command-openable, it is
        the exact thing the original batch was for."""
        assert not unexplained_gates(), (
            f"这些默认关的开关只能靠编辑 env 块打开：{unexplained_gates()}。17.0 不收"
            f"新的能力闸 —— 要么默认开，要么在 NOT_A_GATE 里写下它为什么不锁能力。")

    def test_no_command_can_open_a_capability_gate(self):
        """17.0：登记安装用的 `dhole init` 与选模型的 `dhole model` 都删了 —— 现在
        **没有任何 CLI 动词能写能力闸**，所以这条守卫从"扫 init 写了哪些变量"升级成
        "init 与 model 根本不存在"。留着扫描一个不存在的文件，等于守一个不存在的门。

        装机流程因此是一句话：pip install → 把 README 那块配置交给宿主 agent 自己写
        → `dhole doctor`。少一个会改用户机器的命令，就少一次它改错的机会。
        """
        import importlib.util

        subcommands = _cli_subcommands(_cli_text())
        assert subcommands, "读不到 _SUBCOMMANDS —— 守卫要扫的东西换了形状，得同步改这里"
        for gone in ("init", "model"):
            assert gone not in subcommands, f"dhole {gone} 又回来了：{sorted(subcommands)}"
        assert importlib.util.find_spec("dhole_mcp.init") is None, \
            "src/dhole_mcp/init.py 必须整个删除，不是留一个空壳"
        assert not (REPO / "src" / "dhole_mcp" / "init.py").exists()

    def test_the_verb_that_wrote_plaintext_credentials_is_gone(self):
        """`dhole proxy add` 与整个代理池一起删了。它往 ~/.dhole/search_proxies.json
        写明文凭据，换来的"这个 IP 被盯上就换下一个"对免密引擎并不成立（同一台机器
        换端口不换指纹）。出口因此退回一个字符串、一条 env 变量；读侧由
        tests/test_search_proxy.py 守，这里只钉"那条写用户机器的命令不存在"。"""
        subcommands = _cli_subcommands(_cli_text())
        assert "proxy" not in subcommands, f"dhole proxy 又回来了：{sorted(subcommands)}"
        from dhole_mcp import server
        assert not hasattr(server, "_cmd_proxy"), "_cmd_proxy 还在，只是没登记进表里"

    def test_the_subcommand_scan_reacts_to_a_command_that_should_not_exist(self):
        """机制自检：上一条靠"读不到就红"防不住解析形状变了还返回空集的情形 ——
        这里喂一段真的有 init 的源码，确认扫描器认得它。"""
        assert "init" in _cli_subcommands(
            'def main():\n    _SUBCOMMANDS = ("doctor", "init", "config")\n')
        assert _cli_subcommands('def main():\n    x = 1\n') == set()

    def test_no_stale_excuses(self):
        """Every name in the excuse table must still be a knob dhole reads.

        An excuse for a variable that no longer exists protects nothing.
        """
        registered = {e.name for e in er.ALL}
        stale = sorted(set(NOT_A_GATE) - registered)
        assert not stale, f"这些名字注册表里已经没有，条目该删：{stale}"

    def test_every_surviving_flag_is_described_in_the_corpus(self):
        """A knob nobody can find is a dead end all the same - so the handbook
        (which is what sys_guide serves) must name what remains and why."""
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
