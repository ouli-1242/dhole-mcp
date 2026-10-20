# -*- coding: utf-8 -*-
"""R02-062 · 已经写成绝对形式的输入，不该再被建议「换成绝对形式」。

现场（2026-10-17 01:59 实测两发，原文在 `review/round-02/sessions/calls.jsonl`）：

    doc_parse(file_path="D:/Dev/Tools/dhole-mcp/review/.runtime/nope-absolute.txt")
      error       = "File not found: <server cwd>/review/.runtime/nope-absolute.txt.
                     Pass an absolute path, or pass cwd=<working directory> so relative
                     paths resolve against it."
      next_action = "See the error field for the paths that were tried. Pass an absolute
                     path, or pass cwd=<your working directory> so relative paths resolve
                     against it."
      diagnosis.recover_with = "pass an absolute path, or pass cwd="

那一发传进来的**本来就是绝对路径**，于是同一句话在这封回执里说了三遍，而每一遍都不能改变
任何事。更糟的是第二扇门：`cwd=` 对绝对路径不生效 —— 这不是我的推断，是**工具自己的 schema**
说的（`server.py:10851`："Base directory for a relative file_path. Ignored for absolute paths."）。
一句出路点名了一颗按它自己的定义在这发上不起作用的参数，这就是假出路。

同族先例已在册：`quality.py:1680-1690` 的 "Path is a directory" 那一支（R02-F48 / R02-026）
已经按「路径解析成功了就别再让人改写法」改过 —— 本条是同一族里漏掉的另一支：**写法没错，
文件不在**。

修法的方向（一位作者）：形状只在 `server` 那一侧当场判一次（`os.path.isabs(file_path)`），
由它决定 `error` 里那句出路；下游两处（`quality` 的 next_action 与 diagnosis 表格那行的
recover_with）**不再复述出路**，改为指回 `error` 说的那几处与那一颗还能改变答案的参数。
理由不是省字：三处复述就是三个抄本，任何一处按形状改了而另两处没改，回执就自相矛盾
（本轮 R02-046 / R02-050 那一族的反面正是「同一维两处各数一遍」）。

不许有的东西（反面锁）：
  * 不许按具体盘符、目录或文件名特判 —— 判据只有「传进来的那一串是不是绝对形式」这一维；
  * 不许改判决：两种形状都还是 `code=file_not_found`、`content_ok=false`、都逐字列出被找过的那几处；
  * 不许放松相对那一支：那句两扇门在 `error` 里一字不改（旧测试也钉着它）；
  * 不加新字段、不动 wire 形状 —— 这改的是说法，不是数据。
"""

from __future__ import annotations

import asyncio
import os

import pytest

from dhole_mcp.server import MasterFetchServer

ADVICE = "pass an absolute path"


def _parse(**kw):
    return asyncio.run(MasterFetchServer().doc_parse(**kw))


def _all_text(r) -> str:
    """整封回执里会被 agent 读到的散文（判决字段除外都是文案）。"""
    diag = r.diagnosis or {}
    return " ".join(x or "" for x in (r.error, r.next_action, r.summary,
                                      diag.get("recover_with"), diag.get("why")))


def _relative_name(tmp_path) -> str:
    """一颗相对写法的名（不在任何根里），让 server 走「按几个根试过」那一支。"""
    name = "r02-062-gone.txt"
    for root in (os.getcwd(), os.path.expanduser("~")):
        if os.path.exists(os.path.join(root, name)):
            pytest.fail(f"测试环境里 {root} 下已经有 {name}，换一颗名字再跑")
    return name


# ── 1 · 绝对那一发：不许再教人换绝对形式 ────────────────────────────────────

def test_an_absolute_path_is_not_told_to_become_absolute(tmp_path):
    gone = str(tmp_path / "nope-absolute.txt")
    assert os.path.isabs(gone), "这一格的前提是传进去的就是绝对形式"

    r = _parse(file_path=gone)

    assert r.error.startswith("File not found"), r.error
    assert ADVICE not in r.error.lower(), f"已经是绝对形式还被建议换绝对：{r.error[:220]}"
    assert ADVICE not in (r.next_action or "").lower(), r.next_action
    assert ADVICE not in ((r.diagnosis or {}).get("recover_with") or "").lower(), r.diagnosis


def test_the_absolute_answer_names_the_door_that_actually_moves(tmp_path):
    """出路要说得出**这一发**还有什么可动：名字本身，或点名目录 + 里面的文件。"""
    r = _parse(file_path=str(tmp_path / "nope-absolute.txt"))

    said = r.error.lower()
    assert "cwd" in said, f"没交代 `cwd=` 在这一发上的作用：{r.error[:240]}"
    assert "ignored" in said or "cannot" in said or "does not" in said, (
        f"那句关于 `cwd=` 的话没有把『不生效』说出口：{r.error[:240]}")
    assert "name" in said, f"没点名真正那扇门（名字/位置）：{r.error[:240]}"


def test_the_shape_judgment_is_not_a_path_special_case(tmp_path):
    """同一形状换拼法（反斜杠 / 正斜杠、另一颗盘符式）走同一支 —— 判据只有形状这一维。"""
    a = _parse(file_path=str(tmp_path / "one.txt"))
    b = _parse(file_path=(tmp_path / "two.txt").as_posix())

    for r in (a, b):
        assert ADVICE not in r.error.lower(), r.error[:200]
    # 两句同形：只差在被找的那一处，出路部分必须一致
    assert a.error.split(". ")[-1] == b.error.split(". ")[-1], (a.error, b.error)


# ── 2 · 相对那一发：一个字都不许放松 ────────────────────────────────────────

def test_a_relative_path_still_gets_both_doors_verbatim(tmp_path):
    r = _parse(file_path=_relative_name(tmp_path))

    assert r.error.startswith("File not found"), r.error
    assert "Pass an absolute path, or pass cwd=<working directory> so relative paths " \
           "resolve against it." in r.error, r.error
    assert r.error.lower().count(ADVICE) == 1, r.error


# ── 3 · 一位作者：出路在整封回执里只说一遍 ─────────────────────────────────

def test_the_way_out_is_written_by_one_author_not_three(tmp_path):
    """相对那一发：两句复述从三个抄本收成一位作者（指回 `error` 不算复述）。"""
    rel = _parse(file_path=_relative_name(tmp_path))
    assert _all_text(rel).lower().count(ADVICE) == 1, _all_text(rel)[:600]

    ab = _parse(file_path=str(tmp_path / "nope.txt"))
    assert _all_text(ab).lower().count(ADVICE) == 0, _all_text(ab)[:600]


def test_downstream_points_at_error_instead_of_guessing_the_shape(tmp_path):
    """下游两处不复述出路，但必须把读者送去那一句真的说对了的地方。"""
    for kw in ({"file_path": str(tmp_path / "nope.txt")},
               {"file_path": _relative_name(tmp_path)}):
        r = _parse(**kw)
        assert "error" in (r.next_action or "").lower(), r.next_action
        assert "error" in ((r.diagnosis or {}).get("recover_with") or "").lower(), r.diagnosis


# ── 4 · 判决与形状不许被文案带走 ────────────────────────────────────────────

def test_the_verdict_and_the_tried_places_survive_the_wording(tmp_path):
    ab = _parse(file_path=str(tmp_path / "nope-absolute.txt"))
    rel = _parse(file_path=_relative_name(tmp_path))

    for r in (ab, rel):
        assert r.status == 0 and not r.content, r
        assert r.content_ok is False, r
        assert (r.diagnosis or {}).get("code") == "file_not_found", r.diagnosis
        assert "nope-absolute.txt" in r.error or "r02-062-gone.txt" in r.error, r.error

    # 绝对那一发只在一处找过；相对那一发按几个根试过 —— 这个差别不许被抹平
    # （读「被找过的哪几处」那一段，不数整句里的逗号 —— 文案会改，形状不会）
    ab_tried = ab.error.split(". ")[0]
    rel_tried = rel.error.split(". ")[0]
    assert ", " not in ab_tried, ab_tried
    assert ", " in rel_tried, rel_tried


# ── 5 · 手册要说得出这件事（B-16 那一族：字段有话，手册没册）─────────────────

def test_the_handbook_names_the_two_shapes_and_the_one_author():
    """`tools.md` 的 doc_parse 那一节要说得出：两种形状两种出路，而出路只有一位作者。"""
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "src" / "dhole_mcp" / "skills"
            / "dhole-web" / "references" / "tools.md").read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines()
             if "genuinely missing file keeps its own sentence" in ln]
    assert len(lines) == 1, f"那段话丢了或被拆成多行：{len(lines)}"
    said = lines[0]
    assert "already absolute" in said and "ignored for an absolute path" in said, said[-700:]
    assert "one author" in said, said[-700:]
    # 手册不许把自己写成第二个抄本：它引用相对那一句是「说明这一发听到什么」，
    # 但绝对那一支必须说得出「两句出路都不给」，否则读者仍旧去找那句自相矛盾的话
    assert "gets neither" in said, said[-700:]
