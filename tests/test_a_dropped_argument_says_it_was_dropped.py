# -*- coding: utf-8 -*-
"""被丢掉的参数要说它被丢了（评审 R02-101 · 更正后的那一条）。

入账时这行写得比事实重（"sys_guide 交散文不交信封 → 失败在线上不可判定"）。重测之后
（`review/.runtime/p1/r124_section_object.json` · 档案
`review/round-02/sessions/P02-R25-section-object.jsonl` · HEAD ac4431e · pid 9000）：

  · 散文回执**写在它的线描述里**：`An empty or wrong topic answers with the topic list
    instead of an error.` —— 那是这扇的设计决定，不是漏；本轮也没有一条真回执证明它害到了
    某个程序化调用方（第一个踩到的是本轮自己的量具，那是 A 类）。
  · 真正有毛病的是另一半：`sys_guide(section="timeout")`（不给 topic）与裸问**逐字节相同**
    （sha 同为 2983e98542，1145 字符）—— 那一颗被丢了，整页一个字都没说；而页尾恰恰写着
    `Pass section='<heading text>' to get one section of a volume` —— 一边丢它一边教它。
  · 对照：给了 topic 之后，`section` 不管中不中都有话说 —— `topic='config', section='timeout'`
    交 429 字符并写明 `section='timeout' matched no heading` 并把这一册的标题列出来。

所以这一行降到 P4，修的是**不对称**：同一颗参数在有 volume 的那条路上会解释自己，在没有
volume 的那条路上静默消失。下面每一支都钉这个形状。
"""
from __future__ import annotations

from dhole_mcp import guide

DROPPED = "did nothing here"


class TestADroppedSectionSaysSo:

    def test_a_section_without_a_volume_is_named_as_dropped(self):
        header, body = guide.read(section="timeout")
        assert header == "sys_guide: topics", header
        assert "section='timeout'" in body, body[:300]
        assert DROPPED in body, body[:300]

    def test_the_map_is_still_the_answer(self):
        """修法不许把名册换掉：这一页是错的调用唯一能读到的出路（线描述里就这么承诺）。"""
        header, body = guide.read(section="timeout")
        for t in guide.TOPICS:
            assert f"topic='{t}'" in body, t
        assert "Pass section=" in body, body[-260:]

    def test_an_unknown_topic_with_a_section_says_both(self):
        header, body = guide.read(topic="private", section="timeout")
        assert "unknown topic 'private'." in body, body[:300]
        assert DROPPED in body, body[:300]

    def test_a_bare_call_says_nothing_about_sections(self):
        """控制腿：没传 `section=` 就不许冒出这句话 —— 无中生出这句是另一种误导。"""
        header, body = guide.read()
        assert body.startswith("no topic given."), body[:120]
        assert DROPPED not in body, body[:300]

    def test_a_matched_section_still_narrows_one_volume(self):
        """另一条控制腿：有 topic 时 `section=` 的老行为一字不改（切一册，不解释丢弃）。"""
        full = guide.read(topic="config")[1]
        cut = guide.read(topic="config", section="Variables by scenario")[1]
        assert len(cut) < len(full), (len(cut), len(full))
        assert DROPPED not in cut, cut[:200]

    def test_an_unmatched_section_inside_a_volume_still_lists_headings(self):
        """这一支读**两半**：`matched no heading` 住在 header，标题清单住在 body。

        （第一版只读 body 就红了一格 —— 判据自己的错，与被测产品无关：那句解释本来就
        在，只是不在那一半里。）
        """
        header, body = guide.read(topic="config", section="timeout")
        assert "matched no heading" in header, header[:200]
        assert "Headings in this volume:" in body, body[:300]
        assert DROPPED not in f"{header}\n{body}", f"{header}\n{body}"[:300]


class TestTheSentenceHasOneAuthor:
    def test_only_the_no_volume_branch_speaks_it(self):
        """`matched no heading` 与 `did nothing here` 说的是两件事，不许并成一句。

        前者：volume 在、标题没对上（出路是列标题）；后者：连 volume 都没有。合成一句就会
        在只有 volume 的那条路上谎报"没有 volume 可切"。
        """
        src = guide.read.__code__.co_consts
        both = [c for c in src if isinstance(c, str) and DROPPED in c]
        assert len(both) == 1, both
