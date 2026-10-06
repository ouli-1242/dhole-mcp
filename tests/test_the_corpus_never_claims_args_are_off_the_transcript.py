"""语料把「值不进转录」说成了无条件 —— 传参本身就是进转录。

症状（2026-10-06 F-C1-5 复现）：SKILL.md 第 13 条标题断言
「Credential values never enter the transcript」，examples.md 同句复述。
但 `cred_defaults(set={...})` 的值就在那次调用的参数里 —— 已经在转录中；
`web_request(auth=...)`、`cookies=...` 同理。存储的真实承诺只有半句：
存进去的值不会**回来**（回执只报名字与 host 范围）。

判词：把假的那半句删掉，换成双向真话 —— 存储不回吐；传参在转录里；值必须
完全不出现时，让操作员预配置。同刀捎带 SKILL.md 第 11 条的同胞格：attach
「is audit-logged」是无条件断言，而审计通道默认关（DHOLE_USAGE_LOG，默认
off —— no log target, no record），说的是模型的属性，不是这硅的。
"""

from __future__ import annotations

import pathlib

CORPUS = pathlib.Path(__file__).resolve().parents[1] / "src" / "dhole_mcp" / "skills" / "dhole-web"


def _corpus_files():
    files = [CORPUS / "SKILL.md"]
    refs = CORPUS / "references"
    files.extend(sorted(refs.glob("*.md")))
    return [p for p in files if p.exists()]


def test_the_corpus_never_claims_args_are_off_the_transcript():
    offenders = []
    for path in _corpus_files():
        if "never enter the transcript" in path.read_text(encoding="utf-8"):
            offenders.append(path.name)
    assert not offenders, (
        f"{offenders} 仍在断言值不进转录 —— set= 调用自己的参数就在转录里，"
        "这句是无条件假话；换成双向真话（存储不回吐，传参在转录里）")


def test_the_skill_says_values_never_come_back_out_of_the_store():
    skill = (CORPUS / "SKILL.md").read_text(encoding="utf-8")
    assert "Values never come back out of the store" in skill
    assert "pre-configure" in skill, "值必须完全不出现时的那条路（操作员预配置）要说出口"


def test_the_examples_parenthetical_tells_both_halves():
    examples = (CORPUS / "references" / "examples.md").read_text(encoding="utf-8")
    assert "the store never echoes values back" in examples


def test_the_skill_does_not_claim_an_unconditional_audit_log():
    skill = (CORPUS / "SKILL.md").read_text(encoding="utf-8")
    assert "audit-logged" not in skill, (
        "审计通道默认关（DHOLE_USAGE_LOG，默认 off）—— 无条件断言换成条件句")
    assert "DHOLE_USAGE_LOG" in skill
