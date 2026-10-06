"""一颗 wire 参数在手册里查无此人 = 这颗参数对只读手册的 agent 不存在。

症状（2026-10-06 F-E-11 复现）：dump 11 颗工具共 108 参数，用词边界匹配对全
语料（SKILL.md ＋ references/*.md）逐颗比对 → 6 颗查无此人：doc_parse.cwd、
web_crawl.max_depth、web_search 的 exclude_sites / fetch_schema / max_results /
region。同法跑 web_read 的 26 颗选项参数 → 0 缺席（其守卫
test_every_smart_fetch_key_is_explained_somewhere 在岗）——其余 10 颗工具没有
对应的语料覆盖断言，这正是 fetch_schema 复发（W10 同款缝）的原因。

守卫口径（照 test_guide.py:176 的 _mentions 词边界匹配；表面**只用语料**，
不许掺 wire 文本——参数名在 wire 上本来就出现，掺了就是同义反复的自查）。
"""

from __future__ import annotations

import re

import pytest

from dhole_mcp import guide
from dhole_mcp.server import _TOP_LEVEL_ARGS


def _mentions(text: str, key: str) -> bool:
    """按词边界找键名：`wait` 不该被 `network_idle` 里的一段算成已解释。"""
    return re.search(rf"(?<![\w.]){re.escape(key)}(?![\w])", text) is not None


def _corpus() -> str:
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in guide._ROOT.rglob("*.md"))


def test_the_parameter_coverage_surface_is_the_corpus_alone():
    """机制自检：表面若混进 wire 文本，覆盖率永远假绿；语料空了同理。"""
    corpus = _corpus()
    assert len(corpus) > 20_000, "语料空了：参数覆盖率会伪装成通过"
    assert not _mentions("network_idle and waits", "wait")
    assert _mentions("hops from the start URL: max_depth", "max_depth")


@pytest.mark.parametrize("tool", sorted(_TOP_LEVEL_ARGS))
def test_every_wire_parameter_is_findable_in_the_manual(tool):
    corpus = _corpus()
    missing = sorted(k for k in _TOP_LEVEL_ARGS[tool]
                     if not _mentions(corpus, k))
    assert not missing, (
        f"{tool} 的这些参数在全语料查无此人 —— 只读手册的 agent 不知道它们存在: "
        f"{missing}。补进 references/tools.md 对应节（使用者视角一句），"
        "或从 _TOP_LEVEL_ARGS 里删掉。")
