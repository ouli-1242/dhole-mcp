"""守卫：wire 形状 —— 每工具的参数名与每条参数描述都只能比今天更小。

这两条最初是 17.0 的"形状宪法"（每工具 ≤20 参数名、每条描述 ≤200 字符）。B5 拆工具
时实测发现落不到：`web_read` 收口后仍是 **27** 颗，最长的一条描述是 `sys_guide.topic`
的 **296** 字符。作者裁决**能力优先** —— 与作废 ≤26,000 连接预算是同一句话（"字符多
可以，最重要的是能力"）。所以这里不是删掉守卫，而是把它改成**棘轮**：数字钉在今天
实测的最大值上，只准往下。任何让某颗工具重新变胖、或让某条描述重新变长的改动，都会
被它接住。

两条只钉"只准变小"：数字是今天实测的最大值，不是目标。连接期的总账不进测试 ——
它每改一句描述就要来改一次，而它从没拦住过任何事故；量尺与真机同值那条
（`test_server_3.py`）已经守住了"账算得对"。

失败信息**不许写"太长"**（ce8af44：守卫惩罚的是编辑，不是缺陷）。参数超预算指向
拆分 —— 一个工具带着这么多旋钮，说明它在做两件事；描述超预算指向搬家 —— 常驻层在替
说明书（sys_guide 语料）干活，细节应该挪过去，而不是把句子压短。
"""

from dhole_mcp.server import _TOP_LEVEL_ARGS, MasterFetchServer

# 棘轮：钉在今天实测最大值上（见下方守卫），只准往下 —— 不写"其余几颗 ≤几"
# 这类次级断言，那类注释会被下一次形状变化悄悄否定。改小是一次"瘦身"；改大要
# 说明为什么，并同步总账那份账本。
PARAM_CAP = 31   # web_read（今天实测最大值，由下方守卫对照）
# 27 → 28 的那一颗是 W1 的 `headers`（账写在
# tests/test_anti_detect_b5.py 的档位面那条注释里）：这批的缺陷原文是"我没设过的头"，
# 而旧拼写 `extra_headers` 是 §3-17 具名拒绝的身份通道 —— 补的是能力面，不是装饰。
# 同一次没有加第二颗伪装旋钮：`stealthy_headers` 由 `anti_detect` 档位表读，
# 公开面上它根本不存在（`rungs.py`）。
#
# 28 → 31（2026-10-23，R3 + R4）：`include_headers` / `save_to` / `max_save_bytes`。
# 三颗一次抬上去，所以这条注释必须交代清楚它们是不是"一个工具在做两件事"。
#
# 它们不是三颗无关的旋钮，是**同一扇门的第三种产出**。`web_read` 从前只会交出一样
# 东西：抽出来的正文。这三颗把另外两样也接上了 —— 这一发的**证据**（状态行、响应头、
# 逐跳 302 链）与这一发的**字节**（原样落盘）。三种产出共用同一条链路（同一份守卫、
# 同一个缓存判定、同一套身份与反爬台阶），而这条链路只有一份；把它们拆去别的工具，
# 拆的不是职责，是复制。
#
# 拆成第四颗工具的真实价格也量过：一颗新工具要付自己的描述 + 路由行 ≈ 2,300 字符
# （对照 `web_request` 今天的 2,824），而这三颗在常驻层一共 531 字符。差一个数量级，
# 而且连接期是每一个会话付一次的那笔账（`CONNECT_CEILING`）。
#
# 所以这次抬的是上限，不是判据：**下一个人要往 `web_read` 上再加一颗时，先回答
# "这是第四种产出，还是第三种产出的一个旋钮"**。答不上来就该拆工具 —— 31 不是新常态，
# 这一次的账就这些。
DESC_CAP = 288   # web_act.headed（同上；sys_guide.topic 拆分后降到 285）
# R3/R4 之后最长的一条仍是它：`web_read.headers` 291、`web_read.schema` 290、
# `web_read.include_headers` 289 —— 三颗都贴着 292 的下沿，没有一颗推开它。


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
