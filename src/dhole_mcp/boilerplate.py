"""页内重复样板行的有界折叠。

抓回的正文里，导航 / 页脚 / 订阅条这类样板常常在同一页里出现两三次 ——
trafilatura 的正文抽取去掉了大部分，但"同一句在正文里再说一遍"（页眉一次、
页脚一次）会留下。折叠规则保守到只会误伤极少见的情况：**逐行完全相等**、
行**长度 ≥ `_MIN_LEN`**、出现 **≥ `_MIN_REPEATS`** 次、且**不是** markdown
结构行（标题 / 列表 / 表格 / 引用 / 代码围栏）。保留首次出现，删掉其余，并把
删掉的条数交回调用方（信封上 `duplicate_lines_collapsed`）—— 静默删内容比留着
样板更糟，所以条数必须可观测。

纯函数、零模型：只依赖 stdlib。
"""

from __future__ import annotations

import re

# 太短的行（"Read more"、"1"、分隔符）不碰：它们是结构，不是样板。
_MIN_LEN = 24
# 出现两次就折叠：页眉 + 页脚各一次是最常见的形状。
_MIN_REPEATS = 2
# 结构行不碰 —— 重复可能是有意的（同名的两个小节、表格里的重复取值）。
_STRUCTURAL_PREFIXES = ("#", "-", "*", "+", ">", "|", "```", "~~~")
_ORDERED_ITEM = re.compile(r"\s*\d+[.)]\s")
# 超长正文不做：代价（逐行计数）与收益（样板通常在前几百行）不成比例。
_MAX_LINES = 20_000


def _is_structural(line: str) -> bool:
    if line.lstrip().startswith(_STRUCTURAL_PREFIXES):
        return True
    return bool(_ORDERED_ITEM.match(line))


def collapse_repeated_lines(text: str) -> tuple[str, int]:
    """把重复出现的整行折叠成第一次出现，返回 ``(新文本, 删掉的条数)``。

    空文本、没有重复、或行数超过 :data:`_MAX_LINES` 时原样返回 ``(text, 0)``。
    """
    if not text:
        return text, 0
    lines = text.split("\n")
    if len(lines) > _MAX_LINES:
        return text, 0
    seen: dict[str, int] = {}
    out: list[str] = []
    dropped = 0
    in_fence = False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            # 围栏行本身与围栏内的内容都不碰：代码块里重复的行是代码。
            in_fence = not in_fence
            out.append(line)
            continue
        key = line.strip()
        if (not in_fence and len(key) >= _MIN_LEN
                and not _is_structural(line)):
            n = seen.get(key, 0)
            seen[key] = n + 1
            if n >= _MIN_REPEATS - 1:
                dropped += 1
                continue
        out.append(line)
    if not dropped:
        return text, 0
    return "\n".join(out), dropped
