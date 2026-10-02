"""守卫：wire 形状 —— 每工具 ≤20 个参数名，每条参数描述 ≤200 字符。

这两条是大众化一刀切（17.0）的形状宪法。B4 已经把 `options` 袋拆平（袋子本身
没了，常驻层也不再有一段 2,800 字符的散文），但它只是把旋钮摊开：今天
`smart_fetch` 一家仍带 44 个参数名，13 条描述超过 200 字符（最长 858）。所以
整文件继续标 ``xfail(strict=True)``：现在记 xfail（套件保持绿），B5 拆工具 +
瘦身落地的那一刻 XPASS 会把套件打红，逼着有人来摘掉这块牌。摘牌的时候这两条
守卫就是常驻规则，任何让工具重新变胖的改动都会被它接住。

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
    reason="B5（拆工具 + 描述瘦身）之前必然不成立：袋已在 B4 拆平，smart_fetch "
           "仍带 44 个参数名、13 条描述超预算。拆完的那一批摘掉这块牌。",
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
