# -*- coding: utf-8 -*-
"""一次缓存回放不许做第二个作者：写进去的那几颗判决，读出来必须还是那几颗。

实测（2026-10-11，靶站 `/shell`）：live 那一发量到整页 6 个可见字、判 `nav_only`、
`content_ok=false`；第二次读同一颗 URL 命中缓存，拿回的是 `content_ok=true`、`substance`
空、`visible_text_chars=0`。三处都不是"缓存把旧答案交回来了"，而是**判决的输入没跟着进
缓存**：`quality._agent_hints` 每次都从 status/content **重算** `content_ok`，而 3.4 那颗
闸读的是 `substance` —— 重算时看不见它，绿就发了出去，而这一发一个包都没发。同一颗 URL
两个答案，正是本仓最防的那类错（"一句没有检查支撑的绿灯"换了主语：一次没有判据的绿灯）。

这一条守卫钉的是**两处必须共认同一组键**：写入侧的 `envelope={…}` 与回放侧的
`ResponseModel(…)`。用源码对照而不是用假缓存跑一遍，是因为跑一遍只覆盖被打到的那条分支，
而这一族的失效方式恰好是"少搬一颗键"——少搬的那颗不会红，只会静默回到默认值（`""` / `0` /
`False`，而 `content_ok` 的默认在重算里会变成 `True`）。这与
`test_a_low_trust_mark_moves_no_row_and_drops_no_row.py` 读四个函数源码钉"那颗字段名一次
都不许出现"是同一个手法。
"""

from __future__ import annotations

import re
from pathlib import Path

SERVER = (Path(__file__).resolve().parents[1] / "src" / "dhole_mcp" / "server.py"
          ).read_text(encoding="utf-8")

# 判决的输入：少任何一颗，回放都会重新推导一次调用方看不见的东西。
VERDICT_KEYS = ("content_ok", "substance", "substance_basis", "visible_text_chars")


def _block(start_marker: str, needle: str) -> str:
    """从 `start_marker` 之后找 `needle`（以一颗开括号结尾），取那段配平的括号体。"""
    i = SERVER.index(start_marker)
    j = SERVER.index(needle, i) + len(needle) - 1   # 停在开括号那颗字符上
    opening = SERVER[j]
    closing = {"{": "}", "(": ")"}[opening]
    depth, k = 0, j
    while k < len(SERVER):
        ch = SERVER[k]
        if ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                return SERVER[j:k + 1]
        k += 1
    raise AssertionError(f"没找到 {start_marker} 之后的括号体")


def test_every_verdict_input_that_the_gate_reads_is_written_into_the_envelope():
    writer = _block("async def _finalize_result", "envelope={")
    missing = [k for k in VERDICT_KEYS if re.search(rf'"{k}"\s*:', writer) is None]
    assert not missing, f"缓存写入侧没存下判决的输入 {missing} —— 回放只能重猜"


def test_the_replay_restores_each_of_those_inputs_rather_than_defaulting_them():
    reader = _block("async def _cache_hit_response", "ResponseModel(")
    missing = [k for k in VERDICT_KEYS if re.search(rf"\b{k}=", reader) is None]
    assert not missing, f"缓存回放侧没还原 {missing} —— 它们会取默认值，而默认值不是缺席"


def test_the_two_sides_agree_on_the_same_set_of_keys():
    """两处各说各话就是第二个作者：写的比读的多一颗，那一颗在线上永远取不到。"""
    writer = set(re.findall(r'"(\w+)"\s*:', _block("async def _finalize_result", "envelope={")))
    reader = set(re.findall(r"\b(\w+)=", _block("async def _cache_hit_response", "ResponseModel(")))
    assert set(VERDICT_KEYS) <= writer and set(VERDICT_KEYS) <= reader
    drifted = {k for k in VERDICT_KEYS if (k in writer) != (k in reader)}
    assert not drifted, f"一处存一处不存：{drifted}"
