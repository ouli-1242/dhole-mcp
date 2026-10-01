"""守卫：wire 形状 —— 每工具 ≤20 个参数名，每条参数描述 ≤200 字符。

这两条是大众化一刀切（17.0）的形状宪法。今天（B4/B5 落地前）它**必然**不成立：
`smart_fetch` 一家就收 57 个参数名，`options` 袋的描述一件事写了 2,800 字符 ——
所以整文件标 ``xfail(strict=True)``：现在记 xfail（套件保持绿），B4/B5 拆平参数
通道的那一刻 XPASS 会把套件打红，逼着有人来摘掉这块牌。摘牌的时候这两条守卫
就是常驻规则，任何让工具重新变胖的改动都会被它接住。

失败信息**不许写"太长"**（ce8af44：守卫惩罚的是编辑，不是缺陷）。参数超预算
指向拆分 —— 一个工具带着这么多旋钮，说明它在做两件事；描述超预算指向搬家 ——
常驻层在替说明书（sys_guide / dhole_doc 语料）干活，细节应该挪过去，而不是把
句子压短。
"""

import pytest

from dhole_mcp.server import _TOP_LEVEL_ARGS, MasterFetchServer

PARAM_CAP = 20
DESC_CAP = 200

pytestmark = pytest.mark.xfail(
    strict=True,
    reason="B4/B5（拆平参数通道 + 拆工具）之前必然不成立：options 袋还在。"
           "拆平落地的同一批摘掉这块牌。",
)


def test_each_tool_stays_inside_the_param_budget():
    offenders = sorted(
        (name, len(params)) for name, params in _TOP_LEVEL_ARGS.items()
        if len(params) > PARAM_CAP
    )
    assert not offenders, (
        f"{offenders} —— 上限 {PARAM_CAP} 个参数名。一个工具带这么多旋钮，"
        "说明它在做两件事：沿着职责边界把它拆开，而不是把参数挪进 options 袋 "
        "（袋子只是把同一样东西换个地方数）。"
    )


def test_each_param_description_leaves_the_manual_to_the_guide():
    offenders = []
    for td in MasterFetchServer._TOOL_DEFS:
        tool = td["name"]
        for pname, pschema in (td.get("inputSchema", {}).get("properties") or {}).items():
            desc = pschema.get("description") or ""
            if len(desc) > DESC_CAP:
                offenders.append((tool, pname, len(desc)))
    assert not offenders, (
        f"{offenders} —— 上限每条 {DESC_CAP} 字符。这些描述在常驻层替说明书干活："
        "每一次连接都在为它们付 token。把怎么用的细节搬进 sys_guide（dhole_doc "
        "语料按需取段），常驻层只留路由需要的判断依据 —— 搬家，不是缩写。"
    )
