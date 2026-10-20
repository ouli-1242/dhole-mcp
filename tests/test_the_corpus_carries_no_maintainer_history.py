"""语料是写给 agent 的操作手册，不是变更日志。

维护者视角的元数据——版本号、ISO 日期、决策编号、批次代号、"某个版本删了
什么"的叙事——对执行任务的 agent 是噪音：它不改变任何一次调用该怎么做，还会
把手册变成需要随版本维护的负债。本测试把语料钉在"只谈方向"这一侧：凡是正文
出现这类令牌，直接点名文件与行号。

唯一的豁免：``"since":`` / ``"after":`` 的 JSON 示例载荷行——那是对搜索参数
取值形状的演示（示例里的日期是格式样例），不是历史叙事。
"""

import ast
import re
from pathlib import Path

import dhole_mcp
from dhole_mcp.guide import _ROOT

PKG = Path(dhole_mcp.__file__).parent          # src/dhole_mcp
REPO = PKG.parent.parent                        # 仓库根（报告里用相对路径）

FILES = [
    "SKILL.md",
    "references/tools.md",
    "references/configuration.md",
    "references/examples.md",
    "references/troubleshooting.md",
    "references/recipes.md",
    "references/fields.md",
]

# 版本号：17.0 / 16.x / 17.1（排除 12.5s 这类时长与 198.18.0.0 这类 IP 段）
_VERSION = r"(?<![\d.])1[0-9]\.[0-9xX]+(?![\d.s])"
_TOKEN = re.compile(
    _VERSION
    + r"|\u00a7\s?[0-9]"        # § 条目引用
    + r"|[Dd]ecision [0-9]+"    # 决策编号
    + r"|\u6279\u6b21 ?[0-9]+"  # 批次代号
    + r"|\bG[0-9]{1,2}\b|\bM[0-9]{1,2}\b|\bB[0-9]{1,2}\b"  # G/M/B 批次代号
    + r"|20[0-9]{2}-[0-9]{2}-[0-9]{2}"                     # ISO 日期
)


def _offending_lines() -> list[str]:
    offenders = []
    for rel in FILES:
        for i, line in enumerate(
            (_ROOT / rel).read_text(encoding="utf-8").splitlines(), 1
        ):
            if _TOKEN.search(line) and not (
                '"since":' in line or '"after":' in line
            ):
                offenders.append(f"{rel}:{i}: {line.strip()[:140]}")
    return offenders


def test_corpus_lines_carry_no_version_or_decision_tokens():
    offenders = _offending_lines()
    assert not offenders, (
        "语料里出现了维护者才需要的元数据（版本号/日期/决策编号/批次代号/§引用）"
        "——改写成对 agent 有操作意义的说法，或直接删掉：\n" + "\n".join(offenders)
    )


# ── 门的另一条腿：会走到 wire 上的那批句子 ────────────────────────────────
#
# 手册七份之外，`src/**.py` 里还有一批文本是**产品自己发出去的话**：`error=` /
# `next_action=` / `diagnosis=` / `summary=` / `ArgumentRefusal(...)` /
# `logger.info|warning|error(...)`。R02-F49 之前有三处版本口吻就在这条腿上待了很久
# 而没人拦 —— 门只扫手册，而这三句从来不在手册里（R02-064 记的就是这个覆盖面洞）。
#
# 判据同一条 `_TOKEN`，不改判只扩面；注释与 docstring 不算（它们不是发出去的话），
# 豁免也照手册那条的**形状**来：日期作为取值示例出现在 wire 句子里是操作信息
# （"写成 '2026-09-20' 那样"），不是历史叙事——所以只豁免 `like '<ISO 日期>'` 这一种写法，
# 其余位置的日期仍算撞令牌。

_WIRE_KEYWORDS = {"error", "next_action", "diagnosis", "summary", "message",
                  "reason", "advice", "hint", "detail"}
_WIRE_LOGGER_METHODS = {"info", "warning", "error"}
_EXAMPLE_DATE = re.compile(r"like\s+[\"']20[0-9]{2}-[0-9]{2}-[0-9]{2}")


def _wire_string_leaves(node):
    """一个调用位里所有会发出去的字符串叶子（f-string 的每一段也算）。"""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            yield sub.value, sub.lineno


def _is_wire_site(call):
    func = call.func
    name = getattr(func, "attr", "") or getattr(func, "id", "")
    if any(kw.arg in _WIRE_KEYWORDS for kw in call.keywords):
        return True
    if name in {"ArgumentRefusal", "SecurityError", "_note_text"}:
        return True
    return name in _WIRE_LOGGER_METHODS


def _offending_wire_strings():
    offenders = []
    root = PKG
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = path.relative_to(REPO).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _is_wire_site(node):
                continue
            args = list(node.args) + [kw.value for kw in node.keywords]
            for arg in args:
                for text, line in _wire_string_leaves(arg):
                    if not _TOKEN.search(text):
                        continue
                    if _EXAMPLE_DATE.search(text):
                        continue   # 取值示例是操作信息，与手册那条豁免同判据
                    offenders.append(f"{rel}:{line}: {text.strip()[:140]}")
    return offenders


def test_wire_sentences_carry_no_maintainer_history():
    offenders = _offending_wire_strings()
    assert not offenders, (
        "发出去的话里出现了维护者才需要的元数据（版本号/日期/决策编号/批次代号/§引用）。"
        "调用方不在某个版本上，这些句子对它既是噪音也是假话——改写成说清"
        "「现在这是什么形状」的说法：\n" + "\n".join(offenders)
    )


def test_the_wire_leg_still_scans_the_product_sources():
    """覆盖面自己也要被钉住：门不许哪天悄悄缩回只剩手册那七份。"""
    scanned = {p.relative_to(REPO).as_posix() for p in PKG.rglob("*.py")}
    for needed in ("src/dhole_mcp/identity.py", "src/dhole_mcp/sessions.py",
                   "src/dhole_mcp/search.py", "src/dhole_mcp/server.py"):
        assert needed in scanned, needed
    # 反例：一个纯版本口吻的句子必须被这条腿抓出来（否则等于门是开的）
    probe = 'logger.info("migrated the pre-17.1 identity.db")'
    tree = ast.parse(probe)
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
    assert _is_wire_site(call) and _TOKEN.search("migrated the pre-17.1 identity.db")
