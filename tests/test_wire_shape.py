"""守卫：wire 形状 —— 每工具的参数名与每条参数描述都只能比今天更小。

这两条最初是 17.0 的"形状宪法"（每工具 ≤20 参数名、每条描述 ≤200 字符）。B5 拆工具
时实测发现落不到：`web_read` 收口后仍是 **27** 颗，最长的一条描述是 `sys_guide.topic`
的 **296** 字符。作者裁决**能力优先** —— 与作废 ≤26,000 连接预算是同一句话（"字符多
可以，最重要的是能力"）。所以这里不是删掉守卫，而是把它改成**棘轮**：数字钉在今天
实测的最大值上，只准往下。任何让某颗工具重新变胖、或让某条描述重新变长的改动，都会
被它接住。

真正的**总预算**不在这里，在
`tests/test_wire_moves_land_in_the_manual.py` 的 `CONNECT_CEILING`
（钉在实测值，只降不升；数字见该文件的账本，本文件只引用它的模块名 ——
下方守卫的 import 在模块改名/搬家时先红）—— 那一条管整条连接期的账，这两条
管单颗工具的形状：一颗工具自己
长胖、总账却被别处的收缩抵消掉时，只有这两条看得见。

失败信息**不许写"太长"**（ce8af44：守卫惩罚的是编辑，不是缺陷）。参数超预算指向
拆分 —— 一个工具带着这么多旋钮，说明它在做两件事；描述超预算指向搬家 —— 常驻层在替
说明书（sys_guide 语料）干活，细节应该挪过去，而不是把句子压短。
"""

from dhole_mcp.server import _TOP_LEVEL_ARGS, MasterFetchServer

# 棘轮：钉在今天实测最大值上（见下方守卫），只准往下 —— 不写"其余几颗 ≤几"
# 这类次级断言，那类注释会被下一次形状变化悄悄否定。改小是一次"瘦身"；改大要
# 说明为什么，并同步总账那份账本。
PARAM_CAP = 28   # web_read（今天实测最大值，由下方守卫对照）
# 27 → 28 的那一颗是 W1 的 `headers`（账写在
# tests/test_anti_detect_b5.py 的档位面那条注释里）：这批的缺陷原文是"我没设过的头"，
# 而旧拼写 `extra_headers` 是 §3-17 具名拒绝的身份通道 —— 补的是能力面，不是装饰。
# 同一次没有加第二颗伪装旋钮：`stealthy_headers` 由 `anti_detect` 档位表读，
# 公开面上它根本不存在（`rungs.py`）。
DESC_CAP = 296   # sys_guide.topic（同上）


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


def test_the_ratchet_sits_on_todays_maximum_not_a_stale_number():
    """两把棘轮必须钉在**今天**的实测最大值上（F-E-5 的守卫化）。

    曾经 PARAM_CAP 的注释带次级断言「其余 12 颗都 ≤19」，被 web_search 的
    20 参悄悄否定而断言本体照绿 —— 注释漂移无人对照。现在两个常量直接等于
    当日真实最大值：形状变了而棘轮没跟着落，这里先红。
    """
    assert PARAM_CAP == max(len(p) for p in _TOP_LEVEL_ARGS.values()), (
        "PARAM_CAP 不在今天实测最大值上 —— 要么工具瘦了（把棘轮落下来），"
        "要么胖了（先解释为什么，再改这一格与总账）")
    assert DESC_CAP == max(
        len(p.get("description") or "")
        for td in MasterFetchServer._TOOL_DEFS
        for p in (td.get("inputSchema", {}).get("properties") or {}).values()
    ), "DESC_CAP 不在今天实测最大值上 —— 同上"


def test_the_ceiling_reference_is_a_module_name_not_a_number():
    """总账的活引用走 import 不走数字（F-E-4）：本文件 docstring 曾把 ceiling
    引作 33,460 —— 账本此后又动了至少 8 次没人扫。模块改名/搬家时这条先红。"""
    from tests.test_wire_moves_land_in_the_manual import CONNECT_CEILING
    assert isinstance(CONNECT_CEILING, int) and CONNECT_CEILING > 0
