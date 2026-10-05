"""守卫：wire 形状 —— 每工具的参数名与每条参数描述都只能比今天更小。

这两条最初是 17.0 的"形状宪法"（每工具 ≤20 参数名、每条描述 ≤200 字符）。B5 拆工具
时实测发现落不到：`web_read` 收口后仍是 **27** 颗，最长的一条描述是 `sys_guide.topic`
的 **296** 字符。作者裁决**能力优先** —— 与作废 ≤26,000 连接预算是同一句话（"字符多
可以，最重要的是能力"）。所以这里不是删掉守卫，而是把它改成**棘轮**：数字钉在今天
实测的最大值上，只准往下。任何让某颗工具重新变胖、或让某条描述重新变长的改动，都会
被它接住。

真正的**总预算**不在这里，在
`tests/test_wire_moves_land_in_the_manual.py` 的 `CONNECT_CEILING`
（33,460，只降不升）—— 那一条管整条连接期的账，这两条管单颗工具的形状：一颗工具自己
长胖、总账却被别处的收缩抵消掉时，只有这两条看得见。

失败信息**不许写"太长"**（ce8af44：守卫惩罚的是编辑，不是缺陷）。参数超预算指向
拆分 —— 一个工具带着这么多旋钮，说明它在做两件事；描述超预算指向搬家 —— 常驻层在替
说明书（sys_guide 语料）干活，细节应该挪过去，而不是把句子压短。
"""

from dhole_mcp.server import _TOP_LEVEL_ARGS, MasterFetchServer

# 棘轮：实测最大值，只准往下。改小是一次"瘦身"；改大要说明为什么，并同步
# `test_wire_compaction.py` 的 CONNECT_CEILING。
PARAM_CAP = 27   # web_read（其余 12 颗都 ≤19）
DESC_CAP = 296   # sys_guide.topic


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
        "每一次连接都在为它们付 token。把怎么用的细节搬进 sys_guide（sys_guide "
        "语料按需取段），常驻层只留路由需要的判断依据 —— 搬家，不是缩写。"
    )
