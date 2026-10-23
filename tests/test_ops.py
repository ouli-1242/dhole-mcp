"""test_ops.py —— 由 3 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import pathlib
import re
from dhole_mcp import env_registry
from dhole_mcp.ops import _help_epilog
from pathlib import Path
from dhole_mcp.ops import _cmd_skill, _skill_manifest


# ============================================================================
# 来源：test_unreleased_changelog_tells_the_truth.py
# ============================================================================
# 未发版那一版的 CHANGELOG 小节必须是今天的形状 —— 它是说明书，不是历史。

CHANGELOG = pathlib.Path(__file__).resolve().parents[1] / "CHANGELOG.md"

#: 一行里出现这些词，就算它是在"宣布注销"而不是"教人用"
_RETIRED_MARKERS = ("注销", "删掉", "删除", "不再被读", "收掉", "收成", "写死",
                    "随大众化", "曾有", "一度", "整组删", "退役", "搬走", "取消", "已拆平")

#: 旧工具名只允许出现在"说这个名字后来怎么了"的行里
_RENAMED_MARKERS = ("落地时叫", "原名", "旧名", "曾用名", "改名", "移进", "拆平")

#: 本版内改过两次以上的名字：出现在未发版段里一定是漏扫，不是历史
_OLD_NAMES = ("smart_fetch", "smart_crawl", "smart_search", "feed_fetch",
              "dhole_doc", "solve_cloudflare", "real_chrome", "discover_only")

#: 17.0 未发布即移除的四颗工具：在未发版段里只允许出现在"说它们被移除"的行里
_REMOVED_NAMES = ("web_recipe", "web_watch", "cred_vault", "cred_login")

#: 一行里出现这些词，就算它是在"宣布移除"而不是"教人用"
_REMOVAL_MARKERS = ("移除", "未发布即", "一并删", "下线") + _RETIRED_MARKERS

#: 一行里的工具数不是今天的数时，同一行必须说明它已被取代 —— 手法同 `_RETIRED_MARKERS`：
#: 数字出现不是问题，**冒充现行**才是。
_SUPERSEDED_MARKERS = ("阶段7", "后为", "当时", "时代", "截面", "已被取代", "不再冒充")

#: 把工具数说成"现在时"的说法。报告 D12 的来路就是这几句 —— 数字本身没错，错在
#: 一个已经过去的形状被写成了今天的形状。只在**这些说法**所在的行上较真，是因为
#: 「71 颗参数」「30 颗设计过的拒绝」里的数不是工具数，而「v16.2 是 9 颗工具」
#: 那样的行本来就带着年份，不冒充谁。
_CURRENT_CLAIM_MARKERS = ("定稿", "当前", "同步到", "默认注册面")


def _today_tool_count() -> int:
    """今天的工具数，从 `_TOOL_DEFS` 现读 —— 手抄的那个数每拆一次工具就要改一次。"""
    from dhole_mcp.server import MasterFetchServer

    return len(MasterFetchServer._TOOL_DEFS)


def _text() -> str:
    return CHANGELOG.read_text(encoding="utf-8")


def _section(version: str) -> str:
    """切出某一版的小节（到下一个 `## [` 为止）。

    反证类用例必须点名**真装着那份标本**的版本，不能读 `_current_version()`：
    17.1 发版那天，17.0 段落里的「阶段7 后为 N 颗」与 `DHOLE_MULTIMODAL` 那一行
    就不在"本版"里了，两条反证会以 `StopIteration` 收场 —— 而那个异常看起来像
    "语料变了"，实际是尺子指错了地方。反证要演的是"标记表还在起作用"，标本在哪
    一版就该读哪一版。
    """
    s = _text()
    head = f"## [{version}]"
    assert head in s, f"CHANGELOG 里没有 {version} 的小节"
    i = s.index(head)
    j = s.find("\n## [", i + len(head))
    return s[i:(j if j > 0 else len(s))]


def _current_version() -> str:
    from dhole_mcp import __version__

    return _section(__version__)


def _lines(body: str) -> list[str]:
    return body.splitlines()


def test_the_helper_finds_the_unreleased_section():
    """机制自检：小节切错了，下面三条守卫会一起伪装成通过。"""
    body = _current_version()
    assert body.startswith("## [")
    assert "### 新增" in body, "小节切片没吃到正文"
    assert "## [16." not in body, "切片越界，已发版的历史被算进管辖内了"


def test_no_knob_is_taught_as_if_it_still_exists():
    """扫整个本版小节，把所有"冒充现行"的行一次性报出来。

    原先按行参数化（CHANGELOG 一行一例，约 90 例）；聚合成一条后**覆盖不变**，
    失败信息反而更全 —— 断言消息本来就该列出全部违规行，而不是一次只暴露一行。
    """
    registered = {getattr(n, "name", n) for n in env_registry.ALL}
    bad = []
    for line in _lines(_current_version()):
        for tok in sorted(set(re.findall(r"DHOLE_[A-Z_]+", line))):
            if tok in registered:
                continue
            if any(m in line for m in _RETIRED_MARKERS):
                continue
            bad.append(f"{tok} ← {line[:140]}")
    assert not bad, (
        "本版小节里教了今天不再被读的旋钮，而那一行没说它已注销：\n"
        + "\n".join(bad)
        + "\n要么换成真的门（cred_defaults / dhole config），要么把『本版注销』写明。")


def test_every_command_the_version_section_teaches_exists():
    body = _current_version()
    real = set(re.findall(r"dhole ([a-z\-]+)", _help_epilog())) | {"--http", "-v"}
    bad = []
    for line in _lines(body):
        for cmd in set(re.findall(r"dhole ([a-z][a-z\-]+)", line)):
            if cmd in real:
                continue
            if any(m in line for m in _RETIRED_MARKERS):
                continue
            bad.append(f"dhole {cmd} ← {line[:90]}")
    assert not bad, "本版小节里出现的命令今天敲会报错：\n" + "\n".join(bad)


def test_no_pre_rename_tool_name_survives_in_the_unreleased_section():
    body = _current_version()
    bad = []
    for line in _lines(body):
        for name in _OLD_NAMES:
            if name in line and not any(m in line for m in _RENAMED_MARKERS):
                bad.append(f"{name} ← {line[:90]}")
    assert not bad, (
        "这些名字在 17.0 自己里就被改掉了（B5 改名 / B4 拆袋），未发版段落里只能出现在"
        "『说这个名字后来怎么了』的行里：\n" + "\n".join(bad))


def test_no_removed_tool_is_taught_as_if_it_still_exists():
    """17.0 未发布即移除的四颗：留在未发版段里必须是"说它们没了"。

    与上面那条（旧名）分开，因为语义不同：旧名是"改过名字"，这几颗是"整颗没了"。
    一颗今天不存在的工具被写进本版安装说明里，读者照着打字只会撞墙。
    """
    body = _current_version()
    bad = []
    for line in _lines(body):
        for name in _REMOVED_NAMES:
            if name in line and not any(m in line for m in _REMOVAL_MARKERS):
                bad.append(f"{name} ← {line[:120]}")
    assert not bad, (
        "这四颗工具在本版未发布即移除（见本版「移除」小节），未发版段落里只能出现在"
        "『说它们被移除』的行里：\n" + "\n".join(bad))


def test_the_options_bag_is_not_taught_as_current_in_this_version():
    """`options` 袋是本版删掉的，不是上一版就有的历史。"""
    body = _current_version()
    bad = [ln[:100] for ln in _lines(body)
           if re.search(r"\boptions\.[a-z_]+|`options`", ln)
           and not any(m in ln for m in _RETIRED_MARKERS + ("拆平", "袋"))]
    assert not bad, "还有行把参数写成袋里的键（今天这么写会被拒）：\n" + "\n".join(bad)


def test_every_tool_count_in_this_version_is_todays_or_says_it_was_superseded():
    """本版小节里的「N 颗」要么是今天的数，要么同一行说明它已被取代（报告 D12）。

    报告 D12 的来路：17.0 段落同时出现 11 / 13 / 17 三个工具数。13 不是抄错 ——
    它是四颗状态工具移除后的**真实中间态**，阶段7 又移除 `web_screenshot` 与
    `web_resolve` 才到 11（见「安全」）。错的是更早那几条 bullet 把 13 与 17 写成
    了**现在时**（"本版定稿 13 颗"、"当前 17 颗"），读者落到那一条会以为今天还是
    那个形状。判据与上面那张旋钮表同一个手法：数字出现不是问题，**冒充现行**才是。
    """
    today = _today_tool_count()
    bad = []
    for line in _lines(_current_version()):
        if not any(m in line for m in _CURRENT_CLAIM_MARKERS):
            continue
        for n in re.findall(r"(\d+)\s*颗", line):
            if int(n) == today:
                continue
            if any(m in line for m in _SUPERSEDED_MARKERS):
                continue
            bad.append(f"{n} 颗 ← {line[:140]}")
    assert not bad, (
        f"本版小节里把工具数写成了现在时，而那个数不是今天的（{today} 颗），"
        "同一行也没说它已被取代：\n"
        + "\n".join(bad)
        + "\n要么改成今天的数，要么把『阶段7 后为 N 颗』写明。")


def test_the_guard_notices_a_superseded_count():
    """反证：把那一行的取代标记抹掉，同一个数就该被判为"冒充现行"（同下一条的手法）。"""
    today = _today_tool_count()
    line = next(ln for ln in _lines(_section("17.0"))
                if "阶段7" in ln and any(m in ln for m in _CURRENT_CLAIM_MARKERS)
                and re.search(r"(\d+)\s*颗", ln))
    stale = [n for n in re.findall(r"(\d+)\s*颗", line) if int(n) != today]
    assert stale, "这一行本来就带着一个不是今天的数"
    naked = line
    for m in _SUPERSEDED_MARKERS:
        naked = naked.replace(m, "")
    assert any(m in naked for m in _CURRENT_CLAIM_MARKERS), "现在时说法仍应留着"
    assert not any(m in naked for m in _SUPERSEDED_MARKERS)
    assert [n for n in re.findall(r"(\d+)\s*颗", naked) if int(n) != today] == stale, \
        "抹掉取代标记后仍应留下那些冒充现行的数，否则这条反证没有演到东西"


def test_the_guard_notices_a_forgotten_knob():
    """反证：把"注销"那两个字抹掉，同一行就该被判为"冒充现行能力"。

    这条守卫唯一的宽容是同行有注销标记，所以它的有效性等于那个标记的存在性 ——
    拿真语料演一遍，防止标记表退化成什么都放行。
    """
    registered = {getattr(n, "name", n) for n in env_registry.ALL}
    line = next(ln for ln in _lines(_section("17.0")) if "DHOLE_MULTIMODAL" in ln)
    assert "DHOLE_MULTIMODAL" not in registered
    assert any(m in line for m in _RETIRED_MARKERS), "这一行本来就该带注销标记"
    naked = line
    for m in _RETIRED_MARKERS:
        naked = naked.replace(m, "")
    assert not any(m in naked for m in _RETIRED_MARKERS)


# ============================================================================
# 来源：test_readme_tells_the_truth.py
# ============================================================================
# README 的每一颗 `DHOLE_*` 与每一条 `dhole <命令>` 都必须真的存在。

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

    这个数字是 17.0 收尾这一批的发言权所在（44,016 → 40,010）：每改一颗描述它都会动，
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


# ============================================================================
# 来源：test_skill_install_g32.py
# ============================================================================
# G32 — the agent skill ships inside the package and installs via `dhole skill`.

_BUNDLED = Path(__file__).resolve().parent.parent / "src" / "dhole_mcp" / "skills" / "dhole-web"


def test_bundled_skill_is_complete():
    assert (_BUNDLED / "SKILL.md").is_file()
    refs = _BUNDLED / "references"
    assert refs.is_dir()
    names = {p.name for p in refs.glob("*.md")}
    assert {"tools.md", "examples.md", "fields.md", "troubleshooting.md",
            "configuration.md", "recipes.md", "start.md"} <= names
    manifest = _skill_manifest(_BUNDLED)
    # 语料是 8 份（SKILL.md + 七篇 serve 出去的 references）。第 9 份
    # evals/evals.json 是技能自己的校准跑规格：它进包是为了装完之后技能目录是完整的，
    # 但它不在 guide.TOPICS 里，所以永远不会被当成说明书的一节端出去 —— 下面那条就是
    # 钉这一点的，否则"多塞一份"从一次改包变成一条谎。
    assert len(manifest) == 9
    assert manifest["evals/evals.json"]
    from dhole_mcp.guide import TOPICS
    served = {path for (path, _when) in TOPICS.values()}
    assert "evals/evals.json" not in served, "校准规格不是语料"
    assert all(v for v in manifest.values())


def test_install_copies_everything(tmp_path, capsys):
    target = tmp_path / "skills" / "dhole-web"
    assert _cmd_skill(["install"], target=target) == 0
    assert _skill_manifest(target) == _skill_manifest(_BUNDLED)
    out = capsys.readouterr().out
    assert "skill installed" in out


def test_install_twice_is_up_to_date(tmp_path, capsys):
    target = tmp_path / "sk"
    assert _cmd_skill(["install"], target=target) == 0
    assert _cmd_skill(["install"], target=target) == 0
    assert "already up to date" in capsys.readouterr().out


def test_install_refuses_to_clobber_local_edits(tmp_path, capsys):
    target = tmp_path / "sk"
    assert _cmd_skill(["install"], target=target) == 0
    (target / "SKILL.md").write_text(
        (target / "SKILL.md").read_text(encoding="utf-8") + "\nlocal edit\n",
        encoding="utf-8")
    rc = _cmd_skill(["install"], target=target)
    assert rc == 2
    out = capsys.readouterr().out
    assert "differs" in out and "--force" in out
    # the local edit survived the refusal
    assert "local edit" in (target / "SKILL.md").read_text(encoding="utf-8")


def test_install_force_resyncs(tmp_path, capsys):
    target = tmp_path / "sk"
    assert _cmd_skill(["install"], target=target) == 0
    (target / "SKILL.md").write_text("drifted", encoding="utf-8")
    (target / "references" / "tools.md").unlink()
    assert _cmd_skill(["install", "--force"], target=target) == 0
    assert _skill_manifest(target) == _skill_manifest(_BUNDLED)
    assert (target / "SKILL.md").read_text(encoding="utf-8") != "drifted"


def test_status_reports_state(tmp_path, capsys):
    target = tmp_path / "sk"
    assert _cmd_skill(["status"], target=target) == 0
    assert "not installed" in capsys.readouterr().out
    _cmd_skill(["install"], target=target)
    capsys.readouterr()
    assert _cmd_skill(["status"], target=target) == 0
    assert "up to date" in capsys.readouterr().out


def test_unknown_subcommand_is_rejected(tmp_path, capsys):
    assert _cmd_skill(["bogus"], target=tmp_path / "sk") == 2
    assert "unknown subcommand" in capsys.readouterr().out


def test_manifest_is_content_hashed(tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    (a / "f.md").write_text("one", encoding="utf-8")
    b = tmp_path / "b"
    b.mkdir()
    (b / "f.md").write_text("two", encoding="utf-8")
    assert _skill_manifest(a) != _skill_manifest(b)
