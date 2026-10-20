"""夹具的"抽出来多少字"这一格，作者是谁。

`tests/page_fixtures/*.meta.json` 里的 `extracted_markdown_chars` 量的不是页面自己，
而是**页面 + 那一版抽取器**的乘积：同一份字节，trafilatura 2.2.0 抽 travel_2 得 1,640
字、2.3.1 抽得 1,566 字；quotes_index 反过来，2,179 → 2,864。所以一个裸数字必然只对
一台机器成立 —— CI 与全新克隆就是这么在 2026-10-20 一起红的（记录值按本机版本量，
runner 装了另一版）。

这一格从此按版本登记：`{"2.2.0": 1640, "2.3.1": 1566}`。查不到当前版本 = 大声失败，
并指名要重测登记 —— 抽取器换版本来就是一次要人看一眼的变化，静悄悄放过它才是这条
守卫存在的理由的反面。

`visible_text_chars` 不在这张表里：那颗数是 dhole 自己从 DOM 量的，与抽取器无关。
"""

from __future__ import annotations

import trafilatura


def recorded_markdown_chars(meta: dict, name: str = "") -> int:
    """取**当前这版 trafilatura** 对应的记录值；没登记就抛，绝不退回"最近的一个数"。"""
    raw = meta.get("extracted_markdown_chars")
    if not isinstance(raw, dict):
        raise AssertionError(
            f"{name or 'fixture'}: extracted_markdown_chars 还是裸数字（{raw!r}）。"
            "这个数只对量它的那版抽取器成立，必须改成按版本登记。")
    v = trafilatura.__version__
    if v not in raw:
        raise AssertionError(
            f"{name or 'fixture'}: 当前 trafilatura {v} 没有登记读数"
            f"（已登记：{sorted(raw)}）。换版抽取器 = 换了一把自己量的尺，"
            "重测这一格的长度并登记，不要改判据。")
    return raw[v]
