"""语料是写给 agent 的操作手册，不是变更日志。

维护者视角的元数据——版本号、ISO 日期、决策编号、批次代号、"某个版本删了
什么"的叙事——对执行任务的 agent 是噪音：它不改变任何一次调用该怎么做，还会
把手册变成需要随版本维护的负债。本测试把语料钉在"只谈方向"这一侧：凡是正文
出现这类令牌，直接点名文件与行号。

唯一的豁免：``"since":`` / ``"after":`` 的 JSON 示例载荷行——那是对搜索参数
取值形状的演示（示例里的日期是格式样例），不是历史叙事。
"""

import re

from dhole_mcp.guide import _ROOT

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
