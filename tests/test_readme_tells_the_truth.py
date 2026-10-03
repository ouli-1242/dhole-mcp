"""README 的每一颗 `DHOLE_*` 与每一条 `dhole <命令>` 都必须真的存在。

17.0 把一大批旋钮收成了"能算清的数"（`DHOLE_COOKIE_TTL_S` /
`DHOLE_ENGINE_HEARTBEAT` / `DHOLE_BROWSER_PROFILE` …全部注销、运行时静默忽略），又把
`dhole init` / `dhole model` / `dhole proxy` 三组命令整个删掉。代码跟着走完了，README
没有：清点过一次，正文里 34 颗名字有 23 颗已经不会被读，`dhole init --cdp` 那一段还
在教人敲一条今天会直接报错的命令。

一份会说谎的安装说明比一份短的更糟 —— 它读起来完全正常，而照它做的人得到的是
"unsupported" 或者更糟：一个静默无效的变量，于是操作员以为自己开了某扇门。

守卫的形状：README 里出现的每个 `DHOLE_[A-Z_]+` 必须在 `env_registry.ALL`
（那份文档说它是所有 `DHOLE_*` 字面量的唯一来源）里出现；每个 `dhole <子命令>` 必须在
`_help_epilog()` 的命令表里。**CHANGELOG 不在管辖范围内**：它是按日期钉住的历史，
"当时开了一个后来关掉的开关"是一句真话，把它改成今天的形状才是造假。
"""

from __future__ import annotations

import re
from pathlib import Path

from dhole_mcp import env_registry
from dhole_mcp.ops import _help_epilog

README = Path(__file__).resolve().parents[1] / "README.md"

# 命令表里真实存在的顶层入口：`dhole`（serve）、`--http`、`-v`、以及 epilog 列出的
# 每一个子命令。epilog 是唯一的权威来源，所以这里解析它而不是另抄一份名单 ——
# 名单会漂移，而它是自己就说过"今天 34 颗里有 23 颗没读"的那种漂移。
_SUBCOMMANDS = None


def _real_subcommands() -> set[str]:
    global _SUBCOMMANDS
    if _SUBCOMMANDS is None:
        _SUBCOMMANDS = set(re.findall(r"dhole ([a-z]+)", _help_epilog()))
    return _SUBCOMMANDS


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def test_every_env_name_readme_documents_is_read():
    documented = set(re.findall(r"`(DHOLE_[A-Z_0-9]+)", _readme()))
    live = {v.name for v in env_registry.ALL}

    dead = sorted(documented - live)
    assert not dead, (
        f"README 教了 {len(dead)} 颗今天没人读的变量：{dead}。"
        "17.0 把它们收成了固定值或写死的策略（运行时静默忽略）——"
        "从 README 删掉，或把旋钮做回去，但不要留着教人。")


def test_readme_covers_every_live_knob():
    """反方向：真的旋钮也得在 README 里 —— 少一条就是"这个开关不存在"的另一种谎。"""
    live = {v.name for v in env_registry.ALL}
    missing = sorted(n for n in live if f"`{n}" not in _readme())
    assert not missing, f"README 漏了 {len(missing)} 颗真实存在的旋钮：{missing}"


def test_no_retired_command_is_still_documented():
    """`dhole init` / `dhole model` / `dhole proxy` 都已经在 17.0 被删掉。"""
    cmds = set(re.findall(r"`dhole ([a-z]+)", _readme()))
    unknown = sorted(cmds - _real_subcommands() - {"dhole"})
    assert not unknown, (
        f"README 里这些 `dhole <命令>` 不在 `dhole --help` 的命令表上：{unknown}。")


def test_registry_names_are_the_single_source_of_truth():
    """反方向：真的旋钮也得在 README 里 —— 少一条就是"这个开关不存在"的另一种谎。"""
    live = {v.name for v in env_registry.ALL}
    documented = set(re.findall(r"`(DHOLE_[A-Z_0-9]+)", _readme()))

    assert documented <= live
    assert len(documented) == len(live), (
        f"README 记了 {len(documented)} 颗，注册表说一共 {len(live)} 颗 —— "
        "两份名单必须一样长，否则'配置'一节本身就不完整")


def test_the_skill_corpus_names_no_dead_knob():
    """`references/configuration.md` 是 agent 读的那份配置说明，同一条规矩管它。

    它比 README 更要紧：README 由人读，语料由 agent 照着发消息。这一条今天过，
    是因为 B1 那批把语料一起扫过 —— 而"今天过"不是明天还过的理由。
    """
    live = {v.name for v in env_registry.ALL}
    root = Path(__file__).resolve().parents[1] / "src" / "dhole_mcp" / "skills"
    offenders = {}
    for md in sorted(root.rglob("*.md")):
        dead = sorted(set(re.findall(r"`(DHOLE_[A-Z_0-9]+)",
                                     md.read_text(encoding="utf-8"))) - live)
        if dead:
            offenders[md.name] = dead

    assert not offenders, f"技能语料里教了不存在的旋钮：{offenders}"


class TestLayerDocsTellTheTruth:
    """17.0 B6 拆出去的每一层：文档要说出它存在，而它不许反过来吃 server。

    这两件事都会被下一刀破：搬了新模块而 CONTRIBUTING 的 Layout 还在说
    "server.py 里什么都有"，下一个来读的人就会把新代码继续写进那个 8k 行的文件；
    而在某个搬出去的层里加一句模块级 `from dhole_mcp.server import X`，拆出去
    的那一块就再也单独导不进来（循环），于是"分层"退化成一个文件名。
    """

    LAYERS = ("models", "wire", "params", "quality", "context",
              "rungs", "audit", "ops")

    def _layout_text(self) -> str:
        import re
        doc = (Path(__file__).resolve().parents[1] / "CONTRIBUTING.md").read_text(
            encoding="utf-8")
        block = re.search(r"^## Layout\n+```(.*?)```", doc, re.S | re.M)
        assert block, "CONTRIBUTING.md 没有 Layout 代码块"
        return block.group(1)

    def test_every_split_layer_is_documented(self):
        layout = self._layout_text()
        missing = [f"{n}.py" for n in self.LAYERS if f"{n}.py" not in layout]
        assert not missing, (
            f"B6 拆出去的这些层没写进 Layout，读的人会继续往 server.py 里加东西："
            f"{missing}")

    def test_no_layer_imports_server_at_module_level(self):
        import ast

        pkg = Path(__file__).resolve().parents[1] / "src" / "dhole_mcp"
        offenders = []
        for name in self.LAYERS:
            if name == "ops":
                continue  # 唯一的例外，理由写在 ops.py 的 docstring 与 Layout 之后
            tree = ast.parse((pkg / f"{name}.py").read_text(encoding="utf-8"))
            for node in tree.body:          # 只看模块级：函数内 import 是许可的
                if (isinstance(node, ast.ImportFrom)
                        and node.module == "dhole_mcp.server"):
                    offenders.append(f"{name}.py:{node.lineno}")
        assert not offenders, (
            f"{offenders} 在模块级 import server —— 这一层再也单独导不进来")

    def test_the_ops_exception_still_says_why(self):
        """例外必须留在那儿并且带理由，否则它看起来像疏忽而不是决定。"""
        import ast

        pkg = Path(__file__).resolve().parents[1] / "src" / "dhole_mcp"
        src = (pkg / "ops.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        assert any(isinstance(n, ast.ImportFrom) and n.module == "dhole_mcp.server"
                   for n in tree.body), (
            "ops.py 的模块级 MasterFetchServer import 被改掉了：那会让缺依赖的人"
            "看到 argparse 的参数错误而不是该装的那件东西（见 ops.py docstring）")
        assert "惰性" in src, "这条约束的理由必须留在源码里，不只在 commit message 里"


def test_the_connect_size_readme_quotes_is_the_one_the_ruler_measures():
    """README 里那句「连接期开销 N 字符」必须等于尺子此刻量的数。

    这个数字是 17.0 收尾这一批的发言权所在（44,016 → 40,472）：每改一颗描述它都会动，
    而 README 是操作员唯一真会读的那一份。手抄一次的下场已经在正文里写过一次 ——
    旧数字比实测高 233 的那条修复记录就是它。所以这里不比对"写得差不多"，只比对
    同一把尺子（compact tools/list + instructions）的当下读数。
    """
    import json

    from dhole_mcp.server import DHOLE_INSTRUCTIONS, MasterFetchServer

    total = len(json.dumps({"tools": MasterFetchServer._TOOL_DEFS}, ensure_ascii=False,
                           separators=(",", ":"))) + len(DHOLE_INSTRUCTIONS)
    quoted = [int(m.replace(",", "")) for m in
              re.findall(r"连接期开销 ([\d,]+) 字符", _readme())]
    assert quoted, ("README 不再写连接期开销了：那这条守卫该跟着退场，"
                    "而不是留在这里空转成一个看起来通过的东西")
    assert quoted == [total], (
        f"README 写的是 {quoted}，尺子量到 {total:,} —— 要么改 README，"
        "要么说清为什么这次改动没进 wire")
