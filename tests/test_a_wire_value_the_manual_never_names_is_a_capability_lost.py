"""一个 wire 取值在手册里没有特征串 = 那个取值对 agent 不存在，用了才算 loss。

症状（2026-10-06 F-E-12 复现）：web_search.mode 的 wire 描述里有
"'find_similar' needs url=."，但 grep -rn "find_similar" 语料 → 0 命中；
extraction_type='check' 同格 —— 线路活着（server.py 分发、changed /
first_divergence_at / diff_window / 7 天基线），手册五类清单没有第六类。

**裸词假绿陷阱（表头注记，本守卫存在的原因之一）**：值级覆盖不能用裸词匹配
—— 对语料查 "check" 返回 True（"Cross-check"、"check the proxy itself" 等
别义命中），而 check 收据的真实特征串 "changed-since-last-fetch" /
"first_divergence_at" 均 0 命中。所以本表配的是**特征串**（该取值独有、
别义文本蹭不上的词），不是枚举字面量本身。

首版两行（F-E-10 / F-E-12）；新增取值时在此表加一行。
"""

from __future__ import annotations

import re

from dhole_mcp import guide

# (能力， 语料里必须出现的特征串)。特征串取「只有真正讲了它才会写出的词」。
CAPABILITY_NEEDLES = [
    ("extraction_type='check' 的变更收据", "first_divergence_at"),
    ("web_search.mode='find_similar' 相似页查找", "find_similar"),
    # 体验修复刀1b-R1：`error` 的新前缀也是一种新取值。线路活着而手册不点名，
    # 调用方就会拿它去 parse 散文，或者更糟 —— 照着"长正文一定不是壳"的旧常识
    # 把它当成成功。特征串取那个前缀本身（手册讲它的时候不可能不写出这个名字）。
    ("error='obfuscated_payload' 的打包脚本壳", "obfuscated_payload"),
    # 体验修复刀1b-R3：降级搬进结论位之后，那句话就是调用方唯一一定会读到的东西。
    # 手册若不再提它，字段级的诚实就退回到"活在 metadata 第 14 行"。
    ("archive 降级写在 summary 里的那一句", "archived copy"),
    # 体验修复刀1b-C1：一次只回起始页的 crawl 有四种成因，选哪一种靠的是链接发现器
    # 交回来的计数，而那计数只活在 summary 那句话里。手册不再写它，调用方就退回
    # "改一下 path_include 再试" —— 一条对其中三种形状完全无效的解药。
    ("crawl 一页不涨时点名成因的那组 summary", "same-domain candidate"),
]


def _mentions(text: str, needle: str) -> bool:
    return re.search(rf"(?<![\w.]){re.escape(needle)}(?![\w])", text) is not None


def test_a_wire_value_the_manual_never_names_is_a_capability_lost():
    corpus = "\n".join(p.read_text(encoding="utf-8")
                       for p in guide._ROOT.rglob("*.md"))
    lost = [cap for cap, needle in CAPABILITY_NEEDLES
            if not _mentions(corpus, needle)]
    assert not lost, (
        f"这些 wire 取值在手册里没有特征串 —— 线路活着，agent 不知道: {lost}。"
        "补进 references/tools.md / fields.md（使用者视角），别用裸词凑覆盖。")
