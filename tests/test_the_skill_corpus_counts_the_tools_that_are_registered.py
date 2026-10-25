"""R01-009：skill 文案说的工具数必须等于注册表里的工具数。

现场（2026-10-25 · P03 Sarah · S2 sys_guide 求助 · review/round-01）：
start 路标页开头「dhole is eleven tools」、SKILL.md「ten everyday tools … All
eleven are registered」——`tools/list` 里躺着 **13** 颗（web_research /
web_recall 上线时文案没跟）。一个数错的路标，读者会拿它去怀疑没列出的那两颗
"是不是不该用"。

references/ 两个深处卷（tools.md / configuration.md）早就写了 13；漂移的只有
首页这两处 —— 恰恰是每个读者都读、深卷没人读的那两处。

契约（本文件钉住）：
  * start.md 与 SKILL.md 必须点名注册表里**每一颗**工具（按名字）；
  * 两份文案里不许再出现任何写死的旧计数（eleven / ten everyday）；
  * 计数句要么不写数，写就必须与 `MasterFetchServer._TOOL_DEFS` 同源 —— 本测
    试用注册表反查，文案再漂一格就会红。
"""

from __future__ import annotations

from pathlib import Path

from dhole_mcp.server import MasterFetchServer

SKILL_DIR = Path(__file__).resolve().parent.parent / "src" / "dhole_mcp" / "skills" / "dhole-web"


def _text(rel: str) -> str:
    return (SKILL_DIR / rel).read_text(encoding="utf-8")


def _registered_names() -> set[str]:
    return {d["name"] for d in MasterFetchServer._TOOL_DEFS}


def test_start_names_every_registered_tool():
    names = _registered_names()
    assert len(names) >= 13, "注册表面变了：先数清再改文案"
    start = _text("references/start.md")
    missing = sorted(n for n in names if n not in start)
    assert not missing, f"start 路标页没点名这些工具：{missing}"


def test_skill_md_names_every_registered_tool():
    names = _registered_names()
    skill = _text("SKILL.md")
    missing = sorted(n for n in names if n not in skill)
    assert not missing, f"SKILL.md 没点名这些工具：{missing}"


def test_no_stale_counts_survive_in_the_two_front_pages():
    for rel in ("references/start.md", "SKILL.md"):
        text = _text(rel)
        assert "eleven tools" not in text, f"{rel} 还在说 eleven tools"
        assert "ten everyday tools" not in text, f"{rel} 还在说 ten everyday tools"


def test_the_count_sentence_agrees_with_the_registry():
    """写死的计数句必须有，且数得对 —— 这一条让「13」从文案变成对账。"""
    count = len(_registered_names())
    start = _text("references/start.md")
    assert f"is {count} tools" in start, f"start.md 没写 {count} tools"
    skill = _text("SKILL.md")
    assert f"All {count} are registered" in skill, f"SKILL.md 没写 All {count} are registered"
